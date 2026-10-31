"""platform_compat.py - cross-platform primitives shared by every Planner 4.0 core engine.

Standard library only. Python 3.10+. Works on native Windows 11, WSL2 and Linux.

What lives here (and why it is shared):
  * normalize_rel_path / patterns_overlap  - ONE definition of "same path" for leases, scope checks and
    sealed-vault guards, so a Windows worker and a WSL2 worker can never disagree about a conflict.
  * run_process / kill_process_tree        - subprocess.run(timeout=) kills only the direct child. pytest,
    node and git all spawn grandchildren, so a plain timeout leaves orphans that keep files locked
    (fatal on Windows) and keep ports bound. We start a new process group / session and kill the tree.
  * build_sandbox_env                      - allow-list environment so secrets never leak into sandboxes.
  * filesystem_kind / parse_mountinfo      - detect network / 9p (WSL2 DrvFs) filesystems where SQLite WAL
    is unsafe.
  * atomic_write_text / safe_rmtree        - Windows-aware file helpers (retry on AV-induced PermissionError,
    clear read-only bits before deleting).
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

IS_WINDOWS = os.name == "nt"

# --------------------------------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------------------------------


def is_wsl() -> bool:
    """True when running inside WSL (1 or 2)."""
    if IS_WINDOWS:
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def platform_tag() -> str:
    if IS_WINDOWS:
        return "windows"
    if is_wsl():
        return "wsl"
    return sys.platform  # linux / darwin


# --------------------------------------------------------------------------------------------------
# Path normalisation and overlap (lease keys, scope globs)
# --------------------------------------------------------------------------------------------------


class PathError(ValueError):
    """Raised when a repository-relative path is unsafe or ambiguous."""


_WIN_RESERVED = {"con", "prn", "aux", "nul", "clock$"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
_SHORT_NAME = re.compile(r"^[^.~]{1,6}~[0-9]+(\.[^.]{0,3})?$")
GLOB_CHARS = frozenset("*?[")


def has_glob(segment: str) -> bool:
    return any(c in GLOB_CHARS for c in segment)


def normalize_rel_path(p: str) -> str:
    """Normalise a repository-relative path or glob into a canonical lease key.

    The key is deliberately CONSERVATIVE: it is case-folded on every platform. Windows and macOS are
    case-insensitive, so two spellings of one file must collide there; on case-sensitive Linux this can
    only produce a false conflict (safe), never a missed one. Because workers run on different OSes
    (native Windows and WSL2) the policy must be identical for all of them.

    Rejects: empty, NUL, absolute / drive / UNC paths, '..' traversal, NTFS alternate data streams
    ('file:stream'), Windows reserved device names (CON, NUL, COM1...) and 8.3 short names (ambiguous).
    Strips: leading './', duplicate slashes, trailing dots/spaces on a segment (Windows ignores them),
    the '\\\\?\\' long-path prefix. Applies Unicode NFC (macOS hands out NFD).
    """
    if not isinstance(p, str):
        raise PathError(f"path must be str, got {type(p).__name__}")
    s = p.strip()
    if not s:
        raise PathError("empty path")
    if "\x00" in s:
        raise PathError("NUL byte in path")
    s = s.replace("\\", "/")
    for prefix in ("//?/", "//./"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = unicodedata.normalize("NFC", s)
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        raise PathError(f"absolute path not allowed: {p!r}")
    parts = []
    for seg in s.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise PathError(f"parent traversal not allowed: {p!r}")
        seg = seg.rstrip(" .")
        if not seg:
            raise PathError(f"invalid path segment in {p!r}")
        if ":" in seg:
            raise PathError(f"colon (drive / alternate data stream) not allowed: {p!r}")
        if seg.split(".")[0].lower() in _WIN_RESERVED:
            raise PathError(f"Windows reserved device name in {p!r}")
        if _SHORT_NAME.match(seg):
            raise PathError(f"8.3 short name is ambiguous, use the long name: {p!r}")
        parts.append(seg.casefold())
    if not parts:
        raise PathError(f"path resolves to the repository root: {p!r} (use '**' to claim everything)")
    return "/".join(parts)


def _segment_may_match(a: str, b: str) -> bool:
    ga, gb = has_glob(a), has_glob(b)
    if not ga and not gb:
        return a == b
    if ga and not gb:
        return fnmatch.fnmatchcase(b, a)
    if gb and not ga:
        return fnmatch.fnmatchcase(a, b)
    return True  # two wildcards in one segment: assume they can match (conservative)


def patterns_overlap(a: str, b: str) -> bool:
    """True if the (already normalised) paths/globs `a` and `b` may refer to a common file.

    Directory semantics: 'src/api' overlaps 'src/api/routes/x.py' (a lease on a directory covers its
    children). '**' matches any depth. A literal compared with a glob uses fnmatch per segment.
    The function is conservative: when unsure it answers True (a missed conflict corrupts files, a
    false conflict only delays a worker).
    """
    sa, sb = a.split("/"), b.split("/")
    for x, y in zip(sa, sb):
        if x == "**" or y == "**":
            return True
        if not _segment_may_match(x, y):
            return False
    return True


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    """Does the concrete repo-relative `path` fall under any scope pattern (glob or directory)?

    `*` stays inside one folder, `**` crosses folders, a pattern without glob characters also matches
    everything beneath it when it names a directory ('src/api' matches 'src/api/a.py').
    """
    try:
        target = normalize_rel_path(path)
    except PathError:
        return False
    for pat in patterns:
        try:
            norm = normalize_rel_path(pat)
        except PathError:
            continue
        if _glob_match(target, norm):
            return True
    return False


def _glob_match(target: str, pattern: str) -> bool:
    t, p = target.split("/"), pattern.split("/")

    def rec(i: int, j: int) -> bool:
        while j < len(p):
            if p[j] == "**":
                if j == len(p) - 1:
                    return True
                return any(rec(k, j + 1) for k in range(i, len(t) + 1))
            if i >= len(t):
                return False
            if not (fnmatch.fnmatchcase(t[i], p[j]) if has_glob(p[j]) else t[i] == p[j]):
                return False
            i += 1
            j += 1
        # pattern exhausted: either exact match, or the pattern named a directory containing the target
        return True

    return rec(0, 0)


# --------------------------------------------------------------------------------------------------
# Environment scrubbing
# --------------------------------------------------------------------------------------------------

_ENV_ALLOW = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "VIRTUAL_ENV", "CONDA_PREFIX", "LD_LIBRARY_PATH",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "PLAYWRIGHT_BROWSERS_PATH", "NODE_PATH", "WSL_DISTRO_NAME",
)
SECRET_NAME_RE = re.compile(
    r"(?i)(^|[_\-.])(api[_\-]?key|apikey|token|secret|passw(or)?d|passwd|pwd|credential|credentials|private[_\-]?key|"
    r"auth|authorization|cookie|session|bearer|access[_\-]?key|pat|dsn|connection[_\-]?string)([_\-.]|$)"
)


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RE.search(name or ""))


def build_sandbox_env(extra: Optional[Mapping[str, str]] = None, *, base: Optional[Mapping[str, str]] = None,
                      keep: Sequence[str] = ()) -> dict:
    """Allow-list environment for sandboxed subprocesses. Secrets are dropped even if explicitly listed in
    `keep` unless they are passed through `extra` (an explicit, visible decision by the caller)."""
    src = dict(os.environ if base is None else base)
    allow = {k.upper() for k in _ENV_ALLOW} | {k.upper() for k in keep}
    env = {}
    for k, v in src.items():
        ku = k.upper()
        if ku in allow and not is_secret_name(k):
            env[k] = v
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD", ""),
    })
    if not env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]:
        env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


# --------------------------------------------------------------------------------------------------
# Subprocess with a real timeout (kills the whole tree)
# --------------------------------------------------------------------------------------------------


@dataclass
class ProcResult:
    returncode: Optional[int]
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration: float = 0.0
    truncated: bool = False
    pid: Optional[int] = None
    error: str = ""
    cmd: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error


class _Capture:
    """Bounded capture: keeps the first and the last `limit/2` bytes so a runaway process cannot exhaust memory."""

    def __init__(self, limit: int):
        self.half = max(1024, limit // 2)
        self.head = bytearray()
        self.tail = bytearray()
        self.dropped = 0
        self.lock = threading.Lock()

    def feed(self, chunk: bytes) -> None:
        with self.lock:
            room = self.half - len(self.head)
            if room > 0:
                self.head += chunk[:room]
                chunk = chunk[room:]
            if chunk:
                self.tail += chunk
                if len(self.tail) > self.half:
                    over = len(self.tail) - self.half
                    del self.tail[:over]
                    self.dropped += over

    def text(self, encoding: str) -> str:
        with self.lock:
            data = bytes(self.head)
            if self.dropped:
                data += f"\n...[{self.dropped} bytes dropped]...\n".encode()
            data += bytes(self.tail)
        return data.decode(encoding, errors="replace")


def _pump(stream, cap: _Capture) -> None:
    try:
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                break
            cap.feed(chunk)
    except (OSError, ValueError):
        pass


def kill_process_tree(proc: "subprocess.Popen", grace: float = 2.0) -> None:
    """Terminate `proc` and all its descendants. Best effort, never raises."""
    pid = proc.pid
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None and not _group_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def run_process(cmd: Sequence[str], *, cwd: Optional[str] = None, env: Optional[Mapping[str, str]] = None,
                timeout: float = 60.0, input_text: Optional[str] = None, max_output: int = 1_000_000,
                grace: float = 2.0, encoding: str = "utf-8") -> ProcResult:
    """Run `cmd` (argv list, never a shell string) with a hard wall-clock timeout that also kills grandchildren.

    * POSIX: new session (own process group) + killpg. Strays left by a process that exited normally
      (for example pytest-forked children) are reaped as well.
    * Windows: CREATE_NEW_PROCESS_GROUP + `taskkill /F /T`.
    * Output is bounded (head + tail), decoded as UTF-8 with replacement, so a binary or hostile stream
      cannot crash the caller.
    * Never raises for ordinary failures; inspect `ProcResult.error` / `.timed_out` / `.returncode`.
    """
    argv = [str(c) for c in cmd]
    started = time.monotonic()
    kwargs: dict = dict(stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                        env=dict(env) if env is not None else None)
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kwargs)
    except (OSError, ValueError) as exc:
        return ProcResult(returncode=None, error=f"{type(exc).__name__}: {exc}", duration=time.monotonic() - started, cmd=argv)

    out_cap, err_cap = _Capture(max_output), _Capture(max_output)
    threads = [threading.Thread(target=_pump, args=(proc.stdout, out_cap), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, err_cap), daemon=True)]
    for t in threads:
        t.start()
    if input_text is not None:
        def _feed():
            try:
                proc.stdin.write(input_text.encode(encoding, errors="replace"))
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        threading.Thread(target=_feed, daemon=True).start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(proc, grace)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    else:
        if not IS_WINDOWS:  # reap strays that outlived the leader
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    for t in threads:
        t.join(timeout=3.0)  # a surviving grandchild could hold the pipe open; do not wait forever
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except Exception:
            pass
    return ProcResult(returncode=proc.returncode, stdout=out_cap.text(encoding), stderr=err_cap.text(encoding),
                      timed_out=timed_out, duration=time.monotonic() - started,
                      truncated=bool(out_cap.dropped or err_cap.dropped), pid=proc.pid, cmd=argv)


# --------------------------------------------------------------------------------------------------
# Filesystem classification (is SQLite WAL safe here?)
# --------------------------------------------------------------------------------------------------

NETWORK_FSTYPES = frozenset({
    "9p", "drvfs", "cifs", "smb", "smb2", "smb3", "smbfs", "nfs", "nfs3", "nfs4", "afs", "ncpfs", "davfs",
    "fuse.davfs2", "fuse.sshfs", "sshfs", "fuse.rclone", "fuse.s3fs", "fuse.gcsfuse", "vboxsf", "virtiofs",
    "9pfs", "fuse.portal", "lustre", "gpfs", "ceph", "glusterfs", "fuse.glusterfs",
})


def _unescape_mount(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_mountinfo(text: str, path: str) -> dict:
    """Pure helper (unit-testable): classify `path` from /proc/self/mountinfo text."""
    best = None
    best_len = -1
    for line in text.splitlines():
        if " - " not in line:
            continue
        pre, post = line.split(" - ", 1)
        pf = pre.split()
        if len(pf) < 5:
            continue
        mount_point = _unescape_mount(pf[4])
        fstype = post.split()[0] if post.split() else ""
        norm = mount_point.rstrip("/") or "/"
        if path == norm or path.startswith(norm.rstrip("/") + "/") or norm == "/":
            if len(norm) > best_len:
                best, best_len = (mount_point, fstype, post), len(norm)
    if best is None:
        return {"kind": "unknown", "fstype": "", "mount": ""}
    mount_point, fstype, post = best
    low = fstype.lower()
    network = low in NETWORK_FSTYPES or low.startswith("fuse.") and low in NETWORK_FSTYPES
    # WSL2 mounts Windows drives as 9p with aname=drvfs
    drvfs = low in ("9p", "drvfs") and ("drvfs" in post.lower() or "aname=" in post.lower() or mount_point.startswith("/mnt/"))
    return {"kind": "network" if network else "local", "fstype": fstype, "mount": mount_point, "wsl_drvfs": bool(drvfs)}


def filesystem_kind(path: str) -> dict:
    """Classify the filesystem holding `path`: {'kind': 'local'|'network'|'unknown', 'fstype', 'mount', ...}.

    Used to refuse SQLite WAL on 9p/DrvFs/SMB/NFS where the -shm mmap coherency it relies on is not
    guaranteed. Never raises."""
    try:
        real = os.path.realpath(path if os.path.exists(path) else os.path.dirname(os.path.abspath(path)) or ".")
    except OSError:
        return {"kind": "unknown", "fstype": "", "mount": ""}
    if IS_WINDOWS:
        try:
            import ctypes  # noqa: WPS433
            if real.startswith("\\\\") and not real.startswith("\\\\?\\"):
                return {"kind": "network", "fstype": "unc", "mount": real}
            drive = os.path.splitdrive(real)[0]
            root = (drive + "\\") if drive else real
            dtype = ctypes.windll.kernel32.GetDriveTypeW(root)  # 4 = DRIVE_REMOTE
            return {"kind": "network" if dtype == 4 else "local", "fstype": f"drivetype{dtype}", "mount": root}
        except Exception:
            return {"kind": "unknown", "fstype": "", "mount": ""}
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
                info = parse_mountinfo(fh.read(), real)
            return info
        except OSError:
            return {"kind": "unknown", "fstype": "", "mount": ""}
    return {"kind": "local", "fstype": sys.platform, "mount": "/"}


# --------------------------------------------------------------------------------------------------
# File helpers
# --------------------------------------------------------------------------------------------------


def replace_with_retry(src: str, dst: str, attempts: int = 8, delay: float = 0.05) -> None:
    """os.replace that survives transient Windows sharing violations (antivirus / indexer holding the file)."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (2 ** i) if delay * (2 ** i) < 1.0 else 1.0)


