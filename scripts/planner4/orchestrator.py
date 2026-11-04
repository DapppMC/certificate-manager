#!/usr/bin/env python3
"""orchestrator.py - drives signed Contract-First tickets through the bicameral fleet until they are DONE or ESCALATED.

    python orchestrator.py plan   [--root .]                      what would run, in which wave, by which worker, on which ports
    python orchestrator.py run    [--root .] [--executor command --cmd "..."]
    python orchestrator.py status [--root .]

The state lives in the blackboard (`.planner/board.db`), so a run can be interrupted and resumed with the same command.

Per ticket (one signed contract, several write scopes):

  CONTRACTED  AG1 contract is signed and registered. AG1 stubs (`contracts/<ticket>/*`) are written deterministically.
  SEALED      AG6 (The Inquisitor) writes acceptance tests per scope in its OWN workspace. They must fail against the stubs
              ("red": an oracle that passes on an unimplemented stub proves nothing) and must name every INV-NN invariant.
              They are moved into the vault `.planner/sealed/<ticket>.<AGx>/` and never enter a worker's workspace.
  LEASED      a baseline snapshot of the tree is taken and every implementing scope (AG2 DB, AG5 API, AG3 UI, AG7 DevOps)
              gets its own workspace copy.
  IN_PROGRESS the scopes are dispatched in parallel through swarm.py (path leases, MASP ports, scrubbed environment).
  VERIFYING   every scope is judged by verifier.py (contract, scope, ports, symbols, invariants, anti-weakening, sealed
              oracle, UI linter); mutation testing is deferred to the integration step so that AG4 can earn the bounty.
              Passing scopes are merged into a STAGING copy of the current tree (with conflict detection), where
                * the sealed vaults of every scope run against the merged result,
                * AG4 (Adversarial QA, zero-sum bounty) attacks it: failing AG4 tests are claims the implementers must
                  answer; surviving mutants are the bounty AG4 must kill with new tests,
                * the diff-scoped mutation kill score (default >= 0.92) over sealed + AG4 + existing tests must hold.
  DONE        the staging result is applied to the real tree (again with conflict detection).
  FAILED      a failed round: oracle-safe feedback and trace slices go back to the failing scopes (Reflexion). The same
              failure signature twice in a row, or `max_attempts` rounds, ESCALATES the ticket with a written report
              (`.planner/escalations/<ticket>.md`); with `--standby` AG8 (The Arbiter, promoted to the premium tier as escalation
              solver) takes over a scope that failed twice and gets one extra attempt first.

AG4's utility is recorded per ticket: U = alpha*falsified_claims + beta*kill_ratio - gamma*false_positives. The spec fixes
the shape, not the weights; the defaults (1, 2, 1.5) are this implementation's choice and are configurable.

Honest limits: agent quality is the executor's business; this module makes cheating expensive and failure legible. It
runs in your OS account: use a container or a separate user when the agents are not trusted.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import swarm as sw  # noqa: E402
import verifier as vf  # noqa: E402
from core import contract_ticket as ct  # noqa: E402
from core import fleet, symbol_check  # noqa: E402
from core import worktree as wt  # noqa: E402
from core.coordination.blackboard import Board, IllegalTransition  # noqa: E402
from core.platform_compat import atomic_write_text, build_sandbox_env, matches_any, replace_with_retry, run_process, safe_rmtree  # noqa: E402
from core.verification import mutation_gate, sealed_gate, weakening  # noqa: E402

IMPL_SCOPES = ("worker_db", "worker_backend", "worker_ui", "worker_devops", "worker_security")
QA_SCOPE = "worker_qa_guard"
ORCH = "ORCHESTRATOR"
DONE, ESCALATED, FAILED_STATE = "DONE", "ESCALATED", "FAILED"
SCOPE_ORDER = {s: i for i, s in enumerate(("worker_db", "worker_backend", "worker_devops", "worker_security", "worker_ui", "worker_qa_guard"))}


@dataclass
class Policy:
    max_attempts: int = 3
    use_standby: bool = False           # AG8 (escalation solver, premium tier) gets one extra attempt on a scope that keeps failing
    standby_after: int = 2              # ...once a scope has failed this many times (Reflexion retries >= 2), or its rounds are exhausted
    lead: str = "AG1"                   # which architect carries the premium quota: AG1 | AG6 | AG8 (see fleet.effective_tier)
    arbiter: str = "auto"               # auto: AG8, or AG6 when AG8 implements/solves on the same ticket | AG8 | AG6
    parallel_scopes: int = 4
    parallel_tickets: int = 1
    agent_timeout: float = 900.0
    claim_wait: float = 30.0
    bounty: str = "auto"                # auto | always | off  (auto: AG4 attacks once per ticket, then only for survivors)
    bounty_rounds: int = 2
    mutation: str = "on"                # on | off (off is recorded as a waiver in the ticket outcome)
    mutation_threshold: Optional[float] = None
    mutation_max: int = 60
    mutation_budget: float = 600.0
    mutation_workers: int = 1
    sealed_dir: Optional[str] = None    # <dir>/<ticket>/<AGx>/ holds human-written acceptance tests; AG6 is then not called
    require_red: bool = True
    allow_unsigned: bool = False
    alpha: float = 1.0
    beta: float = 2.0
    gamma: float = 1.5
    models: Dict[str, str] = field(default_factory=dict)  # model_tier -> model string handed to the executor
    effort: Dict[str, str] = field(default_factory=dict)
    python: str = sys.executable
    visible_timeout: float = 180.0
    sealed_timeout: float = 180.0

    def __post_init__(self) -> None:
        self.lead = fleet.parse_lead(self.lead)
        if self.arbiter not in ("auto", "AG8", "AG6"):
            raise ValueError(f"arbiter must be auto, AG8 or AG6, not {self.arbiter!r}")
        if int(self.standby_after) < 1:
            raise ValueError("standby_after must be >= 1")


@dataclass
class TicketOutcome:
    ticket: str
    state: str
    reason: str = ""
    rounds: int = 0
    ledger: dict = field(default_factory=dict)
    escalation: str = ""
    waivers: List[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def done(self) -> bool:
        return self.state == DONE

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RunSummary:
    outcomes: Dict[str, TicketOutcome] = field(default_factory=dict)
    blocked: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(o.done for o in self.outcomes.values()) and not self.blocked

    def to_dict(self) -> dict:
        return {"ok": self.ok, "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()}, "blocked": self.blocked}


class MergeConflict(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ prompts
def _bullets(items: Iterable[str], prefix: str = "- ") -> str:
    return "\n".join(prefix + str(i) for i in items) or "- (none)"


def scope_brief(doc: dict, scope: str) -> str:
    sc = doc["worker_scopes"][scope]
    meta = doc["ticket_meta"]
    others = [p for n, s in doc["worker_scopes"].items() if n != scope for p in s.get("allowed_paths", [])]
    sig = [f"{s['symbol']}\n{s['signature'].rstrip()}" for s in (doc.get("shared_contracts") or {}).get("scip_symbols", [])]
    lines = [f"TICKET {meta['id']} (epoch {meta['epoch']}): {meta.get('goal', '')}",
             f"YOUR SCOPE: {scope} as {ct.SCOPE_WORKER[scope]}",
             "YOU MAY WRITE ONLY:", _bullets(sc.get("allowed_paths", [])),
             "OTHER WORKERS' PATHS (read only):", _bullets(others),
             "NEVER TOUCH:", _bullets(ct.NEVER_TOUCH)]
    if sig:
        lines += ["LOCKED CONTRACT SYMBOLS (the signature text is frozen; importing/using them is fine, changing them is a failure):", "\n\n".join(sig)]
    inv = sc.get("formal_invariants")
    if inv:
        lines += ["DECLARED INVARIANTS:", _bullets(inv["properties"] if isinstance(inv, dict) else inv)]
    for key in ("forbidden_ast", "forbidden_calls"):
        if sc.get(key):
            lines += [f"{key.upper()} (must not appear in new code):", _bullets(sc[key])]
    if scope == "worker_ui":
        lines += [f"UI SPEC: fsm={sc.get('mandatory_fsm')} kinematic={json.dumps(sc.get('kinematic_spec'))} selector={sc.get('selector')} url={sc.get('url')}"]
    return "\n".join(lines)


RULES = """\
RULES (the verifier enforces them; breaking one fails the attempt):
- Write only inside your allowed paths. Do not edit tests, test configuration, conftest/pytest/jest/stryker/package files.
- Never bind or connect to a port outside your own MASP ranges (env DEV_PORT / API_PORT / MASP_*_RANGE).
- No secrets, tokens or .env files. No network calls in code under test.
- The acceptance tests are SEALED: you cannot read them. Implement the contract and its invariants literally; do not special-case examples.
- Your result is also mutation-tested on the lines you changed: keep code minimal, no dead branches, no unspecified behaviour.
- Finish with a short message starting with `REFLECTION:` (what you did, what you were unsure about). If you believe an AG4 claim is wrong, add a line `DISPUTE: <test id> - <reason>`.
"""


def implementer_prompt(doc: dict, scope: str, feedback: str = "", claims: Sequence[dict] = (), attempt: int = 1) -> str:
    parts = [f"You are {ct.SCOPE_WORKER[scope]} of the Planner 4.0 fleet. Attempt {attempt}.", scope_brief(doc, scope), RULES]
    if feedback:
        parts += ["THE PREVIOUS ATTEMPT FAILED. Oracle-safe verifier feedback follows. Do not repeat it; change approach if the same check fails again:", feedback]
    if claims:
        parts += ["AG4 (adversarial QA) CLAIMS the following tests falsify your implementation. Fix the code, or dispute with evidence:"]
        for c in claims:
            parts += [f"* {c['test']} in {c['file']}\n{c.get('source', '')[:1500]}\nfailure: {c.get('failure', '')[:400]}"]
    return "\n\n".join(parts)


def inquisitor_prompt(doc: dict, scopes: Sequence[str], feedback: str = "", attempt: int = 1) -> str:
    meta = doc["ticket_meta"]
    lines = [f"You are AG6, The Inquisitor. Attempt {attempt}. Write the SEALED acceptance tests for ticket {meta['id']}: {meta.get('goal', '')}",
             "You do NOT implement anything and you do NOT edit the contract. The implementation does not exist yet; the stubs under contracts/ show the locked signatures.",
             "For each scope below write pytest tests in `tests/acceptance/<AGx>/` (plain Python, deterministic, no network, no skips/xfail, no mocking of the unit under test).",
             "Every declared invariant id (INV-NN) must be named in a test function name or docstring. The tests MUST FAIL against the current stubs.",
             "Cover boundaries (off-by-one), error paths, idempotence and state invariants; weak tests let mutants live and block the ticket."]
    for s in scopes:
        lines += ["", f"== {ct.SCOPE_WORKER[s]} ({s}) ==", scope_brief(doc, s)]
    if feedback:
        lines += ["", "THE PREVIOUS ATTEMPT WAS REJECTED:", feedback]
    return "\n".join(lines)


def bounty_prompt(doc: dict, survivors: Sequence[str], prior: str = "", attempt: int = 1) -> str:
    sc = doc["worker_scopes"][QA_SCOPE]
    lines = [f"You are AG4, the Adversarial QA bounty hunter. Round {attempt}. Ticket {doc['ticket_meta']['id']}.",
             scope_brief(doc, QA_SCOPE),
             "You are paid for (a) tests that FAIL on a violation of the contract/invariants (falsified claims) and (b) killing surviving mutants.",
             "You lose points for false positives (a failing test that contradicts the contract). Property-based or boundary tests are preferred.",
             f"Mutation threshold to reach: {sc.get('mutation_gate', {}).get('min_kill_score', 0.92)} on the changed lines.",
             f"Do not use: {', '.join(sc.get('forbidden_calls', [])) or '(nothing listed)'}. Do not touch production code."]
    if survivors:
        lines += ["SURVIVING MUTANTS (write tests that kill them):", _bullets(survivors[:40])]
    if prior:
        lines += ["NOTES FROM THE LAST ROUND:", prior]
    return "\n".join(lines)


def arbiter_prompt(doc: dict, claim: dict, dispute: str, arbiter: str = "AG8") -> str:
    who = "AG8, The Arbiter" if arbiter == "AG8" else "AG6, The Inquisitor"
    return (f"You are {who}, acting as arbiter for ticket {doc['ticket_meta']['id']}.\nAG4 submitted a test that FAILS on the implementation; the implementer disputes it.\n"
            f"Decide strictly from the signed contract and invariants below whether the test is faithful to them.\n\n{scope_brief(doc, QA_SCOPE)}\n\n"
            f"TEST {claim['test']} ({claim['file']}):\n{claim.get('source', '')[:2500]}\n\nFAILURE: {claim.get('failure', '')[:600]}\n\nDISPUTE: {dispute}\n\n"
            "Answer with exactly one line `VERDICT: UPHELD` (the test is right, the code is wrong) or `VERDICT: REJECTED` (the test contradicts the contract), then one sentence.")


# ------------------------------------------------------------------------------------------------ run records
class RunRecord:
    """Small JSON file per ticket: attempts, failure signatures, feedback, AG4 ledger, stored AG4 tests."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"rounds": 0, "scopes": {}, "ledger": {"falsified": 0, "false_positives": 0, "kill_ratio": None, "utility": None},
                           "history": [], "t4": {}, "bounty_rounds": 0}
        if path.is_file():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    def scope(self, name: str) -> dict:
        return self.data["scopes"].setdefault(name, {"attempts": 0, "signatures": [], "feedback": "", "standby_used": False})

    def log(self, event: str, **kw) -> None:
        self.data["history"].append({"t": round(time.time(), 1), "event": event, **kw})
        self.data["history"] = self.data["history"][-200:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(self.path), json.dumps(self.data, indent=2, ensure_ascii=False))


