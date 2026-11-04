"""mutation_gate.py - diff-scoped mutation testing (the "kill score >= 0.92" rule of AG4's zero-sum bounty).

Why an own engine (evidence: docs/ENGINE_AUDIT.md, research_notes verification_tooling.md)
  * mutmut 3.x does not run on native Windows (needs os.fork), mutates only code inside functions, has no diff
    scoping and no threshold/exit code; a gate would have to post-process its mutants/*.meta files.
  * StrykerJS has native `--mutate file:a-b` line ranges, but only for JS/TS (see mutation_adapters.py).
  * The planner's Python tickets need: mutants limited to the lines the worker changed, a hard timeout per mutant
    that kills the whole process tree, and rules that a cheating worker cannot satisfy by emptiness.

Rules (all fail closed)
  1. Baseline first: the test commands must pass on the UNMUTATED sandbox, otherwise the score is meaningless.
  2. Mutants come only from lines inside the supplied changed ranges. A worker cannot hide code from the gate with
     `# pragma: no mutate` (the pragma is ignored AND reported as a violation).
  3. killed = any failing exit (1 failed, 2 interrupted/collection error, 3 internal error) or a TIMEOUT.
     Timeouts count as killed (an infinite loop IS an observable behaviour change; Stryker does the same).
     Exit 5 (no tests collected) is NOT a kill: deleting the tests must not earn points. Exit 4 (usage error) is a
     tooling error and makes the run inconclusive.
  4. score = (killed + timeout) / (killed + timeout + survived + no_tests). Invalid/equivalent-by-construction
     mutants (the mutant does not parse, or unparses to the same text) are excluded and listed.
  5. Zero mutants for a diff that contains executable statements is a FAILURE ("NO_MUTANTS"), never a pass.
     A diff with nothing executable (comments, imports, docstrings) is NOT_APPLICABLE.
  6. Budgeted: `max_mutants` (deterministic stratified sample, seed) and `time_budget`. When the budget ends before
     the sample is finished the verdict is INCONCLUSIVE unless the score is already provably below the threshold.

Mutation operators (classic Offutt/PIT set): comparison and boundary swaps, and/or, arithmetic, augmented
assignment, unary not / minus removal, constants (int+1, bool flip, string garbling), return value -> None, if / while
condition negation, break<->continue, statement deletion (call, assignment, raise).

Honest limits: mutants are produced by `ast.unparse` of a fresh parse (comments/formatting of the *sandbox copy* change,
never of the worker's files); equivalent mutants cannot be detected, which is why the threshold is 0.92 and not 1.0;
the sandbox is a copy, not a container - a hostile test can still touch the machine (see the sealed gate notes).

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import ast
import json
import queue
import random
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

try:
    from core.platform_compat import build_sandbox_env, matches_any, run_process
    from core.verification import sealed_gate, weakening
    from core import worktree as wt
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.platform_compat import build_sandbox_env, matches_any, run_process
    from core.verification import sealed_gate, weakening
    from core import worktree as wt

Range = Tuple[int, int]
DEFAULT_THRESHOLD = 0.92
PRAGMA = re.compile(r"#\s*(?:pragma\s*:\s*no[\s_-]*mutate|nomut(?:ate)?\b|mutation\s*:\s*(?:off|skip|ignore))", re.I)

# statuses of one mutant
KILLED, TIMEOUT, SURVIVED, NO_TESTS, ERROR, INVALID, SKIPPED = "killed", "timeout", "survived", "no_tests", "error", "invalid", "skipped"
# statuses of a run
PASS, FAIL, INCONCLUSIVE, NOT_APPLICABLE = "PASS", "FAIL", "INCONCLUSIVE", "NOT_APPLICABLE"

_CMP = {ast.Lt: (ast.LtE, ast.GtE), ast.LtE: (ast.Lt, ast.Gt), ast.Gt: (ast.GtE, ast.LtE), ast.GtE: (ast.Gt, ast.Lt),
        ast.Eq: (ast.NotEq,), ast.NotEq: (ast.Eq,), ast.Is: (ast.IsNot,), ast.IsNot: (ast.Is,), ast.In: (ast.NotIn,), ast.NotIn: (ast.In,)}
_BIN = {ast.Add: (ast.Sub,), ast.Sub: (ast.Add,), ast.Mult: (ast.Div,), ast.Div: (ast.Mult,), ast.FloorDiv: (ast.Mult,),
        ast.Mod: (ast.Mult,), ast.Pow: (ast.Mult,), ast.LShift: (ast.RShift,), ast.RShift: (ast.LShift,),
        ast.BitAnd: (ast.BitOr,), ast.BitOr: (ast.BitAnd,), ast.BitXor: (ast.BitAnd,)}
_SYM = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=", ast.Is: "is", ast.IsNot: "is not", ast.In: "in",
        ast.NotIn: "not in", ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
        ast.LShift: "<<", ast.RShift: ">>", ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^", ast.And: "and", ast.Or: "or"}
_EXEC_STMTS = (ast.Assign, ast.AugAssign, ast.Return, ast.Raise, ast.If, ast.While, ast.For, ast.AsyncFor, ast.Assert, ast.Delete, ast.With,
               ast.AsyncWith, ast.Try)


# ------------------------------------------------------------------------------------------------ site discovery
@dataclass
class Slot:
    """A node together with where it hangs in the tree, so it can be replaced."""
    node: ast.AST
    parent: Optional[ast.AST]
    field: str
    pos: Optional[int]


def _walk(tree: ast.AST) -> Iterator[Slot]:
    """Deterministic pre-order walk, skipping annotations (mutating a type hint changes nothing at runtime and would
    only create surviving junk mutants)."""
    stack: List[Slot] = [Slot(tree, None, "", None)]
    while stack:
        slot = stack.pop()
        yield slot
        node = slot.node
        children: List[Slot] = []
        for fname, value in ast.iter_fields(node):
            if fname in ("annotation", "returns", "type_comment", "type_params"):
                continue
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        children.append(Slot(item, node, fname, i))
            elif isinstance(value, ast.AST):
                children.append(Slot(value, node, fname, None))
        stack.extend(reversed(children))


def _is_docstring(slot: Slot) -> bool:
    n = slot.node
    return (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))


def _in_ranges(line: int, ranges: Sequence[Range]) -> bool:
    return any(a <= line <= b for a, b in ranges)


@dataclass
class Mutant:
    id: str
    file: str
    line: int
    col: int
    operator: str
    description: str
    site: int          # index of the slot in the deterministic walk
    variant: int       # which replacement of the operator
    status: str = ""
    duration: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "file": self.file, "line": self.line, "operator": self.operator, "description": self.description,
                "status": self.status, "duration": round(self.duration, 3), "detail": self.detail}


def _stringy(n: ast.AST) -> bool:
    return isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)))


def _variants(slot: Slot) -> List[Tuple[str, str]]:
    """[(operator, description)] for the replacements applicable to this node. Index in the list = variant number."""
    n = slot.node
    out: List[Tuple[str, str]] = []
    if isinstance(n, ast.Compare):
        for i, op in enumerate(n.ops):
            for new in _CMP.get(type(op), ()):
                out.append((f"cmp:{i}:{new.__name__}", f"{_SYM[type(op)]} -> {_SYM[new]}"))
    elif isinstance(n, ast.BoolOp):
        new = ast.Or if isinstance(n.op, ast.And) else ast.And
        out.append(("bool", f"{_SYM[type(n.op)]} -> {_SYM[new]}"))
    elif isinstance(n, ast.BinOp) and type(n.op) in _BIN and not (_stringy(n.left) or _stringy(n.right)):
        for new in _BIN[type(n.op)]:
            out.append((f"bin:{new.__name__}", f"{_SYM[type(n.op)]} -> {_SYM[new]}"))
    elif isinstance(n, ast.AugAssign) and type(n.op) in _BIN and not _stringy(n.value):
        for new in _BIN[type(n.op)][:1]:
            out.append((f"aug:{new.__name__}", f"{_SYM[type(n.op)]}= -> {_SYM[new]}="))
    elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.Not, ast.USub)):
        out.append(("unary", "remove " + ("not" if isinstance(n.op, ast.Not) else "unary minus")))
    elif isinstance(n, ast.Constant) and slot.parent is not None and not (isinstance(slot.parent, ast.Expr)):
        v = n.value
        if isinstance(v, bool):
            out.append(("const", f"{v} -> {not v}"))
        elif isinstance(v, int):
            out.append(("const", f"{v} -> {v + 1}"))
        elif isinstance(v, float):
            out.append(("const", f"{v!r} -> {v + 1.0!r}"))
        elif isinstance(v, str) and v and len(v) <= 80:
            out.append(("const", f"{v[:24]!r} -> 'XX..XX'"))
    elif isinstance(n, ast.Return) and n.value is not None and not (isinstance(n.value, ast.Constant) and n.value.value is None):
        out.append(("return", "return value -> None"))
    elif isinstance(n, (ast.If, ast.While)):
        out.append(("cond", f"negate {'if' if isinstance(n, ast.If) else 'while'} condition"))
    elif isinstance(n, ast.IfExp):
        out.append(("cond", "negate conditional expression"))
    elif isinstance(n, ast.Break):
        out.append(("loop", "break -> continue"))
    elif isinstance(n, ast.Continue):
        out.append(("loop", "continue -> break"))
    if isinstance(n, (ast.Assign, ast.AugAssign, ast.Raise)) or (isinstance(n, ast.AnnAssign) and n.value is not None) \
            or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and not _is_docstring(slot)):
        out.append(("sdl", "delete statement"))
    return out


def _apply(tree: ast.Module, slots: List[Slot], m: Mutant) -> bool:
    """Mutate `tree` in place according to `m`. Returns False when the site no longer matches."""
    slot = slots[m.site]
    n = slot.node
    kind = m.operator.split(":")[0]
    try:
        if kind == "cmp":
            _, i, name = m.operator.split(":")
            n.ops[int(i)] = getattr(ast, name)()
        elif kind == "bool":
            n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
        elif kind in ("bin", "aug"):
            n.op = getattr(ast, m.operator.split(":")[1])()
        elif kind == "unary":
            _replace(slot, n.operand)
        elif kind == "const":
            v = n.value
            n.value = (not v) if isinstance(v, bool) else (v + 1 if isinstance(v, (int, float)) else "XX" + v + "XX")
        elif kind == "return":
            n.value = ast.Constant(value=None)
        elif kind == "cond":
            n.test = ast.UnaryOp(op=ast.Not(), operand=n.test)
        elif kind == "loop":
            _replace(slot, ast.Continue() if isinstance(n, ast.Break) else ast.Break())
        elif kind == "sdl":
            _replace(slot, ast.Pass())
        else:
            return False
    except (AttributeError, TypeError, ValueError, IndexError):
        return False
    ast.fix_missing_locations(tree)
    return True


def _replace(slot: Slot, new: ast.AST) -> None:
    if slot.parent is None:
        raise ValueError("cannot replace the root")
    if slot.pos is None:
        setattr(slot.parent, slot.field, new)
    else:
        getattr(slot.parent, slot.field)[slot.pos] = new


def discover(src: str, rel: str, ranges: Sequence[Range]) -> Tuple[List[Mutant], dict]:
    """All mutants of `src` whose site starts on a changed line. Returns (mutants, info)."""
    tree = ast.parse(src)
    info = {"executable_statements": 0, "pragma_lines": [], "docstring_lines": 0}
    lines = src.splitlines()
    for a, b in ranges:
        for ln in range(a, min(b, len(lines)) + 1):
            if PRAGMA.search(lines[ln - 1]):
                info["pragma_lines"].append(ln)
    mutants: List[Mutant] = []
    for idx, slot in enumerate(_walk(tree)):
        n = slot.node
        line = getattr(n, "lineno", None)
        if line is None or not _in_ranges(line, ranges):
            continue
        if isinstance(n, _EXEC_STMTS) or (isinstance(n, ast.Expr) and not _is_docstring(slot)) \
                or (isinstance(n, ast.AnnAssign) and n.value is not None):
            info["executable_statements"] += 1
        for v, (op, desc) in enumerate(_variants(slot)):
            mutants.append(Mutant(f"{rel}:{line}:{getattr(n, 'col_offset', 0)}:{op}", rel, line, getattr(n, "col_offset", 0), op, desc, idx, v))
    return mutants, info


def build_mutant_source(src: str, m: Mutant) -> Optional[str]:
    """Source text of the mutant, or None when it is invalid / equivalent-by-construction."""
    try:
        tree = ast.parse(src)
        base = ast.unparse(ast.parse(src))
        slots = list(_walk(tree))
        if m.site >= len(slots) or not _apply(tree, slots, m):
            return None
        out = ast.unparse(tree)
        compile(out, m.file, "exec")
    except (SyntaxError, ValueError, RecursionError, TypeError):
        return None
    return None if out == base else out


def sample(mutants: List[Mutant], limit: int, seed: int = 0) -> List[Mutant]:
    """Deterministic stratified sample: first one mutant per (file, line), then the rest at random; stable order."""
    if limit <= 0 or len(mutants) <= limit:
        return list(mutants)
    rng = random.Random(seed)
    by_line: Dict[Tuple[str, int], List[Mutant]] = {}
    for m in mutants:
        by_line.setdefault((m.file, m.line), []).append(m)
    chosen: List[Mutant] = []
    keys = sorted(by_line)
    rng.shuffle(keys)
    for k in keys:
        if len(chosen) >= limit:
            break
        chosen.append(rng.choice(by_line[k]))
    if len(chosen) < limit:
        taken = {m.id + str(m.variant) for m in chosen}
        rest = [m for m in mutants if m.id + str(m.variant) not in taken]
        rng.shuffle(rest)
        chosen.extend(rest[:limit - len(chosen)])
    order = {m.id + str(m.variant): i for i, m in enumerate(mutants)}
    return sorted(chosen, key=lambda m: order[m.id + str(m.variant)])


# ------------------------------------------------------------------------------------------------ execution
@dataclass
class MutationConfig:
    threshold: float = DEFAULT_THRESHOLD
    max_mutants: int = 60
    time_budget: float = 600.0
    timeout_factor: float = 3.0
    min_timeout: float = 10.0
    max_timeout: float = 300.0
    workers: int = 1
    seed: int = 0
    python: str = sys.executable
    extra_env: Dict[str, str] = field(default_factory=dict)
    isolate_network: str = "auto"
    skip_globs: Tuple[str, ...] = ("**/migrations/**", "**/node_modules/**")


@dataclass
class MutationReport:
    status: str
    reason: str
    score: Optional[float] = None
    threshold: float = DEFAULT_THRESHOLD
    counts: Dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    planned: int = 0
    executed: int = 0
    sampled: bool = False
    duration: float = 0.0
    baseline_seconds: float = 0.0
    survivors: List[dict] = field(default_factory=list)
    invalid: int = 0
    violations: List[str] = field(default_factory=list)
    files: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["passed"] = self.passed
        return d


def default_commands(*, has_sealed: bool, visible_tests: Sequence[str] = (), python: str = sys.executable) -> List[List[str]]:
    """Templates with {tree}, {sealed}, {python} placeholders. Sealed tests (the oracle) first, the worker's own
    visible tests second; a mutant is killed by either."""
    cmds: List[List[str]] = []
    base = ["-m", "pytest", "-x", "-q", "--tb=no", "-rN", "--disable-warnings", "-p", "no:cacheprovider", "--import-mode=importlib"]
    if has_sealed:
        cmds.append(["{python}", *base, "{sealed}", "-c", "{ini}", "--rootdir", "{sealed}", "--confcutdir", "{sealed}"])
    if visible_tests:
        cmds.append(["{python}", *base, *[f"{{tree}}/{t}" for t in visible_tests], "-c", "{ini}", "--rootdir", "{tree}"])
    return cmds


def _fmt(cmd: Sequence[str], ctx: Dict[str, str]) -> List[str]:
    return [c.format(**ctx) for c in cmd]


def _classify(returncode: Optional[int], timed_out: bool, error: str) -> Tuple[str, str]:
    if error:
        return ERROR, error
    if timed_out:
        return TIMEOUT, "timed out (process tree killed)"
    if returncode == 0:
        return SURVIVED, ""
    if returncode == 5:
        return NO_TESTS, "no tests collected"
    if returncode == 4:
        return ERROR, "runner usage error (exit 4)"
    if returncode in (1, 2, 3):
        return KILLED, {1: "tests failed", 2: "collection error / interrupted", 3: "internal runner error"}[returncode]
    return ERROR, f"unexpected exit code {returncode}"


class _Sandbox:
    def __init__(self, root: Path, tree: Path, sealed: Optional[Path], ini: Path):
        self.root, self.tree, self.sealed, self.ini = root, tree, sealed, ini

    def ctx(self, python: str) -> Dict[str, str]:
        return {"tree": str(self.tree), "sealed": str(self.sealed or ""), "python": python, "ini": str(self.ini)}


def _make_sandbox(base: Path, name: str, worktree: Path, sealed_tests: Optional[Path]) -> _Sandbox:
    root = base / name
    root.mkdir(parents=True)
    sealed_gate.copy_tree_safe(worktree, root / "tree")
    sealed_dst = None
    if sealed_tests is not None:
        sealed_gate.copy_tree_safe(sealed_tests, root / "sealed", ignore_dirs=("__pycache__", ".pytest_cache"))
        sealed_dst = root / "sealed"
    ini = root / "pytest.ini"
    ini.write_text(sealed_gate._PYTEST_INI, encoding="utf-8")
    return _Sandbox(root, root / "tree", sealed_dst, ini)


def _run_commands(sb: _Sandbox, commands: Sequence[Sequence[str]], cfg: MutationConfig, timeout: float, prefix: Sequence[str]):
    """Run the commands in order; stop at the first non-zero. -> (returncode, timed_out, error, seconds)."""
    env = build_sandbox_env({"PYTHONPATH": str(sb.tree), "PYTHONHASHSEED": "0", **cfg.extra_env})
    started = time.monotonic()
    last = 0
    for cmd in commands:
        remaining = max(1.0, timeout - (time.monotonic() - started))
        res = run_process(list(prefix) + _fmt(cmd, sb.ctx(cfg.python)), cwd=str(sb.tree), env=env, timeout=remaining)
        if res.error or res.timed_out or res.returncode != 0:
            return res.returncode, res.timed_out, res.error, time.monotonic() - started
        last = res.returncode
    return last, False, "", time.monotonic() - started


def run_mutation(worktree, changed: Dict[str, Sequence[Range]], *, sealed_tests=None, visible_tests: Sequence[str] = (),
                 commands: Optional[Sequence[Sequence[str]]] = None, config: Optional[MutationConfig] = None,
                 progress: Optional[Callable[[Mutant], None]] = None) -> MutationReport:
    """Run diff-scoped mutation testing of the Python files in `changed` ({relative path: [(start, end)]})."""
    cfg = config or MutationConfig()
    started = time.monotonic()
    root = Path(worktree).resolve()
    rep = MutationReport(NOT_APPLICABLE, "", threshold=cfg.threshold)
    if not (0 < cfg.threshold <= 1):
        return MutationReport(FAIL, f"invalid threshold {cfg.threshold}", threshold=cfg.threshold)

    # 1. candidates -------------------------------------------------------------------------------------------
    sources: Dict[str, str] = {}
    mutants: List[Mutant] = []
    executable = 0
    for rel, ranges in sorted(changed.items()):
        if not rel.endswith(".py") or weakening.is_test_file(rel) or matches_any(rel, cfg.skip_globs) or not ranges:
            continue
        try:
            src = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            rep.violations.append(f"{rel}: unreadable ({exc})")
            continue
        try:
            found, info = discover(src, rel, ranges)
        except SyntaxError as exc:
            rep.violations.append(f"{rel}: UNPARSABLE_SOURCE line {exc.lineno}")
            continue
        sources[rel] = src
        mutants.extend(found)
        executable += info["executable_statements"]
        rep.files[rel] = len(found)
        for ln in info["pragma_lines"]:
            rep.violations.append(f"{rel}:{ln}: mutation-suppressing pragma in changed code (ignored by the gate; remove it)")
    rep.candidates = len(mutants)
    if any("UNPARSABLE_SOURCE" in v for v in rep.violations):
        rep.status, rep.reason = FAIL, "UNPARSABLE_SOURCE: a changed file does not parse"
        return _finish(rep, started)
    if rep.violations:
        rep.status, rep.reason = FAIL, "MUTATION_PRAGMA: " + rep.violations[0]
        return _finish(rep, started)
    if not mutants:
        if executable == 0:
            rep.status, rep.reason = NOT_APPLICABLE, "no executable Python statements in the changed lines"
        else:
            rep.status, rep.reason = FAIL, f"NO_MUTANTS: {executable} executable statements changed but none could be mutated"
        return _finish(rep, started)

    # 2. build sources, drop invalid / equivalent ---------------------------------------------------------------
    built: List[Tuple[Mutant, str]] = []
    for m in mutants:
        text = build_mutant_source(sources[m.file], m)
        if text is None:
            m.status, m.detail = INVALID, "does not parse or equals the original"
            rep.invalid += 1
        else:
            built.append((m, text))
    if not built:
        rep.status, rep.reason = FAIL, "NO_MUTANTS: every candidate mutant was invalid or equivalent"
        return _finish(rep, started)
    chosen = sample([m for m, _ in built], cfg.max_mutants, cfg.seed)
    text_of = {m.id + str(m.variant): t for m, t in built}
    rep.sampled = len(chosen) < len(built)
    rep.planned = len(chosen)

    # 3. commands -----------------------------------------------------------------------------------------------
    sealed_path = Path(sealed_tests) if sealed_tests else None
    cmds = list(commands) if commands else default_commands(has_sealed=sealed_path is not None, visible_tests=visible_tests, python=cfg.python)
    if not cmds:
        rep.status, rep.reason = FAIL, "NO_TEST_COMMAND: nothing to run the mutants against (no sealed tests and no visible tests)"
        return _finish(rep, started)
    prefix: List[str] = []
    if cfg.isolate_network in ("auto", "yes"):
        prefix = sealed_gate._unshare_prefix()
        if cfg.isolate_network == "yes" and not prefix:
            rep.status, rep.reason = INCONCLUSIVE, "NETWORK_ISOLATION_UNAVAILABLE"
            return _finish(rep, started)

    with tempfile.TemporaryDirectory(prefix="mutgate_", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        try:
            n_workers = max(1, min(int(cfg.workers), len(chosen)))
            sandboxes = [_make_sandbox(base, f"s{i}", root, sealed_path) for i in range(n_workers)]
        except sealed_gate.GateError as exc:
            rep.status, rep.reason = INCONCLUSIVE, f"SANDBOX_REFUSED: {exc}"
            return _finish(rep, started)
        # 4. baseline ------------------------------------------------------------------------------------------
        b_rc, b_to, b_err, b_secs = _run_commands(sandboxes[0], cmds, cfg, cfg.max_timeout, prefix)
        rep.baseline_seconds = round(b_secs, 3)
        if b_err or b_to or b_rc != 0:
            why = b_err or ("timed out" if b_to else f"exit {b_rc}")
            rep.status = FAIL if (not b_err and not b_to and b_rc in (1, 2, 3, 5)) else INCONCLUSIVE
            rep.reason = f"BASELINE_NOT_GREEN: the tests do not pass on the unmutated code ({why}); a mutation score would be meaningless"
            return _finish(rep, started)
        per_mutant = min(cfg.max_timeout, max(cfg.min_timeout, b_secs * cfg.timeout_factor + 2.0))

        # 5. run -----------------------------------------------------------------------------------------------
        pool: "queue.Queue[_Sandbox]" = queue.Queue()
        for sb in sandboxes:
            pool.put(sb)
        deadline = started + cfg.time_budget
        lock = threading.Lock()

        def work(m: Mutant) -> None:
            if time.monotonic() >= deadline:
                m.status = SKIPPED
                m.detail = "time budget exhausted"
                return
            sb = pool.get()
            try:
                target = sb.tree / m.file
                original = target.read_bytes()
                try:
                    target.write_text(text_of[m.id + str(m.variant)], encoding="utf-8", newline="\n")
                    t0 = time.monotonic()
                    rc, to, err, _ = _run_commands(sb, cmds, cfg, per_mutant, prefix)
                    m.duration = time.monotonic() - t0
                    m.status, m.detail = _classify(rc, to, err)
                finally:
                    target.write_bytes(original)
            except OSError as exc:
                m.status, m.detail = ERROR, f"sandbox I/O: {exc}"
            finally:
                pool.put(sb)
            if progress:
                with lock:
                    progress(m)

        if len(sandboxes) == 1:
            for m in chosen:
                work(m)
        else:
            with ThreadPoolExecutor(max_workers=len(sandboxes)) as ex:
                list(ex.map(work, chosen))

    # 6. score ----------------------------------------------------------------------------------------------
    counts = {s: 0 for s in (KILLED, TIMEOUT, SURVIVED, NO_TESTS, ERROR, SKIPPED)}
    for m in chosen:
        counts[m.status] = counts.get(m.status, 0) + 1
    rep.counts = counts
    rep.executed = len(chosen) - counts[SKIPPED]
    detected = counts[KILLED] + counts[TIMEOUT]
    denominator = detected + counts[SURVIVED] + counts[NO_TESTS]
    rep.score = round(detected / denominator, 4) if denominator else None
    rep.survivors = [m.to_dict() for m in chosen if m.status in (SURVIVED, NO_TESTS)][:50]
    if counts[ERROR]:
        rep.notes.append(f"{counts[ERROR]} mutants hit a runner error and are excluded from the score")
    if denominator == 0:
        rep.status, rep.reason = INCONCLUSIVE, "no mutant produced a usable result (all errors or skipped)"
    else:
        # the best score still reachable if every unfinished mutant were killed
        best = (detected + counts[SKIPPED]) / (denominator + counts[SKIPPED])
        if rep.score >= cfg.threshold and counts[SKIPPED] == 0:
            rep.status, rep.reason = PASS, f"kill score {rep.score:.3f} >= {cfg.threshold:.2f} ({detected}/{denominator})"
        elif best < cfg.threshold:
            rep.status, rep.reason = FAIL, f"kill score {rep.score:.3f} < {cfg.threshold:.2f} ({detected}/{denominator} detected, {counts[SURVIVED]} survived, {counts[NO_TESTS]} not covered)"
        elif counts[SKIPPED]:
            rep.status, rep.reason = INCONCLUSIVE, f"time budget exhausted after {rep.executed}/{rep.planned} mutants (score so far {rep.score:.3f})"
        else:
            rep.status, rep.reason = FAIL, f"kill score {rep.score:.3f} < {cfg.threshold:.2f} ({counts[SURVIVED]} survived, {counts[NO_TESTS]} not covered)"
        if counts[ERROR] and rep.status == PASS and counts[ERROR] > max(2, 0.25 * rep.planned):
            rep.status, rep.reason = INCONCLUSIVE, f"{counts[ERROR]} of {rep.planned} mutants hit runner errors; the score is not trustworthy"
    return _finish(rep, started)


def _finish(rep: MutationReport, started: float) -> MutationReport:
    rep.duration = round(time.monotonic() - started, 3)
    return rep


def changed_from_baseline(baseline, worktree, *, only: Optional[Sequence[str]] = None) -> Dict[str, List[Range]]:
    """{file: ranges} of every non-test Python file changed against the baseline snapshot."""
    ch = wt.changes(baseline, worktree, only=only)
    rels = [r for r in ch.touched if r.endswith(".py") and not weakening.is_test_file(r)]
    return wt.diff_lines(baseline, worktree, rels)


# ------------------------------------------------------------------------------------------------ CLI
def _cli(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="mutation_gate", description="Diff-scoped mutation testing (Python). Exit 0 pass, 1 fail, 3 inconclusive, 2 tooling.")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("run", "list"):
        p = sub.add_parser(name)
        p.add_argument("--worktree", required=True)
        p.add_argument("--baseline", required=True, help="baseline snapshot directory (core.worktree.capture)")
        p.add_argument("--sealed", help="directory with the sealed tests (vault/<ticket>/tests)")
        p.add_argument("--visible", action="append", default=[], help="visible test path inside the worktree (repeatable)")
        p.add_argument("--only", action="append", default=[], help="restrict to these path globs (repeatable)")
        p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
        p.add_argument("--max-mutants", type=int, default=60)
        p.add_argument("--time-budget", type=float, default=600.0)
        p.add_argument("--workers", type=int, default=1)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    try:
        for label, d in (("worktree", a.worktree), ("baseline", a.baseline)):
            if not Path(d).is_dir():
                raise OSError(f"{label} directory not found: {d}")
        changed = changed_from_baseline(a.baseline, a.worktree, only=a.only or None)
        if a.cmd == "list":
            out = []
            for rel, rngs in changed.items():
                src = Path(a.worktree, rel).read_text(encoding="utf-8")
                for m in discover(src, rel, rngs)[0]:
                    out.append(m.to_dict())
            print(json.dumps(out, indent=2))
            return 0
        cfg = MutationConfig(threshold=a.threshold, max_mutants=a.max_mutants, time_budget=a.time_budget, workers=a.workers, seed=a.seed)
        rep = run_mutation(a.worktree, changed, sealed_tests=a.sealed, visible_tests=a.visible, config=cfg)
    except (OSError, wt.WorktreeError) as exc:
        print(f"mutation_gate: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep.to_dict(), indent=2))
    else:
        print(f"{rep.status}: {rep.reason}")
        for s in rep.survivors[:15]:
            print(f"  survived {s['file']}:{s['line']} {s['description']}")
    return {PASS: 0, NOT_APPLICABLE: 0, FAIL: 1, INCONCLUSIVE: 3}.get(rep.status, 2)


main = _cli

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