def atomic_write_text(path, text: str, encoding: str = "utf-8", mode: Optional[int] = None) -> None:
    """Write-then-rename so readers never see a half-written file; fsyncs the data."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        if mode is not None and not IS_WINDOWS:
            os.chmod(tmp, mode)
        replace_with_retry(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _chmod_and_retry(func, path, exc_info):  # shutil.rmtree onerror
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except OSError:
        pass


def safe_rmtree(path) -> None:
    """rmtree that clears read-only bits (git objects, sealed vault) and tolerates Windows file locks."""
    p = str(path)
    if not os.path.lexists(p):
        return
    for attempt in range(4):
        shutil.rmtree(p, onerror=_chmod_and_retry)
        if not os.path.lexists(p):
            return
        time.sleep(0.1 * (attempt + 1))


def make_tree_readonly(root) -> None:
    """Best-effort: remove write bits from every file under root (sealed vault hardening)."""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                os.chmod(fp, stat.S_IREAD)
            except OSError:
                pass


def is_relative_to(path, base) -> bool:
    """Case-aware containment check (os.path.normcase on Windows) without following a missing path."""
    try:
        p = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(base))
        return os.path.commonpath([p, b]) == b
    except ValueError:  # different drives
        return False


def find_executable(name: str) -> Optional[str]:
    return shutil.which(name)
