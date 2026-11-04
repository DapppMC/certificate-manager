#!/usr/bin/env python3
"""dag_planner.py - compile a plan into Contract-First ticket YAMLs plus an execution DAG (Planner 4.0, M4).

Input  : a plan file (YAML subset or JSON), see PLAN FORMAT below.
Output : <out>/<ticket-id>.yaml   one signed contract ticket per work item (spec section 6)
         <out>/../dag.json        waves, edges (explicit / touch-conflict / symbol-owner), critical path, ticket hashes
         <out>/../symbols.lock.json   SCIP symbol -> {lock, owner}
         <out>/flat/*.yaml        optional Planner 3.0 flat tickets per worker (for tools/ticketc.py)
         .planner/board.db        optional: every contract is registered (PLANNED -> CONTRACTED)

PLAN FORMAT
    epoch: 14
    defaults:                       # all optional
      kinematic_spec: {spring_damping_zeta: 0.82, target_apca_lc: 75.0, optical_trim: "@capsizecss/core"}
      mutation_gate: {min_kill_score: 0.92, target_diff_only: true}
      forbidden_calls: [jest.mock, vi.mock, sinon.stub]
      package: "@planner/core"      # used to build SCIP symbols from {name, file, kind}
      version: 1.0.0
    items:
      - id: PLT-4092-ESCROW-SETTLEMENT
        goal: Settle an escrow exactly once
        depends_on: []
        touches: [packages/api/src/domain/escrow/settle.pure.ts, packages/db/migrations/20261025_escrow.sql, ...]
        symbols:                    # shared contracts: explicit symbol, or {name, file, kind} to build one
          - {symbol: "scip-typescript npm ...#", signature: "export interface ..."}
          - {name: SettlementCommand, file: packages/api/src/domain/escrow/types.ts, kind: type, signature: "..."}
        scopes:                     # optional per-role details and overrides (worker_db / worker_backend / worker_ui / worker_qa_guard)
          worker_backend: {formal_invariants: {engine: fast-check, properties: ["INV-01: ..."]}}
        roles: {"some/odd/path": worker_backend}     # force a role for a path the heuristics get wrong

How roles are chosen (first match wins): tests -> worker_qa_guard (AG4); .sql/prisma/migrations -> worker_db (AG2);
ui files (.tsx .jsx .vue .svelte .css .html, or .ts/.js under components|ui|views|pages|styles) -> worker_ui (AG3);
Docker/CI/infra files -> worker_devops (AG7); a `security/` directory -> worker_security (AG8); everything else -> worker_backend (AG5).

The DAG has three kinds of edges: the explicit `depends_on`; `touch-conflict` (two items whose touches overlap are
serialised in plan order, because the blackboard would refuse the second lease anyway); `symbol-owner` (an item that
re-uses a symbol declared by another item runs after its owner). Cycles are reported with the cycle itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import contract_ticket as ct  # noqa: E402
from core import miniyaml, scip_symbols as scip  # noqa: E402
from core.platform_compat import PathError, atomic_write_text, normalize_rel_path, patterns_overlap  # noqa: E402

DEFAULT_KINEMATIC = {"spring_damping_zeta": 0.82, "target_apca_lc": 75.0, "optical_trim": "@capsizecss/core"}
DEFAULT_MUTATION = {"min_kill_score": 0.92, "target_diff_only": True}
DEFAULT_FORBIDDEN_CALLS = ["jest.mock", "vi.mock", "sinon.stub", "unittest.mock", "mock.patch", "monkeypatch.setattr"]
DEFAULT_FSM = "@zag-js/core"

UI_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html", ".astro"}
UI_DIRS = {"components", "ui", "views", "pages", "styles", "screens"}
SCRIPT_EXT = {".ts", ".js", ".mjs", ".cjs"}
DB_EXT = {".sql", ".prisma"}
INFRA_NAMES = re.compile(r"(^|/)(dockerfile[^/]*|docker-compose[^/]*|makefile|jenkinsfile|\.github/.+|[^/]+\.tf|[^/]+\.tfvars|helm/.+|k8s/.+|deploy/.+|infra/.+)$", re.I)


class PlanError(ValueError):
    """The plan cannot be compiled (bad item, unknown dependency, cycle, conflicting contract)."""


def classify_path(path: str) -> str:
    """Scope key for a touched path (see module docstring)."""
    p = path.replace("\\", "/")
    low = p.lower()
    if ct.TEST_HINT.search(p):
        return "worker_qa_guard"
    ext = os.path.splitext(low)[1]
    segs = set(low.split("/")[:-1])
    if ext in DB_EXT or ("migrations" in segs and ext in (".py", ".ts", ".js", ".sql")):
        return "worker_db"
    if ext in UI_EXT or (ext in SCRIPT_EXT and segs & UI_DIRS):
        return "worker_ui"
    if "security" in segs:
        return "worker_security"
    if INFRA_NAMES.search(low):
        return "worker_devops"
    return "worker_backend"


# ---------------------------------------------------------------------------------------------------------------
# Plan loading and item validation
# ---------------------------------------------------------------------------------------------------------------


def load_plan(path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except ValueError as e:
            raise PlanError(f"{path}: invalid JSON: {e}") from e
    try:
        return miniyaml.loads(text)
    except miniyaml.YamlError as e:
        raise PlanError(f"{path}: {e}") from e


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


@dataclass
class CompiledPlan:
    tickets: Dict[str, dict] = field(default_factory=dict)        # id -> signed contract doc (plan order)
    dag: dict = field(default_factory=dict)
    symbols: Dict[str, dict] = field(default_factory=dict)        # symbol -> {lock, owner}
    warnings: List[str] = field(default_factory=list)


def _item_symbols(item: dict, defaults: dict, where: str) -> List[dict]:
    out = []
    for i, s in enumerate(item.get("symbols") or []):
        w = f"{where}.symbols[{i}]"
        if not isinstance(s, dict):
            raise PlanError(f"{w} must be a mapping")
        sig = s.get("signature")
        if not isinstance(sig, str) or not sig.strip():
            raise PlanError(f"{w}.signature is required")
        if s.get("symbol"):
            sym = s["symbol"]
        elif s.get("name") and s.get("file"):
            pkg, ver = defaults.get("package"), defaults.get("version")
            if not pkg or ver is None:
                raise PlanError(f"{w}: building a symbol from name/file needs defaults.package and defaults.version")
            try:
                build = scip.python_symbol if str(s["file"]).lower().endswith((".py", ".pyi")) else scip.typescript_symbol
                sym = build(str(pkg), str(ver), s["file"], s["name"], s.get("kind", "type"))
            except scip.SymbolError as e:
                raise PlanError(f"{w}: {e}") from e
        else:
            raise PlanError(f"{w} needs either `symbol` or `name` + `file`")
        entry = {"symbol": sym, "signature": sig}
        if s.get("owner"):
            entry["owner"] = str(s["owner"])
        out.append(entry)
    return out


def _build_scopes(item: dict, defaults: dict, warnings: List[str], where: str) -> dict:
    touches = item["touches"]
    roles = item.get("roles") or {}
    by_scope: Dict[str, List[str]] = {}
    for t in touches:
        scope = roles.get(t) or classify_path(t)
        if scope not in ct.SCOPE_WORKER:
            raise PlanError(f"{where}.roles[{t!r}]: unknown scope {scope!r}")
        by_scope.setdefault(scope, []).append(t)
    overrides = item.get("scopes") or {}
    for k in overrides:
        if k not in ct.SCOPE_WORKER:
            raise PlanError(f"{where}.scopes: unknown scope {k!r}")
    scopes: Dict[str, dict] = {}
    order = ["worker_db", "worker_backend", "worker_ui", "worker_devops", "worker_security", "worker_qa_guard"]
    production = [s for s in by_scope if s in ("worker_db", "worker_backend", "worker_ui", "worker_security")]
    for name in order:
        paths = list(by_scope.get(name, []))
        ov = overrides.get(name) or {}
        if name == "worker_qa_guard" and not paths and not ov.get("allowed_paths"):
            if not production or "worker_qa_guard" in (item.get("skip_scopes") or []):
                continue
            ov = dict(ov)
            ov["allowed_paths"] = [f"tests/{_slug(item['id'])}/**"]
            warnings.append(f"{where}: no test paths given - defaulting AG4's write scope to tests/{_slug(item['id'])}/**")
        if not paths and not ov.get("allowed_paths"):
            continue
        sc: dict = {"worker_id": ct.SCOPE_WORKER[name]}
        if name == "worker_qa_guard":
            sc["role"] = "ADVERSARIAL_BOUNTY"
        sc["allowed_paths"] = list(ov.get("allowed_paths") or paths)
        if name == "worker_db":
            sc["forbidden_ast"] = list(ov.get("forbidden_ast", defaults.get("forbidden_ast", [])))
            if ov.get("formal_invariants"):
                sc["formal_invariants"] = ov["formal_invariants"]
        elif name == "worker_security":
            if ov.get("formal_invariants"):
                sc["formal_invariants"] = ov["formal_invariants"]
            if ov.get("forbidden_calls"):
                sc["forbidden_calls"] = ov["forbidden_calls"]
        elif name == "worker_backend":
            if ov.get("formal_invariants"):
                sc["formal_invariants"] = ov["formal_invariants"]
            else:
                warnings.append(f"{where}: worker_backend has no formal_invariants - AG5 has nothing to prove")
            if ov.get("forbidden_calls"):
                sc["forbidden_calls"] = ov["forbidden_calls"]
        elif name == "worker_ui":
            sc["mandatory_fsm"] = ov.get("mandatory_fsm", defaults.get("mandatory_fsm", DEFAULT_FSM))
            ks = dict(defaults.get("kinematic_spec") or DEFAULT_KINEMATIC)
            ks.update(ov.get("kinematic_spec") or {})
            sc["kinematic_spec"] = ks
            for k in ("selector", "url"):
                if ov.get(k):
                    sc[k] = ov[k]
            if not ov.get("kinematic_spec") and not defaults.get("kinematic_spec"):
                warnings.append(f"{where}: worker_ui uses the spec's example kinematic_spec; set defaults.kinematic_spec or scopes.worker_ui.kinematic_spec for this product")
        elif name == "worker_qa_guard":
            sc["forbidden_calls"] = list(ov.get("forbidden_calls", defaults.get("forbidden_calls", DEFAULT_FORBIDDEN_CALLS)))
            mg = dict(defaults.get("mutation_gate") or DEFAULT_MUTATION)
            mg.update(ov.get("mutation_gate") or {})
            sc["mutation_gate"] = mg
        if ov.get("acceptance"):
            sc["acceptance"] = list(ov["acceptance"])
        scopes[name] = sc
    return scopes


def _validate_items(plan: dict) -> Tuple[List[dict], dict]:
    if not isinstance(plan, dict):
        raise PlanError("plan must be a mapping with `items`")
    for k in plan:
        if k not in ("project", "epoch", "defaults", "items"):
            raise PlanError(f"unknown plan key {k!r}")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise PlanError("plan.items must be a non-empty list")
    defaults = plan.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise PlanError("plan.defaults must be a mapping")
    seen = set()
    for i, it in enumerate(items):
        w = f"items[{i}]"
        if not isinstance(it, dict):
            raise PlanError(f"{w} must be a mapping")
        for k in it:
            if k not in ("id", "goal", "depends_on", "touches", "symbols", "scopes", "roles", "epoch", "acceptance", "stop_if", "skip_scopes", "protected_tests"):
                raise PlanError(f"{w}: unknown key {k!r}")
        tid = it.get("id")
        if not isinstance(tid, str) or not ct.TICKET_ID_RE.match(tid):
            raise PlanError(f"{w}.id {tid!r} must match {ct.TICKET_ID_RE.pattern}")
        if tid in seen:
            raise PlanError(f"duplicate item id {tid}")
        seen.add(tid)
        if not isinstance(it.get("goal"), str) or not it["goal"].strip():
            raise PlanError(f"{w} ({tid}): goal is required")
        t = it.get("touches")
        if not isinstance(t, list) or not t or any(not isinstance(x, str) for x in t):
            raise PlanError(f"{w} ({tid}): touches must be a non-empty list of paths")
        dep = it.get("depends_on") or []
        if not isinstance(dep, list) or any(not isinstance(x, str) for x in dep):
            raise PlanError(f"{w} ({tid}): depends_on must be a list of ids")
    for it in items:
        for d in it.get("depends_on") or []:
            if d not in seen:
                raise PlanError(f"{it['id']}: depends_on unknown ticket {d!r}")
            if d == it["id"]:
                raise PlanError(f"{it['id']}: depends on itself")
    return items, defaults


# ---------------------------------------------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------------------------------------------


def find_cycle(nodes: List[str], preds: Dict[str, set]) -> Optional[List[str]]:
    """A cycle as [a, b, ..., a] or None. Iterative DFS (no recursion limit)."""
    state: Dict[str, int] = {}
    for start in nodes:
        if state.get(start):
            continue
        stack = [(start, iter(sorted(preds.get(start, ()))))]
        path = [start]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
                continue
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            if not state.get(nxt):
                state[nxt] = 1
                path.append(nxt)
                stack.append((nxt, iter(sorted(preds.get(nxt, ())))))
    return None


def build_dag(order: List[str], explicit: Dict[str, set], touches: Dict[str, List[str]], owners: Dict[str, str]) -> dict:
    preds: Dict[str, set] = {i: set(explicit.get(i, ())) for i in order}
    edges = [{"from": d, "to": i, "kind": "depends_on"} for i in order for d in sorted(explicit.get(i, ()))]
    cyc = find_cycle(order, preds)
    if cyc:
        raise PlanError("dependency cycle: " + " -> ".join(cyc))

    def reaches(a: str, b: str) -> bool:  # is a an ancestor of b?
        seen, todo = set(), [b]
        while todo:
            n = todo.pop()
            for p in preds.get(n, ()):
                if p == a:
                    return True
                if p not in seen:
                    seen.add(p)
                    todo.append(p)
        return False

    for sym, (owner, users) in owners.items():
        for u in users:
            if u != owner and owner not in preds[u] and not reaches(owner, u):
                preds[u].add(owner)
                edges.append({"from": owner, "to": u, "kind": "symbol-owner", "symbol": sym})
    norm = {i: [normalize_rel_path(t) for t in touches[i]] for i in order}
    for ai in range(len(order)):
        for bi in range(ai + 1, len(order)):
            a, b = order[ai], order[bi]
            if reaches(a, b) or reaches(b, a):
                continue
            hit = next(((x, y) for x in norm[a] for y in norm[b] if patterns_overlap(x, y)), None)
            if hit:
                preds[b].add(a)
                edges.append({"from": a, "to": b, "kind": "touch-conflict", "paths": [hit[0], hit[1]]})
    level: Dict[str, int] = {}
    remaining = set(order)
    waves: List[List[str]] = []
    while remaining:
        ready = [i for i in order if i in remaining and not (preds[i] & remaining)]
        if not ready:  # pragma: no cover - cycles were rejected above
            raise PlanError("internal error: no ready ticket while some remain")
        for i in ready:
            level[i] = len(waves)
        waves.append(ready)
        remaining -= set(ready)
    depth: Dict[str, int] = {}
    parent: Dict[str, Optional[str]] = {}
    for w in waves:
        for i in w:
            best = max(preds[i], key=lambda p: depth[p], default=None)
            depth[i] = (depth[best] + 1) if best else 1
            parent[i] = best
    end = max(order, key=lambda i: depth[i])
    crit, cur = [], end
    while cur:
        crit.append(cur)
        cur = parent[cur]
    return {"waves": waves, "edges": edges, "critical_path": list(reversed(crit)),
            "predecessors": {i: sorted(preds[i]) for i in order}}


# ---------------------------------------------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------------------------------------------


def compile_plan(plan: dict, *, key: Optional[bytes] = None, epoch: Optional[int] = None) -> CompiledPlan:
    items, defaults = _validate_items(plan)
    ep_default = epoch or plan.get("epoch") or 1
    cp = CompiledPlan()
    order = [it["id"] for it in items]
    sym_sig: Dict[str, str] = {}
    sym_owner: Dict[str, str] = {}
    sym_users: Dict[str, set] = {}
    docs: Dict[str, dict] = {}
    for it in items:
        tid = it["id"]
        w = f"item {tid}"
        try:
            for t in it["touches"]:
                normalize_rel_path(t)
        except PathError as e:
            raise PlanError(f"{w}: touches: {e}") from e
        symbols = _item_symbols(it, defaults, w)
        for s in symbols:
            sym, lock = s["symbol"], scip.signature_lock(s["signature"])
            if sym in sym_sig and sym_sig[sym] != lock:
                raise PlanError(f"{w}: symbol {sym!r} is declared with a different signature than in {sym_owner[sym]}")
            if sym not in sym_sig:
                sym_sig[sym] = lock
                sym_owner[sym] = s.get("owner") or tid
            elif s.get("owner") and s["owner"] != sym_owner[sym]:
                raise PlanError(f"{w}: symbol {sym!r} owner {s['owner']} disagrees with {sym_owner[sym]}")
            sym_users.setdefault(sym, set()).add(tid)
        meta = {"id": tid, "epoch": int(it.get("epoch") or ep_default), "goal": it["goal"].strip(), "touches": list(it["touches"])}
        if it.get("depends_on"):
            meta["depends_on"] = list(it["depends_on"])
        for k in ("acceptance", "stop_if", "protected_tests"):
            if it.get(k):
                meta[k] = list(it[k])
        scopes = _build_scopes(it, defaults, cp.warnings, w)
        if not scopes:
            raise PlanError(f"{w}: no worker scope could be derived")
        doc: dict = {"ticket_meta": meta}
        if symbols:
            doc["shared_contracts"] = {"scip_symbols": [{k: v for k, v in s.items() if k != "owner"} for s in symbols]}
        doc["worker_scopes"] = scopes
        ct.lock_symbols(doc)
        docs[tid] = doc
    for sym, owner in sym_owner.items():
        if owner not in docs:
            raise PlanError(f"symbol {sym!r}: owner {owner!r} is not a ticket in this plan")
        sym_users[sym].add(owner)
    explicit = {it["id"]: set(it.get("depends_on") or []) for it in items}
    owners = {sym: (sym_owner[sym], sorted(sym_users[sym])) for sym in sym_owner}
    dag = build_dag(order, explicit, {i: docs[i]["ticket_meta"]["touches"] for i in order}, owners)
    # a ticket that depends on another must also carry that dependency in its own meta
    for tid, ps in dag["predecessors"].items():
        if ps:
            docs[tid]["ticket_meta"]["depends_on"] = ps
    for tid in order:
        ct.sign(docs[tid], key)
        rep = ct.validate(docs[tid], check_signature=True, key=key)
        if not rep.ok:
            raise PlanError(f"item {tid} produced an invalid contract: " + "; ".join(rep.errors))
        cp.warnings.extend(f"{tid}: {w}" for w in rep.warnings)
        cp.tickets[tid] = docs[tid]
    dag["ticket_sha"] = {tid: ct.ticket_sha(d) for tid, d in cp.tickets.items()}
    dag["epoch"] = int(ep_default)
    dag["project"] = plan.get("project", "")
    dag["signature_kind"] = "hmac-sha256" if key else "sha256"
    cp.dag = dag
    cp.symbols = {s: {"lock": sym_sig[s], "owner": sym_owner[s]} for s in sorted(sym_sig)}
    return cp


def write_plan(cp: CompiledPlan, out_dir, *, dag_path=None, flat: bool = False) -> List[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for tid, doc in cp.tickets.items():
        p = out / f"{tid}.yaml"
        ct.save_ticket(p, doc)
        written.append(str(p))
        if flat:
            fdir = out / "flat"
            fdir.mkdir(exist_ok=True)
            for scope in doc["worker_scopes"]:
                fp = fdir / f"{tid}.{ct.SCOPE_WORKER[scope]}.yaml"
                atomic_write_text(str(fp), ct.flat_to_yaml(ct.to_flat(doc, scope)))
                written.append(str(fp))
    dag_file = Path(dag_path) if dag_path else out.parent / "dag.json"
    atomic_write_text(str(dag_file), json.dumps(cp.dag, indent=2, ensure_ascii=False) + "\n")
    lock_file = dag_file.parent / "symbols.lock.json"
    atomic_write_text(str(lock_file), json.dumps(cp.symbols, indent=2, ensure_ascii=False) + "\n")
    return written + [str(dag_file), str(lock_file)]


def ready_tickets(dag: dict, done: set) -> List[str]:
    """Ticket ids whose predecessors are all done (and which are not done themselves), in plan order."""
    preds = dag["predecessors"]
    return [t for w in dag["waves"] for t in w if t not in done and set(preds.get(t, ())) <= set(done)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compile a plan into Contract-First ticket YAMLs and an execution DAG")
    ap.add_argument("plan")
    ap.add_argument("--out", default=".planner/tickets")
    ap.add_argument("--dag", help="where to write dag.json (default: next to --out)")
    ap.add_argument("--flat", action="store_true", help="also write Planner 3.0 flat tickets per worker")
    ap.add_argument("--board", help="register every contract on this blackboard (e.g. .planner/board.db)")
    ap.add_argument("--hmac-env", default="", help="env var holding the Lawgiver HMAC key (never put the key on the command line)")
    ap.add_argument("--epoch", type=int)
    ap.add_argument("--check", action="store_true", help="compile and validate only; write nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    key = ct.key_from_env(a.hmac_env) if a.hmac_env else None
    if a.hmac_env and not key:
        print(f"FAIL: environment variable {a.hmac_env} is not set", file=sys.stderr)
        return 2
    try:
        cp = compile_plan(load_plan(a.plan), key=key, epoch=a.epoch)
    except (PlanError, OSError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    files: List[str] = []
    if not a.check:
        files = write_plan(cp, a.out, dag_path=a.dag, flat=a.flat)
        if a.board:
            from core.coordination.blackboard import Board
            with Board(a.board) as b:
                for doc in cp.tickets.values():
                    ct.register_contract(b, doc)
    summary = {"tickets": list(cp.tickets), "waves": cp.dag["waves"], "critical_path": cp.dag["critical_path"],
               "edges": cp.dag["edges"], "warnings": cp.warnings, "files": files}
    if a.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"{len(cp.tickets)} tickets in {len(cp.dag['waves'])} waves; critical path: {' -> '.join(cp.dag['critical_path'])}")
        for i, w in enumerate(cp.dag["waves"], 1):
            print(f"  wave {i}: {', '.join(w)}")
        for e in cp.dag["edges"]:
            if e["kind"] != "depends_on":
                print(f"  derived edge ({e['kind']}): {e['from']} -> {e['to']}")
        for w in cp.warnings:
            print(f"  warning: {w}")
        if a.check:
            print("check only: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
