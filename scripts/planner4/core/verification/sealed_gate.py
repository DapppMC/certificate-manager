"""sealed_gate.py - Sealed Oracle verification gate (AG6 "The Inquisitor").

Threat model: the worker is an LLM that optimises `exit_code == 0` (Goodhart). It will weaken assertions, skip
tests, delete tests, edit conftest / pytest.ini, read the oracle, or write code that special-cases the tests.
This gate therefore:

  1. keeps the acceptance tests in a SEALED VAULT outside every worker lease, with a sha256 manifest (tamper =
     fail closed) and read-only files;
  2. runs them in a CLEAN COPY of the worker tree (symlinks dropped, size-capped, secrets scrubbed from the
     environment, optional network namespace on Linux) with a hard timeout that kills the whole process tree;
  3. never trusts exit code alone: JUnit XML must show tests > 0, no failures/errors, NO skips, and at least the
     number of tests recorded at sealing time (pytest exit code 5 "nothing collected" is a failure, not a pass);
  4. does not leak the oracle back to the worker: by default the result carries test IDS and counts only. The
     assertion text of a failing sealed test (`assert 3 == 5`) would reveal the expected value;
  5. runs the AST anti-weakening audit (weakening.py) on the worker's own tests and test configuration;
  6. fails closed when no sealed tests exist for a ticket unless the caller explicitly allows it (the prototype
     returned `passed: True` for a missing vault, which silently disables the oracle);
  7. optionally AUTHENTICATES the manifest with HMAC-SHA256 under AG6's own key (environment variable
     PLANNER_INQUISITOR_KEY, never a file or an argument). A sha256 manifest only detects accidents: whoever can edit a
     vault file can edit the manifest next to it. With a key configured, `seal` signs the manifest and `verify_vault`
     refuses a manifest without a valid MAC (so stripping the MAC is a downgrade attack that fails closed). The key is
     separate from the Lawgiver's (PLANNER_LAWGIVER_KEY): the author of the contract cannot forge the oracle. The key
     lives in the orchestrator's environment only; worker processes get a scrubbed environment and never see it.

Honest limits: code running inside the pytest process can still lie to pytest. The gate raises the cost of
cheating (counts, manifest, clean copy, mutation score in mutation_gate.py), it is not a container. For stronger
isolation run the whole gate in a container / separate OS user - see docs/ENGINE_AUDIT.md.

Standard library only (pytest must exist in the TARGET environment). Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

try:
    from core.platform_compat import (build_sandbox_env, find_executable, is_relative_to, make_tree_readonly, run_process,
                                      safe_rmtree, atomic_write_text)
    from core.verification import weakening
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.platform_compat import (build_sandbox_env, find_executable, is_relative_to, make_tree_readonly, run_process,
                                      safe_rmtree, atomic_write_text)
    from core.verification import weakening

DEFAULT_SEALED_ROOT = ".planner/sealed"
ENV_SEAL_KEY = "PLANNER_INQUISITOR_KEY"
MIN_KEY_BYTES = 16
MAX_ATTEMPTS = 3
TICKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")
IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".tox", ".planner"}
PYTEST_EXIT = {0: "ok", 1: "tests failed", 2: "interrupted", 3: "internal error", 4: "usage error", 5: "no tests collected"}


class GateError(RuntimeError):
    pass


def sealed_root() -> Path:
    return Path(os.environ.get("PLANNER_SEALED_ROOT", DEFAULT_SEALED_ROOT)).resolve()


def report_dir() -> Path:
    return Path(os.environ.get("PLANNER_REPORT_DIR", ".planner/reports")).resolve()


def check_ticket_id(ticket_id: str) -> str:
    if not isinstance(ticket_id, str) or not TICKET_ID_RE.match(ticket_id) or ticket_id in (".", ".."):
        raise GateError(f"invalid ticket id {ticket_id!r}")
    return ticket_id


# -------------------------------------------------------------------------------------------- path guard
def path_guard(path: str, worktree, *, sealed: Optional[Path] = None) -> Path:
    """Resolve `path` (relative paths are taken relative to the WORKTREE, not the process CWD) and refuse anything
    inside the sealed vault or outside the worktree. Symlinks are resolved first so a link cannot smuggle access."""
    wt = Path(worktree).resolve()
    sroot = (sealed or sealed_root()).resolve()
    raw = Path(path)
    real = (raw if raw.is_absolute() else wt / raw).resolve()
    if is_relative_to(real, sroot):
        raise PermissionError("Access Denied: Path is within sealed oracle storage")
    if not is_relative_to(real, wt):
        raise PermissionError("Access Denied: Path is outside assigned worktree")
    return real


# -------------------------------------------------------------------------------------------- copying
@dataclass
class CopyStats:
    files: int = 0
    bytes: int = 0
    skipped_symlinks: List[str] = field(default_factory=list)
    skipped_large: List[str] = field(default_factory=list)


def copy_tree_safe(src, dst, *, max_file_bytes: int = 50_000_000, max_total_bytes: int = 500_000_000,
                   ignore_dirs: Iterable[str] = IGNORE_DIRS) -> CopyStats:
    """Copy `src` to `dst` without following symlinks (a link to ~/.ssh must never enter the sandbox), skipping
    heavy / irrelevant directories and enforcing a size budget so a disk bomb cannot fill the machine."""
    stats = CopyStats()
    src_p, dst_p = Path(src), Path(dst)
    dst_p.mkdir(parents=True, exist_ok=True)
    skip = set(ignore_dirs)
    for dirpath, dirs, files in os.walk(src_p, followlinks=False):
        rel_dir = Path(dirpath).relative_to(src_p)
        kept = []
        for d in dirs:
            full = Path(dirpath) / d
            if full.is_symlink():
                stats.skipped_symlinks.append((rel_dir / d).as_posix())
            elif d not in skip:
                kept.append(d)
        dirs[:] = kept
        (dst_p / rel_dir).mkdir(parents=True, exist_ok=True)
        for f in files:
            full = Path(dirpath) / f
            rel = (rel_dir / f).as_posix()
            if full.is_symlink():
                stats.skipped_symlinks.append(rel)
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                stats.skipped_large.append(rel)
                continue
            stats.bytes += size
            if stats.bytes > max_total_bytes:
                raise GateError(f"worktree exceeds the {max_total_bytes} byte sandbox budget")
            shutil.copyfile(full, dst_p / rel_dir / f)
            try:
                shutil.copystat(full, dst_p / rel_dir / f)
            except OSError:
                pass
            stats.files += 1
    return stats


# -------------------------------------------------------------------------------------------- vault
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_tree(root: Path) -> dict:
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
        for f in sorted(files):
            if f.endswith((".pyc", ".pyo")):
                continue
            full = Path(dirpath) / f
            out[full.relative_to(root).as_posix()] = _sha256(full)
    return out


def _unshare_prefix() -> List[str]:
    """Linux network namespace wrapper when the kernel allows unprivileged user namespaces; else no isolation."""
    if not sys.platform.startswith("linux"):
        return []
    exe = find_executable("unshare")
    if not exe:
        return []
    probe = run_process([exe, "-rn", "true"], timeout=5)
    return [exe, "-rn"] if probe.ok else []


_PYTEST_INI = "[pytest]\naddopts =\ncache_dir = .gate_cache\n"


def _pytest_cmd(python: str, sealed_dir: Path, ini: Path, junit: Path, extra: Sequence[str], collect_only: bool = False) -> List[str]:
    cmd = [python, "-m", "pytest", str(sealed_dir), "-c", str(ini), "--rootdir", str(sealed_dir), "--confcutdir", str(sealed_dir),
           "-p", "no:cacheprovider", "--import-mode=importlib", "-q", "--tb=no", "-rN", "--disable-warnings"]
    if collect_only:
        cmd += ["--collect-only"]
    else:
        cmd += [f"--junitxml={junit}"]
    return cmd + list(extra)


def static_test_count(tests_dir) -> int:
    """Number of test cases in a directory of Python test files, counted from the AST (parametrize literals expanded)."""
    total = 0
    for dirpath, dirs, files in os.walk(tests_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
        for f in files:
            if not f.endswith(".py") or not (f.startswith("test_") or f.endswith("_test.py")):
                continue
            try:
                prof = weakening.analyze_python(Path(dirpath, f).read_text(encoding="utf-8", errors="replace"), f)
            except OSError:
                continue
            total += sum(max(1, p.param_cases or 1) for p in prof.funcs.values())
    return total


def _seal_key(explicit=None) -> Optional[bytes]:
    """The HMAC key of the Inquisitor: the explicit argument, else the environment. Too-short keys are refused (a 3-byte key
    is a checksum, not a MAC)."""
    raw = explicit if explicit is not None else os.environ.get(ENV_SEAL_KEY)
    if raw is None or raw == "" or raw == b"":
        return None
    key = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise GateError(f"the HMAC key ({ENV_SEAL_KEY}) must be at least {MIN_KEY_BYTES} bytes")
    return bytes(key)


def manifest_mac(manifest: dict, key: bytes) -> str:
    """HMAC-SHA256 over the canonical JSON of the manifest without its own `hmac` field."""
    body = {k: v for k, v in manifest.items() if k != "hmac"}
    return hmac.new(key, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"), hashlib.sha256).hexdigest()


def seal(ticket_id: str, tests_dir, *, root: Optional[Path] = None, sealed_by: str = "AG6", expected_tests: Optional[int] = None,
         hmac_key=None, **_compat) -> dict:
    """AG6: move acceptance tests into the vault. Writes manifest.json (sha256 per file, expected test count) and makes
    the files read-only. Re-sealing an existing ticket replaces it (new epoch). With an HMAC key (argument or
    PLANNER_INQUISITOR_KEY) the manifest is authenticated too."""
    tid = check_ticket_id(ticket_id)
    vault = (root or sealed_root()) / tid
    src = Path(tests_dir)
    if not src.is_dir():
        raise GateError(f"tests directory not found: {src}")
    if vault.exists():
        safe_rmtree(vault)
    tests_dst = vault / "tests"
    stats = copy_tree_safe(src, tests_dst, ignore_dirs=("__pycache__", ".pytest_cache"))
    if stats.files == 0:
        raise GateError("no files to seal")
    # Contract-first: the sealed tests are written BEFORE the implementation exists, so `pytest --collect-only` would
    # fail on the import of the not-yet-written module. The expected count is therefore derived statically (AST).
    expected = expected_tests if expected_tests is not None else static_test_count(tests_dst)
    if not expected:
        safe_rmtree(vault)
        raise GateError("sealing refused: no test functions found in the supplied directory")
    manifest = {"ticket": tid, "sealed_by": sealed_by, "sealed_at": time.time(), "expected_tests": expected,
                "files": _hash_tree(tests_dst), "schema": 1}
    key = _seal_key(hmac_key)
    if key:
        manifest["auth"] = "hmac-sha256"
        manifest["hmac"] = manifest_mac(manifest, key)
    atomic_write_text(vault / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    make_tree_readonly(vault)
    return manifest


def verify_vault(ticket_id: str, root: Optional[Path] = None, hmac_key=None) -> tuple:
    """-> (ok, problems, manifest). Any extra, missing or modified file in the vault fails closed. With an HMAC key (argument or
    PLANNER_INQUISITOR_KEY) the manifest must also carry a valid MAC; without a key a MAC cannot be checked and only the
    sha256 manifest is."""
    tid = check_ticket_id(ticket_id)
    vault = (root or sealed_root()) / tid
    mf = vault / "manifest.json"
    if not vault.is_dir() or not mf.is_file():
        return False, ["NO_SEALED_TESTS"], None
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, [f"VAULT_MANIFEST_UNREADABLE: {exc}"], None
    actual = _hash_tree(vault / "tests")
    problems = []
    try:
        key = _seal_key(hmac_key)
    except GateError as exc:
        return False, [f"VAULT_KEY_INVALID: {exc}"], manifest
    if key:
        mac = manifest.get("hmac")
        if not isinstance(mac, str) or manifest.get("auth") != "hmac-sha256":
            problems.append("VAULT_UNAUTHENTICATED: an HMAC key is configured but the manifest carries no valid HMAC")
        elif not hmac.compare_digest(mac, manifest_mac(manifest, key)):
            problems.append("VAULT_HMAC_INVALID: the manifest was not written by the holder of the key or was edited afterwards")
    for rel, want in manifest.get("files", {}).items():
        if rel not in actual:
            problems.append(f"VAULT_FILE_MISSING: {rel}")
        elif actual[rel] != want:
            problems.append(f"VAULT_FILE_MODIFIED: {rel}")
    for rel in actual:
        if rel not in manifest.get("files", {}):
            problems.append(f"VAULT_FILE_ADDED: {rel}")
    return (not problems), problems, manifest


# -------------------------------------------------------------------------------------------- junit
def parse_junit(path: Path) -> dict:
    """-> {tests, failures, errors, skipped, failed: [ids], passed: [ids], skipped_ids: [ids]}. Never raises."""
    res = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "failed": [], "passed": [], "skipped_ids": [], "ok": False}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return res
    for case in root.iter("testcase"):
        tid = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        res["tests"] += 1
        if case.find("failure") is not None:
            res["failures"] += 1
            res["failed"].append(tid)
        elif case.find("error") is not None:
            res["errors"] += 1
            res["failed"].append(tid)
        elif case.find("skipped") is not None:
            res["skipped"] += 1
            res["skipped_ids"].append(tid)
        else:
            res["passed"].append(tid)
    res["ok"] = True
    return res


# -------------------------------------------------------------------------------------------- the gate
@dataclass
class GateResult:
    passed: bool
    reason: str = ""
    ticket: str = ""
    returncode: Optional[int] = None
    timed_out: bool = False
    duration: float = 0.0
    counts: dict = field(default_factory=dict)
    failed_tests: List[str] = field(default_factory=list)
    vault_problems: List[str] = field(default_factory=list)
    weakening: List[dict] = field(default_factory=list)
    output: str = ""
    sandbox: dict = field(default_factory=dict)
    network_isolated: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @property
    def message(self) -> str:  # prototype-compatible field
        return self.reason


def run_isolated_verification(worktree, ticket_id: str, *, root: Optional[Path] = None, timeout: float = 120.0,
                              reveal: str = "ids", allow_missing: bool = False, allow_skips: bool = False,
                              python: str = sys.executable, extra_pytest_args: Sequence[str] = (),
                              extra_env: Optional[dict] = None, isolate_network: str = "auto",
                              command: Optional[Sequence[str]] = None, max_total_bytes: int = 500_000_000) -> GateResult:
    """Run the sealed acceptance tests of `ticket_id` against a clean copy of `worktree`.

    reveal: 'none' (counts only) | 'ids' (+ failing test ids, default for workers) | 'summary' (+ pytest short
            summary with assertion text; for the Inquisitor / orchestrator only, never forward to a worker).
    command: replace the pytest invocation (for example a Vitest / fast-check runner). Placeholders {tree},
            {sealed}, {junit}, {python}. Pass/fail then comes from the return code and the JUnit file if present.
    """
    tid = check_ticket_id(ticket_id)
    started = time.monotonic()
    ok, problems, manifest = verify_vault(tid, root)
    if not ok:
        if problems == ["NO_SEALED_TESTS"] and allow_missing:
            return GateResult(True, "No sealed tests for ticket (explicitly allowed)", tid, duration=time.monotonic() - started)
        return GateResult(False, "NO_SEALED_TESTS: refusing to pass a ticket without an oracle" if problems == ["NO_SEALED_TESTS"]
                          else "VAULT_TAMPERED: " + "; ".join(problems[:5]), tid, vault_problems=problems)
    vault = (root or sealed_root()) / tid
    wt = Path(worktree).resolve()
    if not wt.is_dir():
        return GateResult(False, f"worktree not found: {wt}", tid)

    with tempfile.TemporaryDirectory(prefix="sealed_", ignore_cleanup_errors=True) as sandbox:
        sb = Path(sandbox)
        try:
            stats = copy_tree_safe(wt, sb / "tree", max_total_bytes=max_total_bytes)
            copy_tree_safe(vault / "tests", sb / "sealed", ignore_dirs=("__pycache__", ".pytest_cache"))
        except GateError as exc:
            return GateResult(False, f"SANDBOX_REFUSED: {exc}", tid)
        ini = sb / "pytest.ini"
        ini.write_text(_PYTEST_INI, encoding="utf-8")
        junit = sb / "report.xml"
        env = build_sandbox_env({"PYTHONPATH": str(sb / "tree"), "PYTHONHASHSEED": "0", **(extra_env or {})})
        if command:
            fmt = {"tree": str(sb / "tree"), "sealed": str(sb / "sealed"), "junit": str(junit), "python": python}
            cmd = [c.format(**fmt) for c in command]
        else:
            cmd = _pytest_cmd(python, sb / "sealed", ini, junit, extra_pytest_args)
        prefix = _unshare_prefix() if isolate_network in ("auto", "yes") else []
        if isolate_network == "yes" and not prefix:
            return GateResult(False, "NETWORK_ISOLATION_UNAVAILABLE: 'yes' was requested but unshare -rn is not usable", tid)
        proc = run_process(prefix + cmd, cwd=str(sb / "tree"), env=env, timeout=timeout)
        j = parse_junit(junit)
        counts = {k: j[k] for k in ("tests", "failures", "errors", "skipped")}
        sbinfo = {"files": stats.files, "bytes": stats.bytes, "skipped_symlinks": stats.skipped_symlinks[:20],
                  "skipped_large": stats.skipped_large[:20]}
        result = GateResult(False, "", tid, proc.returncode, proc.timed_out, time.monotonic() - started, counts, j["failed"][:50],
                            sandbox=sbinfo, network_isolated=bool(prefix))
        if proc.error:
            result.reason = f"RUNNER_FAILED: {proc.error}"
        elif proc.timed_out:
            result.reason = f"TIMEOUT after {timeout:g}s (process tree killed)"
        elif command is None and proc.returncode == 5:
            result.reason = "NO_TESTS_COLLECTED: pytest exit code 5 is a failure, not a pass"
        elif command is None and not j["ok"]:
            result.reason = f"NO_JUNIT_REPORT: runner exited {proc.returncode} without a readable report"
        elif command is None and j["tests"] == 0:
            result.reason = "ZERO_TESTS_RAN"
        elif command is None and manifest.get("expected_tests") and j["tests"] < int(manifest["expected_tests"]):
            result.reason = f"TESTS_MISSING: ran {j['tests']} of {manifest['expected_tests']} sealed tests"
        elif j["failures"] or j["errors"] or (proc.returncode not in (0,)):
            result.reason = f"SEALED_TESTS_FAILED: {PYTEST_EXIT.get(proc.returncode, proc.returncode)}"
        elif j["skipped"] and not allow_skips:
            result.reason = f"SEALED_TESTS_SKIPPED: {j['skipped']} skipped (a skipped oracle is not a passed oracle)"
        else:
            result.passed = True
            result.reason = "ALL_SEALED_TESTS_PASSED"
        if reveal == "summary":
            result.output = (proc.stdout[-1500:] + ("\n" + proc.stderr[-500:] if proc.stderr.strip() else "")).strip()
        elif reveal == "ids" and not result.passed:
            result.output = "failed: " + ", ".join(j["failed"][:20]) if j["failed"] else ""
    return result


# -------------------------------------------------------------------------------------------- baseline + weakening
def capture_baseline(worktree, dest) -> int:
    """Copy only test files and test-configuration files (the things check_weakening compares) to `dest`."""
    wt, dst = Path(worktree), Path(dest)
    n = 0
    for dirpath, dirs, files in os.walk(wt):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            rel = (Path(dirpath) / f).relative_to(wt).as_posix()
            if weakening.is_test_file(rel) or f in weakening.CONFIG_NAMES:
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not (Path(dirpath) / f).is_symlink():
                    shutil.copyfile(Path(dirpath) / f, target)
                    n += 1
    return n


def check_weakening(before_dir, after_dir, **kw) -> List[str]:
    """Prototype-compatible wrapper: returns human readable violation strings."""
    return [str(v) for v in weakening.check_weakening(before_dir, after_dir, **kw)]


# -------------------------------------------------------------------------------------------- CLI
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="sealed_gate", description="Sealed oracle gate (seal | run | verify | weakening | guard)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("seal"); s.add_argument("ticket"); s.add_argument("tests_dir"); 
    r = sub.add_parser("run"); r.add_argument("ticket"); r.add_argument("worktree"); r.add_argument("--timeout", type=float, default=120)
    r.add_argument("--reveal", choices=["none", "ids", "summary"], default="ids"); r.add_argument("--allow-missing", action="store_true")
    v = sub.add_parser("verify"); v.add_argument("ticket")
    w = sub.add_parser("weakening"); w.add_argument("before"); w.add_argument("after"); w.add_argument("--allow-config", nargs="*", default=[])
    g = sub.add_parser("guard"); g.add_argument("path"); g.add_argument("worktree")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    try:
        if a.cmd == "seal":
            print(json.dumps(seal(a.ticket, a.tests_dir), indent=2))
        elif a.cmd == "run":
            res = run_isolated_verification(a.worktree, a.ticket, timeout=a.timeout, reveal=a.reveal, allow_missing=a.allow_missing)
            print(json.dumps(res.to_dict(), indent=2))
            return 0 if res.passed else 1
        elif a.cmd == "verify":
            ok, problems, _ = verify_vault(a.ticket)
            print(json.dumps({"ok": ok, "problems": problems}))
            return 0 if ok else 1
        elif a.cmd == "weakening":
            vs = weakening.check_weakening(a.before, a.after, allow_config=a.allow_config)
            print(json.dumps([x.to_dict() for x in vs], indent=2))
            return 0 if not vs else 1
        elif a.cmd == "guard":
            print(path_guard(a.path, a.worktree))
    except (GateError, PermissionError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
