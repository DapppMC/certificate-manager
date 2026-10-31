"""miniyaml.py - a small, strict, standard-library YAML subset (parser + emitter) for Planner 4.0 tickets.

Why not PyYAML: Planner's hard rule is "Python standard library only". Why not the flat parser in tools/ticketc.py:
contract-first tickets (spec section 6) are nested (maps in maps, lists of maps, block literals for SCIP signatures).

Supported (everything the spec's ticket example needs, and nothing that can surprise):
  * block mappings and block sequences, 2+ space indentation, sequence at the same indent as its key
  * flow sequences / mappings on ONE line:  [a, "b c", 1]   {k: v}   []   {}
  * scalars: null (`null`, `~`, empty), true/false, integers, floats, plain strings,
             "double quoted" (JSON escapes) and 'single quoted' ('' escape)
  * block scalars  |  |-  |+  >  >-  >+
  * comments (`# ...`), one optional leading `---`, one optional trailing `...`
Rejected with a line-numbered YamlError instead of being guessed at: tabs for indentation, duplicate keys, anchors /
aliases / tags / merge keys / directives, multi-line flow collections, multiple documents, inconsistent indentation,
input over MAX_BYTES or nesting over MAX_DEPTH.

Typing rules are deliberately narrower than YAML 1.1: only `true`/`false`/`null`/`~` are special (so `yes`, `no`,
`on`, `off` stay strings), and integers with leading zeros (`007`) or versions (`1.0.0`) stay strings. The emitter
quotes any string that another YAML implementation might read differently, so files remain portable.

Guarantee (property-tested): loads(dumps(x)) == x for any JSON-like value built from dict[str, ...], list, str, int,
float (finite), bool and None.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, List, Optional, Tuple

MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 64


class YamlError(ValueError):
    def __init__(self, message: str, line: Optional[int] = None):
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


_INT_RE = re.compile(r"-?(0|[1-9][0-9]*)")
_FLOAT_RE = re.compile(r"-?(0|[1-9][0-9]*)\.[0-9]+([eE][+-]?[0-9]+)?")
_PLAIN_KEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./@\-]*")
_AMBIGUOUS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "~"}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u0085\u2028\u2029]")


# ---------------------------------------------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------------------------------------------


def _typed_plain(s: str) -> Any:
    if s in ("", "~", "null"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if _INT_RE.fullmatch(s):
        return int(s)
    if _FLOAT_RE.fullmatch(s):
        return float(s)
    return s


def _find_closing_double(s: str, start: int) -> int:
    i = start + 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i
        i += 1
    return -1


def _find_closing_single(s: str, start: int) -> int:
    i = start + 1
    while i < len(s):
        if s[i] == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                i += 2
                continue
            return i
        i += 1
    return -1


def _decode_double(tok: str, line: Optional[int]) -> str:
    try:
        val = json.loads(tok)
    except ValueError as e:
        raise YamlError(f"bad double-quoted string {tok[:40]!r} (only JSON escapes are supported)", line) from e
    return val


def _decode_single(tok: str) -> str:
    return tok[1:-1].replace("''", "'")


def strip_comment(s: str) -> str:
    """Remove a trailing ` # comment` that is not inside quotes. A `#` glued to text (`a#b`) is kept."""
    out_end = len(s)
    i = 0
    prev_sig = ""  # previous non-space character (decides whether a quote opens a string)
    while i < len(s):
        c = s[i]
        if c == "#" and (i == 0 or s[i - 1] in " \t"):
            out_end = i
            break
        if c in "\"'" and (prev_sig == "" or prev_sig in ":-[{,"):
            close = _find_closing_double(s, i) if c == '"' else _find_closing_single(s, i)
            if close != -1:
                i = close + 1
                prev_sig = c
                continue
        if c not in " \t":
            prev_sig = c
        i += 1
    return s[:out_end].rstrip()


_BAD_START = ("&", "*", "!", "%", "@", "`")


def _check_plain_start(s: str, line: Optional[int]) -> None:
    if s.startswith(_BAD_START):
        raise YamlError(f"unsupported YAML construct starting with {s[0]!r}: anchors, aliases, tags and directives are not supported (quote the value if it is text)", line)
    if s.startswith("<<"):
        raise YamlError("merge keys (<<) are not supported", line)
    if s.startswith("? "):
        raise YamlError("complex keys are not supported", line)


# ---------------------------------------------------------------------------------------------------------------
# Flow collections (single line)
# ---------------------------------------------------------------------------------------------------------------


class _Flow:
    def __init__(self, s: str, line: Optional[int]):
        self.s, self.i, self.line = s, 0, line

    def err(self, msg):
        return YamlError(f"{msg} in flow collection {self.s[:60]!r}", self.line)

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def parse(self, depth=0):
        if depth > MAX_DEPTH:
            raise self.err("nesting too deep")
        self.ws()
        if self.i >= len(self.s):
            raise self.err("unexpected end")
        c = self.s[self.i]
        if c == "[":
            self.i += 1
            items = []
            self.ws()
            if self.s[self.i:self.i + 1] == "]":
                self.i += 1
                return items
            while True:
                items.append(self.parse(depth + 1))
                self.ws()
                c2 = self.s[self.i:self.i + 1]
                if c2 == ",":
                    self.i += 1
                    self.ws()
                    if self.s[self.i:self.i + 1] == "]":  # trailing comma
                        self.i += 1
                        return items
                    continue
                if c2 == "]":
                    self.i += 1
                    return items
                raise self.err("expected ',' or ']'")
        if c == "{":
            self.i += 1
            d = {}
            self.ws()
            if self.s[self.i:self.i + 1] == "}":
                self.i += 1
                return d
            while True:
                self.ws()
                key = self.key()
                self.ws()
                if self.s[self.i:self.i + 1] != ":":
                    raise self.err("expected ':' after key")
                self.i += 1
                val = self.parse(depth + 1)
                if key in d:
                    raise YamlError(f"duplicate key {key!r}", self.line)
                d[key] = val
                self.ws()
                c2 = self.s[self.i:self.i + 1]
                if c2 == ",":
                    self.i += 1
                    self.ws()
                    if self.s[self.i:self.i + 1] == "}":
                        self.i += 1
                        return d
                    continue
                if c2 == "}":
                    self.i += 1
                    return d
                raise self.err("expected ',' or '}'")
        return self.scalar()

    def quoted(self):
        c = self.s[self.i]
        close = _find_closing_double(self.s, self.i) if c == '"' else _find_closing_single(self.s, self.i)
        if close == -1:
            raise self.err("unterminated string")
        tok = self.s[self.i:close + 1]
        self.i = close + 1
        return _decode_double(tok, self.line) if c == '"' else _decode_single(tok)

    def key(self):
        if self.s[self.i:self.i + 1] in ('"', "'"):
            return self.quoted()
        j = self.i
        while j < len(self.s) and self.s[j] not in ":,]}[{":
            j += 1
        k = self.s[self.i:j].strip()
        if not k:
            raise self.err("empty key")
        _check_plain_start(k, self.line)
        self.i = j
        return k

    def scalar(self):
        self.ws()
        if self.s[self.i:self.i + 1] in ('"', "'"):
            return self.quoted()
        j = self.i
        while j < len(self.s) and self.s[j] not in ",]}[{":
            j += 1
        tok = self.s[self.i:j].strip()
        if self.s[j:j + 1] in ("[", "{"):
            raise self.err("unexpected bracket")
        _check_plain_start(tok, self.line)
        self.i = j
        return _typed_plain(tok)


def _parse_flow(s: str, line: Optional[int]) -> Any:
    f = _Flow(s, line)
    v = f.parse()
    f.ws()
    if f.i != len(s):
        raise YamlError(f"unexpected text after flow collection: {s[f.i:f.i + 30]!r}", line)
    return v


def _parse_inline(s: str, line: Optional[int]) -> Any:
    s = s.strip()
    if s.startswith(("[", "{")):
        return _parse_flow(s, line)
    if s.startswith('"'):
        close = _find_closing_double(s, 0)
        if close == -1:
            raise YamlError("unterminated double-quoted string (multi-line quoted strings are not supported)", line)
        if s[close + 1:].strip():
            raise YamlError(f"unexpected text after quoted string: {s[close + 1:][:30]!r}", line)
        return _decode_double(s[:close + 1], line)
    if s.startswith("'"):
        close = _find_closing_single(s, 0)
        if close == -1:
            raise YamlError("unterminated single-quoted string", line)
        if s[close + 1:].strip():
            raise YamlError(f"unexpected text after quoted string: {s[close + 1:][:30]!r}", line)
        return _decode_single(s[:close + 1])
    _check_plain_start(s, line)
    return _typed_plain(s)


# ---------------------------------------------------------------------------------------------------------------
# Block structure
# ---------------------------------------------------------------------------------------------------------------


class _Line:
    __slots__ = ("no", "indent", "text", "raw", "tab")

    def __init__(self, no: int, indent: int, text: str, raw: str, tab: bool = False):
        self.no, self.indent, self.text, self.raw, self.tab = no, indent, text, raw, tab


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _split_key(text: str, line: int) -> Optional[Tuple[str, str]]:
    """Return (key, rest) if `text` starts with `key:`; None if it is not a mapping entry."""
    if not text or text[0] in "[{":
        return None
    if text[0] in "\"'":
        close = _find_closing_double(text, 0) if text[0] == '"' else _find_closing_single(text, 0)
        if close == -1:
            return None
        after = text[close + 1:]
        if after.startswith(":") and (len(after) == 1 or after[1] in " \t"):
            tok = text[:close + 1]
            key = _decode_double(tok, line) if text[0] == '"' else _decode_single(tok)
            return key, after[1:].strip()
        return None
    m = re.search(r":(\s|$)", text)
    if not m:
        return None
    key = text[:m.start()].strip()
    if not key:
        return None
    _check_plain_start(key, line)
    return key, text[m.end():].strip()


class _Parser:
    def __init__(self, text: str):
        if len(text.encode("utf-8", "surrogatepass")) > MAX_BYTES:
            raise YamlError(f"input larger than {MAX_BYTES} bytes")
        text = text.lstrip("﻿")
        raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines: List[_Line] = []
        started = False
        ended = False
        for no, raw in enumerate(raw_lines, 1):
            stripped = raw.strip()
            if raw.startswith("%"):
                raise YamlError("YAML directives are not supported", no)
            if raw.rstrip() == "---" and not started:
                started = True
                continue
            if raw.rstrip() == "---":
                raise YamlError("multiple documents are not supported", no)
            if raw.rstrip() == "...":
                ended = True
                continue
            if ended and stripped:
                raise YamlError("content after document end marker '...'", no)
            lead = raw[:len(raw) - len(raw.lstrip(" \t"))]
            started = True
            # a tab is only an error where the line is read as structure; inside a block scalar it is content
            self.lines.append(_Line(no, len(lead), strip_comment(raw.strip()) if stripped else "", raw, "\t" in lead and bool(stripped)))
        self.pos = 0

    # -- helpers
    def peek(self) -> Optional[_Line]:
        while self.pos < len(self.lines) and not self.lines[self.pos].text:
            self.pos += 1
        if self.pos < len(self.lines):
            ln = self.lines[self.pos]
            if ln.tab:
                raise YamlError("tabs are not allowed for indentation", ln.no)
            return ln
        return None

    def advance(self):
        self.pos += 1

    def parse_document(self) -> Any:
        ln = self.peek()
        if ln is None:
            return None
        value = self.parse_block(ln.indent, 0)
        extra = self.peek()
        if extra is not None:
            raise YamlError(f"unexpected content (bad indentation?): {extra.text[:40]!r}", extra.no)
        return value

    def parse_block(self, indent: int, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise YamlError("nesting too deep", self.peek().no if self.peek() else None)
        ln = self.peek()
        if _is_item(ln.text):
            return self.parse_list(indent, depth)
        if _split_key(ln.text, ln.no) is not None:
            return self.parse_map(indent, depth)
        # top-level / nested bare scalar or flow value
        if ln.indent != indent:
            raise YamlError("bad indentation", ln.no)
        self.advance()
        if ln.text[0] in "|>" and re.match(r"^[|>][+-]?$", ln.text):
            return self.block_scalar(ln.text, indent - 1, ln.no)
        return _parse_inline(ln.text, ln.no)

    def parse_map(self, indent: int, depth: int) -> dict:
        result: dict = {}
        while True:
            ln = self.peek()
            if ln is None or ln.indent < indent:
                break
            if ln.indent > indent:
                raise YamlError(f"unexpected indentation (expected {indent} spaces, found {ln.indent})", ln.no)
            if _is_item(ln.text):
                break
            kv = _split_key(ln.text, ln.no)
            if kv is None:
                raise YamlError(f"expected 'key: value', found {ln.text[:40]!r}", ln.no)
            key, rest = kv
            if key in result:
                raise YamlError(f"duplicate key {key!r}", ln.no)
            self.advance()
            result[key] = self.parse_value(rest, ln, indent, depth)
        return result

    def parse_value(self, rest: str, ln: _Line, parent_indent: int, depth: int) -> Any:
        if rest == "":
            nxt = self.peek()
            if nxt is not None and nxt.indent > parent_indent:
                return self.parse_block(nxt.indent, depth + 1)
            if nxt is not None and nxt.indent == parent_indent and _is_item(nxt.text):
                return self.parse_list(parent_indent, depth + 1)
            return None
        if re.match(r"^[|>][+-]?$", rest):
            return self.block_scalar(rest, parent_indent, ln.no)
        return _parse_inline(rest, ln.no)

    def parse_list(self, indent: int, depth: int) -> list:
        items: list = []
        while True:
            ln = self.peek()
            if ln is None or ln.indent < indent:
                break
            if ln.indent > indent:
                raise YamlError(f"unexpected indentation in list (expected {indent}, found {ln.indent})", ln.no)
            if not _is_item(ln.text):
                break
            after = ln.text[1:]
            rest = after.lstrip(" ")
            content_indent = ln.indent + 1 + (len(after) - len(rest))
            self.advance()
            if rest == "":
                nxt = self.peek()
                items.append(self.parse_block(nxt.indent, depth + 1) if nxt is not None and nxt.indent > indent else None)
            elif _is_item(rest):
                self.lines.insert(self.pos, _Line(ln.no, content_indent, rest, " " * content_indent + rest))
                items.append(self.parse_list(content_indent, depth + 1))
            elif _split_key(rest, ln.no) is not None and not rest.startswith(("[", "{")):
                self.lines.insert(self.pos, _Line(ln.no, content_indent, rest, " " * content_indent + rest))
                items.append(self.parse_map(content_indent, depth + 1))
            elif re.match(r"^[|>][+-]?$", rest):
                items.append(self.block_scalar(rest, indent, ln.no))
            else:
                items.append(_parse_inline(rest, ln.no))
        return items

    def block_scalar(self, header: str, parent_indent: int, line_no: int) -> str:
        style, chomp = header[0], header[1:]
        collected: List[str] = []
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            if ln.raw.strip() == "":
                collected.append("")
                self.pos += 1
                continue
            if ln.indent <= parent_indent:
                break
            collected.append(ln.raw)
            self.pos += 1
        trailing_blank = 0
        while collected and collected[-1] == "":
            collected.pop()
            trailing_blank += 1
        if not collected:
            return "\n" * trailing_blank if chomp == "+" else ""
        block_indent = min(len(r) - len(r.lstrip(" ")) for r in collected if r != "")
        body = [r[block_indent:] if r != "" else "" for r in collected]
        if style == "|":
            text = "\n".join(body)
        else:  # folded: single newlines become spaces, blank lines and more-indented lines keep their breaks
            out: List[str] = []
            buf: List[str] = []
            for line in body:
                if line == "" or line.startswith(" "):
                    if buf:
                        out.append(" ".join(buf))
                        buf = []
                    out.append(line)
                else:
                    buf.append(line)
            if buf:
                out.append(" ".join(buf))
            text = "\n".join(out)
        if chomp == "-":
            return text
        if chomp == "+":
            return text + "\n" * (1 + trailing_blank)
        return text + "\n"


def loads(text: str) -> Any:
    if not isinstance(text, str):
        raise TypeError("loads() takes str")
    return _Parser(text).parse_document()


def load_file(path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())


# ---------------------------------------------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------------------------------------------


def _plain_string_ok(s: str) -> bool:
    if s == "" or s != s.strip() or s.lower() in _AMBIGUOUS:
        return False
    if _CONTROL_RE.search(s) or "\n" in s or "\t" in s:
        return False
    if not isinstance(_typed_plain(s), str):
        return False
    if s[0] in "?:,[]{}#&*!|>'\"%@`":
        return False
    if s[0] == "-" and (len(s) == 1 or not s[1].isalnum()):
        return False
    if s.startswith(("<<", "---", "...")):
        return False
    if ": " in s or " #" in s or s.endswith(":") or ":\t" in s:
        return False
    return True


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise ValueError("NaN / Infinity cannot be represented")
        r = repr(v)
        if "e" in r.lower() and "." not in r:  # 1e+20 -> 1.0e+20 so it reloads as a float
            m, e = r.lower().split("e")
            r = f"{m}.0e{e}"
        return r
    if isinstance(v, str):
        return v if _plain_string_ok(v) else json.dumps(v, ensure_ascii=False)
    raise TypeError(f"cannot dump {type(v).__name__}")


def _key(k: Any) -> str:
    if not isinstance(k, str):
        raise TypeError(f"mapping keys must be str, got {type(k).__name__}")
    if _PLAIN_KEY_RE.fullmatch(k) and k.lower() not in _AMBIGUOUS and isinstance(_typed_plain(k), str):
        return k
    return json.dumps(k, ensure_ascii=False)


def _block_header(s: str) -> Optional[str]:
    """'|' or '|-' if `s` round-trips as a literal block scalar, else None (the caller then double-quotes it)."""
    if "\n" not in s or "\r" in s or _CONTROL_RE.search(s):
        return None
    body = s[:-1] if s.endswith("\n") else s
    if body == "" or body.endswith("\n") or body.startswith(("\n", " ")):
        return None
    if any(line.strip() == "" and line != "" for line in body.split("\n")):
        return None  # whitespace-only lines would be read back as empty
    return "|" if s.endswith("\n") else "|-"


def _emit(obj: Any, indent: int, out: List[str], depth: int) -> None:
    """Lines for a NON-EMPTY dict or list whose entries start at column `indent`."""
    if depth > MAX_DEPTH:
        raise ValueError("nesting too deep to dump")
    pad = " " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            _emit_entry(pad, _key(k), v, indent, out, depth)
    else:
        for v in obj:
            _emit_item(pad, v, indent, out, depth)


def _emit_block(v: str, header: str, col: int, out: List[str]) -> None:
    for line in (v[:-1] if v.endswith("\n") else v).split("\n"):
        out.append((" " * (col + 2) + line) if line else "")


def _emit_entry(lead: str, key: str, v: Any, col: int, out: List[str], depth: int) -> None:
    if isinstance(v, dict) and v:
        out.append(f"{lead}{key}:")
        _emit(v, col + 2, out, depth + 1)
    elif isinstance(v, (list, tuple)) and v:
        out.append(f"{lead}{key}:")
        _emit(list(v), col + 2, out, depth + 1)
    elif isinstance(v, dict):
        out.append(f"{lead}{key}: {{}}")
    elif isinstance(v, (list, tuple)):
        out.append(f"{lead}{key}: []")
    elif isinstance(v, str) and _block_header(v):
        h = _block_header(v)
        out.append(f"{lead}{key}: {h}")
        _emit_block(v, h, col, out)
    else:
        out.append(f"{lead}{key}: {_scalar(v)}")


def _emit_item(lead: str, v: Any, col: int, out: List[str], depth: int) -> None:
    if isinstance(v, (dict, list, tuple)) and v:
        sub: List[str] = []
        _emit(v if isinstance(v, dict) else list(v), col + 2, sub, depth + 1)
        sub[0] = f"{lead}- " + sub[0][col + 2:]
        out.extend(sub)
    elif isinstance(v, dict):
        out.append(f"{lead}- {{}}")
    elif isinstance(v, (list, tuple)):
        out.append(f"{lead}- []")
    elif isinstance(v, str) and _block_header(v):
        h = _block_header(v)
        out.append(f"{lead}- {h}")
        _emit_block(v, h, col, out)
    else:
        out.append(f"{lead}- {_scalar(v)}")


def dumps(obj: Any, *, sort_keys: bool = False) -> str:
    """Serialise JSON-like data (dict[str, ...], list, str, int, float, bool, None). Insertion order is kept
    unless sort_keys=True."""
    if sort_keys:
        obj = _sorted(obj)
    out: List[str] = []
    if isinstance(obj, (dict, list, tuple)) and obj:
        _emit(obj if isinstance(obj, dict) else list(obj), 0, out, 0)
    elif isinstance(obj, dict):
        out.append("{}")
    elif isinstance(obj, (list, tuple)):
        out.append("[]")
    else:
        out.append(_scalar(obj))
    return "\n".join(out) + "\n"


def _sorted(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _sorted(o[k]) for k in sorted(o)}
    if isinstance(o, (list, tuple)):
        return [_sorted(x) for x in o]
    return o


def dump_file(path, obj: Any, **kw) -> None:
    from core.platform_compat import atomic_write_text  # local import keeps this module standalone
    atomic_write_text(path, dumps(obj, **kw))
