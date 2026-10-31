"""appsec_audit.py - AG8 "The Arbiter": a deterministic security + concurrency audit of Python source (stdlib `ast` only).

AG8's duties are "security AST taint analysis & input sanitization" and "algorithmic & concurrency optimization".
Neither can be judged by a model's say-so, so this module gives the audit a floor that is mechanical and repeatable:

SECURITY (rule ids start with SEC-)
  SEC-EVAL            eval/exec of anything that is not a literal (compile() alone only builds a code object, it is not flagged)
  SEC-SHELL           os.system / os.popen / subprocess(shell=True) with a non-literal command
  SEC-SQL             a SQL string built by f-string, %, + or .format and passed to .execute/.executemany/.executescript
  SEC-DESERIALIZE     pickle/marshal load(s), yaml.load without a safe loader
  SEC-PATH            a value that flows from a function PARAMETER into open()/Path()/os.path.join/os.remove/... without passing a sanitizer
  SEC-HARDCODED-SECRET a secret-looking name assigned a string literal, or a key/token-shaped literal anywhere
  SEC-TLS             verify=False, ssl._create_unverified_context
  SEC-WEAK-RANDOM     random.* used to make a token/secret/password/nonce/session id
  SEC-DEBUG           debug=True passed to a server run call
  SEC-CHMOD           world-writable permission constants
  SEC-HASH            md5/sha1 (low)         SEC-TMP  tempfile.mktemp (low)
CONCURRENCY (rule ids start with CONC-)
  CONC-UNLOCKED-STATE a class that owns a lock mutates its own state in a method that does not hold it
  CONC-GLOBAL-MUTATION a module that imports threading/asyncio/concurrent/multiprocessing mutates module-level mutable state
                      from a function without holding a lock
  CONC-TOCTOU         `if os.path.exists(p): open(p)` style check-then-act on the file system
  CONC-ASYNC-BLOCKING time.sleep / requests.* / blocking subprocess inside `async def`

Taint is deliberately small: forward, intra-function, statement order, sources = the function's parameters, sanitizers =
calls to functions whose name says so (int, float, quote, escape, basename, safe_*, sanitize*, validate_*, ...). It
trusts those NAMES: the sanitizer itself is code AG8 writes and the sealed oracle must exercise. There are NO inline
suppression pragmas on purpose (a worker could add `# noqa` to its own vulnerability); the operator may pass an
explicit allow-list (`allow=["SEC-PATH:src/files.py"]`) which is echoed in the report.

Honest limits: not a SAST product. No inter-procedural flow, no alias analysis, no framework knowledge, Python only
(other languages are reported as not audited, never as clean). A clean report means "none of these patterns", not "secure".

CLI: python -m core.verification.appsec_audit ROOT [FILE ...] [--fail-on high|medium|low] [--allow RULE:PATH] [--json]
Exit: 0 nothing at/above --fail-on (default medium), 1 findings, 2 usage error.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

SEVERITY = {"low": 1, "medium": 2, "high": 3}
MAX_BYTES = 1_000_000
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".planner", ".pytest_cache", ".mypy_cache", "dist", "build", ".stryker-tmp", "mutants"}

SECRET_NAME = re.compile(r"(?i)(^|[_\-.])(api[_\-]?key|apikey|token|secret|passw(or)?d|passwd|pwd|private[_\-]?key|access[_\-]?key|auth[_\-]?key|bearer)([_\-.]|$)")
SECRET_VALUE = re.compile(r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")
PLACEHOLDER = re.compile(r"(?i)^(\s*|<.*>|\$\{.*\}|\{\{.*\}\}|x+|\*+|changeme|change[-_ ]me|your[-_ ].*|example.*|dummy.*|placeholder.*|none|null|todo.*|test)$")
TOKENISH = re.compile(r"(?i)(token|secret|passw|nonce|session|api_?key|salt|otp|csrf)")
SANITIZERS = {"int", "float", "bool", "len", "str.isdigit", "quote", "quote_plus", "escape", "escape_string", "basename", "secure_filename", "hexdigest",
              "uuid4", "safe_join", "resolve_inside", "sanitize", "validate", "fullmatch", "isalnum"}
SANITIZER_PREFIXES = ("safe_", "sanitize", "sanitise", "validate_", "secure_", "escape_", "clean_")
PATH_SINKS = {"open", "Path", "PurePath", "os.path.join", "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace", "os.makedirs", "os.mkdir",
              "os.listdir", "os.scandir", "os.walk", "shutil.copy", "shutil.copyfile", "shutil.move", "shutil.rmtree", "shutil.copytree", "send_file",
              "send_from_directory", "os.chmod", "os.chown"}
MUTATORS = {"append", "extend", "update", "add", "pop", "popitem", "clear", "setdefault", "remove", "insert", "discard", "appendleft", "popleft", "sort", "reverse"}
MUTABLE_CTORS = {"list", "dict", "set", "defaultdict", "Counter", "OrderedDict", "deque", "bytearray"}
LOCK_CTORS = {"Lock", "RLock", "Condition", "Semaphore", "BoundedSemaphore"}
THREAD_MODULES = {"threading", "concurrent", "multiprocessing", "asyncio", "gevent", "trio", "anyio"}
BLOCKING_IN_ASYNC = {"time.sleep", "requests.get", "requests.post", "requests.put", "requests.delete", "requests.request", "subprocess.run", "subprocess.call",
                     "subprocess.check_output", "urllib.request.urlopen", "socket.create_connection"}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    message: str
    snippet: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def __str__(self) -> str:
        return f"[{self.severity.upper():6}] {self.rule} {self.file}:{self.line}: {self.message}"


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    audited: List[str] = field(default_factory=list)
    not_audited: List[str] = field(default_factory=list)   # other languages, oversized, unreadable
    allowed: List[str] = field(default_factory=list)       # findings the operator explicitly allowed (echoed, never hidden)

    def at_least(self, level: str) -> List[Finding]:
        floor = SEVERITY[level]
        return [f for f in self.findings if SEVERITY[f.severity] >= floor]

    def counts(self) -> Dict[str, int]:
        c = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings:
            c[f.severity] += 1
        return c

    def to_dict(self) -> dict:
        return {"counts": self.counts(), "audited": self.audited, "not_audited": self.not_audited, "allowed": self.allowed,
                "findings": [f.to_dict() for f in self.findings]}


# ----------------------------------------------------------------------------------------------------- helpers
def dotted(node: ast.AST) -> str:
    """`a.b.c` for Name/Attribute chains, '' otherwise."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def call_name(call: ast.Call) -> str:
    return dotted(call.func)


