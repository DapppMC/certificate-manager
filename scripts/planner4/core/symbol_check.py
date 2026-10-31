"""symbol_check.py - find the declaration of a locked SCIP symbol in source code and detect signature drift.

`contract_ticket.check_symbol_drift` compares signature TEXTS; this module produces those texts from real source so the
verifier can answer "did the worker change the shared type that AG1 locked?".

Languages
  * TypeScript / JavaScript (.ts .tsx .mts .cts .js .jsx .mjs .cjs .d.ts): a small scanner (comments and strings aware,
    bracket depth) extracts `interface | type | class | enum | function | const | let | var <Name>`; comparison is
    token based, so whitespace, comments, trailing `;` and `,` do not matter. Interfaces/types/classes/enums must
    match EXACTLY (an added or removed member is drift). For `function`/`const` the lock covers the SIGNATURE: the found
    declaration must START with the locked tokens (a body is allowed to follow).
  * Python (.py .pyi): parsed with `ast`. The comparable "interface" of a function is (name, decorators, arguments,
    return annotation); of a class: bases, decorators and its members (methods as interfaces, annotated fields with their
    defaults). Bodies, docstrings and `pass`/`...` are ignored.
  * Everything else: unsupported -> the caller gets status "unsupported" (never a silent pass).

Honest limits: a lexical scanner is not a TypeScript compiler. It does not resolve re-exports, namespaces, declaration
merging or overloads (the first declaration with that name wins), and it cannot see types produced by mapped/conditional
types at the type-checker level. Where the compiler matters, run the project's `tsc --noEmit` as an acceptance command
(the verifier supports `acceptance` commands for exactly that). Standard library only.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from core import scip_symbols as scip
    from core.platform_compat import matches_any, atomic_write_text
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import scip_symbols as scip
    from core.platform_compat import matches_any, atomic_write_text

TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
PY_SUFFIXES = (".py", ".pyi")
FOUND, MISSING, DRIFT, UNSUPPORTED = "found", "missing", "drift", "unsupported"


def language_of(path: str, signature: str = "") -> str:
    p = path.lower()
    if p.endswith(TS_SUFFIXES):
        return "ts"
    if p.endswith(PY_SUFFIXES):
        return "py"
    return ""


def signature_language(signature: str) -> str:
    """Guess from the locked text: `def`/`class X:`/`@decorator` -> py, otherwise TS-like when it has TS keywords."""
    s = signature.lstrip()
    if re.match(r"(async\s+def|def|class)\s+\w+[^\n]*:\s*(#.*)?(\n|$)", s) or s.startswith("@"):
        return "py"
    if re.match(r"(export\s+|declare\s+|abstract\s+)*(interface|type|class|enum|function|const|let|var)\b", s):
        return "ts"
    return ""


# ------------------------------------------------------------------------------------------------ TS scanner
_PUNCT3 = ("...", "===", "!==", "<<=", ">>=", "**=", "&&=", "||=", "??=")
_PUNCT2 = ("=>", "?.", "??", "==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "<<", ">>", "::", "**")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUM = re.compile(r"\d[\w.]*")


def _skip_string(s: str, i: int) -> int:
    q = s[i]
    j = i + 1
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j + 1
        if q != "`" and s[j] == "\n":
            return j  # unterminated string: stop at the newline instead of swallowing the file
        j += 1
    return len(s)


def ts_tokens(text: str) -> List[str]:
    """Comment- and whitespace-free tokens; `;` and `,` are dropped (they are interchangeable member separators)."""
    toks: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif c in "'\"`":
            j = _skip_string(text, i)
            toks.append(text[i:j])
            i = j
        elif c in ";,":
            i += 1
        else:
            m = _IDENT.match(text, i) or _NUM.match(text, i)
            if m:
                toks.append(m.group(0))
                i = m.end()
                continue
            for group in (_PUNCT3, _PUNCT2):
                w = len(group[0])
                if text[i:i + w] in group:
                    toks.append(text[i:i + w])
                    i += w
                    break
            else:
                toks.append(c)
                i += 1
    return toks


_NEW_STMT = re.compile(r"^[ \t]*(export|declare|interface|type|class|enum|function|const|let|var|import|abstract|@|//|/\*)\b|^[ \t]*(@|//|/\*)")


def ts_declaration(text: str, name: str) -> Optional[Tuple[str, str, int]]:
    """-> (declaration source, kind, 1-based line) of the first `<kind> name` in `text`, or None."""
    pat = re.compile(r"(?m)^[ \t]*(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
                     r"(interface|type|class|enum|function\*?|const|let|var)\s+" + re.escape(name) + r"(?![\w$])")
    m = pat.search(text)
    if not m:
        return None
    start = m.start()
    kind = m.group(1).rstrip("*")
    depth = 0
    seen_brace = False
    i, n = m.end(), len(text)
    end = n
    while i < n:
        c = text[i]
        if c in "'\"`":
            i = _skip_string(text, i)
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "{([":
            depth += 1
            seen_brace = seen_brace or c == "{"
        elif c in "})]":
            depth -= 1
            if depth <= 0 and c == "}" and seen_brace and kind in ("interface", "class", "enum", "function"):
                end = i + 1
                break
            if depth < 0:
                end = i
                break
        elif c == ";" and depth == 0:
            end = i + 1
            break
        elif c == "\n" and depth == 0 and kind in ("type", "const", "let", "var"):
            rest = text[i + 1:]
            nxt = rest.lstrip(" \t")
            prev = text[start:i].rstrip()
            continues = bool(nxt[:1] in ("|", "&", "?", ":", ".", "+", "-", "*", "/", "=", "<", ">") or prev.endswith(("=", "|", "&", "=>", "(", "{", "[", ",", "?", ":", "<")))
            if not continues and (not nxt or _NEW_STMT.match(rest.split("\n", 1)[0]) or rest.startswith("\n")):
                end = i
                break
        i += 1
    line = text.count("\n", 0, start) + 1
    return text[start:end], kind, line


def ts_matches(locked: str, found: str, kind_hint: str = "") -> bool:
    lt, ft = ts_tokens(locked), ts_tokens(found)
    if lt == ft:
        return True
    kind = kind_hint
    if not kind:
        m = re.search(r"\b(interface|type|class|enum|function|const|let|var)\b", locked)
        kind = m.group(1) if m else ""
    # signature-only locks (functions, arrow-function consts) may be followed by a body
    if kind in ("function", "const", "let", "var") and lt and lt[-1] != "}" and ft[:len(lt)] == lt:
        return True
    return False


# ------------------------------------------------------------------------------------------------ Python
def _dump(node: Optional[ast.AST]) -> str:
    return "" if node is None else ast.dump(node, annotate_fields=True, include_attributes=False)


def py_interface(node: ast.AST):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ("async def" if isinstance(node, ast.AsyncFunctionDef) else "def", node.name, tuple(_dump(d) for d in node.decorator_list),
                _dump(node.args), _dump(node.returns))
    if isinstance(node, ast.ClassDef):
        members = []
        for st in node.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                members.append(py_interface(st))
            elif isinstance(st, ast.AnnAssign):
                members.append(("field", _dump(st.target), _dump(st.annotation), _dump(st.value)))
            elif isinstance(st, ast.Assign):
                members.append(("assign", tuple(_dump(t) for t in st.targets), _dump(st.value)))
        return ("class", node.name, tuple(_dump(b) for b in node.bases), tuple(_dump(k) for k in node.keywords),
                tuple(_dump(d) for d in node.decorator_list), tuple(members))
    return ("other", _dump(node))


def _py_find(tree: ast.AST, name: str) -> Optional[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node
    return None


def _locked_py_tree(locked: str) -> Optional[ast.AST]:
    try:
        return ast.parse(locked)
    except SyntaxError:
        pass
    # a header without a body, e.g. "def f(a: int) -> int:" -> give it one
    try:
        return ast.parse(locked.rstrip() + "\n    ...\n")
    except SyntaxError:
        return None


def py_declaration(text: str, name: str) -> Optional[Tuple[ast.AST, int]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    node = _py_find(tree, name)
    return (node, node.lineno) if node is not None else None


def py_matches(locked: str, found_node: ast.AST, name: str) -> Optional[bool]:
    """True/False, or None when the locked text itself cannot be parsed (a contract bug, reported by the caller)."""
    lt = _locked_py_tree(locked)
    if lt is None:
        return None
    ln = _py_find(lt, name)
    if ln is None:
        return None
    if isinstance(ln, (ast.Assign, ast.AnnAssign)) or isinstance(found_node, (ast.Assign, ast.AnnAssign)):
        return _dump(ln) == _dump(found_node)
    return py_interface(ln) == py_interface(found_node)


# ------------------------------------------------------------------------------------------------ search
@dataclass
class SymbolStatus:
    symbol: str
    status: str   # found | missing | drift | unsupported
    path: str = ""
    line: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == FOUND

    def __str__(self) -> str:
        where = f" ({self.path}:{self.line})" if self.path else ""
        return f"{self.symbol}: {self.status}{where}" + (f" - {self.detail}" if self.detail else "")


def symbol_name(sym: "scip.Symbol") -> str:
    for d in reversed(sym.descriptors):
        if d.kind in ("type", "term", "method", "macro", "meta"):
            return d.name
    return sym.descriptors[-1].name if sym.descriptors else ""


def path_hints(sym: "scip.Symbol") -> List[str]:
    """Candidate relative files implied by the symbol's namespace descriptors (scip-typescript puts the file in one)."""
    ns = [d.name for d in sym.descriptors if d.kind == "namespace"]
    hints: List[str] = []
    if sym.scheme == "scip-python" and ns:
        mod = "/".join(ns[0].split("."))
        return [mod + ".py", mod + ".pyi", mod + "/__init__.py", mod + "/__init__.pyi"]
    for i, name in enumerate(ns):
        if "." in name:  # an escaped identifier like `types.ts` is a file name
            hints.append("/".join(ns[:i + 1]))
    if ns:
        base = "/".join(ns)
        hints.extend(base + ext for ext in (".ts", ".tsx", ".d.ts", ".py", ".pyi", ".js"))
        hints.extend(base + "/index" + ext for ext in (".ts", ".tsx", ".d.ts", ".js"))
    return hints


