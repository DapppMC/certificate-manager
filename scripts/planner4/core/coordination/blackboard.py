"""blackboard.py - inter-process coordination for the 7 physical Planner 4.0 workers.

Seven OS processes (one per Antigravity account) share one SQLite database:

  events   append-only pointer log (kind, ref, sha, summary <= 240 chars). Workers exchange POINTERS, never
           file bodies, which is what keeps token cost linear instead of O(N^2) (the MetaGPT failure).
  leases   pessimistic path leases with TTL, glob/directory-aware overlap detection and FENCING TOKENS.
  cursors  per-worker read position (at-least-once delivery with an explicit ack).
  tickets  ticket state machine with compare-and-set transitions, so an orchestrator that crashes can resume.

Hardening over the 4.0 prototype (see docs/ENGINE_AUDIT.md for the evidence):
  * Fencing tokens: a worker whose lease expired (hung LLM call, laptop sleep) can no longer write with a stale
    lease; `post(..., fence=)` and `require()` reject it. TTL alone cannot give this guarantee.
  * Real overlap semantics for globs (`packages/db/migrations/*.sql`) and directories, one shared
    normaliser (case-folded, Windows-safe, traversal-proof) used by every OS.
  * Journal-mode policy: WAL only on local filesystems. WSL2 `/mnt/c` (9p/DrvFs) and SMB/NFS get
    `journal_mode=DELETE`; opening a WAL database on such a filesystem is refused instead of corrupting it.
  * BEGIN IMMEDIATE for every read-modify-write (the prototype's `pull()` updated the cursor without a
    transaction), explicit busy retry with jitter, fork-safe and thread-safe connection handling.
  * Summaries are sanitised (control characters, bidi overrides, newlines) because workers read each other's
    events - an unsanitised summary is a prompt-injection channel between agents.
  * Clock skew margin on lease expiry, `reap()` that emits observable `lease_expired` events,
    event pruning that never deletes unread events, schema versioning with in-place v1 -> v2 migration.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

try:  # package import (tests, orchestrator) and script execution both work
    from core.platform_compat import PathError, filesystem_kind, matches_any, normalize_rel_path, patterns_overlap
except ImportError:  # pragma: no cover - running as a loose script
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.platform_compat import PathError, filesystem_kind, matches_any, normalize_rel_path, patterns_overlap

SCHEMA_VERSION = 2
SUMMARY_MAX = 240
DEFAULT_TTL = 900.0
MIN_TTL, MAX_TTL = 1.0, 24 * 3600.0
CLOCK_SKEW_MARGIN = 2.0  # seconds a lease survives past `expires` before others may take it

WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9_.:\-]{0,39}$")
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")

DDL = """
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  worker TEXT NOT NULL,
  ticket TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  sha TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS leases(
  path TEXT PRIMARY KEY,
  worker TEXT NOT NULL,
  ticket TEXT NOT NULL DEFAULT '',
  expires REAL NOT NULL,
  fence INTEGER NOT NULL DEFAULT 0,
  acquired REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cursors(
  worker TEXT PRIMARY KEY,
  last_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tickets(
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  owner TEXT NOT NULL DEFAULT '',
  epoch INTEGER NOT NULL DEFAULT 0,
  sha TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  updated REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket, id);
CREATE INDEX IF NOT EXISTS idx_leases_worker ON leases(worker);
"""

# Ticket lifecycle. A transition not listed here is rejected (illegal skips are how verification gets bypassed).
TICKET_STATES = ("PLANNED", "CONTRACTED", "SEALED", "LEASED", "IN_PROGRESS", "VERIFYING", "DONE", "FAILED",
                 "ESCALATED", "QUARANTINED")
TRANSITIONS = {
    "PLANNED": {"CONTRACTED", "QUARANTINED"},
    "CONTRACTED": {"SEALED", "QUARANTINED", "ESCALATED"},
    "SEALED": {"LEASED", "QUARANTINED", "ESCALATED"},
    "LEASED": {"IN_PROGRESS", "SEALED", "QUARANTINED", "ESCALATED"},
    "IN_PROGRESS": {"VERIFYING", "FAILED", "LEASED", "QUARANTINED", "ESCALATED"},
    "VERIFYING": {"DONE", "FAILED", "IN_PROGRESS", "QUARANTINED", "ESCALATED"},
    "FAILED": {"IN_PROGRESS", "LEASED", "ESCALATED", "QUARANTINED"},
    "ESCALATED": {"CONTRACTED", "QUARANTINED", "PLANNED"},
    "QUARANTINED": {"PLANNED", "CONTRACTED"},
    "DONE": set(),
}


class BoardError(RuntimeError):
    pass


class BoardBusy(BoardError):
    """The database stayed locked for longer than the busy timeout."""


class BoardConfigError(BoardError):
    """Unsafe configuration (for example WAL requested on a network filesystem)."""


class LeaseLost(BoardError):
    """The caller's fencing token no longer matches a live lease."""


class IllegalTransition(BoardError):
    pass


class ClaimResult:
    """Result of Board.claim. Iterates as `(ok, message)` so the original prototype's tuple API keeps working."""

    __slots__ = ("ok", "message", "fences", "conflicts")

    def __init__(self, ok: bool, message: str, fences: Optional[dict] = None, conflicts: Optional[list] = None):
        self.ok = ok
        self.message = message
        self.fences = fences or {}
        self.conflicts = conflicts or []

    def __iter__(self):
        yield self.ok
        yield self.message

    def __bool__(self) -> bool:
        return self.ok

    @property
    def fence(self) -> int:
        """Highest fencing token granted by this claim (0 if none)."""
        return max(self.fences.values()) if self.fences else 0

    def __repr__(self) -> str:
        return f"ClaimResult(ok={self.ok}, message={self.message!r}, fences={self.fences})"


_BIDI_AND_CONTROL = re.compile(r"[‪-‮⁦-⁩​-‏﻿]")


def clean_text(value, limit: int) -> str:
    """Single-line, printable, bounded text. Defuses newline / control / bidi injection between agents."""
    s = "" if value is None else str(value)
    s = unicodedata.normalize("NFC", s)
    s = _BIDI_AND_CONTROL.sub("", s)
    s = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in s)
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[: max(0, limit - 1)] + "…"
    return s


def _norm_worker(worker: str) -> str:
    if not isinstance(worker, str) or not WORKER_RE.match(worker):
        raise ValueError(f"invalid worker id {worker!r} (use letters, digits, _ . - ; max 32)")
    return worker.upper()


def _norm_ticket(ticket: str) -> str:
    if ticket in (None, ""):
        return ""
    if not isinstance(ticket, str) or not TICKET_RE.match(ticket):
        raise ValueError(f"invalid ticket id {ticket!r}")
    return ticket


def wal_reset_bug_affected(version: Sequence[int] = None) -> bool:
    """SQLite 'WAL-reset' corruption bug: present in 3.7.0 .. 3.51.2, fixed in 3.51.3 (2026-03-13) and backported to
    3.44.6 and 3.50.7. It needs two or more connections writing / checkpointing at the same instant, which is exactly
    the 7-process blackboard. python.org Windows builds bundle their own SQLite; Linux uses the system library."""
    v = tuple(version if version is not None else sqlite3.sqlite_version_info)[:3]
    if v < (3, 7, 0) or v >= (3, 51, 3):
        return False
    if v[:2] == (3, 44) and v[2] >= 6:
        return False
    if v[:2] == (3, 50) and v[2] >= 7:
        return False
    return True


def choose_journal_mode(db_path: str, requested: str = "auto") -> tuple[str, dict]:
    """Return (journal_mode, fs_info). `requested` is auto | wal | delete (env PLANNER_BOARD_JOURNAL overrides)."""
    requested = (os.environ.get("PLANNER_BOARD_JOURNAL") or requested or "auto").lower()
    if requested not in ("auto", "wal", "delete"):
        raise BoardConfigError(f"unknown journal mode {requested!r}")
    if db_path == ":memory:":
        return "memory", {"kind": "memory"}
    info = filesystem_kind(db_path)
    if requested == "delete":
        return "delete", info
    if requested in ("auto", "wal") and wal_reset_bug_affected() and os.environ.get("PLANNER_BOARD_ALLOW_BUGGY_WAL") != "1":
        # Safe fallback: rollback-journal mode has no WAL-reset bug. Upgrade SQLite (>= 3.51.3 / 3.50.7 / 3.44.6) for WAL.
        info = dict(info, wal_bug=sqlite3.sqlite_version)
        return "delete", info
    if info.get("kind") == "network":
        if requested == "wal" and os.environ.get("PLANNER_BOARD_ALLOW_UNSAFE_WAL") != "1":
            raise BoardConfigError(
                f"WAL requested but {db_path!r} is on a network/9p filesystem ({info.get('fstype')}); WAL's shared-memory "
                "index is not coherent there. Put .planner/board.db on a local disk (WSL2: inside the Linux "
                "filesystem, e.g. ~/), or set PLANNER_BOARD_ALLOW_UNSAFE_WAL=1 at your own risk.")
        if requested == "auto":
            return "delete", info
    return "wal", info


class Board:
    """Shared coordination board. One instance per process (it re-opens itself after fork)."""

    def __init__(self, path: str = ".planner/board.db", *, busy_timeout_ms: int = 15000, journal: str = "auto",
                 durability: str = "normal", clock: Callable[[], float] = time.time,
                 skew_margin: float = CLOCK_SKEW_MARGIN):
        self.path = ":memory:" if str(path) == ":memory:" else os.fspath(Path(path))
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.durability = durability
        self._clock = clock
        self.skew = float(skew_margin)
        self._lock = threading.RLock()
        self._db: Optional[sqlite3.Connection] = None
        self._pid = -1
        if self.path != ":memory:":
            Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.journal_mode, self.fs_info = choose_journal_mode(self.path, journal)
        self._conn()  # open + migrate eagerly so configuration errors surface at construction

    # ------------------------------------------------------------------ connection management
    def _conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._db is not None and self._pid == os.getpid():
                return self._db
            # Fork safety: a connection inherited from the parent must never be used or closed by the child.
            self._db = None
            conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000.0, isolation_level=None,
                                   check_same_thread=False)
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            self._configure(conn)
            self._db, self._pid = conn, os.getpid()
            return conn

    def _configure(self, conn: sqlite3.Connection) -> None:
        if self.journal_mode != "memory":
            current = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if current != self.journal_mode:
                try:
                    got = conn.execute(f"PRAGMA journal_mode={self.journal_mode.upper()}").fetchone()[0].lower()
                except sqlite3.OperationalError as exc:
                    got = current
                    if self.journal_mode == "delete" and current == "wal" and self.fs_info.get("kind") == "network":
                        raise BoardConfigError(
                            f"{self.path!r} is already in WAL mode but sits on a network/9p filesystem and other "
                            f"processes hold it open ({exc}). Mixed Windows/WSL2 access to one WAL database is unsafe; "
                            "stop all workers and move the board to a local disk.") from exc
                if got != self.journal_mode and self.fs_info.get("kind") == "network" and got == "wal":
                    raise BoardConfigError(f"{self.path!r} is a WAL database on a network/9p filesystem; refusing to use it")
        full = self.durability == "full" or self.journal_mode == "delete"
        conn.execute(f"PRAGMA synchronous={'FULL' if full else 'NORMAL'}")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.execute("PRAGMA journal_size_limit=67108864")
        conn.execute("PRAGMA foreign_keys=ON")
        self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        if ver >= SCHEMA_VERSION:
            return
        self._begin(conn)
        try:
            # executescript() would COMMIT implicitly, so the DDL is applied statement by statement inside our tx
            for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
                conn.execute(stmt)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(leases)")}
            if "fence" not in cols:  # v1 -> v2 (prototype schema had no fencing)
                conn.execute("ALTER TABLE leases ADD COLUMN fence INTEGER NOT NULL DEFAULT 0")
            if "acquired" not in cols:
                conn.execute("ALTER TABLE leases ADD COLUMN acquired REAL NOT NULL DEFAULT 0")
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('fence', '0')")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _begin(self, conn: sqlite3.Connection) -> None:
        deadline = time.monotonic() + self.busy_timeout_ms / 1000.0 + 1.0
        delay = 0.005
        while True:
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                if time.monotonic() >= deadline:
                    raise BoardBusy(f"database busy for >{self.busy_timeout_ms} ms: {exc}") from exc
                time.sleep(delay + random.random() * delay)
                delay = min(delay * 2, 0.25)

    @contextmanager
    def _tx(self):
        with self._lock:
            conn = self._conn()
            self._begin(conn)
            try:
                yield conn
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            try:
                conn.execute("COMMIT")
            except sqlite3.Error:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def _exec(self, sql: str, params: Sequence = ()):
        """Single-statement autocommit write/read with busy mapping."""
        with self._lock:
            try:
                return self._conn().execute(sql, params)
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise BoardBusy(str(exc)) from exc
                raise

    def now(self) -> float:
        return float(self._clock())

    def close(self) -> None:
        with self._lock:
            if self._db is not None and self._pid == os.getpid():
                try:
                    if self.journal_mode == "wal":
                        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                try:
                    self._db.close()
                except sqlite3.Error:
                    pass
            self._db = None

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown safety
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "Board":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ fencing
    @staticmethod
    def _next_fence(conn: sqlite3.Connection) -> int:
        conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'fence'")
        return int(conn.execute("SELECT value FROM meta WHERE key = 'fence'").fetchone()[0])

    # ------------------------------------------------------------------ leases
    def claim(self, worker: str, ticket: str, paths: Iterable[str], ttl: float = DEFAULT_TTL,
              wait: float = 0.0) -> ClaimResult:
        """Atomically lease every path/glob for `worker` or none of them.

        Conflict = another worker holds an overlapping live lease (directory, glob and '**' aware).
        `wait` > 0 retries (jittered) until free or the deadline passes. A worker may re-claim its own
        paths (renewal keeps the fencing token when the ticket is unchanged; a new ticket gets a new token).
        """
        try:
            w = _norm_worker(worker)
            t = _norm_ticket(ticket)
            want = sorted({normalize_rel_path(p) for p in paths})
            if not want:
                return ClaimResult(False, "no paths to claim")
            ttl = float(ttl)
            if not (MIN_TTL <= ttl <= MAX_TTL):
                return ClaimResult(False, f"ttl must be within [{MIN_TTL}, {MAX_TTL}] seconds")
        except (ValueError, PathError) as exc:
            return ClaimResult(False, f"invalid claim: {exc}")

        deadline = time.monotonic() + max(0.0, wait)
        delay = 0.02
        while True:
            result = self._claim_once(w, t, want, ttl)
            if result.ok or time.monotonic() >= deadline:
                return result
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 1.7, 0.5)

    def _claim_once(self, w: str, t: str, want: list, ttl: float) -> ClaimResult:
        with self._tx() as conn:
            now = self.now()
            conn.execute("DELETE FROM leases WHERE expires + ? < ?", (self.skew, now))
            held = conn.execute("SELECT path, worker, ticket, expires, fence FROM leases").fetchall()
            conflicts = []
            for p in want:
                for hp, hw, ht, _exp, _fence in held:
                    if hw != w and patterns_overlap(p, hp):
                        conflicts.append({"want": p, "held": hp, "worker": hw, "ticket": ht})
            if conflicts:
                c = conflicts[0]
                return ClaimResult(False, f"Conflict: path '{c['want']}' overlaps '{c['held']}' locked by {c['worker']}"
                                          f" (ticket {c['ticket']})", conflicts=conflicts)
            existing = {hp: (hw, ht, hf) for hp, hw, ht, _e, hf in held}
            fences = {}
            new_fence = None
            for p in want:
                prev = existing.get(p)
                if prev and prev[0] == w and prev[1] == t and prev[2] > 0:
                    fence = prev[2]
                    conn.execute("UPDATE leases SET expires = ? WHERE path = ?", (now + ttl, p))
                else:
                    if new_fence is None:
                        new_fence = self._next_fence(conn)
                    fence = new_fence
                    conn.execute(
                        "INSERT INTO leases(path, worker, ticket, expires, fence, acquired) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET worker=excluded.worker, ticket=excluded.ticket, "
                        "expires=excluded.expires, fence=excluded.fence, acquired=excluded.acquired",
                        (p, w, t, now + ttl, fence, now))
                fences[p] = fence
            return ClaimResult(True, "ok", fences=fences)

    def heartbeat(self, worker: str, ticket: Optional[str] = None, ttl: float = DEFAULT_TTL) -> int:
        """Extend every live lease of `worker` (optionally only for `ticket`). Returns how many were extended.
        A return of 0 while the worker believes it holds leases means the leases were lost: stop writing."""
        w = _norm_worker(worker)
        with self._tx() as conn:
            now = self.now()
            sql = "UPDATE leases SET expires = ? WHERE worker = ? AND expires + ? >= ?"
            params: list = [now + float(ttl), w, self.skew, now]
            if ticket:
                sql += " AND ticket = ?"
                params.append(_norm_ticket(ticket))
            return conn.execute(sql, params).rowcount

    def release(self, worker: str, paths: Optional[Iterable[str]] = None, ticket: Optional[str] = None) -> int:
        """Release leases owned by `worker` (all, only `paths`, and/or only `ticket`). Returns the number released."""
        w = _norm_worker(worker)
        with self._tx() as conn:
            if paths is None:
                sql, params = "DELETE FROM leases WHERE worker = ?", [w]
                if ticket:
                    sql += " AND ticket = ?"
                    params.append(_norm_ticket(ticket))
                return conn.execute(sql, params).rowcount
            n = 0
            for p in paths:
                try:
                    key = normalize_rel_path(p)
                except PathError:
                    continue
                n += conn.execute("DELETE FROM leases WHERE worker = ? AND path = ?", (w, key)).rowcount
            return n

    def force_release(self, path_or_worker: str, *, by: str = "ORCHESTRATOR", reason: str = "") -> int:
        """Orchestrator-only escape hatch for a dead worker. Logged as an event so it is never silent."""
        with self._tx() as conn:
            if WORKER_RE.match(path_or_worker) and conn.execute(
                    "SELECT 1 FROM leases WHERE worker = ? LIMIT 1", (path_or_worker.upper(),)).fetchone():
                n = conn.execute("DELETE FROM leases WHERE worker = ?", (path_or_worker.upper(),)).rowcount
                target = path_or_worker.upper()
            else:
                n = conn.execute("DELETE FROM leases WHERE path = ?", (normalize_rel_path(path_or_worker),)).rowcount
                target = path_or_worker
            conn.execute("INSERT INTO events(ts, worker, ticket, kind, ref, sha, summary) VALUES(?,?,?,?,?,?,?)",
                         (self.now(), _norm_worker(by), "", "lease_forced", clean_text(target, 200), "",
                          clean_text(f"force released {n} lease(s): {reason}", SUMMARY_MAX)))
            return n

    def reap(self) -> list:
        """Delete expired leases and emit a `lease_expired` event for each. Returns the reaped rows."""
        with self._tx() as conn:
            now = self.now()
            rows = conn.execute("SELECT path, worker, ticket, expires, fence FROM leases WHERE expires + ? < ?",
                                (self.skew, now)).fetchall()
            for path, worker, ticket, _exp, fence in rows:
                conn.execute("DELETE FROM leases WHERE path = ? AND fence = ?", (path, fence))
                conn.execute("INSERT INTO events(ts, worker, ticket, kind, ref, sha, summary) VALUES(?,?,?,?,?,?,?)",
                             (now, "BOARD", ticket, "lease_expired", clean_text(path, 200), "",
                              clean_text(f"lease of {worker} expired (fence {fence})", SUMMARY_MAX)))
            return [{"path": r[0], "worker": r[1], "ticket": r[2], "fence": r[4]} for r in rows]

    def leases(self, worker: Optional[str] = None, include_expired: bool = False) -> list:
        now = self.now()
        sql = "SELECT path, worker, ticket, expires, fence, acquired FROM leases"
        cond, params = [], []
        if worker:
            cond.append("worker = ?")
            params.append(_norm_worker(worker))
        if not include_expired:
            cond.append("expires + ? >= ?")
            params += [self.skew, now]
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        rows = self._exec(sql + " ORDER BY path", params).fetchall()
        return [{"path": r[0], "worker": r[1], "ticket": r[2], "expires": r[3], "fence": r[4], "acquired": r[5]} for r in rows]

    def holds(self, worker: str, path: str, fence: Optional[int] = None) -> bool:
        """Does `worker` hold a live lease covering concrete `path` (and, if given, with this fencing token)?"""
        w = _norm_worker(worker)
        try:
            target = normalize_rel_path(path)
        except PathError:
            return False
        for lease in self.leases(w):
            if matches_any(target, [lease["path"]]) and (fence is None or lease["fence"] == fence):
                return True
        return False

    def require(self, worker: str, fence: int) -> None:
        """Raise LeaseLost unless `worker` still owns at least one live lease carrying `fence`."""
        w = _norm_worker(worker)
        now = self.now()
        row = self._exec("SELECT 1 FROM leases WHERE worker = ? AND fence = ? AND expires + ? >= ? LIMIT 1",
                         (w, int(fence), self.skew, now)).fetchone()
        if not row:
            raise LeaseLost(f"worker {w} no longer holds a live lease with fence {fence}")

    # ------------------------------------------------------------------ events
    def post(self, worker: str, ticket: str, kind: str, ref: str = "", sha: str = "", summary: str = "",
             fence: Optional[int] = None) -> int:
        """Append a pointer event and return its id. With `fence`, the write is rejected (LeaseLost) when the
        worker's lease is gone, so a zombie worker cannot report stale results."""
        w = _norm_worker(worker)
        t = _norm_ticket(ticket)
        if not isinstance(kind, str) or not KIND_RE.match(kind):
            raise ValueError(f"invalid event kind {kind!r} (lowercase letters, digits, _ . : -)")
        row = (self.now(), w, t, kind, clean_text(ref, 200), clean_text(sha, 80), clean_text(summary, SUMMARY_MAX))
        if fence is None:
            cur = self._exec("INSERT INTO events(ts, worker, ticket, kind, ref, sha, summary) VALUES(?,?,?,?,?,?,?)", row)
            return int(cur.lastrowid)
        with self._tx() as conn:
            ok = conn.execute("SELECT 1 FROM leases WHERE worker = ? AND fence = ? AND expires + ? >= ? LIMIT 1",
                              (w, int(fence), self.skew, row[0])).fetchone()
            if not ok:
                raise LeaseLost(f"worker {w} no longer holds a live lease with fence {fence}")
            cur = conn.execute("INSERT INTO events(ts, worker, ticket, kind, ref, sha, summary) VALUES(?,?,?,?,?,?,?)", row)
            return int(cur.lastrowid)

    @staticmethod
    def _row_to_event(r) -> dict:
        return {"id": r[0], "ts": r[1], "worker": r[2], "ticket": r[3], "kind": r[4], "ref": r[5], "sha": r[6], "summary": r[7]}

    def register(self, worker: str, from_start: bool = False) -> int:
        """Create the worker's cursor. New workers start at the log head unless `from_start`."""
        w = _norm_worker(worker)
        with self._tx() as conn:
            row = conn.execute("SELECT last_id FROM cursors WHERE worker = ?", (w,)).fetchone()
            if row:
                return int(row[0])
            head = 0 if from_start else int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
            conn.execute("INSERT INTO cursors(worker, last_id) VALUES(?, ?)", (w, head))
            return head

    def pull(self, worker: str, limit: int = 50, kinds: Optional[Iterable[str]] = None, advance: bool = True,
             include_own: bool = True) -> list:
        """Return events after the worker's cursor. `advance=True` (default, prototype-compatible) moves the
        cursor atomically with the read; `advance=False` + `ack()` gives at-least-once delivery across crashes."""
        w = _norm_worker(worker)
        limit = max(1, min(int(limit), 1000))
        kind_list = list(kinds) if kinds else None
        with self._tx() as conn:
            row = conn.execute("SELECT last_id FROM cursors WHERE worker = ?", (w,)).fetchone()
            last = int(row[0]) if row else 0
            sql = "SELECT id, ts, worker, ticket, kind, ref, sha, summary FROM events WHERE id > ?"
            params: list = [last]
            if kind_list:
                sql += " AND kind IN (%s)" % ",".join("?" * len(kind_list))
                params += kind_list
            if not include_own:
                sql += " AND worker != ?"
                params.append(w)
            sql += " ORDER BY id ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            if advance:
                # Advance past everything scanned. If fewer than `limit` rows matched, the filter has seen the whole
                # log up to the head (we are inside one write transaction), so filtered-out rows cannot stall the cursor.
                head = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
                new_last = head if len(rows) < limit else int(rows[-1][0])
                if new_last > last or row is None:
                    conn.execute("INSERT INTO cursors(worker, last_id) VALUES(?, ?) "
                                 "ON CONFLICT(worker) DO UPDATE SET last_id = MAX(last_id, excluded.last_id)", (w, max(new_last, last)))
        return [self._row_to_event(r) for r in rows]

    def ack(self, worker: str, upto_id: int) -> int:
        """Advance the cursor to `upto_id` (never backwards). Returns the resulting cursor."""
        w = _norm_worker(worker)
        with self._tx() as conn:
            conn.execute("INSERT INTO cursors(worker, last_id) VALUES(?, ?) "
                         "ON CONFLICT(worker) DO UPDATE SET last_id = MAX(last_id, excluded.last_id)", (w, int(upto_id)))
            return int(conn.execute("SELECT last_id FROM cursors WHERE worker = ?", (w,)).fetchone()[0])

    def events_for(self, ticket: str, limit: int = 200) -> list:
        rows = self._exec("SELECT id, ts, worker, ticket, kind, ref, sha, summary FROM events WHERE ticket = ? "
                          "ORDER BY id ASC LIMIT ?", (_norm_ticket(ticket), int(limit))).fetchall()
        return [self._row_to_event(r) for r in rows]

    def head(self) -> int:
        return int(self._exec("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])

    def prune_events(self, keep_last: int = 10000) -> int:
        """Delete old events that EVERY registered worker has already consumed. Unread events are never deleted."""
        with self._tx() as conn:
            row = conn.execute("SELECT MIN(last_id) FROM cursors").fetchone()
            floor = int(row[0]) if row and row[0] is not None else 0
            head = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
            cutoff = min(floor, head - int(keep_last))
            if cutoff <= 0:
                return 0
            return conn.execute("DELETE FROM events WHERE id <= ?", (cutoff,)).rowcount

    # ------------------------------------------------------------------ ticket state machine
    def set_ticket(self, ticket: str, state: str, *, owner: str = "", epoch: int = 0, sha: str = "", note: str = "",
                   expect: Optional[Iterable[str]] = None) -> dict:
        """Create or transition a ticket. Existing tickets may only move along TRANSITIONS; `expect` makes the
        call a compare-and-set (it fails if the current state is not in `expect`)."""
        t = _norm_ticket(ticket)
        if not t:
            raise ValueError("ticket id required")
        if state not in TICKET_STATES:
            raise ValueError(f"unknown state {state!r}")
        with self._tx() as conn:
            row = conn.execute("SELECT state, attempts FROM tickets WHERE id = ?", (t,)).fetchone()
            now = self.now()
            if row is None:
                conn.execute("INSERT INTO tickets(id, state, owner, epoch, sha, attempts, note, updated) VALUES(?,?,?,?,?,?,?,?)",
                             (t, state, owner, int(epoch), clean_text(sha, 80), 0, clean_text(note, 200), now))
            else:
                cur, attempts = row
                if expect is not None and cur not in set(expect):
                    raise IllegalTransition(f"{t}: expected state in {sorted(set(expect))}, found {cur}")
                if state != cur and state not in TRANSITIONS.get(cur, set()):
                    raise IllegalTransition(f"{t}: {cur} -> {state} is not allowed")
                if state == "FAILED":
                    attempts += 1
                conn.execute("UPDATE tickets SET state=?, owner=COALESCE(NULLIF(?, ''), owner), epoch=CASE WHEN ? > 0 THEN ? ELSE epoch END, "
                             "sha=COALESCE(NULLIF(?, ''), sha), attempts=?, note=?, updated=? WHERE id=?",
                             (state, owner, int(epoch), int(epoch), clean_text(sha, 80), attempts, clean_text(note, 200), now, t))
            r = conn.execute("SELECT id, state, owner, epoch, sha, attempts, note, updated FROM tickets WHERE id = ?", (t,)).fetchone()
        return dict(zip(("id", "state", "owner", "epoch", "sha", "attempts", "note", "updated"), r))

    def get_ticket(self, ticket: str) -> Optional[dict]:
        r = self._exec("SELECT id, state, owner, epoch, sha, attempts, note, updated FROM tickets WHERE id = ?",
                       (_norm_ticket(ticket),)).fetchone()
        return dict(zip(("id", "state", "owner", "epoch", "sha", "attempts", "note", "updated"), r)) if r else None

    def tickets(self, state: Optional[str] = None) -> list:
        sql = "SELECT id, state, owner, epoch, sha, attempts, note, updated FROM tickets"
        params: list = []
        if state:
            sql += " WHERE state = ?"
            params.append(state)
        rows = self._exec(sql + " ORDER BY id", params).fetchall()
        return [dict(zip(("id", "state", "owner", "epoch", "sha", "attempts", "note", "updated"), r)) for r in rows]

    # ------------------------------------------------------------------ diagnostics
    def quick_check(self) -> str:
        return str(self._exec("PRAGMA quick_check").fetchone()[0])

    def snapshot(self) -> dict:
        return {"journal_mode": self.journal_mode, "fs": self.fs_info, "head": self.head(), "leases": self.leases(),
                "tickets": self.tickets(), "schema": SCHEMA_VERSION, "integrity": self.quick_check()}


# ---------------------------------------------------------------------------------------------- CLI
def _cli(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="blackboard", description="Inspect / drive the Planner 4.0 coordination board")
    ap.add_argument("--db", default=".planner/board.db")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("reap")
    c = sub.add_parser("claim")
    c.add_argument("worker"); c.add_argument("ticket"); c.add_argument("paths", nargs="+"); c.add_argument("--ttl", type=float, default=DEFAULT_TTL)
    r = sub.add_parser("release")
    r.add_argument("worker"); r.add_argument("paths", nargs="*")
    p = sub.add_parser("post")
    p.add_argument("worker"); p.add_argument("ticket"); p.add_argument("kind"); p.add_argument("--ref", default=""); p.add_argument("--sha", default=""); p.add_argument("--summary", default="")
    pl = sub.add_parser("pull")
    pl.add_argument("worker"); pl.add_argument("--limit", type=int, default=50)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    try:
        with Board(a.db) as board:
            if a.cmd == "status":
                out = board.snapshot()
            elif a.cmd == "reap":
                out = board.reap()
            elif a.cmd == "claim":
                res = board.claim(a.worker, a.ticket, a.paths, a.ttl)
                out = {"ok": res.ok, "message": res.message, "fences": res.fences}
                print(json.dumps(out, indent=2))
                return 0 if res.ok else 1
            elif a.cmd == "release":
                out = {"released": board.release(a.worker, a.paths or None)}
            elif a.cmd == "post":
                out = {"id": board.post(a.worker, a.ticket, a.kind, a.ref, a.sha, a.summary)}
            else:
                out = board.pull(a.worker, a.limit)
            print(json.dumps(out, indent=2, default=str))
    except (BoardError, ValueError, PathError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
