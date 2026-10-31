#!/usr/bin/env python3
"""verifier.py - the Planner 4.0 verification pipeline for ONE worker attempt of ONE ticket scope.

    python verifier.py run --ticket PLT-4092-ESCROW-SETTLEMENT --scope worker_backend [--root .] [--workspace DIR]

It is the single place that decides "is this attempt acceptable?". The orchestrator calls `verify()`; a worker may
call the same command as its own pre-commit check (`--level self`), which runs everything except the sealed oracle.

Checks, in order (each returns pass | fail | inconclusive | na | waived; INCONCLUSIVE is never a pass):

  contract    signed ticket is valid; its hash equals what AG1 registered on the blackboard (root of trust)
  scope       every changed/deleted file is inside this scope's allowed_paths (nothing protected, nothing foreign)
  secrets     no key/token/private-key material was added
  ports       MASP: binds inside this worker's own port ranges, no connection into another worker's range
  forbidden   forbidden_ast (SQL types) and forbidden_calls were not newly introduced
  symbols     every locked SCIP symbol is still declared with exactly the locked signature
  invariants  declared formal invariants are present (SQL CHECKs) / have a sealed test (INV-NN ids)
  weakening   AST anti-weakening audit of tests and test configuration against the baseline
  visible     the worker's own tests, with trace-slice failure context (feedback only, never the arbiter)
  sealed      the sealed oracle (AG6's vault) in a clean sandbox: tests ran, none skipped, none missing
  mutation    diff-scoped mutation kill score >= threshold (default 0.92) over the sealed + visible tests
  ui          worker_ui only: CDP kinematic/optical/contrast linter (spring zeta, interrupt continuity, APCA, trim)

Exit codes: 0 PASS (or SELF_PASS / PARTIAL_PASS, which never close a ticket), 1 FAIL, 3 INCONCLUSIVE, 2 usage / tooling error.

Honest limits: the verifier raises the cost of cheating, it is not a container. Run the orchestrator under a separate
OS user or container if the workers are not trusted (docs/ENGINE_AUDIT.md). A `SELF_PASS` is feedback for the worker;
only a `PASS` from the orchestrator's full level may close a ticket.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core import contract_ticket as ct  # noqa: E402
from core import fleet, symbol_check  # noqa: E402
from core import worktree as wt  # noqa: E402
from core.platform_compat import atomic_write_text, build_sandbox_env, matches_any, run_process  # noqa: E402
from core.verification import mutation_gate, sealed_gate, weakening  # noqa: E402

PASS, FAIL, INCONCLUSIVE, NA, WAIVED = "pass", "fail", "inconclusive", "na", "waived"
V_PASS, V_FAIL, V_INCONCLUSIVE, V_SELF, V_PARTIAL = "PASS", "FAIL", "INCONCLUSIVE", "SELF_PASS", "PARTIAL_PASS"
EXIT = {V_PASS: 0, V_SELF: 0, V_PARTIAL: 0, V_FAIL: 1, V_INCONCLUSIVE: 3}

CHECK_ORDER = ("contract", "scope", "secrets", "ports", "forbidden", "symbols", "invariants", "weakening", "visible", "sealed", "mutation", "ui")
SELF_LEVEL = ("contract", "scope", "secrets", "ports", "forbidden", "symbols", "invariants", "weakening", "visible")
WAIVABLE = {"mutation", "symbols", "invariants", "ui", "visible", "forbidden"}
NEVER_NA = {"contract", "scope", "sealed"}
MAX_ITEMS = 30
SRC_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def vault_id(ticket_id: str, scope: str) -> str:
    """Sealed vault name of a (ticket, scope) pair: `<ticket>.<AGx>` (the same shape as the flat ticket id)."""
    return f"{ticket_id}.{ct.SCOPE_WORKER[scope]}"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    items: List[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        d = {"name": self.name, "status": self.status, "detail": self.detail, "seconds": round(self.seconds, 3)}
        if self.items:
            d["items"] = self.items[:MAX_ITEMS]
        if self.data:
            d["data"] = self.data
        return d


@dataclass
class Report:
    ticket: str
    scope: str
    worker: str
    verdict: str = V_INCONCLUSIVE
    level: str = "full"
    checks: List[Check] = field(default_factory=list)
    changed: dict = field(default_factory=dict)
    waivers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration: float = 0.0
    started: float = 0.0

    @property
    def passed(self) -> bool:
        return self.verdict == V_PASS

    @property
    def exit_code(self) -> int:
        return EXIT[self.verdict]

    def get(self, name: str) -> Optional[Check]:
        return next((c for c in self.checks if c.name == name), None)

    def failing(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def to_dict(self) -> dict:
        return {"ticket": self.ticket, "scope": self.scope, "worker": self.worker, "verdict": self.verdict, "level": self.level,
                "checks": [c.to_dict() for c in self.checks], "changed": self.changed, "waivers": self.waivers,
                "warnings": self.warnings, "duration": round(self.duration, 3), "started": self.started}


@dataclass
class Options:
    level: str = "auto"                    # auto | self | full
    only: Sequence[str] = ()
    skip: Sequence[str] = ()
    waive: Sequence[str] = ()
    allow_unsigned: bool = False
    require_board: bool = False
    board_path: Optional[str] = None
    baseline: Optional[str] = None
    changed_override: Optional[Sequence[str]] = None
    sealed_root: Optional[str] = None
    planner_dir: Optional[str] = None
    python: str = sys.executable
    sealed_timeout: float = 180.0
    visible_timeout: float = 180.0
    ui_timeout: float = 180.0
    mutation_threshold: Optional[float] = None
    mutation_max: int = 60
    mutation_budget: float = 600.0
    mutation_workers: int = 1
    mutation_seed: int = 0
    reveal: str = "ids"
    isolate_network: str = "auto"
    interrupt_ui: bool = True
    write_report: bool = True


# ------------------------------------------------------------------------------------------------ context
class Ctx:
    """Everything the checks share. Paths are resolved once; nothing here touches the network."""

    def __init__(self, doc: dict, scope: str, root: Path, workspace: Path, opts: Options):
        self.doc, self.scope, self.root, self.workspace, self.opts = doc, scope, root, workspace, opts
        self.ticket_id: str = doc["ticket_meta"]["id"]
        self.worker_id: str = ct.SCOPE_WORKER[scope]
        self.scope_doc: dict = doc["worker_scopes"][scope]
        self.planner = Path(opts.planner_dir) if opts.planner_dir else root / ".planner"
        self.sealed_root = Path(opts.sealed_root) if opts.sealed_root else self.planner / "sealed"
        self.baseline = Path(opts.baseline) if opts.baseline else self.planner / "baselines" / self.ticket_id / "tree"
        self.changes: Optional[wt.Changes] = None
        self.vid = vault_id(self.ticket_id, scope)

    @property
    def has_baseline(self) -> bool:
        return self.baseline.is_dir()

    def source_changes(self) -> List[str]:
        """Changed production files (not tests), as relative paths."""
        if self.changes is None:
            return []
        return [r for r in self.changes.touched if not weakening.is_test_file(r)]

    def qa_threshold(self) -> float:
        if self.opts.mutation_threshold is not None:
            return float(self.opts.mutation_threshold)
        mg = (self.doc["worker_scopes"].get("worker_qa_guard") or {}).get("mutation_gate") or {}
        return float(mg.get("min_kill_score", mutation_gate.DEFAULT_THRESHOLD))

    def sealed_available(self) -> bool:
        return (self.sealed_root / self.vid / "manifest.json").is_file()


def _cap(items: Iterable[str], n: int = MAX_ITEMS) -> List[str]:
    out = list(items)
    if len(out) > n:
        out = out[:n] + [f"... and {len(out) - n} more"]
    return out


def _read(path: Path) -> Optional[str]:
    return wt.read_text(path)


def _text_files(ctx: Ctx, rels: Iterable[str]) -> Iterable[tuple]:
    for rel in rels:
        p = ctx.workspace / rel
        if p.is_file() and not p.is_symlink():
            try:
                if p.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue
            text = _read(p)
            if text is not None:
                yield rel, text


# ------------------------------------------------------------------------------------------------ checks
def check_contract(ctx: Ctx) -> Check:
    key = ct.key_from_env()
    rep = ct.validate(ctx.doc, check_signature=not ctx.opts.allow_unsigned, allow_unsigned=ctx.opts.allow_unsigned, key=key,
                      require_hmac=key is not None)
    items = list(rep.errors)
    detail_extra = []
    if key is None and not ctx.opts.allow_unsigned:
        detail_extra.append("signature is a plain sha256 (tamper evidence only; set PLANNER_LAWGIVER_KEY for HMAC)")
    board_path = Path(ctx.opts.board_path) if ctx.opts.board_path else ctx.planner / "board.db"
    if board_path.is_file():
        try:
            from core.coordination.blackboard import Board
            with Board(str(board_path)) as board:
                ok, why = ct.verify_against_board(board, ctx.doc)
            if not ok:
                items.append(f"blackboard: {why}")
        except Exception as exc:  # a broken board is a failed root of trust, not a crash
            return Check("contract", INCONCLUSIVE, f"blackboard unreadable: {exc}")
    elif ctx.opts.require_board:
        return Check("contract", INCONCLUSIVE, f"no blackboard at {board_path}: the contract's root of trust is unavailable")
    else:
        detail_extra.append("no blackboard found: contract hash not cross-checked")
    if items:
        return Check("contract", FAIL, "contract is invalid or was altered after signing", _cap(items))
    return Check("contract", PASS, "; ".join(["contract valid"] + detail_extra), _cap(rep.warnings))


def check_scope(ctx: Ctx) -> Check:
    if ctx.changes is None:
        return Check("scope", INCONCLUSIVE, "cannot tell which files changed (no baseline snapshot and no explicit file list)")
    bad = ct.check_scope(ctx.doc, ctx.scope, ctx.changes.all)
    if bad:
        return Check("scope", FAIL, f"{len(bad)} change(s) outside the contract of {ctx.worker_id}", _cap(bad))
    n = len(ctx.changes.all)
    if n == 0:
        return Check("scope", INCONCLUSIVE, "the worker changed nothing")
    return Check("scope", PASS, f"{n} changed file(s), all inside {ctx.scope} allowed_paths")


_STRONG_SECRET = re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|sk-[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                            r"xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
_WEAK_SECRET = re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")


def check_secrets(ctx: Ctx) -> Check:
    if ctx.changes is None:
        return Check("secrets", INCONCLUSIVE, "no change list")
    hard, soft = [], []
    for rel, text in _text_files(ctx, ctx.changes.touched):
        for no, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000:
                continue
            if _STRONG_SECRET.search(line):
                hard.append(f"{rel}:{no}: secret-looking value (redacted)")
            elif _WEAK_SECRET.search(line) and not weakening.is_test_file(rel):
                soft.append(f"{rel}:{no}: hard-coded credential-like assignment")
    base = Path(ctx.workspace)
    for rel in ctx.changes.touched:
        name = rel.rsplit("/", 1)[-1]
        if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template"))) or name.endswith((".pem", ".key")) or name.startswith("id_rsa"):
            hard.append(f"{rel}: credential file added or changed")
    if hard:
        return Check("secrets", FAIL, "secret material in the change set", _cap(hard + soft))
    if soft:
        return Check("secrets", PASS, "no strong secret patterns; review the assignments below", _cap(soft))
    return Check("secrets", PASS, "no secret patterns in changed files")


def check_ports(ctx: Ctx) -> Check:
    if ctx.changes is None:
        return Check("ports", INCONCLUSIVE, "no change list")
    found = fleet.scan_files(ctx.workspace, ctx.changes.touched, ctx.worker_id)
    bad = fleet.violations(found)
    warn = [f for f in found if f.severity != "violation"]
    if bad:
        return Check("ports", FAIL, f"MASP port isolation violated by {ctx.worker_id}", _cap(str(f) for f in bad + warn))
    return Check("ports", PASS, "no port outside the worker's MASP ranges", _cap(str(f) for f in warn))


def _strip_comments(text: str, rel: str) -> str:
    if rel.endswith(".sql"):
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        return re.sub(r"--[^\n]*", " ", text)
    if rel.endswith(".py"):
        return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


def _flex(term: str) -> "re.Pattern[str]":
    parts = [re.escape(p) for p in term.split()]
    return re.compile(r"(?<![\w$])" + r"\s+".join(parts) + r"(?![\w$])", re.I)


def check_forbidden(ctx: Ctx) -> Check:
    sc = ctx.scope_doc
    ast_terms = list(sc.get("forbidden_ast") or [])
    calls = list(sc.get("forbidden_calls") or [])
    if not ast_terms and not calls:
        return Check("forbidden", NA, "the contract declares no forbidden_ast / forbidden_calls for this scope")
    if ctx.changes is None:
        return Check("forbidden", INCONCLUSIVE, "no change list")
    bad: List[str] = []
    for rel, text in _text_files(ctx, ctx.changes.touched):
        old = _read(ctx.baseline / rel) if ctx.has_baseline else None
        new_code, old_code = _strip_comments(text, rel), _strip_comments(old or "", rel)
        for term in ast_terms:
            pat = _flex(term)
            n_new, n_old = len(pat.findall(new_code)), len(pat.findall(old_code))
            if n_new > n_old:
                line = next((i for i, ln in enumerate(text.splitlines(), 1) if pat.search(ln)), 0)
                bad.append(f"{rel}:{line}: forbidden construct {term!r} introduced ({n_new} vs {n_old} before)")
        for call in calls:
            if call in new_code and new_code.count(call) > old_code.count(call):
                line = next((i for i, ln in enumerate(text.splitlines(), 1) if call in ln), 0)
                bad.append(f"{rel}:{line}: forbidden call {call!r} introduced")
    if bad:
        return Check("forbidden", FAIL, f"{len(bad)} forbidden construct(s) introduced", _cap(bad))
    return Check("forbidden", PASS, "no forbidden construct introduced")


def _candidate_sources(ctx: Ctx, limit: int = 4000) -> List[str]:
    first = [r for r in (ctx.changes.touched if ctx.changes else [])]
    rest: List[str] = []
    for rel, _p in wt.iter_files(ctx.workspace):
        if rel.endswith(SRC_SUFFIXES + (".pyi",)):
            rest.append(rel)
            if len(rest) >= limit:
                break
    contracts = [r for r in rest if r.startswith("contracts/")]
    seen, out = set(), []
    for r in contracts + first + rest:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def check_symbols(ctx: Ctx) -> Check:
    syms = (ctx.doc.get("shared_contracts") or {}).get("scip_symbols") or []
    if not syms:
        return Check("symbols", NA, "the contract locks no shared symbols")
    statuses = symbol_check.check_contract_symbols(ctx.doc, ctx.workspace, _candidate_sources(ctx))
    bad = [s for s in statuses if s.status in (symbol_check.DRIFT, symbol_check.MISSING)]
    unsup = [s for s in statuses if s.status == symbol_check.UNSUPPORTED]
    if bad:
        return Check("symbols", FAIL, f"{len(bad)} of {len(statuses)} locked symbol(s) drifted or are missing", _cap(str(s) for s in bad + unsup))
    if unsup:
        return Check("symbols", INCONCLUSIVE, f"{len(unsup)} symbol(s) cannot be checked (unsupported language or unparsable lock)", _cap(str(s) for s in unsup))
    return Check("symbols", PASS, f"{len(statuses)} locked symbol(s) intact")


def _norm_sql(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\s*([(),])\s*", r"\1", s)).strip().upper()


def _invariant_referenced(iid: str, tests: str) -> bool:
    """`INV-02` counts as referenced by INV-02, INV_02, inv02 and inv-2 (test function names cannot contain a dash)."""
    m = re.fullmatch(r"(?i)inv[-_ ]?0*(\d+)", iid.strip())
    if not m:
        return iid in tests
    return re.search(r"(?i)(?<![A-Za-z0-9])inv[-_ ]?0*" + m.group(1) + r"(?!\d)", tests) is not None


def check_invariants(ctx: Ctx) -> Check:
    inv = ctx.scope_doc.get("formal_invariants")
    if not inv:
        return Check("invariants", NA, "no formal_invariants declared for this scope")
    missing: List[str] = []
    if ctx.scope == "worker_db":
        corpus = ""
        for rel, p in wt.iter_files(ctx.workspace):
            if rel.endswith(".sql") and matches_any(rel, ct.allowed_paths(ctx.doc, ctx.scope)):
                text = _read(p)
                if text:
                    corpus += "\n" + _strip_comments(text, rel)
        norm = _norm_sql(corpus)
        for item in inv:
            if _norm_sql(item) not in norm:
                missing.append(f"SQL invariant not found in the migrations: {item[:120]}")
        label = "SQL invariants"
    elif isinstance(inv, dict):
        props = inv.get("properties") or []
        tests = ""
        vault_tests = ctx.sealed_root / ctx.vid / "tests"
        sources = [vault_tests] if vault_tests.is_dir() else []
        for src in sources:
            for rel, p in wt.iter_files(src):
                tests += "\n" + (_read(p) or "")
        for rel, p in wt.iter_files(ctx.workspace):
            if weakening.is_test_file(rel):
                tests += "\n" + (_read(p) or "")
        if not tests.strip():
            return Check("invariants", INCONCLUSIVE, "no sealed or visible tests to look for invariant ids in")
        for prop in props:
            iid = prop.split(":", 1)[0].strip()
            if not _invariant_referenced(iid, tests):
                missing.append(f"{iid} is not referenced by any test (name, docstring or comment must carry the id)")
        label = "invariant ids"
    else:
        return Check("invariants", NA, "invariants of this scope are checked by its sealed tests")
    if missing:
        return Check("invariants", FAIL, f"{len(missing)} declared invariant(s) are not enforced", _cap(missing))
    return Check("invariants", PASS, f"all declared {label} are present")


def check_weakening(ctx: Ctx) -> Check:
    if not ctx.has_baseline:
        return Check("weakening", INCONCLUSIVE, f"no baseline snapshot at {ctx.baseline}: test weakening cannot be audited")
    fc = ct.forbidden_calls(ctx.doc, "worker_qa_guard") if "worker_qa_guard" in ctx.doc["worker_scopes"] else []
    viol = weakening.check_weakening(ctx.baseline, ctx.workspace, forbidden_calls=fc if ctx.scope == "worker_qa_guard" else [])
    if viol:
        return Check("weakening", FAIL, f"{len(viol)} test-weakening violation(s)", _cap(str(v) for v in viol))
    return Check("weakening", PASS, "no weakened, removed or reconfigured tests")


def _visible_tests(ctx: Ctx, limit: int = 200) -> List[str]:
    out = []
    for rel, _p in wt.iter_files(ctx.workspace):
        if rel.endswith(".py") and weakening.is_test_file(rel):
            out.append(rel)
            if len(out) >= limit:
                break
    return sorted(out)


def _tool_env_path() -> str:
    return str(HERE / "core" / "verification")


def check_visible(ctx: Ctx) -> Check:
    tests = _visible_tests(ctx)
    if not tests:
        return Check("visible", NA, "the workspace has no Python test files (sealed oracle only)")
    reports = ctx.planner / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    trace_out = reports / f"{ctx.ticket_id}.{ctx.worker_id}.trace.jsonl"
    junit = reports / f"{ctx.ticket_id}.{ctx.worker_id}.visible.xml"
    for p in (trace_out, junit):
        try:
            p.unlink()
        except OSError:
            pass
    env = build_sandbox_env({"PYTHONPATH": str(ctx.workspace) + os.pathsep + _tool_env_path(), "PYTHONHASHSEED": "0",
                             "TRACE_SLICE_OUT": str(trace_out), "TRACE_ROOT": str(ctx.workspace),
                             **fleet.worker_env(ctx.worker_id, ctx.ticket_id)})
    cmd = [ctx.opts.python, "-m", "pytest", "-q", "--tb=no", "-rN", "--disable-warnings", "-p", "no:cacheprovider", "-p", "trace_slice_plugin",
           f"--junitxml={junit}", *tests]
    res = run_process(cmd, cwd=str(ctx.workspace), env=env, timeout=ctx.opts.visible_timeout)
    j = sealed_gate.parse_junit(junit)
    slices = []
    try:
        from core.verification import trace_slice_plugin as tsp
        slices = tsp.read_slices(str(trace_out)) if trace_out.exists() else []
        trace_text = tsp.render_for_prompt(slices) if slices else ""
    except Exception:  # feedback is best effort
        trace_text = ""
    data = {"counts": {k: j[k] for k in ("tests", "failures", "errors", "skipped")}, "trace_slice": trace_text, "trace_file": str(trace_out)}
    if res.error:
        return Check("visible", INCONCLUSIVE, f"pytest could not run: {res.error}", data=data)
    if res.timed_out:
        return Check("visible", INCONCLUSIVE, f"visible tests timed out after {ctx.opts.visible_timeout:g}s", data=data)
    if res.returncode == 5 or (j["ok"] and j["tests"] == 0):
        return Check("visible", NA, "pytest collected no tests from the visible test files", data=data)
    if not j["ok"]:
        return Check("visible", INCONCLUSIVE, f"pytest exited {res.returncode} without a readable report", data=data)
    if j["failures"] or j["errors"] or res.returncode != 0:
        return Check("visible", FAIL, f"{j['failures']} failed, {j['errors']} errors of {j['tests']} visible tests", _cap(j["failed"]), data)
    return Check("visible", PASS, f"{j['tests']} visible tests passed" + (f" ({j['skipped']} skipped)" if j["skipped"] else ""), data=data)


def check_sealed(ctx: Ctx) -> Check:
    if not ctx.sealed_available() and ctx.scope == "worker_ui":
        return Check("sealed", NA, "no sealed vault for the UI scope; the CDP linter is its oracle (see the ui check)")
    res = sealed_gate.run_isolated_verification(ctx.workspace, ctx.vid, root=ctx.sealed_root, timeout=ctx.opts.sealed_timeout, reveal=ctx.opts.reveal,
                                                python=ctx.opts.python, isolate_network=ctx.opts.isolate_network, extra_env=fleet.worker_env(ctx.worker_id, ctx.ticket_id))
    data = {"counts": res.counts, "network_isolated": res.network_isolated, "duration": round(res.duration, 2)}
    if res.passed:
        return Check("sealed", PASS, res.reason + f" ({res.counts.get('tests', 0)} tests)", data=data)
    items = ([res.output] if res.output else []) + list(res.failed_tests)
    status = INCONCLUSIVE if res.reason.startswith(("RUNNER_FAILED", "NETWORK_ISOLATION", "SANDBOX_REFUSED", "NO_JUNIT")) else FAIL
    return Check("sealed", status, res.reason, _cap(items), data)


def check_mutation(ctx: Ctx) -> Check:
    if ctx.scope == "worker_qa_guard":
        return Check("mutation", NA, "the QA bounty is scored at integration, on the production diff of the whole ticket")
    if ctx.changes is None or not ctx.has_baseline:
        return Check("mutation", INCONCLUSIVE, "no baseline snapshot: the diff cannot be mutation-tested")
    src = ctx.source_changes()
    py = [r for r in src if r.endswith(".py")]
    js = [r for r in src if r.endswith(JS_SUFFIXES)]
    if not py and not js:
        return Check("mutation", NA, "no executable source files in the change set")
    reports = []
    if py:
        changed = wt.diff_lines(ctx.baseline, ctx.workspace, py)
        sealed_tests = ctx.sealed_root / ctx.vid / "tests"
        cfg = mutation_gate.MutationConfig(threshold=ctx.qa_threshold(), max_mutants=ctx.opts.mutation_max, time_budget=ctx.opts.mutation_budget,
                                           workers=ctx.opts.mutation_workers, seed=ctx.opts.mutation_seed, python=ctx.opts.python,
                                           extra_env=fleet.worker_env(ctx.worker_id, ctx.ticket_id), isolate_network=ctx.opts.isolate_network)
        reports.append(("python", mutation_gate.run_mutation(ctx.workspace, changed, sealed_tests=sealed_tests if sealed_tests.is_dir() else None,
                                                             visible_tests=_visible_tests(ctx), config=cfg)))
    if js:
        from core.verification import mutation_adapters
        reports.append(("js/ts", mutation_adapters.run_stryker(ctx.workspace, wt.diff_lines(ctx.baseline, ctx.workspace, js), threshold=ctx.qa_threshold(),
                                                               timeout=ctx.opts.mutation_budget)))
    rank = {mutation_gate.FAIL: 3, mutation_gate.INCONCLUSIVE: 2, mutation_gate.PASS: 1, mutation_gate.NOT_APPLICABLE: 0}
    worst = max(reports, key=lambda x: rank[x[1].status])
    rep = worst[1]
    scores = [r.score for _n, r in reports if r.score is not None]
    data = {"score": min(scores) if scores else None, "threshold": rep.threshold, "counts": rep.counts, "candidates": sum(r.candidates for _n, r in reports),
            "executed": sum(r.executed for _n, r in reports), "sampled": any(r.sampled for _n, r in reports), "seconds": round(sum(r.duration for _n, r in reports), 2),
            "engines": [n for n, _r in reports]}
    items = []
    for name, r in reports:
        items += [f"survived [{name}] {s['file']}:{s['line']} {s['description']}" for s in r.survivors[:20]] + r.violations
    reason = rep.reason if len(reports) == 1 else "; ".join(f"{n}: {r.reason}" for n, r in reports)
    status = {mutation_gate.PASS: PASS, mutation_gate.FAIL: FAIL, mutation_gate.INCONCLUSIVE: INCONCLUSIVE, mutation_gate.NOT_APPLICABLE: NA}[rep.status]
    return Check("mutation", status, reason, _cap(items), data)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a):  # noqa: D401
        return

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def _port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ui(ctx: Ctx) -> Check:
    if ctx.scope != "worker_ui":
        return Check("ui", NA, "not a UI scope")
    url = (ctx.scope_doc.get("url") or "").strip()
    selector = (ctx.scope_doc.get("selector") or "").strip()
    if not url or not selector:
        return Check("ui", INCONCLUSIVE, "worker_ui has no `url` and `selector` in the contract: the CDP linter has nothing to look at")
    from core.ui import cdp_linter
    opts = cdp_linter.spec_to_options(ctx.scope_doc.get("kinematic_spec"), interrupt=ctx.opts.interrupt_ui)
    server = None
    target = url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url) or url.startswith("file:"):
        if url.startswith("file:"):
            target = url
        else:
            plan = fleet.allocate_ports("AG3", ctx.ticket_id, probe=True)
            handler = functools.partial(_QuietHandler, directory=str(ctx.workspace))
            try:
                server = http.server.ThreadingHTTPServer(("127.0.0.1", plan.dev), handler)
            except OSError as exc:
                return Check("ui", INCONCLUSIVE, f"could not bind the static server on MASP port {plan.dev}: {exc}")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            target = f"http://127.0.0.1:{plan.dev}/{url.lstrip('/')}"
    else:
        m = re.match(r"^https?://(?:localhost|127\.0\.0\.1):(\d+)", url)
        if m:
            port = int(m.group(1))
            owner = fleet.port_owner(port)
            if owner and owner != "AG3":
                return Check("ui", FAIL, f"the contract points the UI check at port {port}, which belongs to {owner}, not AG3 (MASP)")
            if not _port_listening("127.0.0.1", port):
                return Check("ui", INCONCLUSIVE, f"nothing is listening on {url}: start the dev server on AG3's range or use a relative url (served statically)")
    try:
        res = cdp_linter.run_cdp_linter(target, selector, timeout=ctx.opts.ui_timeout, cwd=str(ctx.workspace), **opts)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
    data = {"verdict": res.verdict, "checks": {k: v.get("status") for k, v in (res.report.get("checks") or {}).items()} if isinstance(res.report, dict) else {}}
    if res.verdict == "HUMAN_CRAFT_VERIFIED":
        return Check("ui", PASS, "CDP linter: HUMAN_CRAFT_VERIFIED", data=data)
    if res.verdict == "REJECTED_AI_SLOP":
        return Check("ui", FAIL, "CDP linter: REJECTED_AI_SLOP", _cap(res.failures()), data)
    return Check("ui", INCONCLUSIVE, f"CDP linter: {res.verdict} {res.error}".strip(), _cap(res.failures()), data)


CHECKS = {"contract": check_contract, "scope": check_scope, "secrets": check_secrets, "ports": check_ports, "forbidden": check_forbidden,
          "symbols": check_symbols, "invariants": check_invariants, "weakening": check_weakening, "visible": check_visible,
          "sealed": check_sealed, "mutation": check_mutation, "ui": check_ui}


# ------------------------------------------------------------------------------------------------ orchestration
def decide(checks: Sequence[Check], level: str) -> str:
    if any(c.status == FAIL for c in checks):
        return V_FAIL
    if any(c.status == INCONCLUSIVE for c in checks):
        return V_INCONCLUSIVE
    bad_na = [c for c in checks if c.status == NA and c.name in NEVER_NA]
    if bad_na:
        return V_INCONCLUSIVE
    return V_PASS if level == "full" else V_SELF


def resolve_level(opts: Options, ctx: Ctx) -> str:
    if opts.level in ("self", "full"):
        return opts.level
    return "full" if (ctx.sealed_available() or ctx.scope == "worker_ui") and ctx.sealed_root.is_dir() else "self"


def load_contract(root: Path, ticket_id: str, planner_dir: Optional[str] = None) -> dict:
    p = (Path(planner_dir) if planner_dir else root / ".planner") / "tickets" / f"{ticket_id}.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"contract not found: {p}")
    return ct.load_ticket(p)


def verify(ticket_id: str, scope: str, *, root=".", workspace=None, doc: Optional[dict] = None, options: Optional[Options] = None) -> Report:
    opts = options or Options()
    t0 = time.monotonic()
    root_p = Path(root).resolve()
    ws = Path(workspace).resolve() if workspace else root_p
    doc = doc if doc is not None else load_contract(root_p, ticket_id, opts.planner_dir)
    if scope not in (doc.get("worker_scopes") or {}):
        raise ValueError(f"ticket {ticket_id} has no scope {scope!r}; it has {sorted(doc.get('worker_scopes') or {})}")
    ctx = Ctx(doc, scope, root_p, ws, opts)
    level = resolve_level(opts, ctx)
    rep = Report(ticket_id, scope, ctx.worker_id, level=level, started=time.time())
    if opts.changed_override is not None:
        ctx.changes = wt.Changes(modified=sorted(set(opts.changed_override)))
    elif ctx.has_baseline:
        ctx.changes = wt.changes(ctx.baseline, ws)
    if ctx.changes is not None:
        rep.changed = ctx.changes.to_dict()
    wanted = [n for n in (CHECK_ORDER if level == "full" else SELF_LEVEL) if (not opts.only or n in opts.only) and n not in opts.skip]
    waive = {w for w in opts.waive if w in WAIVABLE}
    for w in opts.waive:
        if w not in WAIVABLE:
            rep.warnings.append(f"waiver for {w!r} ignored: only {sorted(WAIVABLE)} can be waived")
    for name in wanted:
        if name in waive:
            rep.checks.append(Check(name, WAIVED, "waived by the operator"))
            rep.waivers.append(name)
            continue
        c0 = time.monotonic()
        try:
            chk = CHECKS[name](ctx)
        except Exception as exc:  # a crashing check is a failed tool, never a pass
            chk = Check(name, INCONCLUSIVE, f"check crashed: {type(exc).__name__}: {exc}")
        chk.seconds = time.monotonic() - c0
        rep.checks.append(chk)
    rep.verdict = decide(rep.checks, level)
    if opts.only or opts.skip:
        rep.warnings.append("partial run (--only/--skip): the verdict is not valid for closing a ticket")
        if rep.verdict == V_PASS:
            rep.verdict = V_PARTIAL
    rep.duration = time.monotonic() - t0
    if opts.write_report:
        try:
            out = ctx.planner / "reports"
            out.mkdir(parents=True, exist_ok=True)
            atomic_write_text(str(out / f"{ticket_id}.{ctx.worker_id}.verify.json"), json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
        except OSError:
            pass
    return rep


def render_feedback(rep: Report, limit: int = 1800) -> str:
    """Oracle-safe text for the worker: which checks failed and where, never sealed assertion text."""
    lines = [f"[{rep.verdict}] {rep.ticket} {rep.scope} ({rep.worker}), level {rep.level}"]
    for c in rep.checks:
        if c.status in (FAIL, INCONCLUSIVE):
            lines.append(f"- {c.name}: {c.status.upper()} - {c.detail}")
            lines.extend(f"    {it}" for it in c.items[:8])
            if c.name == "visible" and c.data.get("trace_slice"):
                lines.append("    trace: " + c.data["trace_slice"].replace("\n", " | ")[:600])
    out = "\n".join(lines)
    return out if len(out) <= limit else out[:limit - 20] + "\n... (truncated)"


# ------------------------------------------------------------------------------------------------ CLI
def _csv(v: Optional[str]) -> List[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="verifier", description="Planner 4.0 verification pipeline (exit 0 pass, 1 fail, 3 inconclusive, 2 error)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("checks", help="list the checks")
    r = sub.add_parser("run", help="verify one ticket scope")
    r.add_argument("--ticket", required=True, help="contract id (the file .planner/tickets/<id>.yaml)")
    r.add_argument("--scope", required=True, choices=sorted(ct.SCOPE_WORKER))
    r.add_argument("--root", default=os.environ.get("PLANNER_ROOT", "."), help="integration root (holds .planner/); env PLANNER_ROOT")
    r.add_argument("--workspace", help="the worker's workspace (default: --root)")
    r.add_argument("--level", choices=("auto", "self", "full"), default="auto")
    r.add_argument("--only", default="", help="comma separated check names")
    r.add_argument("--skip", default="")
    r.add_argument("--waive", default="", help="comma separated waivable checks: " + ",".join(sorted(WAIVABLE)))
    r.add_argument("--baseline", help="baseline snapshot dir (default .planner/baselines/<ticket>/tree)")
    r.add_argument("--changed", default="", help="comma separated changed files (when there is no baseline)")
    r.add_argument("--sealed-root")
    r.add_argument("--board")
    r.add_argument("--allow-unsigned", action="store_true")
    r.add_argument("--require-board", action="store_true")
    r.add_argument("--threshold", type=float)
    r.add_argument("--max-mutants", type=int, default=60)
    r.add_argument("--mutation-budget", type=float, default=600.0)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "checks":
        print("\n".join(CHECK_ORDER))
        return 0
    if a.cmd != "run":
        ap.print_help()
        return 0
    opts = Options(level=a.level, only=_csv(a.only), skip=_csv(a.skip), waive=_csv(a.waive), allow_unsigned=a.allow_unsigned,
                   require_board=a.require_board, board_path=a.board, baseline=a.baseline, sealed_root=a.sealed_root,
                   changed_override=_csv(a.changed) or None, mutation_threshold=a.threshold, mutation_max=a.max_mutants,
                   mutation_budget=a.mutation_budget, mutation_workers=a.workers)
    try:
        rep = verify(a.ticket, a.scope, root=a.root, workspace=a.workspace, options=opts)
    except (FileNotFoundError, ValueError, OSError, ct.ContractError) as exc:
        print(f"verifier: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_feedback(rep, limit=6000))
        if rep.verdict in (V_PASS, V_SELF, V_PARTIAL):
            tail = {V_PASS: "ALL CHECKS PASSED", V_SELF: "SELF-CHECK PASSED (the sealed oracle and mutation gate run in the orchestrator)",
                    V_PARTIAL: "PARTIAL RUN PASSED (some checks were skipped on request; not valid for closing a ticket)"}[rep.verdict]
            print(tail)
    return rep.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