def is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(is_literal(e) for e in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return is_literal(node.left) and is_literal(node.right)
    return False


def is_sanitizer(name: str) -> bool:
    last = name.rsplit(".", 1)[-1]
    return last in SANITIZERS or name in SANITIZERS or last.lower().startswith(SANITIZER_PREFIXES)


def is_built_string(node: ast.AST) -> bool:
    """f-string with substitutions, `%` formatting, `+` concatenation with a non-literal, `.format(...)`."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return not is_literal(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


def kw(call: ast.Call, name: str) -> Optional[ast.expr]:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def own_exprs(stmt: ast.stmt) -> Iterator[ast.expr]:
    """Expressions owned by `stmt` itself, not by statements nested in its body."""
    for _name, value in ast.iter_fields(stmt):
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, ast.expr):
                yield v
            elif isinstance(v, ast.withitem):
                yield v.context_expr
            elif isinstance(v, ast.keyword):
                yield v.value
            elif isinstance(v, ast.arguments):
                for d in list(v.defaults) + [x for x in v.kw_defaults if x is not None]:
                    yield d


def child_blocks(stmt: ast.stmt) -> Iterator[List[ast.stmt]]:
    for name in ("body", "orelse", "finalbody"):
        blk = getattr(stmt, name, None)
        if isinstance(blk, list) and blk and isinstance(blk[0], ast.stmt):
            yield blk
    for h in getattr(stmt, "handlers", []) or []:
        yield h.body
    for c in getattr(stmt, "cases", []) or []:
        yield c.body


def under_lock_stmt(stmt: ast.stmt, names: Iterable[str] = ()) -> bool:
    """`with <lock-ish>:` — a context manager whose expression mentions lock/mutex/semaphore/condition or an acquire(),
    or `self.<attr>` where <attr> is an attribute the class assigned a Lock/RLock/Condition/Semaphore (`names`)."""
    if not isinstance(stmt, (ast.With, ast.AsyncWith)):
        return False
    known = set(names)
    for item in stmt.items:
        expr = item.context_expr
        text = ast.unparse(expr).lower()
        if re.search(r"lock|mutex|semaphore|condition|\.acquire\(|critical", text):
            return True
        if known and isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "self" and expr.attr in known:
            return True
    return False


# ----------------------------------------------------------------------------------------------------- audit
class Auditor:
    def __init__(self, source: str, filename: str):
        self.src = source
        self.lines = source.splitlines()
        self.file = filename
        self.findings: List[Finding] = []
        self._seen: Set[Tuple[str, int]] = set()

    def add(self, rule: str, severity: str, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", 0)
        if (rule, line) in self._seen:
            return
        self._seen.add((rule, line))
        snippet = self.lines[line - 1].strip()[:120] if 0 < line <= len(self.lines) else ""
        self.findings.append(Finding(rule, severity, self.file, line, message, snippet))

    def run(self) -> List[Finding]:
        try:
            tree = ast.parse(self.src)
        except SyntaxError as exc:
            self.findings.append(Finding("AUDIT-PARSE", "medium", self.file, exc.lineno or 0, f"cannot parse, so it cannot be audited: {exc.msg}"))
            return self.findings
        self.module_checks(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.call_checks(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.secret_assign(node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if SECRET_VALUE.search(node.value):
                    self.add("SEC-HARDCODED-SECRET", "high", node, "a string literal has the shape of a credential (cloud/VCS/chat token, JWT or private key)")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.taint_function(node)
                if isinstance(node, ast.AsyncFunctionDef):
                    self.async_blocking(node)
            elif isinstance(node, ast.ClassDef):
                self.class_locking(node)
            elif isinstance(node, ast.If):
                self.toctou(node)
        self.findings.sort(key=lambda f: (f.line, f.rule))
        return self.findings

    # -- generic call rules ------------------------------------------------------------------------
    def call_checks(self, call: ast.Call) -> None:
        name = call_name(call)
        last = name.rsplit(".", 1)[-1]
        args = call.args
        if name in ("eval", "exec") and args and not is_literal(args[0]):
            self.add("SEC-EVAL", "high", call, f"{name}() of a non-literal executes data as code")
        if name in ("os.system", "os.popen") and args and not is_literal(args[0]):
            self.add("SEC-SHELL", "high", call, f"{name}() with a non-literal command goes through a shell")
        if name.startswith("subprocess.") and last in ("run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"):
            shell = kw(call, "shell")
            if (isinstance(shell, ast.Constant) and shell.value is True) or last in ("getoutput", "getstatusoutput"):
                if args and not is_literal(args[0]):
                    self.add("SEC-SHELL", "high", call, f"{name}(shell=True) with a non-literal command")
        if last in ("execute", "executemany", "executescript") and isinstance(call.func, ast.Attribute) and args and is_built_string(args[0]):
            if self._is_pragma(args[0]):
                self.add("SEC-SQL", "low", call, "PRAGMA built by string formatting (PRAGMA cannot bind parameters): the value must come from a constant or a validated allow-list")
            else:
                self.add("SEC-SQL", "high", call, "SQL built by string formatting; pass a constant statement and bind parameters")
        if name in ("pickle.load", "pickle.loads", "cPickle.loads", "marshal.load", "marshal.loads", "shelve.open", "dill.loads") and args and not is_literal(args[0]):
            self.add("SEC-DESERIALIZE", "high", call, f"{name}() can execute code embedded in the data")
        if name in ("yaml.load", "yaml.load_all"):
            loader = kw(call, "Loader") or (args[1] if len(args) > 1 else None)
            if loader is None or "safe" not in ast.unparse(loader).lower():
                self.add("SEC-DESERIALIZE", "high", call, "yaml.load without a safe loader can construct arbitrary objects; use yaml.safe_load")
        v = kw(call, "verify")
        if isinstance(v, ast.Constant) and v.value is False:
            self.add("SEC-TLS", "medium", call, "certificate verification disabled (verify=False)")
        if name in ("ssl._create_unverified_context",):
            self.add("SEC-TLS", "medium", call, "unverified TLS context")
        d = kw(call, "debug")
        if last in ("run", "serve") and isinstance(d, ast.Constant) and d.value is True:
            self.add("SEC-DEBUG", "medium", call, "debug=True on a server exposes an interactive debugger / stack traces")
        if name in ("os.chmod", "os.fchmod") and len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, int) and args[1].value & 0o002:
            self.add("SEC-CHMOD", "medium", call, "world-writable permission bits")
        if name in ("hashlib.md5", "hashlib.sha1"):
            self.add("SEC-HASH", "low", call, f"{name} is not collision resistant; fine for checksums, wrong for security decisions")
        if name == "tempfile.mktemp":
            self.add("SEC-TMP", "low", call, "tempfile.mktemp is racy; use mkstemp or NamedTemporaryFile")

    @staticmethod
    def _is_pragma(node: ast.AST) -> bool:
        head = ""
        if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
            head = str(node.values[0].value)
        elif isinstance(node, ast.BinOp) and isinstance(node.left, ast.Constant):
            head = str(node.left.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Constant):
            head = str(node.func.value.value)
        return head.lstrip().upper().startswith("PRAGMA ")

    def secret_assign(self, node) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)) or len(value.value) < 8 or PLACEHOLDER.match(value.value):
            return
        for t in targets:
            n = dotted(t) if not isinstance(t, ast.Subscript) else (t.slice.value if isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str) else "")
            if n and SECRET_NAME.search(n.rsplit(".", 1)[-1]):
                self.add("SEC-HARDCODED-SECRET", "high", node, f"{n.rsplit('.', 1)[-1]!r} is assigned a string literal; read it from the environment or a secret store")

    def module_checks(self, tree: ast.Module) -> None:
        threaded = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                threaded |= any(a.name.split(".")[0] in THREAD_MODULES for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                threaded |= node.module.split(".")[0] in THREAD_MODULES
        self.threaded = threaded
        self.module_mutables: Set[str] = set()
        for stmt in tree.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                value = stmt.value
                is_mut = isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)) or (
                    isinstance(value, ast.Call) and dotted(value.func).rsplit(".", 1)[-1] in MUTABLE_CTORS)
                if is_mut:
                    for t in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]):
                        if isinstance(t, ast.Name):
                            self.module_mutables.add(t.id)
        if threaded:
            for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                self.global_mutation(fn)

    # -- concurrency ---------------------------------------------------------------------------------
    def global_mutation(self, fn) -> None:
        declared = {n for s in ast.walk(fn) if isinstance(s, ast.Global) for n in s.names}
        shadow = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
        names = (self.module_mutables | declared) - shadow

        def visit(block: List[ast.stmt], locked: bool) -> None:
            for stmt in block:
                inner_locked = locked or under_lock_stmt(stmt)
                if not inner_locked:
                    for expr in own_exprs(stmt):
                        for c in ast.walk(expr):
                            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr in MUTATORS and isinstance(c.func.value, ast.Name) and c.func.value.id in names:
                                self.add("CONC-GLOBAL-MUTATION", "medium", c, f"module-level {c.func.value.id!r} is mutated by {fn.name}() without a lock in a threaded module")
                    targets: List[ast.expr] = []
                    if isinstance(stmt, ast.Assign):
                        targets = list(stmt.targets)
                    elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
                        targets = [stmt.target]
                    for t in targets:
                        base = t.value if isinstance(t, ast.Subscript) else t
                        if isinstance(base, ast.Name) and base.id in names and (isinstance(t, ast.Subscript) or base.id in declared):
                            self.add("CONC-GLOBAL-MUTATION", "medium", stmt, f"module-level {base.id!r} is written by {fn.name}() without a lock in a threaded module")
                for blk in child_blocks(stmt):
                    visit(blk, inner_locked)

        visit(fn.body, False)

    def class_locking(self, cls: ast.ClassDef) -> None:
        lock_attrs: Set[str] = set()
        methods = [m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for m in methods:
            for n in ast.walk(m):
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and dotted(n.value.func).rsplit(".", 1)[-1] in LOCK_CTORS:
                    for t in n.targets:
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                            lock_attrs.add(t.attr)
        if not lock_attrs:
            return
        # methods that are only ever called while a lock is held are covered by their callers
        covered: Set[str] = set()
        for m in methods:
            for callee, locked in self._self_calls(m, lock_attrs):
                if locked:
                    covered.add(callee)
        unlocked_calls = {callee for m in methods if m.name not in covered for callee, locked in self._self_calls(m, lock_attrs) if not locked}
        covered -= unlocked_calls

        def mutated_attr(stmt: ast.stmt) -> Optional[Tuple[str, ast.AST]]:
            targets: List[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets = list(stmt.targets)
            elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
                targets = [stmt.target]
            for t in targets:
                base = t.value if isinstance(t, ast.Subscript) else t
                if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id == "self" and base.attr not in lock_attrs:
                    return base.attr, stmt
            for expr in own_exprs(stmt):
                for c in ast.walk(expr):
                    if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr in MUTATORS and isinstance(c.func.value, ast.Attribute)
                            and isinstance(c.func.value.value, ast.Name) and c.func.value.value.id == "self" and c.func.value.attr not in lock_attrs):
                        return c.func.value.attr, c
            return None

        for m in methods:
            if m.name in ("__init__", "__new__", "__post_init__") or m.name in covered:
                continue

            def visit(block: List[ast.stmt], locked: bool, m=m) -> None:
                for stmt in block:
                    inner = locked or under_lock_stmt(stmt, lock_attrs)
                    if not inner:
                        hit = mutated_attr(stmt)
                        if hit:
                            self.add("CONC-UNLOCKED-STATE", "medium", hit[1],
                                     f"{cls.name}.{m.name}() mutates self.{hit[0]} without holding {', '.join('self.' + a for a in sorted(lock_attrs))}")
                    for blk in child_blocks(stmt):
                        visit(blk, inner)
            visit(m.body, False)

    @staticmethod
    def _self_calls(method, lock_attrs: Set[str]) -> List[Tuple[str, bool]]:
        out: List[Tuple[str, bool]] = []

        def visit(block: List[ast.stmt], locked: bool) -> None:
            for stmt in block:
                inner = locked or under_lock_stmt(stmt, lock_attrs)
                for expr in own_exprs(stmt):
                    for c in ast.walk(expr):
                        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and c.func.value.id == "self":
                            out.append((c.func.attr, inner))
                for blk in child_blocks(stmt):
                    visit(blk, inner)
        visit(method.body, False)
        return out

    def toctou(self, node: ast.If) -> None:
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            test = test.operand
        if not (isinstance(test, ast.Call) and call_name(test) in ("os.path.exists", "os.path.isfile", "os.path.isdir", "os.path.lexists", "path.exists", "exists")
                and test.args):
            return
        key = ast.dump(test.args[0])
        for blk in (node.body, node.orelse):
            for stmt in blk:
                for n in ast.walk(stmt):
                    if (isinstance(n, ast.Call) and call_name(n) in ("open", "os.remove", "os.unlink", "os.mkdir", "os.makedirs", "os.rename", "shutil.rmtree")
                            and n.args and ast.dump(n.args[0]) == key):
                        self.add("CONC-TOCTOU", "medium", node, "check-then-act on the file system: the file can change between the check and the use; open/create atomically and handle the error")
                        return

    def async_blocking(self, fn: ast.AsyncFunctionDef) -> None:
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and call_name(n) in BLOCKING_IN_ASYNC:
                self.add("CONC-ASYNC-BLOCKING", "medium", n, f"{call_name(n)}() blocks the event loop inside async def {fn.name}")

    # -- taint ---------------------------------------------------------------------------------------
    def taint_function(self, fn) -> None:
        params = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
        if fn.args.vararg:
            params.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            params.add(fn.args.kwarg.arg)
        params -= {"self", "cls"}
        if not params:
            return
        tainted = set(params)
        self._walk_block(fn.body, tainted)

    def tainted(self, e: ast.AST, t: Set[str]) -> bool:
        if isinstance(e, ast.Name):
            return e.id in t
        if isinstance(e, ast.Constant):
            return False
        if isinstance(e, ast.Call):
            name = call_name(e)
            if name and is_sanitizer(name):
                return False
            parts = list(e.args) + [k.value for k in e.keywords]
            if isinstance(e.func, ast.Attribute):
                parts.append(e.func.value)
            return any(self.tainted(p, t) for p in parts)
        if isinstance(e, (ast.Lambda,)):
            return False
        return any(self.tainted(c, t) for c in ast.iter_child_nodes(e) if isinstance(c, ast.expr))

    def _walk_block(self, block: List[ast.stmt], t: Set[str]) -> None:
        for stmt in block:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # audited on their own
            for expr in own_exprs(stmt):
                for c in ast.walk(expr):
                    if isinstance(c, ast.Call):
                        self.taint_sink(c, t)
            if isinstance(stmt, ast.Assign):
                hot = self.tainted(stmt.value, t)
                for tgt in stmt.targets:
                    self._bind(tgt, hot, t)
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                self._bind(stmt.target, self.tainted(stmt.value, t), t)
            elif isinstance(stmt, ast.AugAssign):
                if self.tainted(stmt.value, t):
                    self._bind(stmt.target, True, t)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                self._bind(stmt.target, self.tainted(stmt.iter, t), t)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    if item.optional_vars is not None:
                        self._bind(item.optional_vars, self.tainted(item.context_expr, t), t)
            for blk in child_blocks(stmt):
                self._walk_block(blk, t)
            if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                for blk in child_blocks(stmt):
                    self._walk_block(blk, t)   # second pass: taint assigned late in the loop body reaches earlier sinks

    def _bind(self, target: ast.AST, hot: bool, t: Set[str]) -> None:
        if isinstance(target, ast.Name):
            (t.add if hot else t.discard)(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                self._bind(e, hot, t)
        elif isinstance(target, ast.Starred):
            self._bind(target.value, hot, t)

    def taint_sink(self, call: ast.Call, t: Set[str]) -> None:
        name = call_name(call)
        if name in PATH_SINKS or name.rsplit(".", 1)[-1] in ("send_file", "send_from_directory"):
            if any(self.tainted(a, t) for a in call.args[:2]) or any(self.tainted(k.value, t) for k in call.keywords if k.arg in ("path", "file", "src", "dst", "filename")):
                self.add("SEC-PATH", "medium", call, f"a function parameter reaches {name}() without a sanitizer (path traversal: '../', absolute paths, symlinks)")
        if name in ("os.system", "os.popen") and call.args and self.tainted(call.args[0], t):
            self.add("SEC-SHELL", "high", call, f"a function parameter flows into {name}()")
        if name in ("eval", "exec") and call.args and self.tainted(call.args[0], t):
            self.add("SEC-EVAL", "high", call, f"a function parameter flows into {name}()")
        if name.startswith("subprocess.") and call.args and self.tainted(call.args[0], t):
            shell = kw(call, "shell")
            if isinstance(shell, ast.Constant) and shell.value is True:
                self.add("SEC-SHELL", "high", call, f"a function parameter flows into {name}(shell=True)")
            elif isinstance(call.args[0], (ast.List, ast.Tuple)) and call.args[0].elts and self.tainted(call.args[0].elts[0], t):
                self.add("SEC-SHELL", "medium", call, f"a function parameter chooses the program run by {name}()")
        last = name.rsplit(".", 1)[-1]
        if last in ("choice", "randint", "random", "getrandbits", "randrange", "sample", "shuffle") and name.startswith("random."):
            # attributed to the assignment target by weak_random(); here only direct token-ish function names matter
            pass


def weak_random(tree: ast.AST, aud: Auditor) -> None:
    """`token = ''.join(random.choice(...))` and friends."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [dotted(t) for t in node.targets]
            if any(TOKENISH.search(n) for n in names if n):
                for c in ast.walk(node.value):
                    if isinstance(c, ast.Call) and call_name(c).startswith("random.") and call_name(c) != "random.SystemRandom":
                        aud.add("SEC-WEAK-RANDOM", "medium", node, "a security-relevant value is made with the random module; use the secrets module")
                        break
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and TOKENISH.search(node.name):
            for c in ast.walk(node):
                if isinstance(c, ast.Call) and call_name(c).startswith("random.") and call_name(c) != "random.SystemRandom":
                    aud.add("SEC-WEAK-RANDOM", "medium", c, f"{node.name}() uses the random module; use the secrets module")
                    break


# ----------------------------------------------------------------------------------------------------- API
def audit_source(source: str, filename: str = "<source>") -> List[Finding]:
    aud = Auditor(source, filename)
    out = aud.run()
    try:
        weak_random(ast.parse(source), aud)
    except SyntaxError:
        pass
    out.sort(key=lambda f: (f.line, f.rule))
    return out


def iter_files(root: Path, rel_files: Optional[Iterable[str]] = None) -> Iterator[str]:
    if rel_files is not None:
        for r in rel_files:
            yield str(r).replace("\\", "/")
        return
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            yield (Path(dirpath) / f).relative_to(root).as_posix()


def audit_tree(root, files: Optional[Iterable[str]] = None, *, allow: Sequence[str] = (), include_tests: bool = False) -> Report:
    """Audit every .py file (or exactly `files`) under `root`. Test files are skipped unless include_tests: they are the
    oracle's business, and they legitimately contain eval-ish strings and fake secrets."""
    base = Path(root)
    rep = Report()
    allowed = [a.split(":", 1) for a in allow if ":" in a]
    for rel in iter_files(base, files):
        p = base / rel
        if p.is_symlink() or not p.is_file():
            continue
        if p.suffix != ".py":
            if p.suffix in (".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".c", ".cpp", ".cs", ".sh"):
                rep.not_audited.append(f"{rel} (language not supported)")
            continue
        if not include_tests and re.search(r"(^|/)(tests?|__tests__)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$", rel):
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                rep.not_audited.append(f"{rel} (larger than {MAX_BYTES} bytes)")
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            rep.not_audited.append(f"{rel} (unreadable: {exc})")
            continue
        rep.audited.append(rel)
        for f in audit_source(text, rel):
            if any(f.rule == r and (rel == pat or rel.startswith(pat.rstrip("*"))) for r, pat in allowed):
                rep.allowed.append(str(f))
            else:
                rep.findings.append(f)
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="appsec_audit", description="Deterministic security + concurrency audit of Python source (AG8)")
    ap.add_argument("root")
    ap.add_argument("files", nargs="*", help="files relative to ROOT (default: every .py file)")
    ap.add_argument("--fail-on", choices=("high", "medium", "low"), default="medium")
    ap.add_argument("--allow", action="append", default=[], metavar="RULE:PATH", help="operator allow-list entry, echoed in the report")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not Path(a.root).is_dir():
        print(f"appsec_audit: not a directory: {a.root}", file=sys.stderr)
        return 2
    rep = audit_tree(a.root, a.files or None, allow=a.allow, include_tests=a.include_tests)
    bad = rep.at_least(a.fail_on)
    if a.json:
        out = rep.to_dict()
        out["fail_on"] = a.fail_on
        out["ok"] = not bad
        print(json.dumps(out, indent=2))
    else:
        for f in rep.findings:
            print(f)
        for s in rep.allowed:
            print(f"[ALLOWED] {s}")
        for s in rep.not_audited:
            print(f"[NOT AUDITED] {s}")
        c = rep.counts()
        print(f"{len(rep.audited)} file(s) audited: {c['high']} high, {c['medium']} medium, {c['low']} low" + ("" if not bad else f"  -> FAIL (>= {a.fail_on})"))
    return 1 if bad else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
