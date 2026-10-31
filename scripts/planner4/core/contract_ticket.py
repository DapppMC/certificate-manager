"""contract_ticket.py - Contract-First executable tickets (Planner 4.0 spec section 6).

A contract ticket is written by AG1 "The Lawgiver" BEFORE any worker starts. It fixes:
  * ticket_meta      id, epoch, touches (the lease set), lawgiver_signature
  * shared_contracts SCIP symbols + the exact signatures every worker must honour (locked by hash)
  * worker_scopes    one scope per role, with EXCLUSIVE write paths and role-specific obligations
                     worker_db (AG2) | worker_backend (AG5) | worker_ui (AG3) | worker_qa_guard (AG4) | worker_devops (AG7, extension)
                     | worker_security (AG8, extension: AppSec / concurrency hardening, never the code it arbitrates)

This module is the single place that knows the schema: strict validation (unknown keys are errors, so a typo cannot
silently disable a guard), canonical signing, scope checks used by the orchestrator, conversion to the flat Planner
3.0 ticket (so `tools/ticketc.py diff` keeps working), and registration on the blackboard.

Signature honesty: `sha256:` is TAMPER-EVIDENCE (an edited ticket no longer matches), not authentication - anybody who
can write the file can recompute it. `hmac-sha256:` with a key from the environment (PLANNER_LAWGIVER_KEY, never from a
file) authenticates the Lawgiver. The root of trust is the blackboard `contract_signed` event: a worker that rewrites
the YAML after AG1 signed it produces a hash that differs from the event, and `verify_against_board` rejects it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from core import miniyaml
from core import scip_symbols as scip
from core.platform_compat import PathError, atomic_write_text, has_glob, matches_any, normalize_rel_path, patterns_overlap

SCOPE_WORKER = {"worker_db": "AG2", "worker_backend": "AG5", "worker_ui": "AG3", "worker_qa_guard": "AG4", "worker_devops": "AG7", "worker_security": "AG8"}
WORKER_SCOPE = {v: k for k, v in SCOPE_WORKER.items()}
TOP_KEYS = {"ticket_meta", "shared_contracts", "worker_scopes"}
META_KEYS = {"id", "epoch", "lawgiver_signature", "touches", "goal", "depends_on", "acceptance", "stop_if", "protected_tests", "frozen"}
SCOPE_KEYS = {
    "worker_db": {"worker_id", "allowed_paths", "forbidden_ast", "formal_invariants", "acceptance"},
    "worker_backend": {"worker_id", "allowed_paths", "formal_invariants", "forbidden_calls", "acceptance"},
    "worker_ui": {"worker_id", "allowed_paths", "mandatory_fsm", "kinematic_spec", "selector", "url", "acceptance"},
    "worker_qa_guard": {"worker_id", "role", "allowed_paths", "forbidden_calls", "mutation_gate", "acceptance"},
    "worker_devops": {"worker_id", "allowed_paths", "acceptance"},
    "worker_security": {"worker_id", "allowed_paths", "formal_invariants", "forbidden_calls", "acceptance"},
}
KINEMATIC_KEYS = {"spring_damping_zeta", "target_apca_lc", "optical_trim"}
MUTATION_KEYS = {"min_kill_score", "target_diff_only"}
PROPERTY_ENGINES = {"fast-check", "hypothesis", "proptest", "quickcheck", "jqwik", "gopter"}
TICKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{2,100}$")
SIG_RE = re.compile(r"^(sha256|hmac-sha256):([0-9a-f]{64})$")
INVARIANT_RE = re.compile(r"^INV-\d{2,3}:\s*\S.*$", re.S)
NEVER_TOUCH = (".git/**", ".planner/**", ".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "**/id_rsa*", "**/*.key", "**/secrets/**")
TEST_HINT = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)|\.(spec|test)\.[a-z]+$|(^|/)test_[^/]*\.py$|_test\.(py|go)$", re.I)
DEFAULT_KEY_ENV = "PLANNER_LAWGIVER_KEY"


# ---------------------------------------------------------------------------------------------------------------
# Canonical form and signature
# ---------------------------------------------------------------------------------------------------------------


def _canon(v: Any) -> Any:
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v.replace("\r\n", "\n").replace("\r", "\n")).rstrip("\n")
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 1e15 else v
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, Mapping):
        return {str(k): _canon(x) for k, x in v.items()}
    return v


def canonical_bytes(doc: Mapping) -> bytes:
    """Deterministic bytes of the ticket WITHOUT its signature: sorted keys, no whitespace, NFC text, LF newlines,
    no trailing newline in strings (block-scalar style cannot change the hash), 75.0 == 75."""
    body = json.loads(json.dumps(doc))  # deep copy; also rejects non-JSON values
    meta = body.get("ticket_meta")
    if isinstance(meta, dict):
        meta.pop("lawgiver_signature", None)
    return json.dumps(_canon(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_signature(doc: Mapping, key: Optional[bytes] = None) -> str:
    data = canonical_bytes(doc)
    if key:
        return "hmac-sha256:" + hmac.new(key, data, hashlib.sha256).hexdigest()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def key_from_env(var: str = DEFAULT_KEY_ENV) -> Optional[bytes]:
    v = os.environ.get(var)
    return v.encode("utf-8") if v else None


def sign(doc: dict, key: Optional[bytes] = None) -> dict:
    """Set ticket_meta.lawgiver_signature in place and return doc."""
    doc.setdefault("ticket_meta", {})["lawgiver_signature"] = compute_signature(doc, key)
    return doc


def verify_signature(doc: Mapping, key: Optional[bytes] = None, *, require_hmac: bool = False) -> tuple:
    """(ok, reason). With `key` an HMAC signature is required to verify; a plain sha256 is accepted only when
    require_hmac is False."""
    sig = (doc.get("ticket_meta") or {}).get("lawgiver_signature")
    if not isinstance(sig, str) or not sig:
        return False, "ticket is unsigned"
    m = SIG_RE.match(sig)
    if not m:
        return False, f"malformed signature {sig[:30]!r} (want sha256:<64 hex> or hmac-sha256:<64 hex>)"
    kind = m.group(1)
    if kind == "hmac-sha256":
        if not key:
            return False, "ticket carries an HMAC signature but no key is available (set PLANNER_LAWGIVER_KEY)"
        ok = hmac.compare_digest(sig, compute_signature(doc, key))
        return ok, "" if ok else "HMAC mismatch: the ticket was edited after AG1 signed it (or the key differs)"
    if require_hmac:
        return False, "an HMAC signature is required but the ticket has a plain sha256"
    ok = hmac.compare_digest(sig, compute_signature(doc, None))
    return ok, "" if ok else "sha256 mismatch: the ticket was edited after it was signed"


# ---------------------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Report:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def raise_if_bad(self) -> None:
        if self.errors:
            raise ContractError("; ".join(self.errors))


class ContractError(ValueError):
    pass


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def _str_list(rep: Report, where: str, v: Any, *, required: bool = False, nonempty: bool = False) -> List[str]:
    if v is None:
        if required:
            rep.err(f"{where} is required")
        return []
    if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
        rep.err(f"{where} must be a list of non-empty strings")
        return []
    if nonempty and not v:
        rep.err(f"{where} must not be empty")
    return list(v)


def _norm_paths(rep: Report, where: str, paths: Sequence[str]) -> List[str]:
    out = []
    for p in paths:
        try:
            out.append(normalize_rel_path(p))
        except PathError as e:
            rep.err(f"{where}: {e}")
    if len(set(out)) != len(out):
        rep.err(f"{where} contains duplicate entries")
    return out


def covers(pattern: str, touch: str) -> bool:
    """Is the lease entry `touch` (concrete path or glob) inside the write scope `pattern`?"""
    if has_glob(touch):
        return patterns_overlap(pattern, touch)
    return matches_any(touch, [pattern])


def validate(doc: Any, *, check_signature: bool = True, allow_unsigned: bool = False, key: Optional[bytes] = None,
             require_hmac: bool = False) -> Report:
    """Strict schema + consistency validation. Never raises for bad content; read Report.errors."""
    rep = Report()
    if not isinstance(doc, dict):
        rep.err("ticket must be a mapping")
        return rep
    for k in doc:
        if k not in TOP_KEYS:
            rep.err(f"unknown top-level key {k!r} (allowed: {', '.join(sorted(TOP_KEYS))})")
    for k in ("ticket_meta", "worker_scopes"):
        if not isinstance(doc.get(k), dict):
            rep.err(f"{k} is required and must be a mapping")
    if rep.errors:
        return rep
    touches = _validate_meta(rep, doc["ticket_meta"])
    symbols = _validate_contracts(rep, doc.get("shared_contracts"))
    scope_paths = _validate_scopes(rep, doc["worker_scopes"], touches)
    _validate_coverage(rep, touches, scope_paths, doc["worker_scopes"])
    if check_signature:
        sig = doc["ticket_meta"].get("lawgiver_signature")
        if not sig and allow_unsigned:
            rep.warn("ticket is unsigned (allowed while planning; AG1 must sign before release)")
        else:
            ok, why = verify_signature(doc, key, require_hmac=require_hmac)
            if not ok:
                rep.err(f"signature: {why}")
    _ = symbols
    return rep


def _validate_meta(rep: Report, meta: dict) -> List[str]:
    for k in meta:
        if k not in META_KEYS:
            rep.err(f"ticket_meta: unknown key {k!r}")
    tid = meta.get("id")
    if not isinstance(tid, str) or not TICKET_ID_RE.match(tid):
        rep.err("ticket_meta.id must match [A-Za-z0-9][A-Za-z0-9_.-]{2,100}")
    ep = meta.get("epoch")
    if not isinstance(ep, int) or isinstance(ep, bool) or ep < 1:
        rep.err("ticket_meta.epoch must be an integer >= 1")
    touches_raw = _str_list(rep, "ticket_meta.touches", meta.get("touches"), required=True, nonempty=True)
    norm = _norm_paths(rep, "ticket_meta.touches", touches_raw)
    for t, n in zip(touches_raw, norm):
        if matches_any(n, NEVER_TOUCH):
            rep.err(f"ticket_meta.touches: {t!r} is a protected location (git, planner state or secrets)")
        if t.strip() in ("*", "**", "**/*"):
            rep.err(f"ticket_meta.touches: {t!r} is too broad to be a lease")
    for opt in ("depends_on", "stop_if", "acceptance", "protected_tests"):
        if opt in meta:
            _str_list(rep, f"ticket_meta.{opt}", meta[opt])
    if "goal" in meta and (not isinstance(meta["goal"], str) or not meta["goal"].strip()):
        rep.err("ticket_meta.goal must be a non-empty string")
    if "frozen" in meta and (not isinstance(meta["frozen"], dict) or any(not isinstance(v, str) for v in meta["frozen"].values())):
        rep.err("ticket_meta.frozen must be a map of path: sha256")
    deps = meta.get("depends_on") or []
    if isinstance(tid, str) and tid in deps:
        rep.err("ticket_meta.depends_on lists the ticket itself")
    return touches_raw


def _validate_contracts(rep: Report, sc: Any) -> Dict[str, str]:
    seen: Dict[str, str] = {}
    if sc is None:
        return seen
    if not isinstance(sc, dict):
        rep.err("shared_contracts must be a mapping")
        return seen
    for k in sc:
        if k != "scip_symbols":
            rep.err(f"shared_contracts: unknown key {k!r}")
    syms = sc.get("scip_symbols", [])
    if not isinstance(syms, list):
        rep.err("shared_contracts.scip_symbols must be a list")
        return seen
    for i, item in enumerate(syms):
        w = f"shared_contracts.scip_symbols[{i}]"
        if not isinstance(item, dict):
            rep.err(f"{w} must be a mapping")
            continue
        for k in item:
            if k not in ("symbol", "signature", "lock", "owner"):
                rep.err(f"{w}: unknown key {k!r}")
        text, sig = item.get("symbol"), item.get("signature")
        try:
            parsed = scip.parse_symbol(text)
        except scip.SymbolError as e:
            rep.err(f"{w}.symbol: {e}")
            continue
        if parsed.is_local:
            rep.err(f"{w}.symbol: local symbols cannot be shared between workers")
        elif parsed.scheme not in scip.KNOWN_SCHEMES:
            rep.warn(f"{w}.symbol: unknown scheme {parsed.scheme!r}")
        if text in seen:
            rep.err(f"{w}.symbol duplicates an earlier symbol")
        if not isinstance(sig, str) or not sig.strip():
            rep.err(f"{w}.signature must be a non-empty string")
            continue
        if len(sig) > 20000:
            rep.err(f"{w}.signature is longer than 20000 characters")
        lock = scip.signature_lock(sig)
        if "lock" in item and item["lock"] != lock:
            rep.err(f"{w}: lock mismatch - the signature of {text!r} changed after it was locked")
        warn = None if parsed.is_local else scip.check_kind(parsed, sig)
        if warn:
            rep.warn(f"{w}: {warn}")
        seen[text] = lock
    return seen


def _num_range(rep: Report, where: str, v: Any, lo: float, hi: float, *, lo_open: bool = False) -> None:
    if not _is_num(v) or (v <= lo if lo_open else v < lo) or v > hi:
        rep.err(f"{where} must be a number in {'(' if lo_open else '['}{lo}, {hi}]")


def _validate_scopes(rep: Report, scopes: dict, touches: List[str]) -> Dict[str, List[str]]:
    if not scopes:
        rep.err("worker_scopes must define at least one scope")
    paths_by_scope: Dict[str, List[str]] = {}
    for name, sc in scopes.items():
        w = f"worker_scopes.{name}"
        if name not in SCOPE_WORKER:
            rep.err(f"{w}: unknown scope (allowed: {', '.join(SCOPE_WORKER)})")
            continue
        if not isinstance(sc, dict):
            rep.err(f"{w} must be a mapping")
            continue
        for k in sc:
            if k not in SCOPE_KEYS[name]:
                rep.err(f"{w}: unknown key {k!r}")
        if sc.get("worker_id") != SCOPE_WORKER[name]:
            rep.err(f"{w}.worker_id must be {SCOPE_WORKER[name]!r}")
        raw = _str_list(rep, f"{w}.allowed_paths", sc.get("allowed_paths"), required=True, nonempty=True)
        norm = _norm_paths(rep, f"{w}.allowed_paths", raw)
        for r in raw:
            if r.strip() in ("*", "**", "**/*", "./**"):
                rep.err(f"{w}.allowed_paths: {r!r} is too broad")
            elif name != "worker_qa_guard" and TEST_HINT.search(r.replace("\\", "/")):
                rep.warn(f"{w}.allowed_paths: {r!r} looks like a test path; only worker_qa_guard should write tests")
        paths_by_scope[name] = norm
        if "acceptance" in sc:
            _str_list(rep, f"{w}.acceptance", sc["acceptance"])
        getattr(_Role, name)(rep, w, sc)
    # exclusive write scopes
    names = list(paths_by_scope)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for a in paths_by_scope[names[i]]:
                for b in paths_by_scope[names[j]]:
                    if patterns_overlap(a, b):
                        rep.err(f"write scopes overlap: {names[i]} {a!r} vs {names[j]} {b!r} (each file needs exactly one writer)")
    return paths_by_scope


class _Role:
    """Role-specific obligations."""

    @staticmethod
    def worker_db(rep, w, sc):
        _str_list(rep, f"{w}.forbidden_ast", sc.get("forbidden_ast"))
        inv = sc.get("formal_invariants")
        if inv is not None:
            _str_list(rep, f"{w}.formal_invariants", inv)

    @staticmethod
    def worker_backend(rep, w, sc):
        _str_list(rep, f"{w}.forbidden_calls", sc.get("forbidden_calls"))
        inv = sc.get("formal_invariants")
        if inv is None:
            return
        if not isinstance(inv, dict):
            rep.err(f"{w}.formal_invariants must be a mapping with engine and properties")
            return
        for k in inv:
            if k not in ("engine", "properties"):
                rep.err(f"{w}.formal_invariants: unknown key {k!r}")
        eng = inv.get("engine")
        if not isinstance(eng, str) or not eng:
            rep.err(f"{w}.formal_invariants.engine is required")
        elif eng not in PROPERTY_ENGINES:
            rep.warn(f"{w}.formal_invariants.engine {eng!r} is not one of {sorted(PROPERTY_ENGINES)}")
        props = _str_list(rep, f"{w}.formal_invariants.properties", inv.get("properties"), required=True, nonempty=True)
        ids = []
        for p in props:
            if not INVARIANT_RE.match(p):
                rep.err(f"{w}.formal_invariants.properties: {p[:50]!r} must look like 'INV-01: <property>'")
            else:
                ids.append(p.split(":", 1)[0])
        if len(set(ids)) != len(ids):
            rep.err(f"{w}.formal_invariants.properties has duplicate invariant ids")

    @staticmethod
    def worker_ui(rep, w, sc):
        fsm = sc.get("mandatory_fsm")
        if fsm is not None and (not isinstance(fsm, str) or not fsm.strip()):
            rep.err(f"{w}.mandatory_fsm must be a non-empty string")
        ks = sc.get("kinematic_spec")
        if ks is None:
            rep.warn(f"{w}.kinematic_spec is missing: the CDP linter will have no spring/contrast/optical targets")
            return
        if not isinstance(ks, dict):
            rep.err(f"{w}.kinematic_spec must be a mapping")
            return
        for k in ks:
            if k not in KINEMATIC_KEYS:
                rep.err(f"{w}.kinematic_spec: unknown key {k!r}")
        if "spring_damping_zeta" in ks:
            _num_range(rep, f"{w}.kinematic_spec.spring_damping_zeta", ks["spring_damping_zeta"], 0, 3, lo_open=True)
        if "target_apca_lc" in ks:
            _num_range(rep, f"{w}.kinematic_spec.target_apca_lc", ks["target_apca_lc"], 15, 108)
        if "optical_trim" in ks and not isinstance(ks["optical_trim"], (str, bool)):
            rep.err(f"{w}.kinematic_spec.optical_trim must be a string (library) or boolean")
        for k in ("selector", "url"):
            if k in sc and (not isinstance(sc[k], str) or not sc[k].strip()):
                rep.err(f"{w}.{k} must be a non-empty string")

    @staticmethod
    def worker_qa_guard(rep, w, sc):
        if "role" in sc and sc["role"] != "ADVERSARIAL_BOUNTY":
            rep.err(f"{w}.role must be 'ADVERSARIAL_BOUNTY'")
        _str_list(rep, f"{w}.forbidden_calls", sc.get("forbidden_calls"))
        mg = sc.get("mutation_gate")
        if not isinstance(mg, dict):
            rep.err(f"{w}.mutation_gate is required (min_kill_score, target_diff_only)")
            return
        for k in mg:
            if k not in MUTATION_KEYS:
                rep.err(f"{w}.mutation_gate: unknown key {k!r}")
        _num_range(rep, f"{w}.mutation_gate.min_kill_score", mg.get("min_kill_score"), 0, 1, lo_open=True)
        if "target_diff_only" in mg and not isinstance(mg["target_diff_only"], bool):
            rep.err(f"{w}.mutation_gate.target_diff_only must be true or false")
        if _is_num(mg.get("min_kill_score")) and mg["min_kill_score"] < 0.5:
            rep.warn(f"{w}.mutation_gate.min_kill_score {mg['min_kill_score']} is below 0.5: weak tests would pass")

    @staticmethod
    def worker_devops(rep, w, sc):
        return None

    @staticmethod
    def worker_security(rep, w, sc):
        # same obligations as the backend scope: declared invariants (e.g. "INV-07: no user input reaches a shell") and forbidden calls
        _Role.worker_backend(rep, w, sc)


def _validate_coverage(rep: Report, touches: List[str], scope_paths: Dict[str, List[str]], scopes: dict) -> None:
    """Every lease entry needs exactly one writer (overlaps between writers are rejected elsewhere); a write scope
    that overlaps no lease entry is suspicious (the lease would not protect it)."""
    for t in touches:
        try:
            normalize_rel_path(t)
        except PathError:
            continue
        if not any(covers(ap, t) for aps in scope_paths.values() for ap in aps):
            rep.err(f"touch {t!r} is not inside any worker's allowed_paths: nobody may write it")
    for name, aps in scope_paths.items():
        if name == "worker_qa_guard":
            continue  # test paths are usually leased separately by the QA bounty round
        for ap in aps:
            if not any(covers(ap, t) or patterns_overlap(ap, t) for t in touches):
                rep.warn(f"worker_scopes.{name}.allowed_paths {ap!r} overlaps no entry of ticket_meta.touches (no lease will be taken for it)")
    if scope_paths and set(scope_paths) == {"worker_qa_guard"}:
        rep.warn("ticket has only worker_qa_guard: it writes tests but no production code")


# ---------------------------------------------------------------------------------------------------------------
# Scope checks (used by the orchestrator / verifier)
# ---------------------------------------------------------------------------------------------------------------


def scope_for(doc: Mapping, worker_id: str) -> Optional[str]:
    """Scope key for a worker id like 'AG3' (case-insensitive), or None when the ticket gives that worker nothing."""
    key = WORKER_SCOPE.get(str(worker_id).upper())
    return key if key and key in doc.get("worker_scopes", {}) else None


def allowed_paths(doc: Mapping, scope: str) -> List[str]:
    return list(doc["worker_scopes"][scope].get("allowed_paths", []))


def check_scope(doc: Mapping, scope: str, changed: Iterable[str]) -> List[str]:
    """Violations (strings) for files `changed` by the worker owning `scope`. Empty list = inside the contract.

    Rules: every changed file must match the scope's allowed_paths; nobody may change another worker's write scope,
    protected locations, or - for non-QA scopes - the sealed vault."""
    own = allowed_paths(doc, scope)
    others = [p for n, s in doc["worker_scopes"].items() if n != scope for p in s.get("allowed_paths", [])]
    out = []
    for f in changed:
        try:
            normalize_rel_path(f)
        except PathError as e:
            out.append(f"{f!r}: {e}")
            continue
        if matches_any(f, NEVER_TOUCH):
            out.append(f"{f}: protected location")
        elif not matches_any(f, own):
            who = "another worker's scope" if matches_any(f, others) else "outside the contract"
            out.append(f"{f}: {who}")
    return out


def forbidden_calls(doc: Mapping, scope: str) -> List[str]:
    return list(doc["worker_scopes"].get(scope, {}).get("forbidden_calls", []))


def symbol_locks(doc: Mapping) -> Dict[str, str]:
    """{symbol: sha256 lock} for every shared symbol."""
    return {s["symbol"]: scip.signature_lock(s["signature"]) for s in (doc.get("shared_contracts") or {}).get("scip_symbols", [])}


def lock_symbols(doc: dict) -> dict:
    """Write each symbol's `lock` field (idempotent). Call before sign()."""
    for s in (doc.get("shared_contracts") or {}).get("scip_symbols", []):
        s["lock"] = scip.signature_lock(s["signature"])
    return doc


