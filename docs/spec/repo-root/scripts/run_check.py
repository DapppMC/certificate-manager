#!/usr/bin/env python3
"""run_check.py - run ONE verification command and print an unambiguous PASS/FAIL signal.

Standard library only, Python 3.9+. No network. Copy it to <repo>/scripts/run_check.py (ticket T-000).

Why: token-saving tools and terse styles can truncate or reword command output. This wrapper
keeps the signal stable: the last two lines are always `EXIT=<code>` and `RESULT: PASS|FAIL`,
the full output goes to .check-logs/<name>.log, and only a short tail is printed.

Usage:
  python scripts/run_check.py --name unit-tests -- npm test
  python scripts/run_check.py --name lint --fail-on "warning:" -- ruff check .
  python scripts/run_check.py --name api --timeout 120 --tail 40 -- "pytest -q && echo done"

Options:
  --name NAME       label used for the log file (default: command word)
  --tail N          lines of output to show (default 15 on PASS, 40 on FAIL)
  --timeout SECS    kill the command after this many seconds (default 600) -> FAIL
  --fail-on TEXT    also FAIL if TEXT appears in the output (repeatable, case-insensitive);
                    use it to catch "no tests ran" or "0 passed"
  --require TEXT    also FAIL unless TEXT appears in the output (repeatable)
  --ticket LABEL    ticket id for the loop guard (default: the attempt name in .agent-run/current, written by
                    collect_evidence.py --start, so a retry starts with a clean count; without any label only the
                    repeated-failure guard runs)
  --no-guard        switch the loop guard off. HUMAN USE ONLY: the executor must never pass it; the log header records it
                    and the evidence bundle raises a flag.
Loop guard (prints lines starting `GUARD:` BEFORE the EXIT= line; the exit code stays the command's own, and when a guard
fires the RESULT line ends with ` | GUARD: <kind>` so a tool that keeps only the last two lines still sees it):
  GUARD: NO PROGRESS  the same check failed with the same error and no file changed since the last run
  GUARD: STOP         the same check failed with the same error 3 times: do not retry, write the escalation report
  GUARD: BUDGET       more than 12 checks per ticket were run (a batch of N tickets gets 12*N): stop and report
It stores hashes only (never output) in .agent-run/ledger.jsonl.
Output is streamed: only the first 200 KB and the last 800 KB are kept (a runaway print loop cannot fill memory or disk).
Windows notes: give the command as separate words (`-- npm test`), not one quoted string. Separate words
run without a shell: the first word is resolved with PATH (so `npm` finds `npm.cmd`) and `python` /
`python3` run with the interpreter that runs this wrapper (so the Microsoft Store `python` alias and a
missing `python` do not matter). A single quoted string runs in the system shell (cmd.exe on Windows,
so PowerShell syntax does not work there); use it only for `&&` chains.
Exit code is the command's exit code, or 1 if a --fail-on/--require rule failed, or 124 on timeout (the whole process tree is killed).
The log header is `[exit <command code>, final <EXIT= value>, <secs>s]` (plus `, no-guard` when the guard was off).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

LEDGER = os.path.join(".agent-run", "ledger.jsonl")
LEDGER_KEEP, STOP_AFTER, BUDGET = 500, 3, 12
ERR_LINE = re.compile(r"(?i)(error|fail|assert|exception|traceback|not found|cannot|denied|unable|refused|timeout)")
HEAD_CAP, TAIL_CAP = 200_000, 800_000
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def _h(text):
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _err_hash(out):
    lines = [x for x in out.splitlines() if x.strip()]
    pick = next((x for x in lines if ERR_LINE.search(x)), lines[-1] if lines else "")
    return _h(re.sub(r"\d+", "N", pick.lower())[:200])


def _git(args):
    p = subprocess.run(["git"] + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    return p.stdout[:2_000_000]


def _state_hash():
    """Hash of the working tree changes (tracked diff plus untracked names, sizes and mtimes), ignoring the tool folders."""
    try:
        ex = ["--", ".", ":(exclude).check-logs", ":(exclude).agent-run"]
        parts = [_git(["diff", "HEAD"] + ex), _git(["ls-files", "-o", "--exclude-standard", "-z"] + ex[1:])]
        for rel in parts[1].decode("utf-8", errors="replace").split("\0"):
            if rel:
                try:
                    st = os.stat(rel)
                    parts.append(("%s|%d|%d" % (rel, st.st_size, st.st_mtime_ns)).encode())
                except OSError:
                    pass
        return hashlib.sha1(b"\n".join(parts)).hexdigest()[:12]
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def _ticket_label(arg):
    """Return (label, budget). `.agent-run/current` holds the attempt name on line 1 and `budget=N` on line 2."""
    if arg:
        return arg, BUDGET
    if os.environ.get("RUN_CHECK_TICKET"):
        return os.environ["RUN_CHECK_TICKET"], BUDGET
    try:
        with open(os.path.join(".agent-run", "current"), encoding="utf-8") as fh:
            lines = [x.strip() for x in fh.read().splitlines() if x.strip()]
    except OSError:
        return "", BUDGET
    label = lines[0] if lines else ""
    budget = BUDGET
    for x in lines[1:]:
        m = re.fullmatch(r"budget=(\d{1,4})", x)
        if m and int(m.group(1)) > 0:
            budget = int(m.group(1))
    return label, budget


def _read_ledger():
    """Valid rows only: a torn line, a non-object or an old-schema row is skipped instead of crashing the check."""
    rows = []
    try:
        with open(LEDGER, encoding="utf-8", errors="replace") as fh:
            for ln in fh.read().splitlines()[-LEDGER_KEEP:]:
                try:
                    r = json.loads(ln)
                except (ValueError, RecursionError):
                    continue
                if isinstance(r, dict) and isinstance(r.get("cmd"), str):
                    r.setdefault("err", "")
                    r.setdefault("state", "")
                    r.setdefault("ticket", "")
                    rows.append(r)
    except OSError:
        pass
    return rows


def guard(ticket, cmd, name, code, passed, out, budget=BUDGET):
    """Append this run to the ledger and return a list of (kind, message) for the GUARD lines to print."""
    entry = {"t": int(time.time()), "ticket": ticket, "name": name, "cmd": _h("\0".join(cmd)), "exit": code,
             "err": "" if passed else _err_hash(out), "state": _state_hash(), "pass": passed}
    rows = _read_ledger()
    rows.append(entry)
    try:
        os.makedirs(".agent-run", exist_ok=True)
        with open(LEDGER, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows[-LEDGER_KEEP:]) + "\n")
    except OSError:
        pass
    found = []
    if not passed:
        same = [r for r in rows if r["cmd"] == entry["cmd"] and r.get("ticket", "") == ticket]
        streak = []
        for r in reversed(same):
            if r.get("pass") or r["err"] != entry["err"]:
                break
            streak.append(r)
        if len(streak) >= STOP_AFTER:
            found.append(("STOP", "GUARD: STOP: this check failed %d times with the same error. Do not run it again. Write the escalation report now." % len(streak)))
        elif len(streak) >= 2 and streak[1]["state"] == entry["state"] and entry["state"] != "nogit":
            found.append(("NO PROGRESS", "GUARD: NO PROGRESS: same error as the last run and no file changed since. Change something (or stop and report) before running it again."))
    if ticket:
        used = len([r for r in rows if r.get("ticket") == ticket])
        if used > budget:
            found.append(("BUDGET", "GUARD: BUDGET: %d checks have been run for %s (limit %d). Stop and write the report." % (used, ticket, budget)))
    return found


class Capture(threading.Thread):
    """Reads the child's output as it is produced; keeps the head and the tail only."""

    def __init__(self, stream):
        threading.Thread.__init__(self, daemon=True)
        self.stream, self.head, self.tail, self.total = stream, bytearray(), bytearray(), 0

    def run(self):
        try:
            while True:
                chunk = self.stream.read1(65536) if hasattr(self.stream, "read1") else self.stream.read(65536)
                if not chunk:
                    break
                self.total += len(chunk)
                room = HEAD_CAP - len(self.head)
                if room > 0:
                    self.head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self.tail += chunk
                    if len(self.tail) > 2 * TAIL_CAP:
                        del self.tail[:len(self.tail) - TAIL_CAP]
        except (OSError, ValueError):
            pass

    def text(self):
        head, tail = bytes(self.head), bytes(self.tail[-TAIL_CAP:])
        dropped = self.total - len(head) - len(tail)
        s = head.decode("utf-8", errors="replace")
        if dropped > 0:
            s += "\n[run_check] ... %d bytes of output cut from the middle ...\n" % dropped
        return s + tail.decode("utf-8", errors="replace")


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def run_command(cmd, shell, timeout):
    """Run the command, stream its output, kill the whole tree on timeout. Returns (code, output)."""
    kw = {} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(cmd[0] if shell else cmd, shell=shell, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **kw)
    except FileNotFoundError as e:
        return 127, "[run_check] command not found: %s\n" % e
    except OSError as e:
        return 126, "[run_check] could not start the command: %s\n" % e
    cap = Capture(proc.stdout)
    cap.start()
    timed_out = False
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out, code = True, 124
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    cap.join(timeout=5)       # a grandchild that keeps the pipe open must not hang the wrapper
    out = cap.text()
    if timed_out:
        out += "\n[run_check] TIMEOUT after %ds\n" % timeout
    return code, out


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Run a command and print EXIT=<code> and RESULT: PASS|FAIL.",
                                 usage="run_check.py [options] -- COMMAND [ARGS...]")
    ap.add_argument("--name")
    ap.add_argument("--tail", type=int)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--fail-on", action="append", default=[])
    ap.add_argument("--require", action="append", default=[])
    ap.add_argument("--ticket")
    ap.add_argument("--no-guard", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("give a command after --")
    shell = len(cmd) == 1
    if not shell:
        if cmd[0] in ("python", "python3", "py"):
            cmd = [sys.executable] + (cmd[2:] if cmd[0] == "py" and len(cmd) > 1 and cmd[1] == "-3" else cmd[1:])
        else:
            cmd = [shutil.which(cmd[0]) or cmd[0]] + cmd[1:]
    if a.name:
        base = a.name
    else:
        first = cmd[0].split()[0] if shell and cmd[0].split() else cmd[0]
        base = os.path.basename(first.replace("\\", "/"))
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", base)[:40] or "check"
    if name.split(".")[0].upper() in RESERVED:
        name = "c-" + name
    os.makedirs(".check-logs", exist_ok=True)
    log = os.path.join(".check-logs", name + ".log")
    start = time.time()
    code, out = run_command(cmd, shell, a.timeout)
    reasons = []
    low = out.lower()
    for t in a.fail_on:
        if t.lower() in low:
            reasons.append("output contains %r" % t)
    for t in a.require:
        if t.lower() not in low:
            reasons.append("output lacks %r" % t)
    passed = code == 0 and not reasons
    final = code if code != 0 else (0 if passed else 1)
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("$ %s\n[exit %s, final %s, %.1fs%s]\n%s" % (" ".join(cmd), code, final, time.time() - start, ", no-guard" if a.no_guard else "", out))
    lines = out.splitlines()
    n = a.tail if a.tail is not None else (15 if passed else 40)
    if lines:
        print("\n".join(lines[-n:]))
    print("[run_check] full output: %s" % log)
    for r in reasons:
        print("[run_check] rule failed: %s" % r)
    kinds = []
    if not a.no_guard:
        try:
            label, budget = _ticket_label(a.ticket)
            for kind, msg in guard(label, cmd, name, final, passed, out, budget):
                print(msg)
                kinds.append(kind)
        except Exception as e:      # the guard is advisory: it must never hide the real result
            print("[run_check] guard error ignored: %s" % str(e)[:120])
    print("EXIT=%d" % final)
    print("RESULT: %s%s" % ("PASS" if passed else "FAIL", (" | GUARD: " + ", ".join(kinds)) if kinds else ""))
    return final


if __name__ == "__main__":
    sys.exit(main())
