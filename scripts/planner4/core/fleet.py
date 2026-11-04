"""fleet.py - the 8-worker bicameral fleet and the MASP (Multi-Agent Shared-Port) isolation rules.

Spec section 4. Two "architect" workers form the bicameral pair that nobody else may impersonate:

    AG1 The Lawgiver   writes CONTRACTS (shared types, stubs, ticket files) and signs them. Never writes the tests
                       and never writes implementation.
    AG6 The Inquisitor writes the ACCEPTANCE TESTS and seals them in a vault the implementers cannot read. Never
                       writes contracts and never writes implementation.

Six specialists do the work inside write scopes granted by the signed ticket:

    AG2 DB (worker_db)  AG3 UI (worker_ui)  AG4 QA bounty (worker_qa_guard)  AG5 API (worker_backend)
    AG7 DevOps (worker_devops, extension: Docker / seed data / runner scripts)
    AG8 The Arbiter (worker_security, extension): AppSec / concurrency hardening, QA-bounty arbiter and the escalation
        solver. AG8 is an ACTIVE worker with its own ports (3700-3799 / 8700-8799). When invoked with `standby_for="AGx"`
        it takes over the scope of a failed worker (and that worker's ports) for the rest of the ticket.

Argon quota rotation ("chameleon" workers). The premium tier is a scarce quota, so exactly one architect runs on it by
default - the LEAD (AG1 unless `--lead 6|8` rotates it). `effective_tier()` is the single place that decides the tier of
a run: the lead is premium, the other architects are standard, and a chameleon worker (AG6, AG8) is promoted to premium
when it is summoned to resolve an escalation. Tickets carry the tier, never a model name.

MASP port isolation
-------------------
Every worker owns one 100-port dev range and one 100-port API range. Servers a worker starts MUST bind inside its own
ranges; a worker must never connect to a port owned by another worker (that would be a covert channel around the
blackboard and, in practice, the way parallel agents end up testing against each other's half-finished servers).
This module provides

  * the table (`FLEET`, `PORT_RANGES`) and `port_owner(port)`;
  * `allocate_ports(worker, ticket)`: deterministic per ticket (hash -> offset inside the range), collision-free
    against ports already handed out, optionally verified free by a real bind probe;
  * `worker_env(...)`: the environment a worker process receives (PORT, DEV_PORT, API_PORT, ranges);
  * `scan_text` / `scan_files`: static detection of binds outside the range ("bind" context) and of connections into
    another worker's range ("connect" context). A static scan cannot see computed ports; it catches literals, which
    is what an LLM writes 99% of the time. Dynamic enforcement needs a netns/container (docs/ENGINE_AUDIT.md).

Honest limits: the scan is lexical, port probes are racy by nature (a port free now may be taken a millisecond later;
`allocate_ports` therefore leaves the final bind to the worker's server and reports collisions it observes), and on
Windows Hyper-V/WinNAT reserves dynamic exclusion ranges - a bind inside the table can fail with WSAEACCES; the
probe treats that as "not free" and the allocator moves on inside the same range.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import hashlib
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from core.platform_compat import normalize_rel_path
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.platform_compat import normalize_rel_path

Range = Tuple[int, int]  # inclusive

# role kinds
LAWGIVER, INQUISITOR, WRITER, ADVERSARY, ARBITER, STANDBY = "lawgiver", "inquisitor", "writer", "adversary", "arbiter", "standby"
# STANDBY is kept as a legacy kind name (the 4.0 pre-release fleet had a cold-standby AG8). No worker has it now.
PREMIUM, STANDARD = "premium", "standard"  # model tiers; tickets carry tiers, never model names

# What the bicameral architects may write. Everything else is read-only to them.
LAWGIVER_PATHS = (".planner/tickets/**", ".planner/dag.json", ".planner/symbols.lock.json", "contracts/**", "packages/*/src/**/*.contract.ts",
                  "**/*.d.ts", "**/*.pyi")
INQUISITOR_PATHS = ("tests/acceptance/**", "**/acceptance/**", ".planner/sealed/**")


@dataclass(frozen=True)
class Worker:
    id: str
    title: str
    role: str
    kind: str
    scope: Optional[str]
    dev: Optional[Range]
    api: Optional[Range]
    model_tier: str = STANDARD
    writes_code: bool = True
    duties: Tuple[str, ...] = ()
    chameleon: bool = False  # may be promoted to the premium tier to resolve an escalation

    @property
    def has_ports(self) -> bool:
        return self.dev is not None

    def owns(self, port: int) -> bool:
        return any(r and r[0] <= port <= r[1] for r in (self.dev, self.api))

    def ranges(self) -> List[Range]:
        return [r for r in (self.dev, self.api) if r]


FLEET: Dict[str, Worker] = {w.id: w for w in (
    Worker("AG1", "The Lawgiver", "Master Architect 1 (contracts)", LAWGIVER, None, (3000, 3099), (8000, 8099), PREMIUM, False,
           ("author and sign contract-first tickets", "own shared SCIP symbols and their locks", "publish stubs/interfaces only")),
    Worker("AG6", "The Inquisitor", "Master Architect 2 (sealed oracle)", INQUISITOR, None, (3500, 3599), (8500, 8599), STANDARD, False,
           ("write acceptance tests from the signed contract", "seal them in the vault before any implementation", "never implement"),
           True),
    Worker("AG2", "DB Engineer", "Backend DB Engineer (3NF DDL, ACID invariants)", WRITER, "worker_db", (3100, 3199), (8100, 8199), STANDARD, True,
           ("migrations and schema", "CHECK constraints for the declared invariants", "no floating point money")),
    Worker("AG3", "UI Craftsman", "Frontend UI/UX Craftsman (FSM, optical trim, kinematics)", WRITER, "worker_ui", (3200, 3299), (8200, 8299), STANDARD, True,
           ("state machine UI", "spring motion within the declared damping", "APCA contrast and optical alignment")),
    Worker("AG4", "QA Bounty Hunter", "Adversarial QA Guard (zero-sum bounty fuzzer)", ADVERSARY, "worker_qa_guard", (3300, 3399), (8300, 8399), STANDARD, True,
           ("write property/adversarial tests that break AG2/AG3/AG5", "earn the bounty only by falsifying or by killing mutants")),
    Worker("AG5", "API Engineer", "Backend API & Service (OpenAPI, property-based tests)", WRITER, "worker_backend", (3400, 3499), (8400, 8499), STANDARD, True,
           ("pure domain functions", "API handlers", "declared invariants as properties")),
    Worker("AG7", "DevOps Launcher", "DevOps & Launch Master (Docker, seed data, runner)", WRITER, "worker_devops", (3600, 3699), (8600, 8699), STANDARD, True,
           ("containers and compose files", "seed data", "run scripts bound to the MASP port plan")),
    Worker("AG8", "The Arbiter", "Master Architect 3 (AppSec, Deep Optimizer & Escalation Solver)", ARBITER, "worker_security", (3700, 3799), (8700, 8799), STANDARD, True,
           ("algorithmic & concurrency optimization", "security AST taint analysis & input sanitization", "arbitrate QA bounty disputes", "escalation solver for retry loops >= 2"),
           True),
)}

PORT_RANGES: Dict[str, Dict[str, Optional[Range]]] = {w.id: {"dev": w.dev, "api": w.api} for w in FLEET.values()}
SCOPE_TO_WORKER: Dict[str, str] = {w.scope: w.id for w in FLEET.values() if w.scope}
WRITE_WORKERS = tuple(w.id for w in FLEET.values() if w.scope)
ARCHITECTS = ("AG1", "AG6", "AG8")
LEADS = ARCHITECTS  # the workers that can carry the premium (Argon) quota as lead architect
DEFAULT_LEAD = "AG1"


class FleetError(ValueError):
    pass


def get(worker_id: str) -> Worker:
    w = FLEET.get(str(worker_id).upper())
    if w is None:
        raise FleetError(f"unknown worker {worker_id!r}; fleet is {', '.join(FLEET)}")
    return w


def parse_lead(value) -> str:
    """`1`, `"6"`, `"AG8"`, `"ag1"` -> the id of an architect that may carry the premium quota."""
    raw = str(value).strip().upper()
    raw = raw[2:] if raw.startswith("AG") else raw
    wid = "AG" + raw
    if wid not in LEADS:
        raise FleetError(f"lead must be one of {', '.join(w[2:] for w in LEADS)} (AG1, AG6 or AG8); got {value!r}")
    return wid


def effective_tier(worker_id: str, *, lead: str = DEFAULT_LEAD, escalated: bool = False) -> str:
    """Model tier ('premium' | 'standard') a worker runs on in THIS run.

    * the lead architect (AG1 by default; `--lead 6|8` rotates the quota) is premium;
    * the other architects are standard;
    * a chameleon worker (AG6, AG8) summoned to resolve an escalation (`escalated=True`) is promoted to premium;
    * every specialist is standard.
    The result is a tier name, never a model name: the operator maps tiers to models at run time."""
    w = get(worker_id)
    lead_id = parse_lead(lead)
    if w.id == lead_id:
        return PREMIUM
    if escalated and w.chameleon:
        return PREMIUM
    return STANDARD


def validate_table(fleet: Mapping[str, Worker] = FLEET) -> List[str]:
    """Self-check of the table itself: ranges are well formed, 100 wide, in the expected bands and pairwise disjoint."""
    problems: List[str] = []
    seen: List[Tuple[Range, str, str]] = []
    for w in fleet.values():
        for kind, rng in (("dev", w.dev), ("api", w.api)):
            if rng is None:
                continue
            lo, hi = rng
            if not (1024 <= lo <= hi <= 65535):
                problems.append(f"{w.id}.{kind} {rng} is not a valid unprivileged range")
            if hi - lo + 1 != 100:
                problems.append(f"{w.id}.{kind} {rng} is not 100 ports wide")
            band = (3000, 3999) if kind == "dev" else (8000, 8999)
            if not (band[0] <= lo and hi <= band[1]):
                problems.append(f"{w.id}.{kind} {rng} leaves its band {band}")
            for other, ow, ok in seen:
                if not (hi < other[0] or lo > other[1]):
                    problems.append(f"{w.id}.{kind} {rng} overlaps {ow}.{ok} {other}")
            seen.append((rng, w.id, kind))
        if w.kind == STANDBY and (w.dev or w.api):
            problems.append(f"{w.id} is a cold standby and must not own ports")
        elif w.kind != STANDBY and not w.has_ports:
            problems.append(f"{w.id} is an active worker and must own a dev and an api range")
        m = re.fullmatch(r"AG(\d)", w.id)
        if m and w.has_ports and w.dev is not None and w.api is not None:
            i = int(m.group(1)) - 1
            if w.dev != (3000 + 100 * i, 3099 + 100 * i) or w.api != (8000 + 100 * i, 8099 + 100 * i):
                problems.append(f"{w.id} ranges {w.dev}/{w.api} do not follow the MASP plan "
                                f"{3000 + 100 * i}-{3099 + 100 * i} / {8000 + 100 * i}-{8099 + 100 * i}")
    return problems


def port_owner(port: int) -> Optional[str]:
    for w in FLEET.values():
        if w.owns(int(port)):
            return w.id
    return None


# ----------------------------------------------------------------------------------------------------- allocation
def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Bind probe. Any OSError (in use, access denied by a Windows exclusion range, ...) means 'not free'."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: REUSEADDR would let us bind over a live listener
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _offset(ticket: str, kind: str, size: int) -> int:
    return int.from_bytes(hashlib.sha256(f"{ticket}\0{kind}".encode("utf-8")).digest()[:4], "big") % size


@dataclass
class PortPlan:
    worker: str
    ticket: str
    dev: Optional[int]
    api: Optional[int]
    warnings: List[str] = field(default_factory=list)

    def env(self) -> Dict[str, str]:
        e: Dict[str, str] = {}
        if self.dev is not None:
            e["DEV_PORT"] = str(self.dev)
            e["PORT"] = str(self.dev)
        if self.api is not None:
            e["API_PORT"] = str(self.api)
        return e


def allocate_ports(worker_id: str, ticket: str, *, taken: Iterable[int] = (), probe: bool = False, host: str = "127.0.0.1") -> PortPlan:
    """Deterministic dev+api ports for (worker, ticket). The same pair is returned on every call (so a restarted worker
    finds its server), unless the preferred port is in `taken` (another live ticket of the same worker) or, with
    `probe=True`, is already bound by something else; then the next free port in the SAME range is used."""
    w = get(worker_id)
    if not w.has_ports:
        raise FleetError(f"{w.id} ({w.title}) has no ports")
    used: Set[int] = {int(p) for p in taken}
    plan = PortPlan(w.id, ticket, None, None)
    for kind, rng in (("dev", w.dev), ("api", w.api)):
        lo, hi = rng  # type: ignore[misc]
        size = hi - lo + 1
        start = _offset(ticket, kind, size)
        chosen = None
        for i in range(size):
            cand = lo + (start + i) % size
            if cand in used:
                continue
            if probe and not port_is_free(cand, host):
                plan.warnings.append(f"{kind} port {cand} is busy or reserved; skipped")
                continue
            chosen = cand
            break
        if chosen is None:
            raise FleetError(f"{w.id}: the {kind} range {lo}-{hi} is exhausted")
        used.add(chosen)
        setattr(plan, kind, chosen)
    return plan


def worker_env(worker_id: str, ticket: str, *, plan: Optional[PortPlan] = None, extra: Optional[Mapping[str, str]] = None,
               tier: Optional[str] = None) -> Dict[str, str]:
    """Environment variables handed to a worker process (merged by the executor over the sandbox base env)."""
    w = get(worker_id)
    env = {"PLANNER_WORKER": w.id, "PLANNER_TICKET": ticket, "PLANNER_MODEL_TIER": tier or w.model_tier}
    if w.scope:
        env["PLANNER_SCOPE"] = w.scope
    if w.has_ports:
        p = plan or allocate_ports(w.id, ticket)
        env.update(p.env())
        env["MASP_DEV_RANGE"] = f"{w.dev[0]}-{w.dev[1]}"  # type: ignore[index]
        env["MASP_API_RANGE"] = f"{w.api[0]}-{w.api[1]}"  # type: ignore[index]
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


# ----------------------------------------------------------------------------------------------------- static scan
# BIND context: this code starts something that listens (or tells a tool which port to serve on).
_BIND_PATTERNS = [
    re.compile(r"(?i)\.listen\(\s*(?:port\s*[:=]\s*)?(?P<p>\d{2,5})\b"),
    re.compile(r"(?i)(?:^|[\s;&|(])(?:--port|-p|--listen-port|--dev-port|--api-port)(?:=|\s+)(?P<p>\d{2,5})\b"),
    re.compile(r"(?i)\b(?:PORT|DEV_PORT|API_PORT|SERVER_PORT|APP_PORT)\s*(?:=|:\s)\s*[\"']?(?P<p>\d{2,5})\b"),
    re.compile(r"(?i)\bport[\"']?\s*(?::|=)\s*(?P<p>\d{2,5})\b"),
    re.compile(r"(?i)\b(?:http\.server|serve|uvicorn|gunicorn|flask run|next dev|vite|webpack-dev-server|vite preview|python -m http\.server)\b[^\n]*?\s(?P<p>\d{4,5})\b"),
    re.compile(r"(?i)\bbind\(\s*\(\s*[\"'][^\"']*[\"']\s*,\s*(?P<p>\d{2,5})\s*\)"),
    re.compile(r"(?i)\"(?P<p>\d{2,5}):\d{2,5}\"\s*(?:#.*)?$"),  # docker-compose "3000:3000" host side
    re.compile(r"(?i)\bEXPOSE\s+(?P<p>\d{2,5})\b"),
]
# CONNECT context: host:port in a URL / DSN.
_CONNECT = re.compile(r"(?i)(?:https?|wss?|tcp|postgres(?:ql)?|mysql|redis|mongodb|amqp)://(?:[^/\s:@\"']+@)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|host\.docker\.internal)\s*:\s*(?P<p>\d{2,5})")
_HOSTPORT = re.compile(r"(?i)\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal):(?P<p>\d{2,5})\b")
# Ports that are not a worker's server: shared infrastructure the worker may CONNECT to.
INFRA_PORTS = {22, 25, 53, 80, 110, 143, 443, 465, 587, 993, 995, 1433, 1521, 2181, 3306, 4222, 5000, 5432, 5672, 6379, 9042, 9092, 9200, 11211, 15672, 27017}


@dataclass
class PortFinding:
    severity: str  # violation | warning
    code: str      # BIND_OUTSIDE_RANGE | CONNECT_FOREIGN_RANGE | PORT_FORBIDDEN_FOR_STANDBY | BIND_WELL_KNOWN
    port: int
    line: int
    file: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.file}:{self.line}: {self.detail}"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


_COMMENT_ONLY = re.compile(r"^\s*(?:#|//|\*|/\*|<!--)")


def scan_text(text: str, worker_id: str, *, file: str = "<text>") -> List[PortFinding]:
    w = get(worker_id)
    out: List[PortFinding] = []
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000 or _COMMENT_ONLY.match(line):
            continue
        binds: Set[int] = set()
        for pat in _BIND_PATTERNS:
            for m in pat.finditer(line):
                p = int(m.group("p"))
                if 1 <= p <= 65535 and p not in binds:
                    binds.add(p)
                    out.extend(_judge_bind(w, p, no, file))
        for pat in (_CONNECT, _HOSTPORT):
            for m in pat.finditer(line):
                p = int(m.group("p"))
                if p in binds or not (1 <= p <= 65535):
                    continue
                owner = port_owner(p)
                if owner and owner != w.id:
                    out.append(PortFinding("violation", "CONNECT_FOREIGN_RANGE", p, no, file,
                                           f"{w.id} connects to port {p}, which belongs to {owner} ({FLEET[owner].title})"))
                elif w.kind == STANDBY:
                    out.append(PortFinding("violation", "PORT_FORBIDDEN_FOR_STANDBY", p, no, file, f"{w.id} is a cold standby and may not use ports"))
    # de-duplicate (same code/port/line)
    seen, uniq = set(), []
    for f in out:
        k = (f.code, f.port, f.line, f.file)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def _judge_bind(w: Worker, port: int, line: int, file: str) -> List[PortFinding]:
    if w.kind == STANDBY:
        return [PortFinding("violation", "PORT_FORBIDDEN_FOR_STANDBY", port, line, file, f"{w.id} is a cold standby and may not bind ports")]
    if w.owns(port):
        return []
    owner = port_owner(port)
    if owner is None and port in INFRA_PORTS:
        # `port: 5432` is usually a client config, not a listener; surface it without blocking.
        return [PortFinding("warning", "BIND_WELL_KNOWN", port, line, file,
                            f"{w.id} mentions well-known service port {port} in a bind-like context; confirm it is a client setting")]
    where = f"belongs to {owner}" if owner else "is outside the MASP table"
    return [PortFinding("violation", "BIND_OUTSIDE_RANGE", port, line, file,
                        f"{w.id} binds port {port} which {where}; its ranges are " + ", ".join(f"{a}-{b}" for a, b in w.ranges()))]


TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".yaml", ".yml", ".toml", ".sh", ".ps1", ".bat", ".cmd",
                 ".env", ".md", ".sql", ".html", ".conf", ".ini", ".cfg", ".txt", ".dockerfile", ".go", ".rs", ".java", ".rb"}
TEXT_NAMES = {"Dockerfile", "Makefile", "Procfile", "docker-compose.yml", "compose.yaml"}


def scan_files(root, files: Iterable[str], worker_id: str, *, max_bytes: int = 1_000_000) -> List[PortFinding]:
    base = Path(root)
    out: List[PortFinding] = []
    for rel in files:
        try:
            rel_n = normalize_rel_path(rel)
        except Exception:
            continue
        p = base / rel_n
        if not p.is_file() or p.is_symlink():
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES and p.name not in TEXT_NAMES and not p.name.startswith(".env"):
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.extend(scan_text(text, worker_id, file=rel_n))
    return out


def violations(findings: Iterable[PortFinding]) -> List[PortFinding]:
    return [f for f in findings if f.severity == "violation"]


# ----------------------------------------------------------------------------------------------------- governance
def check_independence(assignment: Mapping[str, object]) -> List[str]:
    """Bicameral separation of powers for one ticket. `assignment` maps a duty to a worker id:
    {'contract': 'AG1', 'seal': 'AG6', 'implement': ['AG2', ...] or 'AG5', 'bounty': 'AG4',
     'arbiter': 'AG8' (default; AG6 when AG8 implements), 'audit': 'AG8' or a list, 'escalation': {'AG8': 'AG2'}}.

    Rules: the contract author is AG1 and the sealer is AG6; AG1 and AG6 never implement; AG4 may not implement the
    code it attacks; nobody may hold two duties; the arbiter is AG8 (or AG6 when AG8 implements) and a worker never
    arbitrates or audits what it implemented; AG8 may implement only its own scope (`worker_security`) or as a
    DECLARED escalation solver (`escalation={'AG8': '<replaced worker>'}`), and then not on a ticket it arbitrates,
    audits or attacks."""
    problems: List[str] = []
    contract = assignment.get("contract", "AG1")
    seal = assignment.get("seal", "AG6")
    impl = assignment.get("implement", [])
    impl_list = [impl] if isinstance(impl, str) else list(impl)  # type: ignore[arg-type]
    bounty = assignment.get("bounty")
    arbiter = assignment.get("arbiter") or ("AG6" if "AG8" in impl_list else "AG8")
    audit_raw = assignment.get("audit") or []
    audit_list = [audit_raw] if isinstance(audit_raw, str) else list(audit_raw)  # type: ignore[arg-type]
    escalation = assignment.get("escalation") or {}
    if contract != "AG1":
        problems.append(f"contracts are authored by AG1 (The Lawgiver), not {contract}")
    if seal != "AG6":
        problems.append(f"the oracle is sealed by AG6 (The Inquisitor), not {seal}")
    if contract == seal:
        problems.append("the contract author and the sealer must be different workers")
    for w in impl_list:
        if w in ("AG1", "AG6") or w in (contract, seal):
            problems.append(f"{w} is an architect and may not implement (separation of powers)")
        elif w in FLEET and FLEET[w].kind == ADVERSARY:
            problems.append(f"{w} is the adversarial QA guard and may not implement the code it attacks")
        elif w not in FLEET:
            problems.append(f"unknown implementer {w!r}")
    if bounty:
        if bounty in impl_list:
            problems.append(f"the bounty hunter {bounty} may not be an implementer of the same ticket")
        if bounty in (contract, seal):
            problems.append(f"the bounty hunter {bounty} may not be the contract author or the sealer")
    if arbiter not in ("AG8", "AG6"):
        problems.append(f"QA bounty disputes are arbitrated by AG8 (The Arbiter) or, when AG8 implements, AG6; not {arbiter}")
    if arbiter in impl_list:
        problems.append(f"{arbiter} may not implement a ticket it arbitrates (it would judge its own work)")
    for a in audit_list:
        if a in impl_list:
            problems.append(f"{a} may not implement a ticket it audits")
    if "AG8" in impl_list:
        if bounty == "AG8":
            problems.append("AG8 may not implement a ticket it attacks (bounty)")
        if escalation and "AG8" not in escalation:
            problems.append("only AG8 can be declared as the escalation solver")
    if escalation:
        for solver, replaced in dict(escalation).items():  # type: ignore[call-overload]
            if solver != "AG8":
                continue
            if "AG8" not in impl_list:
                problems.append("an escalation solver is declared but AG8 is not listed under implement")
            if replaced == "AG8" or replaced not in FLEET or not FLEET[replaced].scope:
                problems.append(f"AG8 cannot replace {replaced!r}: it must be a different worker that owns a write scope")
    if len(set(impl_list)) != len(impl_list):
        problems.append("an implementer is listed twice")
    return problems


def write_paths_for(worker_id: str) -> Tuple[str, ...]:
    """Fixed write globs for the architects (specialists get theirs from the signed ticket)."""
    w = get(worker_id)
    if w.kind == LAWGIVER:
        return LAWGIVER_PATHS
    if w.kind == INQUISITOR:
        return INQUISITOR_PATHS
    return ()


def default_fleet_summary() -> List[dict]:
    return [{"id": w.id, "title": w.title, "kind": w.kind, "scope": w.scope, "dev": w.dev, "api": w.api, "model_tier": w.model_tier,
             "chameleon": w.chameleon, "writes_code": w.writes_code} for w in FLEET.values()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(prog="fleet", description="Show the 8-worker fleet, allocate MASP ports, scan files for port violations")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("table")
    a = sub.add_parser("ports")
    a.add_argument("worker"); a.add_argument("ticket"); a.add_argument("--probe", action="store_true")
    s = sub.add_parser("scan")
    s.add_argument("worker"); s.add_argument("root"); s.add_argument("files", nargs="+")
    ns = ap.parse_args(argv)
    if not ns.cmd:
        ap.print_help()
        return 0
    try:
        if ns.cmd == "table":
            print(json.dumps({"fleet": default_fleet_summary(), "table_problems": validate_table()}, indent=2))
            return 0
        if ns.cmd == "ports":
            plan = allocate_ports(ns.worker, ns.ticket, probe=ns.probe)
            print(json.dumps({"worker": plan.worker, "ticket": plan.ticket, "dev": plan.dev, "api": plan.api, "warnings": plan.warnings}, indent=2))
            return 0
        found = scan_files(ns.root, ns.files, ns.worker)
        print(json.dumps([f.to_dict() for f in found], indent=2))
        return 1 if violations(found) else 0
    except FleetError as exc:
        print(f"fleet: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