def check_symbol_drift(doc: Mapping, current: Mapping[str, str]) -> List[str]:
    """Compare `current` {symbol: signature text found in the code} against the locks. Returns problems."""
    out = []
    for sym, lock in symbol_locks(doc).items():
        if sym not in current:
            out.append(f"{sym}: declaration not found in the code")
        elif scip.signature_lock(current[sym]) != lock:
            out.append(f"{sym}: signature drifted from the locked contract")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Flat Planner 3.0 tickets (so tools/ticketc.py keeps working)
# ---------------------------------------------------------------------------------------------------------------

DEFAULT_STOP_IF = ["a required file is outside allowed_paths", "an acceptance command fails three times in a row", "the contract looks wrong or contradictory"]


def to_flat(doc: Mapping, scope: str, *, frozen: Optional[Mapping[str, str]] = None, acceptance: Optional[Sequence[str]] = None) -> dict:
    """Planner 3.0 flat ticket for one worker scope. `protected_tests` = the QA scope's paths + meta.protected_tests."""
    meta, sc = doc["ticket_meta"], doc["worker_scopes"][scope]
    others = [p for n, s in doc["worker_scopes"].items() if n != scope for p in s.get("allowed_paths", [])]
    protected = list(meta.get("protected_tests", []))
    if scope != "worker_qa_guard":
        protected += list(doc["worker_scopes"].get("worker_qa_guard", {}).get("allowed_paths", []))
    acc = list(acceptance or sc.get("acceptance") or meta.get("acceptance") or [f"python verifier.py run --ticket {meta['id']} --scope {scope}"])
    return {
        "id": f"{meta['id']}:{SCOPE_WORKER[scope]}",
        "goal": meta.get("goal") or f"Implement {SCOPE_WORKER[scope]} scope of {meta['id']} exactly as the contract states",
        "frozen": dict(frozen or meta.get("frozen") or {}),
        "allowed_paths": list(sc["allowed_paths"]),
        "forbidden_paths": sorted(set(others + [p for p in NEVER_TOUCH])),
        "protected_tests": protected,
        "acceptance": acc,
        "stop_if": list(meta.get("stop_if") or DEFAULT_STOP_IF),
    }