def failure_signature(rep: "vf.Report") -> str:
    parts = sorted(f"{c.name}:{c.status}:{re.sub(r'[0-9]+', '#', c.detail)[:120]}" for c in rep.checks if c.status in (vf.FAIL, vf.INCONCLUSIVE))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def reflection_of(output: str) -> str:
    m = re.search(r"(?im)^\s*REFLECTION:\s*(.+)$", output or "")
    return m.group(1).strip()[:300] if m else ""


def disputes_of(output: str) -> Dict[str, str]:
    out = {}
    for m in re.finditer(r"(?im)^\s*DISPUTE:\s*(\S+)\s*[-:]?\s*(.*)$", output or ""):
        out[m.group(1)] = m.group(2).strip()[:400]
    return out


# ------------------------------------------------------------------------------------------------ orchestrator
class Orchestrator:
    def __init__(self, root=".", executor: Optional[sw.Executor] = None, *, policy: Optional[Policy] = None, board: Optional[Board] = None, log=None):
        self.root = Path(root).resolve()
        self.planner = self.root / ".planner"
        for d in ("tickets", "sealed", "baselines", "work", "reports", "runs", "escalations", "prompts"):
            (self.planner / d).mkdir(parents=True, exist_ok=True)
        self.policy = policy or Policy()
        self._own_board = board is None
        self.board = board or Board(str(self.planner / "board.db"))
        self.executor = executor or sw.DryRunExecutor()
        self.swarm = sw.Swarm(self.executor, board=self.board, planner_dir=self.planner, max_parallel=self.policy.parallel_scopes, claim_wait=self.policy.claim_wait)
        self.sealed_root = self.planner / "sealed"
        self._apply_lock = threading.Lock()
        self._log = log or (lambda msg: None)

    def close(self) -> None:
        if self._own_board:
            self.board.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def log(self, msg: str) -> None:
        self._log(msg)

    # -- loading -------------------------------------------------------------------------------------
    def load_tickets(self) -> Dict[str, dict]:
        docs: Dict[str, dict] = {}
        for p in sorted((self.planner / "tickets").glob("*.yaml")):
            try:
                doc = ct.load_ticket(p)
                docs[doc["ticket_meta"]["id"]] = doc
            except Exception as exc:
                self.log(f"skipping unreadable ticket {p.name}: {exc}")
        return docs

    def load_dag(self, docs: Dict[str, dict]) -> dict:
        p = self.planner / "dag.json"
        if p.is_file():
            try:
                dag = json.loads(p.read_text(encoding="utf-8"))
                if set(dag.get("predecessors", {})) >= set(docs):
                    return dag
            except (OSError, ValueError):
                pass
        ids = sorted(docs)
        return {"waves": [ids], "predecessors": {i: list(docs[i]["ticket_meta"].get("depends_on") or []) for i in ids}}

    def register_all(self, docs: Dict[str, dict]) -> List[str]:
        problems = []
        for tid, doc in docs.items():
            if self.board.get_ticket(tid) is not None and self.board.get_ticket(tid)["state"] != "PLANNED":
                continue
            try:
                if self.policy.allow_unsigned:
                    rep = ct.validate(doc, check_signature=False)
                    rep.raise_if_bad()
                    row = self.board.get_ticket(tid) or self.board.set_ticket(tid, "PLANNED", owner="AG1", epoch=doc["ticket_meta"]["epoch"])
                    self.board.set_ticket(tid, "CONTRACTED", owner="AG1", epoch=doc["ticket_meta"]["epoch"], sha=ct.ticket_sha(doc), note="contract registered (unsigned allowed)")
                else:
                    ct.register_contract(self.board, doc)
            except Exception as exc:
                problems.append(f"{tid}: {exc}")
        return problems

    # -- schedule view -------------------------------------------------------------------------------
    def plan_view(self) -> dict:
        docs = self.load_tickets()
        dag = self.load_dag(docs)
        view = {"waves": [], "problems": []}
        for wave in dag["waves"]:
            entries = []
            for tid in wave:
                doc = docs.get(tid)
                if doc is None:
                    view["problems"].append(f"{tid}: in dag.json but no ticket file")
                    continue
                row = self.board.get_ticket(tid)
                scopes = []
                for s in sorted(doc["worker_scopes"], key=lambda x: SCOPE_ORDER.get(x, 9)):
                    wid = ct.SCOPE_WORKER[s]
                    plan = fleet.allocate_ports(wid, tid)
                    scopes.append({"scope": s, "worker": wid, "tier": self._tier(wid), "paths": doc["worker_scopes"][s]["allowed_paths"],
                                   "dev_port": plan.dev, "api_port": plan.api})
                entries.append({"ticket": tid, "state": row["state"] if row else "UNREGISTERED", "after": dag["predecessors"].get(tid, []), "scopes": scopes,
                                "needs": ["AG6 sealed tests"] + (["AG4 bounty"] if QA_SCOPE in doc["worker_scopes"] else [])})
            view["waves"].append(entries)
        return view

    def status(self) -> dict:
        rows = self.board.tickets()
        counts: Dict[str, int] = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        return {"counts": counts, "tickets": [{k: r[k] for k in ("id", "state", "attempts", "note")} for r in rows], "leases": self.board.leases()}

    # -- running -------------------------------------------------------------------------------------
    def run(self, only: Optional[Sequence[str]] = None) -> RunSummary:
        docs = self.load_tickets()
        summary = RunSummary()
        if not docs:
            return summary
        problems = self.register_all(docs)
        for p in problems:
            self.log(f"registration problem: {p}")
        dag = self.load_dag(docs)
        wanted = set(only) if only else set(docs)
        unknown = wanted - set(docs)
        for tid in sorted(unknown):
            summary.blocked[tid] = "no such ticket"
        for wave in dag["waves"]:
            todo = [t for t in wave if t in wanted and t in docs]
            runnable = []
            for tid in todo:
                row = self.board.get_ticket(tid)
                if row and row["state"] == DONE:
                    summary.outcomes[tid] = TicketOutcome(tid, DONE, "already done")
                    continue
                dep_bad = [d for d in dag["predecessors"].get(tid, []) if (self.board.get_ticket(d) or {}).get("state") != DONE]
                if dep_bad:
                    summary.blocked[tid] = f"waiting for {', '.join(dep_bad)} (not DONE)"
                    continue
                if tid in problems or any(p.startswith(tid + ":") for p in problems):
                    summary.blocked[tid] = next(p for p in problems if p.startswith(tid + ":"))
                    continue
                runnable.append(tid)
            if self.policy.parallel_tickets > 1 and len(runnable) > 1:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=min(self.policy.parallel_tickets, len(runnable))) as ex:
                    for oc in ex.map(self.run_ticket, runnable):
                        summary.outcomes[oc.ticket] = oc
            else:
                for tid in runnable:
                    summary.outcomes[tid] = self.run_ticket(tid)
        return summary

    def run_ticket(self, tid: str) -> TicketOutcome:
        started = time.monotonic()
        try:
            oc = self._run_ticket(tid)
        except Exception as exc:  # the orchestrator must survive one ticket's bug
            self.log(f"{tid}: internal error {type(exc).__name__}: {exc}")
            row = self.board.get_ticket(tid) or {}
            oc = TicketOutcome(tid, row.get("state", "UNKNOWN"), f"orchestrator error: {type(exc).__name__}: {exc}")
        oc.seconds = round(time.monotonic() - started, 2)
        return oc

    # -- board helpers -------------------------------------------------------------------------------
    def _state(self, tid: str) -> str:
        return (self.board.get_ticket(tid) or {}).get("state", "UNREGISTERED")

    def _goto(self, tid: str, target: str, note: str = "") -> None:
        paths = {"LEASED": {"SEALED": ["LEASED"], "LEASED": [], "IN_PROGRESS": ["LEASED"], "VERIFYING": ["IN_PROGRESS", "LEASED"], "FAILED": ["LEASED"]},
                 "IN_PROGRESS": {"SEALED": ["LEASED", "IN_PROGRESS"], "LEASED": ["IN_PROGRESS"], "IN_PROGRESS": [], "VERIFYING": ["IN_PROGRESS"], "FAILED": ["IN_PROGRESS"]},
                 "VERIFYING": {"IN_PROGRESS": ["VERIFYING"], "VERIFYING": []}}
        cur = self._state(tid)
        for step in paths.get(target, {}).get(cur, [target]):
            self.board.set_ticket(tid, step, owner=ORCH, note=note)
        if cur == target:
            return
        if target not in paths or cur not in paths[target]:
            self.board.set_ticket(tid, target, owner=ORCH, note=note)

    def _post(self, tid: str, kind: str, summary: str, sha: str = "") -> None:
        try:
            self.board.post(ORCH, tid, kind, ref=tid, sha=sha, summary=summary)
        except Exception:
            pass

    # -- the ticket pipeline -------------------------------------------------------------------------
    def _run_ticket(self, tid: str) -> TicketOutcome:
        docs = self.load_tickets()
        doc = docs[tid]
        row = self.board.get_ticket(tid)
        if row is None:
            return TicketOutcome(tid, "UNREGISTERED", "ticket is not registered on the blackboard")
        if row["state"] == DONE:
            return TicketOutcome(tid, DONE, "already done")
        if row["state"] == "QUARANTINED":
            return TicketOutcome(tid, "QUARANTINED", row["note"] or "quarantined; replan it")
        ok, why = ct.verify_against_board(self.board, doc)
        if not ok:
            self.board.set_ticket(tid, "QUARANTINED", owner=ORCH, note=why[:180])
            return TicketOutcome(tid, "QUARANTINED", f"contract integrity: {why}")
        run = RunRecord(self.planner / "runs" / f"{tid}.json")
        impl = [s for s in sorted(doc["worker_scopes"], key=lambda x: SCOPE_ORDER.get(x, 9)) if s in IMPL_SCOPES]
        if not impl:
            return self._escalate(tid, doc, run, "the contract has no implementing scope (only QA?)")
        # resume after an interruption or an escalation
        st = self._state(tid)
        if st == "ESCALATED":
            run.data.update(rounds=0, bounty_rounds=0, standby_used=False)
            run.data.pop("standby_pending", None)
            for rec in run.data["scopes"].values():
                rec["signatures"], rec["attempts"] = [], 0
            run.log("resumed", after="escalation")
        elif st == "LEASED":
            self.board.set_ticket(tid, "SEALED", owner=ORCH, note="resumed after interruption; workspaces are rebuilt")
        elif st in ("IN_PROGRESS", "VERIFYING"):
            self.board.set_ticket(tid, "FAILED", owner=ORCH, note="resumed after interruption")
            run.log("resumed", after="interruption")
        # AG1: stubs, deterministically
        made = symbol_check.materialize_stubs(doc, self.root)
        if made:
            run.log("stubs", files=made)
        # AG6: sealed oracle
        if self._state(tid) in ("CONTRACTED", "ESCALATED"):
            if self._state(tid) == "ESCALATED":
                self.board.set_ticket(tid, "CONTRACTED", owner=ORCH, note="resumed after escalation")
            err = self._ensure_sealed(tid, doc, impl, run)
            if err:
                return self._escalate(tid, doc, run, err)
            self.board.set_ticket(tid, "SEALED", owner="AG6", note="oracle sealed")
            self._post(tid, "sealed", f"{len(impl)} vault(s) sealed")
        run.save()
        # rounds
        attempt_limit = self.policy.max_attempts
        pending: List[str] = list(impl)
        claims: List[dict] = list(run.data.get("claims", []))
        base_dir = self.planner / "baselines" / tid / "tree"
        while run.data["rounds"] < attempt_limit + (1 if run.data.get("standby_used") else 0):
            run.data["rounds"] += 1
            rnd = run.data["rounds"]
            standby_round = bool(run.data.pop("standby_pending", False))
            if not base_dir.is_dir() or self._state(tid) in ("SEALED",):
                wt.capture(self.root, base_dir)
                for s in impl:
                    wt.capture(self.root, self._workspace(tid, s))
                run.data["scopes"] = {}
                pending = list(impl)
            self._goto(tid, "LEASED", "workspaces ready")
            self._goto(tid, "IN_PROGRESS", f"round {rnd}")
            results = self._dispatch(tid, doc, pending, run, claims, rnd, standby_round)
            self._goto(tid, "VERIFYING", f"round {rnd}")
            failing: Dict[str, str] = {}
            for s in pending:
                feedback = self._verify_scope(tid, doc, s, results.get(s), run)
                if feedback:
                    failing[s] = feedback
            if failing:
                oc = self._after_failure(tid, doc, run, failing, rnd, attempt_limit)
                if oc:
                    return oc
                pending = sorted(failing, key=lambda x: SCOPE_ORDER.get(x, 9))
                continue
            claims_out, fb = self._integrate(tid, doc, impl, run, rnd)
            if fb is None:
                return TicketOutcome(tid, DONE, "all checks passed", rnd, dict(run.data["ledger"]), waivers=["mutation"] if self.policy.mutation == "off" else [])
            # integration failed: blame everything that implements (or the claims' scopes)
            claims = claims_out
            run.data["claims"] = claims
            failing = {s: fb for s in impl}
            oc = self._after_failure(tid, doc, run, failing, rnd, attempt_limit, integration=True)
            if oc:
                return oc
            pending = list(impl)
        return self._escalate(tid, doc, run, f"no passing round within {run.data['rounds']} round(s)")

    # -- steps -----------------------------------------------------------------------------------------
    def _workspace(self, tid: str, scope: str) -> Path:
        return self.planner / "work" / tid / ct.SCOPE_WORKER[scope]

    def _tier(self, wid: str, escalated: bool = False) -> str:
        return fleet.effective_tier(wid, lead=self.policy.lead, escalated=escalated)

    def _model(self, wid: str, escalated: bool = False) -> Tuple[str, str]:
        tier = self._tier(wid, escalated)
        return self.policy.models.get(tier, ""), self.policy.effort.get(tier, "")

    def _task(self, wid: str, prompt: str, workspace: Path, tid: str, task_id: str, paths: Sequence[str] = (), standby_for: str = "",
              escalated: bool = False) -> sw.Task:
        escalated = escalated or bool(standby_for)  # the escalation solver is always promoted
        model, effort = self._model(wid, escalated)
        return sw.Task(worker=wid, prompt=prompt, workspace=str(workspace), id=task_id, ticket=tid, model=model, effort=effort,
                       timeout=self.policy.agent_timeout, paths=list(paths), standby_for=standby_for, tier=self._tier(wid, escalated))

    def _ensure_sealed(self, tid: str, doc: dict, impl: Sequence[str], run: RunRecord) -> str:
        need = []
        for s in impl:
            vid = vf.vault_id(tid, s)
            ok, problems, _m = sealed_gate.verify_vault(vid, self.sealed_root)
            if ok:
                continue
            if problems != ["NO_SEALED_TESTS"]:
                return f"{vid}: the sealed vault is damaged or tampered with ({'; '.join(problems[:3])}); delete it deliberately to re-seal"
            need.append(s)
        if not need:
            return ""
        # human supplied acceptance tests
        if self.policy.sealed_dir:
            left = []
            for s in need:
                src = Path(self.policy.sealed_dir) / tid / ct.SCOPE_WORKER[s]
                if src.is_dir():
                    err = self._seal_and_check_red(tid, s, src, doc)
                    if err:
                        return err
                else:
                    left.append(s)
            need = left
            if not need:
                return ""
        feedback = ""
        for attempt in range(1, self.policy.max_attempts + 1):
            ws = self.planner / "work" / tid / "AG6"
            wt.capture(self.root, ws)
            task = self._task("AG6", inquisitor_prompt(doc, need, feedback, attempt), ws, tid, f"{tid}.AG6.a{attempt}")
            (res,) = self.swarm.run([task])
            run.log("ag6", attempt=attempt, status=res.status)
            if not res.ok:
                feedback = f"agent run failed: {res.status} {res.stderr[-300:]}"
                continue
            changed = wt.changes(self.root, ws)
            bad = [f for f in changed.all if not matches_any(f, fleet.INQUISITOR_PATHS)]
            if bad:
                feedback = "You wrote outside tests/acceptance/**: " + ", ".join(bad[:10])
                continue
            problems = []
            sources = {}
            for s in need:
                d = ws / "tests" / "acceptance" / ct.SCOPE_WORKER[s]
                if not d.is_dir() or sealed_gate.static_test_count(d) == 0:
                    problems.append(f"no tests for {ct.SCOPE_WORKER[s]} in tests/acceptance/{ct.SCOPE_WORKER[s]}/")
                    continue
                inv = doc["worker_scopes"][s].get("formal_invariants")
                text = "\n".join((wt.read_text(p) or "") for _r, p in wt.iter_files(d))
                if isinstance(inv, dict):
                    for prop in inv.get("properties", []):
                        iid = prop.split(":", 1)[0].strip()
                        if not vf._invariant_referenced(iid, text):
                            problems.append(f"{ct.SCOPE_WORKER[s]}: {iid} is not named by any test")
                sources[s] = d
            if problems:
                feedback = "; ".join(problems)
                continue
            err = ""
            for s, d in sources.items():
                err = self._seal_and_check_red(tid, s, d, doc)
                if err:
                    break
            if not err:
                return ""
            feedback = err
        return f"AG6 could not produce an acceptable oracle: {feedback}"

    def _seal_and_check_red(self, tid: str, scope: str, tests_dir: Path, doc: dict) -> str:
        vid = vf.vault_id(tid, scope)
        try:
            sealed_gate.seal(vid, tests_dir, root=self.sealed_root, sealed_by="AG6")
        except sealed_gate.GateError as exc:
            return f"sealing refused for {vid}: {exc}"
        if self.policy.require_red:
            res = sealed_gate.run_isolated_verification(self.root, vid, root=self.sealed_root, timeout=self.policy.sealed_timeout, python=self.policy.python)
            if res.passed:
                safe_rmtree(self.sealed_root / vid)
                return f"{vid}: VACUOUS ORACLE - the sealed tests already pass against the unimplemented stubs"
        return ""

    def _dispatch(self, tid: str, doc: dict, scopes: Sequence[str], run: RunRecord, claims: Sequence[dict], rnd: int, standby_round: bool) -> Dict[str, sw.TaskResult]:
        tasks = []
        for s in scopes:
            rec = run.scope(s)
            wid = ct.SCOPE_WORKER[s]
            ws = self._workspace(tid, s)
            if not ws.is_dir():
                wt.capture(self.root, ws)
            prompt = implementer_prompt(doc, s, rec["feedback"], claims, rec["attempts"] + 1)
            if standby_round and wid == "AG8":
                # AG8 cannot stand in for itself: it retries its own scope, promoted to the premium tier
                tasks.append(self._task("AG8", prompt, ws, tid, f"{tid}.AG8.r{rnd}.escalated", doc["worker_scopes"][s]["allowed_paths"], escalated=True))
            elif standby_round:
                tasks.append(self._task("AG8", prompt, ws, tid, f"{tid}.{wid}.r{rnd}.standby", doc["worker_scopes"][s]["allowed_paths"], standby_for=wid))
            else:
                tasks.append(self._task(wid, prompt, ws, tid, f"{tid}.{wid}.r{rnd}", doc["worker_scopes"][s]["allowed_paths"]))
        results = {}
        for _try in range(4):
            res = self.swarm.run(tasks)
            blocked = [i for i, r in enumerate(res) if r.status == sw.BLOCKED]
            for i, r in enumerate(res):
                results[scopes[i]] = r
            if not blocked:
                break
            tasks = [tasks[i] for i in blocked]
            scopes_b = [scopes[i] for i in blocked]
            scopes = scopes_b
            time.sleep(1.0)
        self._post(tid, "dispatched", f"round {rnd}: " + ", ".join(f"{ct.SCOPE_WORKER[s]}={r.status}" for s, r in results.items()))
        return results

    def _verify_scope(self, tid: str, doc: dict, scope: str, result: Optional[sw.TaskResult], run: RunRecord) -> str:
        """-> '' when the scope passes, otherwise oracle-safe feedback text."""
        rec = run.scope(scope)
        rec["attempts"] += 1
        wid = ct.SCOPE_WORKER[scope]
        if result is not None and result.output:
            refl = reflection_of(result.output)
            if refl:
                run.log("reflection", worker=wid, text=refl)
            ds = disputes_of(result.output)
            if ds:
                rec["disputes"] = ds
        if result is None or not result.ok:
            status = result.status if result else "no result"
            tail = (result.stderr or result.output)[-400:] if result else ""
            rec["feedback"] = f"The agent run did not complete ({status}). {tail}".strip()
            rec["signatures"].append(f"agent:{status}")
            run.log("agent_failed", worker=wid, status=status)
            run.save()
            return rec["feedback"]
        opts = vf.Options(level="full", skip=("mutation",), planner_dir=str(self.planner), sealed_root=str(self.sealed_root), python=self.policy.python,
                          allow_unsigned=self.policy.allow_unsigned, sealed_timeout=self.policy.sealed_timeout, visible_timeout=self.policy.visible_timeout,
                          baseline=str(self.planner / "baselines" / tid / "tree"))
        rep = vf.verify(tid, scope, root=self.root, workspace=self._workspace(tid, scope), doc=doc, options=opts)
        run.log("verified", worker=wid, verdict=rep.verdict)
        if rep.verdict == vf.V_PARTIAL:
            rec["feedback"] = ""
            rec["signatures"] = []
            run.save()
            return ""
        rec["feedback"] = vf.render_feedback(rep)
        rec["signatures"].append(failure_signature(rep))
        reverted = self._revert_out_of_scope(tid, doc, scope)
        if reverted:
            rec["feedback"] += "\nThe orchestrator reverted these out-of-scope files to the baseline: " + ", ".join(reverted[:8])
        run.save()
        return rec["feedback"]

    def _revert_out_of_scope(self, tid: str, doc: dict, scope: str) -> List[str]:
        """A failed attempt must not leave forbidden files behind to poison the next attempt: restore or delete them."""
        base = self.planner / "baselines" / tid / "tree"
        ws = self._workspace(tid, scope)
        ch = wt.changes(base, ws)
        bad = [f for f in ch.all if not matches_any(f, ct.allowed_paths(doc, scope)) or matches_any(f, ct.NEVER_TOUCH)]
        out = []
        for rel in bad:
            target, src = ws / rel, base / rel
            try:
                if src.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, target)
                elif target.is_file():
                    target.unlink()
                out.append(rel)
            except OSError:
                continue
        return out

    def _after_failure(self, tid: str, doc: dict, run: RunRecord, failing: Dict[str, str], rnd: int, limit: int, integration: bool = False) -> Optional[TicketOutcome]:
        self.board.set_ticket(tid, "FAILED", owner=ORCH, note=f"round {rnd}: " + ", ".join(sorted(failing))[:120])
        self._post(tid, "round_failed", f"round {rnd} failed in {', '.join(ct.SCOPE_WORKER[s] for s in failing)}")
        for s, fb in failing.items():
            if integration:
                run.scope(s)["feedback"] = fb
        stuck = []
        for s in failing:
            sigs = run.scope(s)["signatures"]
            if len(sigs) >= 2 and sigs[-1] == sigs[-2] and not integration:
                stuck.append(s)
        exhausted = rnd >= limit + (1 if run.data.get("standby_used") else 0)
        # Reflexion retries >= standby_after: the escalation solver (AG8, premium) is summoned instead of burning more cheap rounds
        repeated = [s for s in failing if run.scope(s)["attempts"] >= self.policy.standby_after]
        summon = bool(stuck or exhausted or repeated)
        if summon and self.policy.use_standby and not run.data.get("standby_used"):
            run.data["standby_used"] = True
            run.data["standby_pending"] = True
            run.log("standby", scopes=sorted(failing))
            run.save()
            return None
        run.save()
        if stuck:
            return self._escalate(tid, doc, run, "the same failure repeated for " + ", ".join(ct.SCOPE_WORKER[s] for s in stuck) + ": the Reflexion loop is not converging", failing)
        if exhausted:
            return self._escalate(tid, doc, run, f"attempts exhausted after {rnd} round(s)", failing)
        return None

    # -- integration ---------------------------------------------------------------------------------
    def _apply(self, src_root: Path, dst_root: Path, base_root: Path, ch: wt.Changes, allowed: Optional[Sequence[str]] = None) -> None:
        """Copy the changed files of `src_root` into `dst_root`, refusing when `dst_root` moved away from `base_root`."""
        for rel in ch.added + ch.modified + ch.deleted:
            if allowed is not None and not matches_any(rel, allowed):
                raise MergeConflict(f"{rel}: outside the scope while merging")
            d, b = dst_root / rel, base_root / rel
            cur = wt.sha256_file(d) if d.is_file() else None
            was = wt.sha256_file(b) if b.is_file() else None
            if cur != was:
                raise MergeConflict(f"{rel}: changed on the integration tree since the baseline")
        for rel in ch.deleted:
            p = dst_root / rel
            if p.is_file():
                p.unlink()
        for rel in ch.added + ch.modified:
            target = dst_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".orch-tmp")
            shutil.copyfile(src_root / rel, tmp)
            replace_with_retry(str(tmp), str(target))

    def _integrate(self, tid: str, doc: dict, impl: Sequence[str], run: RunRecord, rnd: int) -> Tuple[List[dict], Optional[str]]:
        """-> (claims, None) on success or (claims, feedback) on failure. Success applies the result to the real tree."""
        stage0 = self.planner / "work" / tid / "_stage0"
        stage = self.planner / "work" / tid / "_stage"
        wt.capture(self.root, stage0)
        wt.capture(self.root, stage)
        base = self.planner / "baselines" / tid / "tree"
        try:
            for s in impl:
                ws = self._workspace(tid, s)
                ch = wt.changes(base, ws)
                self._apply(ws, stage, base, ch, ct.allowed_paths(doc, s))
        except MergeConflict as exc:
            return [], f"MERGE_CONFLICT: {exc}. Another ticket changed the same files; rebase on the current tree."
        # 1. the sealed vaults against the merged result
        for s in impl:
            vid = vf.vault_id(tid, s)
            res = sealed_gate.run_isolated_verification(stage, vid, root=self.sealed_root, timeout=self.policy.sealed_timeout, python=self.policy.python, reveal="ids")
            if not res.passed:
                return [], f"INTEGRATION: the sealed oracle of {ct.SCOPE_WORKER[s]} fails on the merged tree: {res.reason}. {res.output}".strip()
        # 2. stored AG4 tests, then the bounty
        claims: List[dict] = []
        have_qa = QA_SCOPE in doc["worker_scopes"] and self.policy.bounty != "off"
        t4_dir = self.planner / "runs" / f"{tid}.t4"
        qa_allowed = ct.allowed_paths(doc, QA_SCOPE) if QA_SCOPE in doc["worker_scopes"] else []
        if have_qa and t4_dir.is_dir():
            for rel, p in wt.iter_files(t4_dir):
                dst = stage / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dst)
            claims = self._adjudicate(tid, doc, self._run_t4(stage, [r for r, _p in wt.iter_files(t4_dir)], tid), run, stage, t4_dir)
            if claims:
                run.data["ledger"]["falsified"] = max(run.data["ledger"]["falsified"], len(claims))
                return claims, "AG4 CLAIMS: " + "; ".join(c["test"] for c in claims[:8]) + " fail on your implementation (see the claim details below)."
        threshold = self._threshold(doc)
        attacks = 0
        while True:
            report = self._mutation(tid, doc, stage, stage0, impl)
            survivors: List[str] = []
            if report is not None:
                if report.status == mutation_gate.FAIL:
                    survivors = [f"{x['file']}:{x['line']} {x['description']}" for x in report.survivors] or [report.reason]
                elif report.status == mutation_gate.INCONCLUSIVE:
                    return claims, f"MUTATION GATE INCONCLUSIVE: {report.reason}"
                run.data["ledger"]["kill_ratio"] = report.score
            first = run.data["bounty_rounds"] == 0 or (self.policy.bounty == "always" and attacks == 0)
            if not have_qa or not (survivors or first):
                if survivors:  # nobody can attack, the gate decides alone
                    return claims, f"MUTATION: kill score {report.score} < {threshold}: " + "; ".join(survivors[:10])
                break
            if survivors and attacks >= self.policy.bounty_rounds:
                return claims, (f"MUTATION: kill score {report.score} < {threshold}; AG4 could not kill the surviving mutants in {attacks} round(s): "
                                + "; ".join(survivors[:10]))
            attacks += 1
            run.data["bounty_rounds"] += 1
            round_claims, err = self._bounty_round(tid, doc, stage, stage0, survivors, run, t4_dir, qa_allowed)
            if err:
                return claims, err
            if round_claims:
                run.data["ledger"]["falsified"] += len(round_claims)
                return round_claims, "AG4 CLAIMS: " + "; ".join(c["test"] for c in round_claims[:8]) + " fail on your implementation."
        led = run.data["ledger"]
        led["utility"] = round(self.policy.alpha * led["falsified"] + self.policy.beta * (led["kill_ratio"] or 0.0) - self.policy.gamma * led["false_positives"], 3)
        # 3. apply to the real tree
        final = wt.changes(stage0, stage)
        with self._apply_lock:
            try:
                self._apply(stage, self.root, stage0, final)
            except MergeConflict as exc:
                return [], f"MERGE_CONFLICT at the final apply: {exc}"
        self.board.set_ticket(tid, DONE, owner=ORCH, sha=hashlib.sha256(json.dumps(sorted(final.touched)).encode()).hexdigest()[:16], note=f"round {rnd}, U_QA={led['utility']}")
        self._post(tid, "ticket_done", f"round {rnd}; files: {len(final.touched)}; U_QA={led['utility']}")
        run.log("done", files=final.touched)
        run.save()
        return [], None

    def _threshold(self, doc: dict) -> float:
        if self.policy.mutation_threshold is not None:
            return self.policy.mutation_threshold
        mg = (doc["worker_scopes"].get(QA_SCOPE) or {}).get("mutation_gate") or {}
        return float(mg.get("min_kill_score", mutation_gate.DEFAULT_THRESHOLD))

    def _union_vaults(self, tid: str, impl: Sequence[str], dest: Path) -> Optional[Path]:
        n = 0
        for s in impl:
            src = self.sealed_root / vf.vault_id(tid, s) / "tests"
            if src.is_dir():
                sealed_gate.copy_tree_safe(src, dest / ct.SCOPE_WORKER[s], ignore_dirs=("__pycache__", ".pytest_cache"))
                n += 1
        return dest if n else None

    def _mutation(self, tid: str, doc: dict, stage: Path, stage0: Path, impl: Sequence[str]) -> Optional["mutation_gate.MutationReport"]:
        if self.policy.mutation == "off":
            return None
        rels = [r for r in wt.changes(stage0, stage).touched if r.endswith(".py") and not weakening.is_test_file(r)]
        changed = wt.diff_lines(stage0, stage, rels)
        union_dir = self.planner / "work" / tid / "_union"
        if union_dir.exists():
            safe_rmtree(union_dir)
        sealed = self._union_vaults(tid, impl, union_dir)
        visible = sorted(r for r, _p in wt.iter_files(stage) if r.endswith(".py") and weakening.is_test_file(r))
        cfg = mutation_gate.MutationConfig(threshold=self._threshold(doc), max_mutants=self.policy.mutation_max, time_budget=self.policy.mutation_budget,
                                           workers=self.policy.mutation_workers, python=self.policy.python)
        return mutation_gate.run_mutation(stage, changed, sealed_tests=sealed, visible_tests=visible, config=cfg)

    def _run_pytest(self, tree: Path, files: Sequence[str], timeout: float = 240.0, junit_name: str = "t4.xml") -> Tuple[Optional[dict], str]:
        junit = self.planner / "reports" / junit_name
        try:
            junit.unlink()
        except OSError:
            pass
        env = build_sandbox_env({"PYTHONPATH": str(tree), "PYTHONHASHSEED": "0"})
        cmd = [self.policy.python, "-m", "pytest", "-q", "--tb=short", "-rN", "--disable-warnings", "-p", "no:cacheprovider", f"--junitxml={junit}", *files]
        res = run_process(cmd, cwd=str(tree), env=env, timeout=timeout)
        j = sealed_gate.parse_junit(junit)
        return (j if j["ok"] else None), (res.stdout[-1500:] + res.stderr[-300:])

    def _run_t4(self, stage: Path, files: Sequence[str], tid: str) -> List[dict]:
        py = [f for f in files if f.endswith(".py")]
        if not py:
            return []
        j, tail = self._run_pytest(stage, py, junit_name=f"{tid}.t4.xml")
        if j is None:
            return [{"test": "(collection)", "file": py[0], "failure": f"AG4 tests could not run: {tail[-300:]}", "source": ""}]
        claims = []
        for nodeid in j["failed"]:
            head, _, name = nodeid.partition("::")
            f = head.replace("\\", "/").replace(".", "/")
            rel = next((p for p in py if Path(p).with_suffix("").as_posix() == f or f.endswith(Path(p).stem)), py[0])
            src = wt.read_text(stage / rel) or ""
            claims.append({"test": f"{rel}::{name}" if name else rel, "file": rel, "source": src[:2500], "failure": tail[-600:]})
        return claims

    def _bounty_round(self, tid: str, doc: dict, stage: Path, stage0: Path, survivors: Sequence[str], run: RunRecord, t4_dir: Path,
                      qa_allowed: Sequence[str]) -> Tuple[List[dict], str]:
        ws = self.planner / "work" / tid / "AG4"
        wt.capture(stage, ws)
        before = self.planner / "work" / tid / "_ag4_before"
        wt.capture(stage, before)
        n = run.data["bounty_rounds"]
        (res,) = self.swarm.run([self._task("AG4", bounty_prompt(doc, survivors, "", n), ws, tid, f"{tid}.AG4.r{n}", qa_allowed)])
        run.log("ag4", round=n, status=res.status)
        if not res.ok:
            return [], f"AG4 run failed ({res.status}): {res.stderr[-300:]}"
        ch = wt.changes(before, ws)
        bad = ct.check_scope(doc, QA_SCOPE, ch.all)
        if bad:
            return [], "AG4 wrote outside its scope: " + "; ".join(bad[:6])
        viol = weakening.check_weakening(before, ws, forbidden_calls=ct.forbidden_calls(doc, QA_SCOPE))
        if viol:
            return [], "AG4 tests violate the anti-weakening rules: " + "; ".join(str(v) for v in viol[:5])
        new_py = [r for r in ch.touched if r.endswith(".py")]
        if not new_py:
            return [], ""
        t4_dir.mkdir(parents=True, exist_ok=True)
        for rel in ch.touched:
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ws / rel, dst)
            keep = t4_dir / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ws / rel, keep)
        claims = self._run_t4(stage, new_py, tid)
        return self._adjudicate(tid, doc, claims, run, stage, t4_dir), ""

    @staticmethod
    def _norm_test_id(raw: str) -> str:
        head, _, name = raw.replace("\\", "/").lstrip("./").partition("::")
        if head.endswith(".py"):
            head = head[:-3]
        return head.replace(".", "/") + ("::" + name if name else "")

    def _adjudicate(self, tid: str, doc: dict, claims: List[dict], run: RunRecord, stage: Path, t4_dir: Path) -> List[dict]:
        """Claims the implementer disputed go to the arbiter (AG8, or AG6 when AG8 took part in the ticket). A rejected claim costs AG4 (false positive) and its test is dropped."""
        if not claims:
            return claims
        disputed: Dict[str, str] = {}
        for sname in (x for x in doc["worker_scopes"] if x in IMPL_SCOPES):
            for k, v in (run.scope(sname).get("disputes") or {}).items():
                disputed[self._norm_test_id(k)] = v
        kept = []
        for c in claims:
            cid = self._norm_test_id(c["test"])
            why = disputed.get(cid) or next((v for k, v in disputed.items() if k and (k in cid or cid in k)), "")
            if why and self._arbiter_rejects(tid, doc, c, why, run):
                run.data["ledger"]["false_positives"] += 1
                run.log("claim_rejected", test=c["test"])
                self._drop_t4_file(stage, t4_dir, c["file"])
            else:
                kept.append(c)
        return kept

    def _drop_t4_file(self, stage: Path, t4_dir: Path, rel: str) -> None:
        for base in (stage, t4_dir):
            try:
                (base / rel).unlink()
            except OSError:
                pass

    def _arbiter_for(self, doc: dict, run: RunRecord) -> str:
        """AG8 arbitrates QA bounty disputes unless it took part in the ticket (own scope or escalation solver): nobody judges
        their own work, so AG6 steps in then. An explicit Policy.arbiter is respected only while it stays independent."""
        took_part = "worker_security" in doc["worker_scopes"] or bool(run.data.get("standby_used"))
        want = self.policy.arbiter
        if want == "auto":
            return "AG6" if took_part else "AG8"
        if want == "AG8" and took_part:
            return "AG6"
        return want

    def _arbiter_rejects(self, tid: str, doc: dict, claim: dict, why: str, run: RunRecord) -> bool:
        arb = self._arbiter_for(doc, run)
        ws = self.planner / "work" / tid / f"{arb}.arbiter"
        wt.capture(self.root, ws)
        (res,) = self.swarm.run([self._task(arb, arbiter_prompt(doc, claim, why, arb), ws, tid, f"{tid}.{arb}.arb.{len(run.data['history'])}")])
        m = re.search(r"(?im)^\s*VERDICT:\s*(UPHELD|REJECTED)", res.output or "")
        run.log("arbiter", worker=arb, test=claim["test"], verdict=m.group(1).upper() if m else "none")
        return bool(m and m.group(1).upper() == "REJECTED")

    # -- escalation ----------------------------------------------------------------------------------
    def _escalate(self, tid: str, doc: dict, run: RunRecord, reason: str, failing: Optional[Dict[str, str]] = None) -> TicketOutcome:
        state = self._state(tid)
        if state not in ("ESCALATED", "DONE"):
            try:
                self.board.set_ticket(tid, "ESCALATED", owner=ORCH, note=reason[:180])
            except IllegalTransition:
                self.board.set_ticket(tid, "QUARANTINED", owner=ORCH, note=reason[:180])
        lines = [f"# Escalation: {tid}", "", f"Reason: {reason}", "", f"Rounds used: {run.data['rounds']}", "",
                 "## What each scope last reported", ""]
        for s, rec in sorted(run.data["scopes"].items()):
            lines += [f"### {ct.SCOPE_WORKER.get(s, s)} ({s}) - {rec['attempts']} attempt(s)", "", "```", (rec.get("feedback") or "(none)")[:2000], "```", ""]
        lines += ["## Suggested next actions", "",
                  "- Read the feedback above. If the oracle (sealed tests) is wrong or too weak, re-seal it: delete `.planner/sealed/<ticket>.<AGx>` and set the ticket back to CONTRACTED.",
                  "- If the contract is wrong or contradictory, re-plan: edit the plan and re-run `dag_planner.py plan`; the new signature gets a new epoch.",
                  "- If only the agent failed, raise `--max-attempts` or enable `--standby` (AG8) and run again; completed work is kept.", ""]
        p = self.planner / "escalations" / f"{tid}.md"
        atomic_write_text(str(p), "\n".join(lines))
        self._post(tid, "escalated", reason[:200])
        run.log("escalated", reason=reason)
        run.save()
        return TicketOutcome(tid, ESCALATED, reason, run.data["rounds"], dict(run.data["ledger"]), str(p))


