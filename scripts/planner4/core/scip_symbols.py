"""scip_symbols.py - parse, build and lock SCIP symbol strings (the "symbol locks" of contract-first tickets).

SCIP (Sourcegraph Code Intelligence Protocol) symbol grammar, from scip.proto:

    <symbol>       ::= <scheme> ' ' <package> ' ' (<descriptor>)+  |  'local ' <local-id>
    <package>      ::= <manager> ' ' <package-name> ' ' <version>
    <descriptor>   ::= <name>'/' namespace | <name>'#' type | <name>'.' term | <name>':' meta | <name>'!' macro
                     | <name>'(' [<disambiguator>] ').' method | '[' <name> ']' type-parameter | '(' <name> ')' parameter
    <name>         ::= simple identifier  [A-Za-z0-9_+\\-$]+   |   `escaped identifier` (a backtick is doubled)
    header parts may contain spaces, written as two spaces.  '.' stands for "empty" in manager/package/version.

Planner 4.0 uses the symbol as a stable NAME for a shared type/function that several workers must agree on, and a
sha256 of the contract text as its LOCK. A worker that edits the signature breaks the lock; the verifier detects it.

The 4.0 prototype example `scip-ts npm @planner/core 1.0.0 domain/escrow#SettlementCommand.` is grammatical but the
trailing `.` makes it a *term* (a value). An interface or type alias is a *type* and ends in `#`; `check_kind` warns.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

KNOWN_SCHEMES = {"scip-typescript", "scip-python", "scip-java", "scip-go", "scip-clang", "scip-ruby", "scip-dotnet",
                 "rust-analyzer", "scip-dart", "scip-ocaml", "scip-php", "semanticdb", "scip-ts"}
_SIMPLE = re.compile(r"[A-Za-z0-9_+\-$]+")
SUFFIX_KIND = {"/": "namespace", "#": "type", ".": "term", ":": "meta", "!": "macro"}


class SymbolError(ValueError):
    pass


@dataclass(frozen=True)
class Descriptor:
    name: str
    kind: str  # namespace | type | term | meta | macro | method | type-parameter | parameter
    disambiguator: str = ""


@dataclass(frozen=True)
class Symbol:
    scheme: str
    manager: str
    package: str
    version: str
    descriptors: Tuple[Descriptor, ...] = field(default_factory=tuple)
    local: Optional[str] = None

    @property
    def is_local(self) -> bool:
        return self.local is not None

    @property
    def last_kind(self) -> str:
        return self.descriptors[-1].kind if self.descriptors else ""


def _split_header(s: str, count: int) -> Tuple[List[str], str]:
    """Take `count` space-separated header tokens ('  ' is an escaped space) and return them plus the rest."""
    toks, cur, i = [], [], 0
    while i < len(s) and len(toks) < count:
        c = s[i]
        if c == " ":
            if i + 1 < len(s) and s[i + 1] == " ":
                cur.append(" ")
                i += 2
                continue
            toks.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if len(toks) < count:
        raise SymbolError(f"symbol header needs {count} space-separated parts (scheme manager package version)")
    return toks, s[i:]


def _read_name(s: str, i: int) -> Tuple[str, int]:
    if i >= len(s):
        raise SymbolError("unexpected end of descriptors")
    if s[i] == "`":
        j, buf = i + 1, []
        while j < len(s):
            if s[j] == "`":
                if j + 1 < len(s) and s[j + 1] == "`":
                    buf.append("`")
                    j += 2
                    continue
                if not buf:
                    raise SymbolError("empty escaped identifier")
                return "".join(buf), j + 1
            buf.append(s[j])
            j += 1
        raise SymbolError("unterminated backtick identifier")
    m = _SIMPLE.match(s, i)
    if not m:
        raise SymbolError(f"unexpected character {s[i]!r} at descriptor position {i}")
    return m.group(0), m.end()


def parse_descriptors(s: str) -> Tuple[Descriptor, ...]:
    out: List[Descriptor] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "[":
            j = s.find("]", i)
            if j <= i + 1:
                raise SymbolError("bad type-parameter descriptor")
            out.append(Descriptor(s[i + 1:j], "type-parameter"))
            i = j + 1
            continue
        if c == "(":
            j = s.find(")", i)
            if j <= i + 1:
                raise SymbolError("bad parameter descriptor")
            out.append(Descriptor(s[i + 1:j], "parameter"))
            i = j + 1
            continue
        name, i = _read_name(s, i)
        if i >= len(s):
            raise SymbolError(f"descriptor {name!r} has no suffix (/ # . : ! or ().)")
        c = s[i]
        if c in SUFFIX_KIND:
            out.append(Descriptor(name, SUFFIX_KIND[c]))
            i += 1
        elif c == "(":
            j = s.find(")", i)
            if j == -1 or s[j + 1:j + 2] != ".":
                raise SymbolError(f"method descriptor {name!r} must end with ').'")
            out.append(Descriptor(name, "method", s[i + 1:j]))
            i = j + 2
        else:
            raise SymbolError(f"descriptor {name!r} followed by {c!r}; expected one of / # . : ! (")
    if not out:
        raise SymbolError("symbol has no descriptors")
    return tuple(out)


def parse_symbol(text: str) -> Symbol:
    if not isinstance(text, str) or not text.strip():
        raise SymbolError("symbol must be a non-empty string")
    if text != text.strip() or "\n" in text or "\t" in text:
        raise SymbolError("symbol must be a single line without surrounding whitespace")
    if text.startswith("local "):
        lid = text[6:]
        if not _SIMPLE.fullmatch(lid):
            raise SymbolError("bad local symbol id")
        return Symbol("local", "", "", "", (), local=lid)
    (scheme, manager, package, version), rest = _split_header(text, 4)
    if not scheme or not rest:
        raise SymbolError("symbol is missing its scheme or descriptors")
    return Symbol(scheme, manager, package, version, parse_descriptors(rest))


def _esc_header(tok: str) -> str:
    return (tok or ".").replace(" ", "  ")


def _esc_name(name: str) -> str:
    return name if _SIMPLE.fullmatch(name) else "`" + name.replace("`", "``") + "`"


def build_symbol(scheme: str, manager: str, package: str, version: str, descriptors: List[Tuple[str, str]]) -> str:
    """descriptors: [(name, kind)], kind in namespace/type/term/meta/macro/method. Returns the symbol string."""
    rev = {v: k for k, v in SUFFIX_KIND.items()}
    parts = []
    for name, kind in descriptors:
        if kind == "method":
            parts.append(_esc_name(name) + "().")
        elif kind in rev:
            parts.append(_esc_name(name) + rev[kind])
        else:
            raise SymbolError(f"unknown descriptor kind {kind!r}")
    sym = " ".join([_esc_header(scheme), _esc_header(manager), _esc_header(package), _esc_header(version)]) + " " + "".join(parts)
    parse_symbol(sym)  # round-trip guard
    return sym


KIND_ALIAS = {"function": "method", "func": "method", "def": "method", "class": "type", "interface": "type", "enum": "type",
              "const": "term", "let": "term", "var": "term", "variable": "term", "value": "term"}


def python_symbol(package: str, version: str, file_path: str, name: str, kind: str = "type", manager: str = "python") -> str:
    """Symbol for a top-level Python definition, scip-python style: the module is one escaped dotted namespace,
    `scip-python python <pkg> <ver> `pkg.mod`/Name#` (class) or `.../func().` (function)."""
    segs = [s for s in file_path.replace("\\", "/").split("/") if s and s != "."]
    if not segs:
        raise SymbolError("file path required")
    last = segs[-1]
    for suf in (".pyi", ".py"):
        if last.endswith(suf):
            last = last[: -len(suf)]
            break
    segs = segs[:-1] + ([last] if last != "__init__" else [])
    if not segs:
        raise SymbolError("a top-level __init__.py has no module name")
    return build_symbol("scip-python", manager, package, version, [(".".join(segs), "namespace"), (name, KIND_ALIAS.get(kind, kind))])


def typescript_symbol(package: str, version: str, file_path: str, name: str, kind: str = "type", manager: str = "npm") -> str:
    """Symbol for a top-level export of a TypeScript file, in scip-typescript style:
    `scip-typescript npm <pkg> <ver> <dir>/<dir>/`<file.ts>`/<Name>#`  (file name is an escaped identifier)."""
    segs = [s for s in file_path.replace("\\", "/").split("/") if s and s != "."]
    if not segs:
        raise SymbolError("file path required")
    descs = [(d, "namespace") for d in segs[:-1]] + [(segs[-1], "namespace"), (name, KIND_ALIAS.get(kind, kind))]
    return build_symbol("scip-typescript", manager, package, version, descs)


def normalize_signature(text: str) -> str:
    """Whitespace-insensitive canonical text for locking: LF newlines, no trailing spaces, no trailing blank lines."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    while lines and lines[0] == "":
        lines.pop(0)
    return "\n".join(lines)


def signature_lock(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_signature(text).encode("utf-8")).hexdigest()


_DECL = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:abstract\s+)?(interface|type|class|enum|function|const|let|var|def)\b")
_TYPE_DECLS = {"interface", "type", "class", "enum"}


def check_kind(symbol: Symbol, signature: str) -> Optional[str]:
    """A warning string when the symbol's trailing descriptor disagrees with the declaration it locks."""
    m = _DECL.match(signature or "")
    if not m or symbol.is_local:
        return None
    decl = m.group(1)
    last = symbol.last_kind
    if decl in _TYPE_DECLS and last != "type":
        return f"signature declares a {decl} but the symbol ends in a {last or 'missing'} descriptor (types end in '#')"
    if decl in ("function", "def") and last not in ("method", "term"):
        return f"signature declares a function but the symbol ends in a {last} descriptor (functions end in '().' or '.')"
    if decl in ("const", "let", "var") and last != "term":
        return f"signature declares a value but the symbol ends in a {last} descriptor (values end in '.')"
    return None