def flat_to_yaml(flat: Mapping) -> str:
    """Emit a flat ticket in the dialect tools/ticketc.py parses (scalars, block lists, one-level map)."""
    def q(s: str) -> str:
        # tools/ticketc.py strips the outer quotes but does not unescape, so never emit backslash escapes:
        # pick the quote style that does not occur in the text (a text with both kinds loses its double quotes).
        s = " ".join(str(s).split("\n"))
        if '"' not in s:
            return f'"{s}"'
        if "'" not in s:
            return f"'{s}'"
        return '"' + s.replace('"', "'") + '"'

    lines = [f"id: {q(flat['id'])}", f"goal: {q(flat['goal'])}"]
    fr = flat.get("frozen") or {}
    lines.append("frozen:" if fr else "frozen: {}")
    for k, v in fr.items():
        lines.append(f"  {q(k)}: {q(v)}")
    for f in ("allowed_paths", "forbidden_paths", "protected_tests", "acceptance", "stop_if"):
        vals = flat.get(f) or []
        if not vals:
            lines.append(f"{f}: []")
            continue
        lines.append(f"{f}:")
        lines.extend(f"  - {q(v)}" for v in vals)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# Files and blackboard
# ---------------------------------------------------------------------------------------------------------------


def load_ticket(path) -> dict:
    return miniyaml.load_file(path)


