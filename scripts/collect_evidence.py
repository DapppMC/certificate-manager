#!/usr/bin/env python3
"""collect_evidence.py - the HUMAN's checkpoint and evidence tool for one ticket (or one batch).

Standard library only, Python 3.9+. No network. Run it yourself in YOUR terminal (cmd or PowerShell);
the executor agent never runs it and never edits it (it lives in scripts/, a protected path).

Why: the planner (Claude on the web) cannot see your repo, and an agent's own report can be wrong or
optimistic. This script records a baseline BEFORE the session and builds a short, secret-redacted
evidence block AFTER it, from git and from the .check-logs the run_check.py wrapper wrote. The block
carries a hash and consistency flags. It proves consistency, not honesty: always also re-run the
ticket's verification command yourself and paste its last two lines.

Commands (LABEL = ticket id such as T-005, or a batch id such as B1):
  python scripts/collect_evidence.py --start LABEL      before the session: needs a clean tree on a normal branch; makes tag pre/LABEL
                                                        and branch t/LABEL, records hashes of protected files and a snapshot of the
                                                        ticket file(s), and resets the loop guard for this attempt
  python scripts/collect_evidence.py --bundle LABEL     after the session: prints the evidence block to paste to the planner
  python scripts/collect_evidence.py --accept LABEL     the ticket passed: scan for secrets, commit leftovers, fast-forward the
                                                        original branch to t/LABEL (nothing is deleted)
  python scripts/collect_evidence.py --abandon LABEL    the ticket failed: switch back to the original branch; the failed branch
                                                        t/LABEL is kept so you can look at the diff
Options: --ticket PATH (repeatable; default: docs/spec/tickets/LABEL*.md or tickets with `batch: LABEL`),
         --protect GLOB (repeatable; default tests/** evals/** scripts/** docs/spec/** .agents/** AGENTS.md),
         --diff (include a redacted diff of changed files, capped), --max-lines N (default 120),
         --allow-dirty (with --start only; the baseline then includes your uncommitted work).
A second --start for the same LABEL becomes attempt 2 (tag pre/LABEL.2, branch t/LABEL.2).
Exit code: 0 done (flags in a bundle do not change it), 1 refused or failed, 2 bad usage.
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys

DEFAULT_PROTECT = ["tests/**", "evals/**", "scripts/**", "docs/spec/**", ".agents/**", "AGENTS.md"]
STATE_DIR = ".agent-run"
HASH_LIMIT = 1_000_000
FILE_CAP = 3000
BUDGET_PER_TICKET = 12
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,30}$")
CMD_FENCES = ("", "bash", "sh", "shell", "console", "powershell", "ps1", "pwsh", "cmd", "bat", "text", "txt")

SECRET_RES = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"), re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"), re.compile(r"AIza[0-9A-Za-z_-]{30,}"), re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:basic|digest)\s+[A-Za-z0-9+/=]{12,}"),
]
URL_CRED_RE = re.compile(r"(?<=://)[^/\s:@]{1,100}:[^/\s@]{3,100}(?=@)")
FLAG_VALUE_RE = re.compile(r"(?i)(--?(?:password|passwd|pwd|token|secret|api[-_]?key|apikey|access[-_]?token|auth)[ =])(?!\[REDACTED)(\S+)")
COOKIE_RE = re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:\s*).+$")
KV_RE = re.compile(
    r"""(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]{0,40}?(?:api[_-]?key|apikey|secret|token|passw(?:or)?d|pwd|private[_-]?key|authorization)[A-Za-z0-9_.-]{0,40})"""
    r"""(["']?\s*[:=]\s*)(?!\[REDACTED)(?:"([^"\n]{4,})"|'([^'\n]{4,})'|([^\s"',;}\]]{6,}))""")


def _kv_sub(m):
    q = '"' if m.group(3) is not None else ("'" if m.group(4) is not None else "")
    return "%s%s%s[REDACTED]%s" % (m.group(1), m.group(2), q, q)


def redact(text):
    home = os.path.expanduser("~")
    if home and len(home) > 3:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    for rx in SECRET_RES:
        text = rx.sub("[REDACTED]", text)
    text = URL_CRED_RE.sub("[REDACTED]", text)
    text = FLAG_VALUE_RE.sub(lambda m: m.group(1) + "[REDACTED]", text)
    text = COOKIE_RE.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return KV_RE.sub(_kv_sub, text)


def git(args, cwd, check=False, text=True):
    p = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = p.stdout.decode("utf-8", errors="replace") if text else p.stdout
    err = p.stderr.decode("utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (err or out).strip()[:300]))
    return p.returncode, out, err


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def matches(path, patterns):
    for pat in patterns:
        if pat.endswith("/**"):
            if path.startswith(pat[:-2]):
                return True
        elif fnmatch.fnmatch(path, pat) or path == pat:
            return True
    return False


def repo_files(root):
    """Tracked plus untracked-but-not-ignored files, relative with forward slashes."""
    rc, out, _ = git(["ls-files", "-z", "-c", "-o", "--exclude-standard"], root)
    return sorted({p for p in out.split("\0") if p})


def hash_protected(root, patterns):
    """Return (hashes, capped). The pattern filter runs BEFORE the cap, so a big node_modules cannot hide tests/."""
    res, capped = {}, False
    for rel in repo_files(root):
        if not matches(rel, patterns):
            continue
        if len(res) >= FILE_CAP:
            capped = True
            break
        full = os.path.join(root, rel)
        try:
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                res[rel] = "nonregular"      # a symlink or device is never opened (it could block forever)
            else:
                res[rel] = sha256_file(full) if st.st_size <= HASH_LIMIT else "size:%d" % st.st_size
        except OSError:
            pass
    return res, capped


def parse_frontmatter_value(text, key):
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return ""
    kv = re.search(r"(?m)^%s\s*:\s*(.*?)\s*$" % re.escape(key), m.group(1))
    return re.sub(r"\s+#.*$", "", kv.group(1)).strip("\"'") if kv else ""


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(2_000_000)
    except OSError:
        return ""


def find_tickets(root, label, explicit):
    paths = []
    if explicit:
        paths = [p if os.path.isabs(p) else os.path.join(root, p) for p in explicit]
    else:
        tdir = os.path.join(root, "docs", "spec", "tickets")
        base = label.split(".")[0]
        if os.path.isdir(tdir):
            for fn in sorted(os.listdir(tdir)):
                if not fn.endswith(".md") or not fn.startswith("T-"):
                    continue
                full = os.path.join(tdir, fn)
                txt = read_text(full)
                if fn.startswith(base + "-") or fn == base + ".md" or parse_frontmatter_value(txt, "batch") == base:
                    paths.append(full)
    return [p for p in paths if os.path.isfile(p)]


def section(text, title):
    m = re.search(r"(?ms)^##\s+%s[^\n]*\n(.*?)(?=^##\s|\Z)" % re.escape(title), text)
    return m.group(1) if m else ""


def ticket_files(text):
    out = []
    for ln in section(text, "Files to create or modify").splitlines():
        if ln.strip().startswith(("-", "*")):
            m = re.search(r"`([^`]+)`", ln)
            if m:
                out.append(re.sub(r"^(?:\./)+", "", m.group(1).replace("\\", "/")))
    return out


def ticket_commands(text):
    """Commands the human should rerun: only fences without a data tag (a ```mutants block is JSON, not a command)."""
    cmds = []
    for info, body in re.findall(r"```([^\n]*)\n(.*?)```", section(text, "Verification"), re.S):
        if info.strip().lower() in CMD_FENCES:
            cmds += [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return cmds


def ticket_norm_sha(text):
    """Hash of a ticket WITHOUT the parts an executor is allowed to change (status, ticked boxes, Execution report)."""
    t = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---", t, re.S)
    if m:
        fm = re.sub(r"(?m)^status\s*:.*\n?", "", m.group(1))
        t = "---\n" + fm + "\n---" + t[m.end():]
    t = re.sub(r"(?ms)^##\s+Execution report[^\n]*\n.*?(?=^##\s|\Z)", "", t)
    t = re.sub(r"(?m)^(\s*[-*]\s*)\[[xX]\]", r"\1[ ]", t)
    t = "\n".join(x.rstrip() for x in t.split("\n")).strip()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def snapshot_tickets(root, paths):
    snap = {}
    for p in paths:
        txt = read_text(p)
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        snap[rel] = {"norm_sha256": ticket_norm_sha(txt), "files": ticket_files(txt), "commands": ticket_commands(txt)}
    return snap


def state_path(root, label):
    return os.path.join(root, STATE_DIR, label + ".json")


def load_state(root, label):
    """The newest attempt for LABEL (LABEL, LABEL.2, LABEL.3 ...)."""
    d = os.path.join(root, STATE_DIR)
    best, best_n = None, 0
    if os.path.isdir(d):
        for fn in os.listdir(d):
            m = re.fullmatch(re.escape(label) + r"(?:\.(\d+))?\.json", fn)
            if m:
                n = int(m.group(1) or 1)
                if n >= best_n:
                    best, best_n = fn, n
    if not best:
        return None
    try:
        with open(os.path.join(d, best), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def repo_root():
    rc, out, err = git(["rev-parse", "--show-toplevel"], os.getcwd())
    if rc != 0:
        print("This folder is not inside a git repository (run `git init`, see ticket T-000a).", file=sys.stderr)
        return None
    return out.strip()


def current_branch(root):
    """The checked-out branch name, or '' on a detached HEAD."""
    rc, out, _ = git(["symbolic-ref", "-q", "--short", "HEAD"], root)
    return out.strip() if rc == 0 else ""


def dirty_lines(root):
    rc, porc, _ = git(["status", "--porcelain"], root)
    keep = []
    for ln in porc.splitlines():
        path = ln[3:].strip().strip('"')
        if "->" in path:
            path = path.split("->")[-1].strip().strip('"')
        if path.split("/")[0] != STATE_DIR:
            keep.append(ln)
    return keep


def unsafe_git_state(root):
    """Why a commit would be wrong right now (merge, rebase, cherry-pick or conflicts), or ''."""
    rc, gd, _ = git(["rev-parse", "--absolute-git-dir"], root)
    gd = gd.strip()
    if rc == 0 and gd:
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            if os.path.exists(os.path.join(gd, name)):
                return "a merge, rebase or cherry-pick is unfinished (%s). Finish or abort it first (`git merge --abort`, `git rebase --abort`)" % name
    rc, porc, _ = git(["status", "--porcelain"], root)
    if any(ln[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD") for ln in porc.splitlines()):
        return "there are files with unresolved conflicts"
    rc, chk, _ = git(["diff", "HEAD", "--check"], root)
    if "conflict marker" in chk:
        return "a file still holds conflict markers (<<<<<<<)"
    return ""


def env_files_exposed(root):
    """.env files that git would stage (tracked, or untracked and not ignored). .env.example style names are fine."""
    bad = []
    for p in repo_files(root):
        base = p.rsplit("/", 1)[-1]
        if re.match(r"(?i)^\.env(\..*)?$", base) and not re.search(r"(?i)\.(example|sample|template|dist)$", base):
            bad.append(p)
    return bad


# ---------------------------------------------------------------- --start
def do_start(a, root):
    label = a.start
    dirty = dirty_lines(root)
    if dirty and not a.allow_dirty:
        print("REFUSED: the working tree has %d uncommitted change(s), so the baseline would be unclear." % len(dirty))
        for ln in dirty[:8]:
            print("  " + ln)
        print("Commit them first (git add -A, then git commit -m \"checkpoint before %s\"), or use --allow-dirty." % label)
        return 1
    rc, head, err = git(["rev-parse", "HEAD"], root)
    if rc != 0:
        print("REFUSED: the repository has no commit yet. Make the first commit (ticket T-000a step 10, or `git add -A` then "
              "`git commit -m \"chore: baseline\"`), then run --start again.")
        return 1
    orig = current_branch(root)
    if not orig:
        print("REFUSED: HEAD is detached (not on a branch), so there is no branch to come back to. Run `git switch main` "
              "(or your main branch), then --start again.")
        return 1
    if orig.startswith("t/"):
        print("REFUSED: you are still on '%s', the branch of an earlier ticket. Finish it first: `--accept LABEL` if it passed, "
              "`--abandon LABEL` if it failed, then run --start again from your main branch." % orig)
        return 1
    attempt = 1
    while True:
        name = label if attempt == 1 else "%s.%d" % (label, attempt)
        if git(["rev-parse", "-q", "--verify", "refs/tags/pre/" + name], root)[0] != 0 and \
           git(["rev-parse", "-q", "--verify", "refs/heads/t/" + name], root)[0] != 0:
            break
        attempt += 1
        if attempt > 20:
            print("REFUSED: more than 20 attempts for %s; start a new label." % label)
            return 1
    git(["tag", "pre/" + name], root, check=True)
    git(["checkout", "-b", "t/" + name], root, check=True)
    patterns = a.protect or DEFAULT_PROTECT
    hashes, capped = hash_protected(root, patterns)
    tpaths = find_tickets(root, label, a.ticket)
    snap = snapshot_tickets(root, tpaths)
    scanner = os.path.join(root, "scripts", "scan_secrets.py")
    state = {"label": label, "name": name, "attempt": attempt, "base_sha": head.strip(), "original_branch": orig, "branch": "t/" + name,
             "started": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "protect": patterns,
             "protected_hashes": hashes, "protected_capped": capped, "script_sha256": sha256_file(os.path.abspath(__file__)),
             "scanner_sha256": sha256_file(scanner) if os.path.isfile(scanner) else "", "tickets": snap,
             "dirty_at_start": [ln for ln in dirty][:20],
             "python": platform.python_version(), "platform": platform.platform(), "allow_dirty": bool(a.allow_dirty)}
    os.makedirs(os.path.join(root, STATE_DIR), exist_ok=True)
    with open(state_path(root, name), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, indent=1)
    # the loop guard is keyed by the ATTEMPT name (T-001.2), so a retry starts with a clean ledger; budget grows with batch size
    with open(os.path.join(root, STATE_DIR, "current"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("%s\nbudget=%d\n" % (name, BUDGET_PER_TICKET * max(1, len(snap))))
    print("Baseline saved: tag pre/%s at %s, branch t/%s (you were on '%s'), %d protected file(s) hashed, %d ticket file(s) snapshotted."
          % (name, head.strip()[:10], name, orig, len(hashes), len(snap)))
    if capped:
        print("WARNING: more than %d protected files; only the first %d were hashed. Narrow the folders with --protect." % (FILE_CAP, FILE_CAP))
    if not hashes:
        print("WARNING: no protected file was hashed (no tests/, scripts/ or docs/spec/ files are committed yet?). The PROTECTED check will not work.")
    if not snap:
        print("NOTE: no ticket file found for %s (pass --ticket PATH); scope and rerun commands cannot be checked." % label)
    if git(["check-ignore", "-q", STATE_DIR + "/x"], root)[0] != 0:
        print("WARNING: .agent-run/ is not in .gitignore; add the line `.agent-run/` (ticket T-000a does this).")
    for key in ("core.longpaths", "core.autocrlf"):
        rc, val, _ = git(["config", "--get", key], root)
        if platform.system() == "Windows" and (rc != 0 or not val.strip()):
            print("NOTE (Windows): git config %s is not set; ask the planner before changing global settings." % key)
    print("Next: start a FRESH agent session for %s. The agent may commit work-in-progress on this branch" % label)
    print("(`git commit -m \"%s wip: ...\"`) but never switches branches, pushes or runs this script." % label)
    print("After the session: python scripts/collect_evidence.py --bundle %s" % label)
    return 0


# ---------------------------------------------------------------- --bundle
ERR_LINE = re.compile(r"(?i)(error|fail|assert|exception|traceback|not found|cannot|denied|unable|refused|timeout|429|503)")
ZERO = re.compile(r"(?i)(\b0 passed\b|no tests ran|collected 0 items|ran 0 tests|0 tests?\b.*\b(run|executed)|no tests found)")
HEADER = re.compile(r"\[exit (-?\d+)(?:, final (-?\d+))?, ([\d.]+)s((?:, [a-z-]+)*)\]")


def read_log(path):
    """Return (command, final_code, command_code, markers, body). final_code is what run_check.py reported (it includes
    --require/--fail-on rules); older logs without it fall back to the command's own exit code."""
    lines = read_text(path).splitlines()
    cmd = lines[0][2:] if lines and lines[0].startswith("$ ") else "?"
    m = HEADER.match(lines[1]) if len(lines) > 1 else None
    code = int(m.group(1)) if m else None
    final = int(m.group(2)) if (m and m.group(2) is not None) else code
    markers = [x.strip() for x in m.group(4).split(",") if x.strip()] if m else []
    body = lines[2:] if m else lines
    return cmd, final, code, markers, body


def ledger_rows(root, name):
    n = 0
    try:
        with open(os.path.join(root, STATE_DIR, "ledger.jsonl"), encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except (ValueError, RecursionError):
                    continue
                if isinstance(r, dict) and r.get("ticket") == name:
                    n += 1
    except OSError:
        pass
    return n


def do_bundle(a, root):
    label = a.bundle
    st = load_state(root, label)
    out = []
    flags = []
    P = out.append
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    P("EVIDENCE BUNDLE %s%s   generated %s UTC" % (label, " (attempt %d)" % st["attempt"] if st else "", now))
    rc, gv, _ = git(["--version"], root)
    P("host: %s | python %s | %s" % (platform.platform(), platform.python_version(), gv.strip()))
    me = sha256_file(os.path.abspath(__file__))
    if not st:
        flags.append("NO BASELINE: --start %s was never run, so nothing below can be compared with a starting point" % label)
        base = None
    else:
        base = st["base_sha"]
        if st.get("script_sha256") != me:
            flags.append("COLLECTOR CHANGED since --start (this script's hash differs): do not trust this bundle")
        P("baseline: tag pre/%s = %s, started %s UTC, branch %s%s" % (st["name"], base[:10], st["started"], st["branch"],
                                                                      ", started DIRTY (--allow-dirty)" if st.get("allow_dirty") else ""))
    rc, head, _ = git(["rev-parse", "HEAD"], root)
    rc, br, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    P("now: HEAD %s on branch %s" % (head.strip()[:10], br.strip()))
    if st and br.strip() != st["branch"]:
        flags.append("BRANCH: expected %s but you are on %s" % (st["branch"], br.strip()))
    changed = set()
    if base:
        rc, logo, _ = git(["log", "--oneline", "-n", "10", "%s..HEAD" % base], root)
        P("commits since baseline: %d" % len([x for x in logo.splitlines() if x.strip()]))
        for ln in logo.splitlines()[:10]:
            P("  " + ln)
        rc, names, _ = git(["diff", "--name-only", base], root)
        changed |= {x.strip() for x in names.splitlines() if x.strip()}
        rc, stat_out, _ = git(["diff", "--shortstat", base], root)
        P("diff vs baseline (tracked files): %s" % (stat_out.strip() or "none"))
    rc, others, _ = git(["ls-files", "-o", "--exclude-standard"], root)
    untracked = [x for x in others.splitlines() if x.strip() and not x.startswith(STATE_DIR + "/")]
    changed |= set(untracked)
    P("changed files: %d (untracked: %d)" % (len(changed), len(untracked)))
    for c in sorted(changed)[:30]:
        P("  " + c)
    if len(changed) > 30:
        P("  ... %d more" % (len(changed) - 30))
    # tickets: lists and commands come from the SNAPSHOT taken at --start, never from the live (agent-editable) file
    snap = (st or {}).get("tickets") or {}
    live = find_tickets(root, label, a.ticket)
    live_rel = {os.path.relpath(p, root).replace(os.sep, "/"): p for p in live}
    for rel in snap:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            live_rel.setdefault(rel, p)
        else:
            flags.append("TICKET DELETED: %s was in the ticket snapshot but is gone" % rel)
    allowed, per_ticket_cmds, statuses = set(), {}, {}
    for rel, p in sorted(live_rel.items()):
        txt = read_text(p)
        statuses[rel] = parse_frontmatter_value(txt, "status")
        if rel in snap:
            allowed |= set(snap[rel]["files"])
            per_ticket_cmds[rel] = snap[rel]["commands"]
            if ticket_norm_sha(txt) != snap[rel]["norm_sha256"]:
                flags.append("TICKET CHANGED outside status and Execution report: %s (the scope list or commands may have been edited; "
                             "if you edited it yourself, ignore this. This bundle still uses the version from --start)" % rel)
        else:
            allowed |= set(ticket_files(txt))
            per_ticket_cmds[rel] = ticket_commands(txt)
            if st:
                flags.append("TICKET NOT SNAPSHOTTED: %s was not present at --start, so its scope and commands cannot be trusted" % rel)
    rel_t = set(live_rel) | set(snap)
    if live_rel:
        P("ticket file(s): %s" % ", ".join(sorted(rel_t)))
        outside = sorted(c for c in changed if c not in allowed and c not in rel_t and not c.startswith((".check-logs/", STATE_DIR + "/")))
        if outside:
            flags.append("OUT OF SCOPE: %d changed file(s) are not in the ticket list: %s" % (len(outside), ", ".join(outside[:6])))
        missing = sorted(x for x in allowed if x not in changed and not os.path.exists(os.path.join(root, x)))
        if missing:
            flags.append("LISTED FILES MISSING: %s" % ", ".join(missing[:6]))
    else:
        P("ticket file: not found (pass --ticket PATH); scope not compared")
    # protected files (a path the ticket itself lists is planner-approved; the ticket list cannot change silently, see TICKET CHANGED)
    if st:
        now_h, capped_now = hash_protected(root, st["protect"])
        diffs, approved = [], []
        for p, h in st["protected_hashes"].items():
            if p in rel_t:
                continue
            if p not in now_h:
                (approved if p in allowed else diffs).append("%s (deleted)" % p)
            elif now_h[p] != h:
                (approved if p in allowed else diffs).append("%s (modified)" % p)
        for p in now_h:
            if p not in st["protected_hashes"] and p not in rel_t:
                (approved if p in allowed else diffs).append("%s (added)" % p)
        P("protected files checked: %d%s" % (len(st["protected_hashes"]), " (CAPPED)" if st.get("protected_capped") else ""))
        if approved:
            P("protected paths changed but listed in the ticket (planner-approved): %s" % "; ".join(sorted(approved)[:8]))
        if diffs:
            flags.append("PROTECTED CHANGED: %s" % "; ".join(sorted(diffs)[:8]))
        if not st["protected_hashes"] or st.get("protected_capped"):
            flags.append("PROTECTED CHECK WEAK: %s" % ("no protected file was hashed at --start" if not st["protected_hashes"]
                                                       else "more than %d protected files, the rest were not hashed" % FILE_CAP))
    # check logs written by run_check.py
    logs_dir = os.path.join(root, ".check-logs")
    started = datetime.datetime.fromisoformat(st["started"]).timestamp() if st else 0
    fresh = []
    P("checks (.check-logs, newest first; exit = what run_check.py reported as EXIT=):")
    if os.path.isdir(logs_dir):
        files = sorted((os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".log")), key=os.path.getmtime, reverse=True)
        for lp in files[:8]:
            cmd, final, code, markers, body = read_log(lp)
            bn = os.path.basename(lp)
            stale = os.path.getmtime(lp) < started
            first_err = next((ln for ln in body if ERR_LINE.search(ln)), "")
            extra = " (command exit %s, a --require/--fail-on rule failed)" % code if (final is not None and code is not None and final != code) else ""
            P("  %s: exit %s%s%s | $ %s" % (bn, final, extra, " [STALE, older than the baseline]" if stale else "", cmd[:110]))
            if final not in (0, None) and first_err:
                P("    first error: %s" % first_err.strip()[:160])
            for ln in body[-4:] if (final not in (0, None)) else body[-2:]:
                P("    | %s" % ln[:160])
            if final == 0 and any(ZERO.search(ln) for ln in body):
                flags.append("ZERO TESTS: %s exited 0 but its output says no tests ran" % bn)
            if not stale:
                fresh.append((bn, final, markers))
    nlogs = len(fresh)
    if st and nlogs == 0:
        flags.append("NO FRESH CHECK LOG: no .check-logs file is newer than the baseline, so no verification ran through run_check.py")
    for bn, final, markers in fresh:
        if "no-guard" in markers:
            flags.append("GUARD SWITCHED OFF: %s was run with --no-guard (the loop guard is a rule for the agent; it is not allowed to turn it off)" % bn)
    if st and nlogs:
        rows = ledger_rows(root, st["name"])
        P("loop-guard ledger: %d run(s) recorded for %s" % (rows, st["name"]))
        if rows < nlogs:
            flags.append("LEDGER SHORTER THAN LOGS: %d fresh check log(s) but only %d ledger row(s) for %s: the ledger was deleted, "
                         "another --ticket label was used, or --no-guard" % (nlogs, rows, st["name"]))
    # tickets that say done
    failed_fresh = [bn for bn, final, _m in fresh if final not in (0, None)]
    for rel in sorted(live_rel):
        status = statuses.get(rel, "")
        P("ticket %s status: %s" % (os.path.basename(rel), status or "?"))
        if status == "done" and nlogs == 0:
            flags.append("DONE WITHOUT EVIDENCE: %s says done but there is no fresh check log" % os.path.basename(rel))
        if status == "done" and failed_fresh:
            flags.append("DONE BUT A CHECK FAILED: %s says done, yet %s ended with a non-zero EXIT (if a later run under another --name passed, "
                         "you rerun it yourself and decide)" % (os.path.basename(rel), ", ".join(failed_fresh[:4])))
        if status == "done" and any(c.startswith(("tests/", "test/")) or "/tests/" in c for c in changed) and not any(c in allowed for c in changed if c.startswith(("tests/", "test/"))):
            flags.append("DONE WITH TESTS EDITED: %s says done but test files changed outside its file list" % os.path.basename(rel))
    # image approvals must come from the human, not from an executor ticket
    if base and "assets/MANIFEST.md" in changed:
        def approved_rows(txt):
            return {ln.strip() for ln in txt.splitlines() if re.search(r"\|\s*approved\s*\|?\s*$", ln)}
        rc, old_txt, _ = git(["show", "%s:assets/MANIFEST.md" % base], root)
        added = approved_rows(read_text(os.path.join(root, "assets", "MANIFEST.md"))) - (approved_rows(old_txt) if rc == 0 else set())
        if added:
            flags.append("ASSET APPROVED IN THIS SESSION: assets/MANIFEST.md gained %d `approved` row(s) during the ticket. Only YOU approve an image; "
                         "look at the picture yourself and check that you wrote that row" % len(added))
    # secret scan (never run a scanner that changed since --start)
    ss = os.path.join(root, "scripts", "scan_secrets.py")
    if os.path.isfile(ss):
        if st and st.get("scanner_sha256") and sha256_file(ss) != st["scanner_sha256"]:
            P("scan_secrets.py: NOT RUN (it changed since --start)")
            flags.append("SECRET SCAN NOT RUN: scripts/scan_secrets.py changed since --start; restore it from the kit and run it yourself")
        else:
            try:
                p = subprocess.run([sys.executable, ss], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
                lines = p.stdout.decode("utf-8", errors="replace").splitlines()
                P("scan_secrets.py: exit %d | %s" % (p.returncode, (lines[-1] if lines else "")[:120]))
                if p.returncode != 0:
                    flags.append("SECRET SCAN FAILED: fix before anything is committed or pasted")
            except (subprocess.TimeoutExpired, OSError) as e:
                P("scan_secrets.py: could not run (%s)" % e)
    else:
        P("scan_secrets.py: not found")
        flags.append("SECRET SCAN MISSING: scripts/scan_secrets.py is not in the repo, so nothing was scanned")
    if any(per_ticket_cmds.values()):
        P("RERUN BY YOU (the ticket's verification, full commands as written in the ticket at --start; paste the last two lines of each to the planner):")
        shown = 0
        total = sum(len(v) for v in per_ticket_cmds.values())
        for rel in sorted(per_ticket_cmds):
            if len(per_ticket_cmds) > 1 and per_ticket_cmds[rel]:
                P("  # %s" % os.path.basename(rel))
            for c in per_ticket_cmds[rel]:
                if shown >= 10:
                    break
                P("  " + c)
                shown += 1
        if total > shown:
            P("  ... %d more command(s): open the ticket file(s) listed above" % (total - shown))
    if a.diff and base:
        rc, d, _ = git(["diff", "--unified=2", base, "--"] + sorted(changed - set(untracked))[:20], root)
        P("diff (redacted, capped at 60 lines):")
        for ln in d.splitlines()[:60]:
            P("  " + ln[:160])
    P("FLAGS: %d" % len(flags))
    for fl in flags:
        P("  FLAG " + fl)
    text = redact("\n".join(out))
    lines = text.splitlines()
    if len(lines) > a.max_lines:
        keep = lines[: a.max_lines - 3]
        keep += ["... (%d lines cut; raise --max-lines or fix the flags first)" % (len(lines) - len(keep) - 2)]
        tail = [ln for ln in lines if ln.startswith(("FLAGS:", "  FLAG "))]
        lines = keep + tail
        text = "\n".join(lines)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    text += ("\nBUNDLE-HASH: %s (detects accidental edits of this block; anyone who can run Python can recompute it, "
             "so it is not proof against a dishonest agent. Always rerun the commands yourself)" % digest)
    print(text)
    try:
        os.makedirs(os.path.join(root, STATE_DIR), exist_ok=True)
        with open(os.path.join(root, STATE_DIR, "%s.bundle.txt" % (st["name"] if st else label)), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
    except OSError:
        pass
    return 0


# ---------------------------------------------------------------- --accept / --abandon
def do_accept(a, root):
    st = load_state(root, a.accept)
    if not st:
        print("No baseline for %s: run --start first." % a.accept)
        return 1
    cur = current_branch(root)
    if cur != st["branch"]:
        print("REFUSED: you are on '%s', expected '%s'." % (cur or "a detached HEAD", st["branch"]))
        return 1
    if not st.get("original_branch") or st["original_branch"] == "HEAD":
        print("REFUSED: the original branch was not recorded properly (it was started on a detached HEAD). Nothing was merged.")
        return 1
    why = unsafe_git_state(root)
    if why:
        print("REFUSED: %s. Nothing was committed or merged." % why)
        return 1
    ss = os.path.join(root, "scripts", "scan_secrets.py")
    if not os.path.isfile(ss):
        print("REFUSED: scripts/scan_secrets.py is missing, so nothing can be scanned for secrets. Restore it from the kit "
              "(bootstrap --update) and try again.")
        return 1
    if st.get("scanner_sha256") and sha256_file(ss) != st["scanner_sha256"]:
        print("REFUSED: scripts/scan_secrets.py changed since --start, so it is not trusted. Restore it from the kit and try again.")
        return 1
    p = subprocess.run([sys.executable, ss], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        print("REFUSED: scan_secrets.py failed. Fix this before committing:\n" + redact(p.stdout.decode("utf-8", errors="replace"))[-600:])
        return 1
    bad_env = env_files_exposed(root)
    if bad_env:
        print("REFUSED: %s would be committed (not ignored by git). Add `.env` and `.env.*` to .gitignore, or remove the file from git "
              "(`git rm --cached`), then try again." % ", ".join(bad_env[:4]))
        return 1
    left = dirty_lines(root)
    if left:
        print("Leftover changes that will be committed as '%s accepted' (%d file(s)):" % (st["label"], len(left)))
        for ln in left[:12]:
            print("  " + ln)
        git(["add", "-A"], root, check=True)
        git(["commit", "-m", "%s accepted" % st["label"]], root, check=True)
        print("Committed the leftover changes as '%s accepted'." % st["label"])
    git(["checkout", st["original_branch"]], root, check=True)
    if current_branch(root) != st["original_branch"]:
        print("ERROR: could not switch back to '%s'. Nothing was merged." % st["original_branch"])
        return 1
    rc, out, err = git(["merge", "--ff-only", st["branch"]], root)
    if rc != 0:
        print("The original branch moved, so a fast-forward is not possible. Nothing was deleted. Ask the planner (paste this):\n" + (err or out).strip()[:300])
        git(["checkout", st["branch"]], root)
        return 1
    print("Accepted: %s now includes %s. The branch t/%s and tag pre/%s are kept." % (st["original_branch"], st["label"], st["name"], st["name"]))
    return 0


def do_abandon(a, root):
    st = load_state(root, a.abandon)
    if not st:
        print("No baseline for %s." % a.abandon)
        return 1
    cur = current_branch(root)
    left = dirty_lines(root)
    if cur != st["branch"]:
        if cur == st["original_branch"] and not left:
            print("Nothing to abandon: you are already back on '%s' with a clean tree. The failed attempt stays on branch t/%s." % (cur, st["name"]))
            return 0
        print("REFUSED: you are on '%s', not on '%s', so nothing was committed or switched. If the failed work is on '%s', run "
              "`git switch %s` first and then --abandon again." % (cur or "a detached HEAD", st["branch"], cur or "this checkout", st["branch"]))
        return 1
    if left:
        git(["add", "-A"], root, check=True)
        git(["commit", "-m", "%s failed attempt (kept for diff)" % st["name"]], root, check=True)
        print("Saved the unfinished work as a commit on t/%s." % st["name"])
    git(["checkout", st["original_branch"]], root, check=True)
    if current_branch(root) != st["original_branch"]:
        print("ERROR: could not switch back to '%s'." % st["original_branch"])
        return 1
    print("Back on '%s'. The failed attempt stays on branch t/%s: compare with `git diff pre/%s t/%s`." % (st["original_branch"], st["name"], st["name"], st["name"]))
    if st.get("allow_dirty") and st.get("dirty_at_start"):
        print("NOTE: this attempt started with your own uncommitted work (--allow-dirty). That work is now committed on t/%s and is NOT in your "
              "working tree here; get it back with `git checkout t/%s -- <file>`." % (st["name"], st["name"]))
    return 0


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Checkpoint and evidence bundle for one ticket (run by the human).", add_help=True)
    g = ap.add_mutually_exclusive_group(required=True)
    for k in ("start", "bundle", "accept", "abandon"):
        g.add_argument("--" + k, metavar="LABEL")
    ap.add_argument("--ticket", action="append")
    ap.add_argument("--protect", action="append")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--max-lines", type=int, default=120)
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args(argv)
    label = a.start or a.bundle or a.accept or a.abandon
    if not LABEL_RE.match(label):
        print("LABEL must look like T-005 or B1 (letters, digits, dot, dash).", file=sys.stderr)
        return 2
    root = repo_root()
    if root is None:
        return 1
    os.chdir(root)
    try:
        if a.start:
            return do_start(a, root)
        if a.bundle:
            return do_bundle(a, root)
        if a.accept:
            return do_accept(a, root)
        return do_abandon(a, root)
    except RuntimeError as e:
        print("ERROR: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