def locate(root, symbol_text: str, signature: str, candidates: Iterable[str]) -> SymbolStatus:
    """Find the declaration of `symbol_text` in `candidates` (relative paths under root; hinted files are tried first)
    and compare it with the locked `signature`."""
    root_p = Path(root)
    try:
        sym = scip.parse_symbol(symbol_text)
    except scip.SymbolError as exc:
        return SymbolStatus(symbol_text, UNSUPPORTED, detail=f"unparsable symbol: {exc}")
    name = symbol_name(sym)
    if not name:
        return SymbolStatus(symbol_text, UNSUPPORTED, detail="symbol has no name descriptor")
    sig_lang = signature_language(signature)
    ordered: List[str] = []
    seen = set()
    for h in path_hints(sym) + list(candidates):
        hn = h.replace("\\", "/")
        if hn not in seen:
            seen.add(hn)
            ordered.append(hn)
    hints = set(h.replace("\\", "/") for h in path_hints(sym))
    results: List[Tuple[bool, SymbolStatus]] = []  # (from a hinted file, status)
    searched = 0
    for rel in ordered:
        lang = language_of(rel)
        if not lang or (sig_lang and lang != sig_lang):
            continue
        p = root_p / rel
        try:
            if not p.is_file() or p.stat().st_size > 2_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        searched += 1
        hinted = rel in hints
        if lang == "ts":
            found = ts_declaration(text, name)
            if found is None:
                continue
            decl, kind, line = found
            ok = ts_matches(signature, decl, kind)
        else:
            pf = py_declaration(text, name)
            if pf is None:
                continue
            node, line = pf
            res = py_matches(signature, node, name)
            if res is None:
                return SymbolStatus(symbol_text, UNSUPPORTED, rel, line, "the locked signature is not parseable Python")
            ok = res
        results.append((hinted, SymbolStatus(symbol_text, FOUND if ok else DRIFT, rel, line,
                                             "" if ok else "declaration differs from the locked signature")))
    # A hinted file (named by the symbol itself) is canonical. Otherwise every declaration we can see must match:
    # a worker must not be able to hide drift behind an untouched copy of the stub.
    pool = [st for h, st in results if h] or [st for _h, st in results]
    drift = next((st for st in pool if st.status == DRIFT), None)
    if drift:
        return drift
    if pool:
        return pool[0]
    if not sig_lang:
        return SymbolStatus(symbol_text, UNSUPPORTED, detail="no declaration extractor for this signature's language (supported: TypeScript/JavaScript, Python)")
    return SymbolStatus(symbol_text, MISSING, detail=f"no declaration of {name!r} found in {searched} candidate file(s)")