# ------------------------------------------------------------------------------------------------ CLI
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orchestrator", description="Drive Contract-First tickets through the bicameral fleet (plan | run | status)")
    sub = ap.add_subparsers(dest="subcmd")
    for name in ("plan", "run", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=".")
        p.add_argument("--json", action="store_true")
        if name == "run":
            p.add_argument("--tickets", default="", help="comma separated ticket ids (default: all)")
            p.add_argument("--executor", choices=("dry-run", "command", "legacy", "antigravity"), default=os.environ.get("PLANNER_EXECUTOR", "dry-run"))
            p.add_argument("--cmd", default="", help="agent command template, see swarm.py")
            p.add_argument("--stdin-prompt", action="store_true")
            p.add_argument("--pass-env", default="")
            p.add_argument("--legacy-script", default="")
            p.add_argument("--max-attempts", type=int, default=3)
            p.add_argument("--standby", action="store_true", help="escalation solver: AG8 (premium tier) takes over a scope that keeps failing, one extra attempt")
            p.add_argument("--standby-after", type=int, default=2, help="failed attempts of a scope before AG8 is summoned (default 2)")
            p.add_argument("--lead", default="1", help="architect that carries the premium (Argon) quota: 1, 6 or 8 (default 1)")
            p.add_argument("--arbiter", choices=("auto", "AG8", "AG6"), default="auto")
            p.add_argument("--parallel-scopes", type=int, default=4)
            p.add_argument("--parallel-tickets", type=int, default=1)
            p.add_argument("--agent-timeout", type=float, default=900.0)
            p.add_argument("--bounty", choices=("auto", "always", "off"), default="auto")
            p.add_argument("--mutation", choices=("on", "off"), default="on")
            p.add_argument("--mutation-threshold", type=float)
            p.add_argument("--mutation-max", type=int, default=60)
            p.add_argument("--mutation-budget", type=float, default=600.0)
            p.add_argument("--sealed-dir", default=None, help="<dir>/<ticket>/<AGx>/ with human-written acceptance tests (AG6 is skipped)")
            p.add_argument("--allow-unsigned", action="store_true")
            p.add_argument("--model-premium", default="")
            p.add_argument("--model-standard", default="")
            p.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args(argv)
    if not a.subcmd:
        ap.print_help()
        return 0
    root = Path(a.root)
    if not (root / ".planner" / "tickets").is_dir():
        print(f"orchestrator: no .planner/tickets under {root.resolve()} - run `dag_planner.py plan.yaml --out .planner/tickets --board .planner/board.db` first", file=sys.stderr)
        return 2
    try:
        if a.subcmd == "run":
            executor = sw.build_executor(a.executor, a.cmd, a.pass_env, a.legacy_script, a.stdin_prompt)
            models = {k: v for k, v in (("premium", a.model_premium), ("standard", a.model_standard)) if v}
            pol = Policy(max_attempts=a.max_attempts, use_standby=a.standby, standby_after=a.standby_after, lead=a.lead, arbiter=a.arbiter, parallel_scopes=a.parallel_scopes, parallel_tickets=a.parallel_tickets,
                         agent_timeout=a.agent_timeout, bounty=a.bounty, mutation=a.mutation, mutation_threshold=a.mutation_threshold,
                         mutation_max=a.mutation_max, mutation_budget=a.mutation_budget, sealed_dir=a.sealed_dir, allow_unsigned=a.allow_unsigned, models=models)
            if a.executor == "dry-run":
                print("orchestrator: the dry-run executor performs no work; AG6/AG2/AG5 cannot author anything. Use `--executor command --cmd ...` (see swarm.py) or `plan`.", file=sys.stderr)
            with Orchestrator(root, executor, policy=pol, log=(lambda m: print(m, file=sys.stderr)) if a.verbose else None) as orch:
                summary = orch.run([t for t in a.tickets.split(",") if t] or None)
            text = json.dumps(summary.to_dict(), indent=2, ensure_ascii=False)
            print(text if a.json else _human(summary))
            return 0 if summary.ok else 1
        with Orchestrator(root) as orch:
            data = orch.plan_view() if a.subcmd == "plan" else orch.status()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except (sw.SwarmError, OSError, ValueError) as exc:
        print(f"orchestrator: {exc}", file=sys.stderr)
        return 2


def _human(s: RunSummary) -> str:
    lines = []
    for tid, oc in s.outcomes.items():
        lines.append(f"{oc.state:10} {tid}  ({oc.rounds} round(s), {oc.seconds}s) {oc.reason}")
        if oc.escalation:
            lines.append(f"           report: {oc.escalation}")
    for tid, why in s.blocked.items():
        lines.append(f"BLOCKED    {tid}  {why}")
    lines.append("ALL DONE" if s.ok else "NOT COMPLETE")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
