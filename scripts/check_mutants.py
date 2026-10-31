#!/usr/bin/env python3
"""check_mutants.py - are the tests strong enough? Apply the planner's hand-written mutations, expect the tests to fail.

Standard library only, Python 3.9+. No network, no fork (works on Windows without WSL).
Run it AFTER a risky ticket passed (risk: high or tests_first: true). It is advisory evidence for the
review loop, not an acceptance gate of the ticket itself: the planner wrote the mutants without seeing
your code, so a STALE mutant means the planner guessed wrong, not that your code is bad.

Usage:
  python scripts/check_mutants.py --ticket docs/spec/tickets/T-005-pricing.md -- python -m pytest -q
  python scripts/check_mutants.py --ticket T-005.md --timeout 120 --tests tests -- npm test
The ticket holds a fenced block (one JSON object per line):
  ```mutants
  {"file": "src/pricing.py", "find": "price * 1.1", "replace": "price * 1.0"}
  ```
How it works: (1) the command must pass on the unchanged code; (2) for each mutant the `find` text must occur
exactly once in `file`; it is replaced IN PLACE, the command runs, and the original bytes are restored right
after (a backup is kept in .agent-run/mutant-backup/ and the restored file is hash-checked); (3) the tests must
FAIL for every mutant (KILLED). SURVIVED means the tests do not notice that behaviour change: ask for stronger tests.
It also scans test files for weak patterns (skip/xfail/.only, assert True, test functions without any assertion).
Refuses to mutate anything under tests/, docs/, scripts/, evals/, .agents/ or any test file, however the path is spelled
(mutants change source code only). Close editors that auto-save while it runs. A mutant that makes the code fail to compile or
import is reported as BROKE (it proves nothing), not as KILLED. If this program is stopped hard (window closed, power loss) the
mutated file stays on disk; the next run restores it from .agent-run/mutant-inflight.json before doing anything else.
Exit code: 0 all mutants killed, 1 survivors / stale mutants / failing baseline, 2 bad usage. Last line: RESULT: PASS|FAIL.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys

MUT_BLOCK = re.compile(r"```mutants[ \t]*\r?\n(.*?)```", re.S)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".agent-run", ".check-logs"}
JS_WEAK = re.compile(r"(\.only\(|\bxit\(|\bxdescribe\(|\.skip\(|\bit\.todo\(|expect\(true\)\.toBe\(true\)|expect\(1\)\.toBe\(1\))")
NEVER_MUTATE_DIRS = {"tests", "test", "__tests__", "evals", "eval", "docs", "scripts", ".agents", ".agent-run", ".check-logs", ".git"}
NEVER_MUTATE_FILES = re.compile(r"(^test_.*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$|^agents\.md$)")
BROKEN = re.compile(r"(SyntaxError|IndentationError|ModuleNotFoundError|ImportError|Cannot find module|SyntaxError:|error TS\d+|Unexpected token)")
INFLIGHT = os.path.join(".agent-run", "mutant-inflight.json")
PY_WEAK_TEXT = re.compile(r"(@pytest\.mark\.(skip|xfail)|@unittest\.skip|\bpytest\.skip\(|\bassert True\b|\bassert 1\b|\bself\.skipTest\()")


def load_mutants(ticket):
    with open(ticket, encoding="utf-8", errors="replace") as fh:
        text = fh.read(2_000_000)
    m = MUT_BLOCK.search(text)
    if not m:
        return None, ["no ```mutants block in %s" % ticket]
    out, errs = [], []
    for n, ln in enumerate(m.group(1).splitlines(), 1):
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
            if not isinstance(o, dict) or not all(isinstance(o.get(k), str) and o.get(k) for k in ("file", "find", "replace")) or o["find"] == o["replace"]:
                raise ValueError("needs a JSON object with different string fields file, find, replace")
            out.append(o)
        except (ValueError, RecursionError) as e:
            errs.append("mutants line %d: %s" % (n, str(e)[:100]))
    return out, errs


def weak_tests(tests_dirs):
    findings = []
    for base in tests_dirs:
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                path = os.path.join(dp, fn)
                if not re.search(r"(^test_.*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$)", fn):
                    continue
                try:
                    text = open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for i, ln in enumerate(text.splitlines(), 1):
                    if (fn.endswith(".py") and PY_WEAK_TEXT.search(ln)) or (not fn.endswith(".py") and JS_WEAK.search(ln)):
                        findings.append("%s:%d weak pattern: %s" % (path, i, ln.strip()[:80]))
                if fn.endswith(".py"):
                    try:
                        tree = ast.parse(text)
                    except SyntaxError:
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                            has = any(isinstance(n, ast.Assert) or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
                                      (n.func.attr.startswith("assert") or n.func.attr in ("raises", "fail"))) or
                                      (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("raises", "check", "expect")) or
                                      isinstance(n, ast.Raise) for n in ast.walk(node))
                            if not has:
                                findings.append("%s:%d test '%s' has no assertion" % (path, node.lineno, node.name))
    return findings


def run_cmd(cmd, timeout):
    shell = len(cmd) == 1
    if not shell:
        if cmd[0] in ("python", "python3", "py"):
            cmd = [sys.executable] + cmd[1:]
        else:
            cmd = [shutil.which(cmd[0]) or cmd[0]] + cmd[1:]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")  # no .pyc written while a mutant is in place
    kw = {} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(cmd[0] if shell else cmd, shell=shell, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, **kw)
    except FileNotFoundError as e:
        return 127, "command not found: %s" % e
    except OSError as e:
        return 126, "could not start: %s" % e
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out[-200_000:].decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.kill()
            proc.communicate(timeout=10)
        except Exception:
            pass
        return 124, "timeout"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def refused_path(root, rel):
    """Why a mutant file must not be touched, or ''. Works on the real, normalised path (symlinks, ./, .., case)."""
    try:
        full = os.path.realpath(os.path.join(root, rel))
        real_root = os.path.realpath(root)
        relp = os.path.relpath(full, real_root)
    except ValueError:
        return "path is on another drive"
    relp = os.path.normcase(relp).replace("\\", "/").lower()
    if relp.startswith("..") or os.path.isabs(relp):
        return "path is outside the project"
    parts = relp.split("/")
    if any(x in NEVER_MUTATE_DIRS for x in parts[:-1]) or parts[0] in NEVER_MUTATE_DIRS:
        return "mutants change source files only, never tests, docs, scripts or tool folders"
    if NEVER_MUTATE_FILES.search(parts[-1]):
        return "mutants change source files only, never test files"
    return ""


def write_inflight(rel, backup, digest):
    tmp = INFLIGHT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"file": rel, "backup": backup, "sha256": digest}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, INFLIGHT)


def recover_inflight(root):
    """A previous run was killed while a mutant was in place: put the original back first. Returns False if it cannot."""
    if not os.path.isfile(INFLIGHT):
        return True
    try:
        with open(INFLIGHT, encoding="utf-8") as fh:
            info = json.load(fh)
        full = os.path.join(root, info["file"])
        with open(info["backup"], "rb") as fh:
            original = fh.read()
        if sha(original) != info["sha256"]:
            raise ValueError("backup hash differs")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print("FATAL: an earlier run left a mutant in place and the backup cannot be used (%s). Restore the file from git "
              "(`git diff` shows it; `git checkout -- <file>`), then delete %s." % (str(e)[:80], INFLIGHT))
        return False
    with open(full, "wb") as fh:
        fh.write(original)
    os.remove(INFLIGHT)
    print("RECOVERED: an earlier run was stopped while mutating %s; the original file was put back." % info["file"])
    return True


def _raise_exit(signum, frame):
    raise SystemExit(130)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Apply planner-written mutants and expect the tests to fail.",
                                 usage="check_mutants.py --ticket PATH [--timeout S] [--tests DIR ...] -- COMMAND [ARGS...]")
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--tests", action="append", default=[])
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("give the test command after --")
    if not os.path.isfile(a.ticket):
        print("ticket not found: %s" % a.ticket)
        return 2
    muts, errs = load_mutants(a.ticket)
    if muts is None or errs:
        for e in errs:
            print("ERROR " + e)
        print("RESULT: FAIL")
        return 1
    root = os.getcwd()
    os.makedirs(os.path.join(".agent-run", "mutant-backup"), exist_ok=True)
    if not recover_inflight(root):
        print("RESULT: FAIL")
        return 1
    for sig in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), _raise_exit)
            except (ValueError, OSError):
                pass
    weak = weak_tests(a.tests or [d for d in ("tests", "test", "__tests__") if os.path.isdir(d)])
    for w in weak[:15]:
        print("WEAK " + w)
    code, out = run_cmd(cmd, a.timeout)
    if code != 0:
        print("BASELINE FAILS (exit %d): the tests must pass on the unchanged code before mutants mean anything." % code)
        print("\n".join(out.splitlines()[-8:]))
        print("RESULT: FAIL")
        return 1
    print("baseline: tests pass on the unchanged code")
    killed = survived = stale = broke = 0
    for i, mu in enumerate(muts, 1):
        rel = mu["file"].replace("\\", "/")
        full = os.path.abspath(os.path.join(root, rel))
        label = "M%d %s: %r -> %r" % (i, rel, mu["find"][:40], mu["replace"][:40])
        why = refused_path(root, rel)
        if why:
            print("REFUSED %s (%s)" % (label, why))
            stale += 1
            continue
        if not os.path.isfile(full):
            print("STALE   %s (file does not exist)" % label)
            stale += 1
            continue
        original = open(full, "rb").read()
        text = original.decode("utf-8", errors="surrogateescape")
        find, rep = mu["find"], mu["replace"]
        if text.count(find) == 0 and "\r\n" in text and "\n" in find:
            find, rep = find.replace("\n", "\r\n"), rep.replace("\n", "\r\n")
        n = text.count(find)
        if n != 1:
            print("%s %s (find text occurs %d times, need exactly 1)" % ("STALE  " if n == 0 else "AMBIG  ", label, n))
            stale += 1
            continue
        backup = os.path.join(".agent-run", "mutant-backup", "%d-%s" % (i, os.path.basename(full)))
        with open(backup, "wb") as fh:
            fh.write(original)
            fh.flush()
            os.fsync(fh.fileno())
        st = os.stat(full)
        write_inflight(rel, backup, sha(original))
        try:
            with open(full, "wb") as fh:
                fh.write(text.replace(find, rep, 1).encode("utf-8", errors="surrogateescape"))
            # a different modification time, so a cached .pyc of the original (same size, same second) is never reused
            os.utime(full, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
            code, out = run_cmd(cmd, a.timeout)
        finally:
            with open(full, "wb") as fh:
                fh.write(original)
            os.utime(full, ns=(st.st_atime_ns, st.st_mtime_ns))
            if sha(open(full, "rb").read()) != sha(original):
                print("FATAL: could not restore %s; copy it back from %s" % (rel, backup))
                return 1
            try:
                os.remove(INFLIGHT)
            except OSError:
                pass
        if code in (124, 126, 127):
            broke += 1
            print("ERRORED %s (exit %d: %s); this proves nothing about the tests" % (label, code, {124: "timeout", 126: "could not start", 127: "command not found"}[code]))
        elif code != 0 and BROKEN.search(out):
            broke += 1
            print("BROKE   %s (the mutant did not compile or import; it proves nothing about the tests: write a mutant that still runs)" % label)
        elif code != 0:
            killed += 1
            print("KILLED  %s (exit %d)" % (label, code))
        else:
            survived += 1
            print("SURVIVED %s: the tests still pass, so they do not check this behaviour" % label)
    print("mutants: %d killed, %d survived, %d stale/refused, %d broke/errored; weak test patterns: %d" % (killed, survived, stale, broke, len(weak)))
    ok = survived == 0 and stale == 0 and broke == 0 and killed > 0
    stale += broke
    if stale:
        print("Stale or refused mutants mean the planner guessed the code wrong: paste this output to the planner for new mutants.")
    if survived:
        print("Survivors: ask the planner for a fix ticket that strengthens the tests (protected path, so the ticket must list them).")
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
