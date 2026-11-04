"""worktree.py - baseline snapshots and diffs for ticket worktrees (no git required).

The verifier needs three answers about a worker's attempt: WHICH files changed (scope check, port scan), WHICH LINES
changed (diff-scoped mutation testing) and WHAT THE TESTS LOOKED LIKE before (anti-weakening). `git diff` answers them
only inside a clean git checkout with committed baselines; planner worktrees are often plain folders, and untracked
files are exactly where a worker hides things. So the orchestrator takes a *baseline snapshot* when it leases a ticket
and every later question is a comparison against it.

  capture(root, dest)            copy regular files (no symlinks, size capped) to `dest`, write nothing else
  changes(baseline, root)        -> Changes(added, modified, deleted)   (content hashes, not mtimes)
  changed_line_ranges(old, new)  -> [(start, end)] 1-based inclusive ranges of new/changed lines in `new`
  git_changed_files(root, base)  optional fallback when only git history exists

Windows notes: paths are always returned as forward-slash relative paths; names are compared with the platform's own
case rule (case-insensitive on Windows/macOS defaults) through `core.platform_compat.normalize_rel_path`.
Honest limit: the snapshot is a full copy of the non-ignored tree. It is capped (`max_total_bytes`); a repo above the
cap must be snapshotted per scope (pass `only=` globs), see `capture(only=...)`.

Standard library only.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from core.platform_compat import matches_any, safe_rmtree, run_process
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.platform_compat import matches_any, safe_rmtree, run_process

IGNORE_DIRS = {".git", ".planner", "__pycache__", ".venv", "venv", "env", "node_modules", ".pytest_cache", ".mypy_cache", ".tox",
               ".ruff_cache", "dist", "build", ".next", ".nuxt", ".turbo", ".cache", "coverage", ".idea", ".vscode", "target", "mutants",
               "reports", ".stryker-tmp"}
IGNORE_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo", ".tmp", ".log", ".sqlite-wal", ".sqlite-shm", ".db-wal", ".db-shm")
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 200_000_000
MAX_DIFF_LINES = 20_000


class WorktreeError(RuntimeError):
    pass


@dataclass
class Changes:
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)

    @property
    def touched(self) -> List[str]:
        """Files that exist after the change (added + modified)."""
        return sorted(set(self.added) | set(self.modified))

    @property
    def all(self) -> List[str]:
        return sorted(set(self.added) | set(self.modified) | set(self.deleted))

    def __bool__(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    def to_dict(self) -> dict:
        return {"added": self.added, "modified": self.modified, "deleted": self.deleted}


def _skip_name(name: str) -> bool:
    return name.endswith(IGNORE_SUFFIXES)


def iter_files(root, *, ignore_dirs: Iterable[str] = IGNORE_DIRS, follow_symlinks: bool = False) -> Iterable[Tuple[str, Path]]:
    """Yield (posix relative path, absolute path) for regular files, skipping ignored dirs, symlinks and junk."""
    base = Path(root)
    ig = set(ignore_dirs)
    for dirpath, dirs, files in os.walk(base, followlinks=follow_symlinks):
        dirs[:] = sorted(d for d in dirs if d not in ig and not (Path(dirpath) / d).is_symlink())
        for f in sorted(files):
            p = Path(dirpath) / f
            if p.is_symlink() or _skip_name(f):
                continue
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            yield p.relative_to(base).as_posix(), p


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root, *, only: Optional[Sequence[str]] = None, max_file_bytes: int = MAX_FILE_BYTES) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rel, p in iter_files(root):
        if only and not matches_any(rel, only):
            continue
        try:
            if p.stat().st_size > max_file_bytes:
                continue
            out[rel] = sha256_file(p)
        except OSError:
            continue
    return out


def capture(root, dest, *, only: Optional[Sequence[str]] = None, max_file_bytes: int = MAX_FILE_BYTES,
            max_total_bytes: int = MAX_TOTAL_BYTES) -> int:
    """Copy the non-ignored tree to `dest` (replacing it). Returns the number of files copied."""
    src, dst = Path(root), Path(dest)
    if not src.is_dir():
        raise WorktreeError(f"worktree not found: {src}")
    if dst.exists():
        safe_rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    n = total = 0
    for rel, p in iter_files(src):
        if only and not matches_any(rel, only):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > max_file_bytes:
            continue
        total += size
        if total > max_total_bytes:
            raise WorktreeError(f"baseline would exceed {max_total_bytes} bytes; snapshot per scope with only=[...]")
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)  # not copy2: metadata is irrelevant and slow on NTFS and /mnt/c
        n += 1
    return n


def changes(baseline, root, *, only: Optional[Sequence[str]] = None) -> Changes:
    before = hash_tree(baseline, only=only)
    after = hash_tree(root, only=only)
    ch = Changes()
    for rel, h in after.items():
        if rel not in before:
            ch.added.append(rel)
        elif before[rel] != h:
            ch.modified.append(rel)
    ch.deleted = [rel for rel in before if rel not in after]
    ch.added.sort(); ch.modified.sort(); ch.deleted.sort()
    return ch


def read_text(path) -> Optional[str]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def changed_line_ranges(old: Optional[str], new: str) -> List[Tuple[int, int]]:
    """1-based inclusive line ranges in `new` that are inserted or replaced relative to `old` (None/'' = whole file)."""
    new_lines = new.splitlines()
    if not new_lines:
        return []
    old_lines = (old or "").splitlines()
    if not old_lines:
        return [(1, len(new_lines))]
    if len(old_lines) > MAX_DIFF_LINES or len(new_lines) > MAX_DIFF_LINES:
        return [(1, len(new_lines))]  # too big to diff cheaply: treat everything as changed (never under-test)
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    out: List[Tuple[int, int]] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert") and j2 > j1:
            out.append((j1 + 1, j2))
    return _merge(out)


def _merge(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def diff_lines(baseline, root, rels: Iterable[str]) -> Dict[str, List[Tuple[int, int]]]:
    """{file: [(start, end)]} for each text file in `rels`, measured against the baseline copy."""
    out: Dict[str, List[Tuple[int, int]]] = {}
    for rel in rels:
        new = read_text(Path(root) / rel)
        if new is None:
            continue
        old = read_text(Path(baseline) / rel)
        rngs = changed_line_ranges(old, new)
        if rngs:
            out[rel] = rngs
    return out


def git_changed_files(root, base: str = "HEAD", *, timeout: float = 30.0) -> Optional[List[str]]:
    """Files changed vs `base` plus untracked files, or None when `root` is not a usable git checkout."""
    git = shutil.which("git")
    if not git:
        return None
    res = run_process([git, "-C", str(root), "rev-parse", "--is-inside-work-tree"], timeout=timeout)
    if not res.ok or res.stdout.strip() != "true":
        return None
    names: List[str] = []
    for args in (["diff", "--name-only", "-z", base, "--"], ["ls-files", "--others", "--exclude-standard", "-z"]):
        r = run_process([git, "-C", str(root), *args], timeout=timeout)
        if r.returncode != 0:
            if args[0] == "diff":  # unborn HEAD / bad base: fall back to tracked-modified only
                r = run_process([git, "-C", str(root), "ls-files", "-m", "-z"], timeout=timeout)
            if r.returncode != 0:
                return None
        names.extend(n for n in r.stdout.split("\0") if n)
    return sorted(set(n.replace("\\", "/") for n in names))