def check_contract_symbols(doc, root, candidates: Iterable[str]) -> List[SymbolStatus]:
    """Status of every shared symbol of a contract ticket document."""
    cands = list(candidates)
    out = []
    for s in (doc.get("shared_contracts") or {}).get("scip_symbols", []):
        out.append(locate(root, s["symbol"], s["signature"], cands))
    return out


# ------------------------------------------------------------------------------------------------ AG1 stubs
def stub_path(ticket_id: str, symbol_text: str, signature: str) -> str:
    sym = scip.parse_symbol(symbol_text)
    name = symbol_name(sym)
    lang = signature_language(signature)
    ext = {"py": ".py", "ts": ".ts"}.get(lang, ".txt")
    safe_t = re.sub(r"[^A-Za-z0-9_.\-]", "_", ticket_id)
    safe_n = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)
    return f"contracts/{safe_t}/{safe_n}{ext}"


def materialize_stubs(doc, root) -> List[str]:
    """AG1: write each locked signature to `contracts/<ticket>/<Name>.<ext>` so implementers import from one place.
    Existing files are never overwritten (returns only the files created)."""
    tid = doc["ticket_meta"]["id"]
    made: List[str] = []
    for s in (doc.get("shared_contracts") or {}).get("scip_symbols", []):
        rel = stub_path(tid, s["symbol"], s["signature"])
        p = Path(root) / rel
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        header = "// Generated by AG1 (The Lawgiver) from the signed contract. Do not edit: the signature is locked.\n"
        if rel.endswith(".py"):
            header = "# Generated by AG1 (The Lawgiver) from the signed contract. Do not edit: the signature is locked.\n"
        atomic_write_text(str(p), header + s["signature"].rstrip() + "\n")
        made.append(rel)
    return made
