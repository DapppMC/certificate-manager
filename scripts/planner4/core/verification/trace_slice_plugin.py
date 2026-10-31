"""trace_slice_plugin.py - zero-hallucination failure context for a worker, in <= ~400 tokens.

When a test fails, an LLM wastes thousands of tokens re-reading files and guessing variable values (or burns 20
interactive-debugger turns, the debug-gym cost). This pytest plugin captures, at the crashing frames, ONLY the local
variables that the failing source line actually mentions, redacts secrets, and writes one valid JSON line.

Use:  python -m pytest -p core.verification.trace_slice_plugin [--trace-slice-out PATH] [--trace-slice-budget TOKENS]
Env:  TRACE_SLICE_OUT, TRACE_BUDGET (tokens; legacy TRACE_BUDGET>=1000 is read as characters), TRACE_FRAMES, TRACE_ROOT

Hardening over the prototype (evidence in docs/ENGINE_AUDIT.md):
  * ALWAYS valid JSON: the prototype cut the serialised string at the budget (`raw[:BUDGET]`) which produced broken
    JSONL exactly when the failure was big. Here the PAYLOAD is shrunk step by step until the line fits.
  * No arbitrary code execution while reporting: user-defined `__repr__` is never called (it may raise, hang or
    leak); objects are summarised from their instance dict, numpy-like objects from shape/dtype.
  * Redaction: secret-looking NAMES (password, token...) and secret-looking VALUES (AWS/GitHub/Slack/JWT/PEM keys,
    long high-entropy strings) are replaced before anything is written.
  * Concurrency: one file per process when running under pytest-xdist (small appends are not atomic across
    platforms), single O_APPEND write per record otherwise; `read_slices()` merges and dedupes by signature.
  * Windows: case-insensitive "is this my code" check, setup-time (fixture) errors captured too, settings are per
    session (no import-time globals) so tests and nested sessions do not interfere.

Standard library only (+ pytest at runtime). Python 3.10+.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import fractions
import glob
import hashlib
import json
import keyword
import linecache
import math
import os
import pathlib
import re
import sys
import uuid
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

try:
    import pytest
except ImportError:  # the pure helpers stay importable without pytest
    pytest = None  # type: ignore[assignment]

def _load_is_secret_name():
    """`core.platform_compat` normally; by file path when a project under test ships its own `core` package (the plugin
    runs inside the pytest process of the worker's tree, where `import core` may resolve to the worker's code)."""
    try:
        from core.platform_compat import is_secret_name as fn
        return fn
    except ImportError:
        import importlib.util
        path = pathlib.Path(__file__).resolve().parents[1] / "platform_compat.py"
        spec = importlib.util.spec_from_file_location("_planner_platform_compat", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod.is_secret_name


is_secret_name = _load_is_secret_name()

TOKEN_CHARS = 4  # chars per token used for the budget (same heuristic the spec uses: 1600 chars ~ 400 tokens)
DEFAULT_TOKENS = 400
SKIP_DIRS = {"site-packages", "dist-packages", ".venv", "venv", "node_modules", "_pytest", "pluggy", "lib64"}
MAX_FILE_BYTES = 2_000_000
_NAME = re.compile(r"[A-Za-z_]\w*")

_SECRET_VALUE = re.compile(
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|sk-[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{30,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:bearer)\s+[A-Za-z0-9._\-]{20,}|(?i:password|passwd|secret|token|apikey|api_key)\s*[=:]\s*\S{4,}")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")


def estimate_tokens(text: str) -> int:
    """Cheap, conservative token estimate: 1 token ~ 4 ASCII chars; non-ASCII characters count double."""
    ascii_len = sum(1 for c in text if ord(c) < 128)
    return -(-(ascii_len + 2 * (len(text) - ascii_len)) // TOKEN_CHARS)


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _looks_secret_value(value: str) -> bool:
    if _SECRET_VALUE.search(value):
        return True
    return any(_entropy(m.group(0)) > 3.6 and not re.fullmatch(r"[0-9a-fA-F]+", m.group(0)) for m in _LONG_TOKEN.finditer(value))


def scrub_known_secrets(text: str, secrets: Iterable[str], gram: int = 10) -> str:
    """Remove every run of >= `gram` characters that also occurs inside a known secret value. pytest's own assertion
    explanation shortens long strings with '...', which can split a secret in two halves that no regex recognises."""
    mark = [False] * len(text)
    for sec in secrets:
        if len(sec) < gram:
            continue
        grams = {sec[i:i + gram] for i in range(len(sec) - gram + 1)}
        for i in range(len(text) - gram + 1):
            if text[i:i + gram] in grams:
                for j in range(i, i + gram):
                    mark[j] = True
    if not any(mark):
        return text
    out, i = [], 0
    while i < len(text):
        if mark[i]:
            while i < len(text) and mark[i]:
                i += 1
            out.append("<redacted>")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def redact(text: str) -> str:
    text = _SECRET_VALUE.sub("<redacted>", text)

    def _maybe(m):
        tok = m.group(0)
        if re.fullmatch(r"[0-9a-fA-F]+", tok):  # git shas / md5 / sha256 are useful and not secret
            return tok
        if "/" in tok and "." not in tok and tok.count("/") > 2 and _entropy(tok) < 4.2:
            return tok
        return "<redacted>" if _entropy(tok) > 3.6 else tok

    return _LONG_TOKEN.sub(_maybe, text)


# ------------------------------------------------------------------------------------------ safe value formatting
_SAFE_SCALARS = (int, float, bool, type(None), complex)
_SAFE_STDLIB = (_dt.date, _dt.datetime, _dt.time, _dt.timedelta, decimal.Decimal, fractions.Fraction, uuid.UUID, pathlib.PurePath)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def fmt_value(v: Any, *, width: int = 40, depth: int = 0, max_depth: int = 2, items: int = 4) -> str:
    """Summarise `v` WITHOUT calling any user-defined __repr__ / __str__ / properties (best effort, never raises)."""
    try:
        t = type(v)
        if t in _SAFE_SCALARS:
            return _clip(repr(v), width)
        if t is str:
            return _clip(repr(v), width)
        if t in (bytes, bytearray):
            return f"{t.__name__}[{len(v)}] {_clip(repr(bytes(v[:12])), width)}"
        if isinstance(v, _SAFE_STDLIB) and t.__module__ in ("datetime", "decimal", "fractions", "uuid", "pathlib", "pathlib._local", "builtins"):
            return _clip(repr(v), width)
        if t in (list, tuple, set, frozenset, dict):
            if depth >= max_depth:
                return f"{t.__name__}[{len(v)}]…"
            if t is dict:
                parts = [f"{fmt_value(k, width=16, depth=depth + 1, max_depth=max_depth)}: {fmt_value(x, width=width, depth=depth + 1, max_depth=max_depth)}"
                         for k, x in list(v.items())[:items]]
                more = ", …" if len(v) > items else ""
                return _clip(f"dict[{len(v)}]{{{', '.join(parts)}{more}}}", width * 3)
            seq = list(v)[:items] if t in (list, tuple) else sorted(list(v), key=lambda x: str(type(x)) + str(id(x)))[:items]
            parts = [fmt_value(x, width=width, depth=depth + 1, max_depth=max_depth) for x in seq]
            more = ", …" if len(v) > items else ""
            return _clip(f"{t.__name__}[{len(v)}][{', '.join(parts)}{more}]", width * 3)
        # numpy / pandas / torch style: shape + dtype, read from the class descriptor-free path
        shape = getattr(v, "shape", None)
        dtype = getattr(v, "dtype", None)
        if shape is not None and dtype is not None:
            return f"{t.__name__} shape={tuple(shape) if isinstance(shape, (tuple, list)) else '?'} dtype={dtype}"
        import enum
        if isinstance(v, enum.Enum):
            return f"{t.__name__}.{v.name}"
        d = getattr(v, "__dict__", None)
        if isinstance(d, dict) and depth < max_depth:
            keys = [k for k in d if not str(k).startswith("_")][:items]
            parts = []
            for k in keys:
                val = "<redacted>" if is_secret_name(str(k)) else fmt_value(d[k], width=24, depth=depth + 1, max_depth=max_depth)
                parts.append(f"{k}={val}")
            return _clip(f"{t.__name__}({', '.join(parts)}{', …' if len(d) > len(keys) else ''})", width * 3)
        slots = getattr(t, "__slots__", None)
        if slots:
            return f"{t.__name__}(slots)"
        return f"<{t.__name__}>"
    except Exception:
        return "<unprintable>"


# ------------------------------------------------------------------------------------------ settings
class Settings:
    def __init__(self, out: str, root: str, tokens: int, frames: int):
        self.out = out
        self.root = root
        self.tokens = tokens
        self.frames = frames

    @property
    def budget_chars(self) -> int:
        return self.tokens * TOKEN_CHARS

    @classmethod
    def from_config(cls, config) -> "Settings":
        root = os.environ.get("TRACE_ROOT") or str(getattr(config, "rootpath", None) or os.getcwd())
        out = config.getoption("trace_slice_out", None) or os.environ.get("TRACE_SLICE_OUT") or os.path.join(root, ".planner", "trace_slice.jsonl")
        tokens = config.getoption("trace_slice_budget", None)
        if tokens is None:
            raw = int(os.environ.get("TRACE_BUDGET", DEFAULT_TOKENS))
            tokens = raw // TOKEN_CHARS if raw >= 1000 else raw  # legacy: characters
        frames = config.getoption("trace_slice_frames", None) or int(os.environ.get("TRACE_FRAMES", "3"))
        return cls(os.path.abspath(out), os.path.abspath(root), max(60, int(tokens)), max(1, int(frames)))


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def is_own_code(path: str, root: str) -> bool:
    """Is `path` inside the project root and not third-party / stdlib? Case-insensitive on Windows."""
    try:
        if not path or path.startswith("<"):
            return False
        rp, rr = _norm(path), _norm(root)
        if os.path.commonpath([rp, rr]) != rr:
            return False
        return not any(part in SKIP_DIRS for part in pathlib.PurePath(path).parts)
    except (ValueError, OSError):
        return False


def names_in(line: str) -> set:
    return {m.group(0) for m in _NAME.finditer(line)} - set(keyword.kwlist) - {"self", "cls"}


# ------------------------------------------------------------------------------------------ slice construction
def build_slice(nodeid: str, exc_type: type, exc_value: BaseException, tb, root: str, *, frames: int = 3, phase: str = "call") -> Optional[dict]:
    chain = []
    while tb is not None:
        chain.append((tb.tb_frame, tb.tb_lineno))
        tb = tb.tb_next
    own = [(f, ln) for f, ln in chain if is_own_code(f.f_code.co_filename, root)][-frames:]
    if not own:
        return None
    last_f, last_ln = own[-1]
    sig_src = f"{nodeid}:{exc_type.__name__}:{os.path.relpath(last_f.f_code.co_filename, root).replace(os.sep, '/')}:{last_ln}"
    sig = hashlib.sha1(sig_src.encode()).hexdigest()[:12]
    out_frames = []
    secret_values = set()
    for f, ln in own:
        fn = os.path.relpath(f.f_code.co_filename, root).replace("\\", "/")
        src = linecache.getline(f.f_code.co_filename, ln).strip()
        wanted = names_in(src)
        locs = {}
        try:
            snapshot = dict(f.f_locals)
        except Exception:
            snapshot = {}
        for name, val in snapshot.items():
            if isinstance(val, str) and len(val) >= 10 and (is_secret_name(str(name)) or _looks_secret_value(val)):
                secret_values.add(val)
        for k in sorted(wanted):
            if k in snapshot:
                locs[k] = "<redacted>" if is_secret_name(k) else fmt_value(snapshot[k])
        out_frames.append({"file": fn, "line": ln, "func": f.f_code.co_name, "src": src, "locals": locs})
    try:
        msg = " ".join(str(exc_value).split())
    except Exception:
        msg = "<unprintable exception>"
    msg = scrub_known_secrets(msg, secret_values)
    return {"sig": sig, "test": nodeid, "phase": phase, "error": f"{exc_type.__name__}: {msg[:160]}", "frames": out_frames}


def _dump(p: dict) -> str:
    return redact(json.dumps(p, ensure_ascii=False, separators=(",", ":")))


def fit_budget(payload: dict, tokens: int = DEFAULT_TOKENS) -> str:
    """Return ONE valid JSON line with estimate_tokens(line) <= tokens, shrinking the payload step by step."""
    p = json.loads(json.dumps(payload))
    limit = tokens

    def ok(line: str) -> bool:
        return estimate_tokens(line) <= limit

    line = _dump(p)
    if ok(line):
        return line
    p["trunc"] = 1
    for fr in p["frames"]:
        fr["src"] = _clip(fr["src"], 100)
        fr["locals"] = {k: _clip(v, 32) for k, v in fr["locals"].items()}
    line = _dump(p)
    if ok(line):
        return line
    for max_locals in (6, 4, 3, 2, 1):
        for fr in p["frames"]:
            fr["locals"] = dict(list(fr["locals"].items())[:max_locals])
        line = _dump(p)
        if ok(line):
            return line
        if len(p["frames"]) > 1:
            pass
    while len(p["frames"]) > 1:
        p["frames"].pop(0)  # keep the frame closest to the crash
        line = _dump(p)
        if ok(line):
            return line
    p["error"] = _clip(p["error"], 90)
    for fr in p["frames"]:
        fr["src"] = _clip(fr["src"], 60)
        fr["locals"] = {k: _clip(v, 20) for k, v in list(fr["locals"].items())[:2]}
    line = _dump(p)
    if ok(line):
        return line
    p["test"] = _clip(p["test"], 70)
    p["error"] = _clip(p["error"], 60)
    for fr in p["frames"]:
        fr["locals"] = {}
        fr["src"] = _clip(fr["src"], 40)
    line = _dump(p)
    if ok(line):
        return line
    minimal = {"sig": p["sig"], "trunc": 1, "test": _clip(p["test"], 50), "error": _clip(p["error"], 40),
               "frames": [{"file": p["frames"][-1]["file"][-40:], "line": p["frames"][-1]["line"]}]}
    return _dump(minimal)


def append_line(path: str, line: str, max_bytes: int = 2_000_000) -> bool:
    """Single O_APPEND write per record. Returns False when the file is full."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        if os.path.getsize(path) > max_bytes:
            return False
    except OSError:
        pass
    data = (line + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return True


# ------------------------------------------------------------------------------------------ reading side
def read_slices(path: str) -> List[dict]:
    """Read one JSONL file or every `<stem>*.jsonl` sibling (per-process files), skipping corrupt lines, deduping by sig."""
    p = pathlib.Path(path)
    files = sorted(glob.glob(str(p.with_suffix("")) + "*.jsonl")) or ([str(p)] if p.exists() else [])
    seen, out = set(), []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("sig") not in seen:
                        seen.add(rec.get("sig"))
                        out.append(rec)
        except OSError:
            continue
    return out


def render_for_prompt(slices: Iterable[dict], max_tokens: int = DEFAULT_TOKENS) -> str:
    """Compact text for the worker's next prompt: the newest failures first, hard-capped at `max_tokens`."""
    lines = []
    used = 0
    for rec in reversed(list(slices)):
        fr = rec.get("frames", [])[-1:] or [{}]
        f = fr[0]
        locs = ", ".join(f"{k}={v}" for k, v in (f.get("locals") or {}).items())
        line = f"{rec.get('test', '?')} -> {rec.get('error', '?')} @ {f.get('file', '?')}:{f.get('line', '?')} `{f.get('src', '')}` [{locs}]"
        t = estimate_tokens(line)
        if used + t > max_tokens:
            if not lines:
                lines.append(_clip(line, max_tokens * TOKEN_CHARS))
            break
        lines.append(line)
        used += t
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ pytest wiring
class _Recorder:
    def __init__(self, settings: Settings):
        self.s = settings
        self.seen: set = set()
        self.written = 0

    def _target(self) -> str:
        worker = os.environ.get("PYTEST_XDIST_WORKER")
        if worker:
            base, ext = os.path.splitext(self.s.out)
            return f"{base}.{worker}.{os.getpid()}{ext or '.jsonl'}"
        return self.s.out

    if pytest is not None:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            try:
                rep = outcome.get_result()
                if not rep.failed or call.excinfo is None or rep.when not in ("setup", "call"):
                    return
                ei = call.excinfo
                payload = build_slice(item.nodeid, ei.type, ei.value, ei.tb, self.s.root, frames=self.s.frames, phase=rep.when)
                if payload is None or payload["sig"] in self.seen:
                    return
                self.seen.add(payload["sig"])
                if append_line(self._target(), fit_budget(payload, self.s.tokens)):
                    self.written += 1
            except Exception:  # a reporting bug must never change test outcomes
                return


def pytest_addoption(parser):
    g = parser.getgroup("trace-slice")
    g.addoption("--trace-slice-out", default=None, help="JSONL file for trace slices (default .planner/trace_slice.jsonl)")
    g.addoption("--trace-slice-budget", type=int, default=None, help="token budget per slice (default 400)")
    g.addoption("--trace-slice-frames", type=int, default=None, help="max frames per slice (default 3)")


def pytest_configure(config):
    settings = Settings.from_config(config)
    config._trace_slice_settings = settings
    config.pluginmanager.register(_Recorder(settings), "trace-slice-recorder")