def save_ticket(path, doc: Mapping) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(p), miniyaml.dumps(doc))


def ticket_sha(doc: Mapping) -> str:
    """Content hash used on the blackboard (signature-independent, so re-signing does not change the identity)."""
    return hashlib.sha256(canonical_bytes(doc)).hexdigest()


def register_contract(board, doc: Mapping, *, by: str = "AG1", key: Optional[bytes] = None) -> dict:
    """AG1 publishes the signed contract: PLANNED -> CONTRACTED and a `contract_signed` event carrying its hash.
    An `hmac-sha256:` signature is verified with `key`, or with the key in PLANNER_LAWGIVER_KEY when none is passed."""
    rep = validate(doc, check_signature=True, key=key if key is not None else key_from_env())
    rep.raise_if_bad()
    meta = doc["ticket_meta"]
    sha = ticket_sha(doc)
    cur = board.get_ticket(meta["id"])
    if cur is None:
        board.set_ticket(meta["id"], "PLANNED", owner=by, epoch=meta["epoch"], note="planned")
        cur = board.get_ticket(meta["id"])
    if cur["state"] not in ("PLANNED", "CONTRACTED", "ESCALATED", "QUARANTINED"):
        raise ContractError(f"{meta['id']} is already {cur['state']}; a signed contract cannot be replaced after sealing (quarantine or escalate it first)")
    row = board.set_ticket(meta["id"], "CONTRACTED", owner=by, epoch=meta["epoch"], sha=sha, note="contract signed")
    board.post(by, meta["id"], "contract_signed", ref=meta["id"], sha=sha, summary=f"epoch {meta['epoch']}, {len(meta['touches'])} touches")
    return row


def verify_against_board(board, doc: Mapping) -> tuple:
    """(ok, reason): the ticket file must hash to what AG1 registered, and the epoch must match."""
    meta = doc.get("ticket_meta") or {}
    row = board.get_ticket(meta.get("id", ""))
    if row is None:
        return False, "ticket is not registered on the blackboard"
    if row["state"] == "PLANNED":
        return False, "ticket has no signed contract yet (state PLANNED)"
    want = row["sha"]
    if row["state"] not in ("CONTRACTED",):
        # past CONTRACTED the row's sha is reused for other fingerprints (the orchestrator stores the DONE change-set hash there),
        # so the root of trust is the hash AG1 posted when it signed: the newest `contract_signed` event
        signed = [e for e in board.events_for(meta["id"]) if e["kind"] == "contract_signed" and e.get("sha")]
        if signed:
            want = signed[-1]["sha"]
    if want != ticket_sha(doc):
        return False, "ticket file differs from the contract AG1 signed (edited after signing?)"
    if row["epoch"] and row["epoch"] != meta.get("epoch"):
        return False, f"epoch mismatch: board has {row['epoch']}, ticket says {meta.get('epoch')}"
    return True, ""
