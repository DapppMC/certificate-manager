#!/usr/bin/env python3
"""sandbox.py: Hermetic twin (git worktree) + deterministic sandbox.lock + production deny-list.

Commands:
  create --repo R --path P [--branch B]   git worktree add -b B P ; write P/sandbox.lock
  lock   --repo R [--branch B] [--out F]  (re)compute the lock, print or write it
  guard  "<command>"                      exit 0 allowed, exit 2 blocked (Claude Code hook convention)
  guard  (no arg)                         read hook JSON on stdin: {"tool_input": {"command": "..."}}
  remove --repo R --path P                git worktree remove
Stdlib only. The lock stores env KEY NAMES only, never values.
"""
import argparse, hashlib, json, os, re, subprocess, sys

DEFAULT_BRANCH = "sandbox/v2.0-modernize"
LOCKFILES = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
             "requirements.txt", "Cargo.lock", "go.sum", "composer.lock"]
ENV_FILES = [".env.example", ".env.sample", ".env.template", ".env"]

# Fail-closed deny-list. Extra hosts: $PLANNER_DENY_HOSTS="db.corp.example,10.0.0.5" or --deny-host.
DENY_PATTERNS = [
    (r"(?<![\w-])ssh(?![\w-])", "ssh is blocked (no remote shells from a sandbox)"),
    (r"(?<![\w-])scp(?![\w-])|(?<![\w-])sftp(?![\w-])", "scp/sftp are blocked"),
    (r"(?<![\w-])kubectl(?![\w-])", "kubectl is blocked (cluster access)"),
    (r"drop\s+(database|schema)\b", "DROP DATABASE/SCHEMA is blocked"),
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]*\s+)*(/|~|/\*|\$HOME)(\s|$)", "recursive delete of / or ~ is blocked"),
    (r"(?i)(^|[\s/@=:.\-_])prod(uction)?([.\-_/:\s]|$)", "production hostname/target is blocked"),
]


def check_command(cmd, extra_hosts=()):
    """Return (allowed: bool, reason: str)."""
    for rx, why in DENY_PATTERNS:
        if re.search(rx, cmd, re.I):
            return False, why
    hosts = [h.strip() for h in (os.environ.get("PLANNER_DENY_HOSTS", "").split(",")) if h.strip()]
    hosts += [h for h in extra_hosts if h]
    for h in hosts:
        if h.lower() in cmd.lower():
            return False, f"host {h} is on the deny-list"
    return True, "ok"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo] + list(args), text=True, stderr=subprocess.PIPE).strip()


def env_keys(root):
    for name in ENV_FILES:
        p = os.path.join(root, name)
        if os.path.exists(p):
            keys = set()
            with open(p, encoding="utf8", errors="ignore") as fh:
                for line in fh:
                    m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                    if m:
                        keys.add(m.group(1))
            return sorted(keys)
    return []


def lockfile_hash(root):
    for name in LOCKFILES:
        p = os.path.join(root, name)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()[:8]
    return "none"


def build_lock(repo, branch=DEFAULT_BRANCH):
    """Deterministic: same commit + same lockfile + same branch => identical lock bytes."""
    commit = git(repo, "rev-parse", "--short=8", "HEAD")
    port = 3000 + int(hashlib.sha256(f"{commit}:{branch}".encode()).hexdigest()[:6], 16) % 6000
    return {
        "parent_commit": commit,
        "lockfile_hash": lockfile_hash(repo),
        "env_keys": env_keys(repo),
        "isolated_port": port,
        "db_snapshot_id": f"test_sandbox_{port}",
    }


def dump_lock(lock):
    return json.dumps(lock, indent=2, sort_keys=True) + "\n"


def create(repo, path, branch=DEFAULT_BRANCH):
    lock = build_lock(repo, branch)
    git(repo, "worktree", "add", "-b", branch, path)
    with open(os.path.join(path, "sandbox.lock"), "w", encoding="utf8", newline="\n") as fh:
        fh.write(dump_lock(lock))
    return lock


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hermetic twin sandbox")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create"); c.add_argument("--repo", default="."); c.add_argument("--path", required=True); c.add_argument("--branch", default=DEFAULT_BRANCH)
    l = sub.add_parser("lock"); l.add_argument("--repo", default="."); l.add_argument("--branch", default=DEFAULT_BRANCH); l.add_argument("--out")
    g = sub.add_parser("guard"); g.add_argument("command", nargs="?"); g.add_argument("--deny-host", action="append", default=[])
    r = sub.add_parser("remove"); r.add_argument("--repo", default="."); r.add_argument("--path", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            print(dump_lock(create(a.repo, a.path, a.branch)), end="")
        elif a.cmd == "lock":
            text = dump_lock(build_lock(a.repo, a.branch))
            if a.out:
                with open(a.out, "w", encoding="utf8", newline="\n") as fh:
                    fh.write(text)
            else:
                print(text, end="")
        elif a.cmd == "guard":
            cmd = a.command
            if cmd is None:
                try:
                    cmd = json.loads(sys.stdin.read()).get("tool_input", {}).get("command", "")
                except ValueError:
                    cmd = ""
            ok, why = check_command(cmd, a.deny_host)
            if not ok:
                print(f"BLOCKED by sandbox deny-list: {why}", file=sys.stderr)
                return 2
        elif a.cmd == "remove":
            git(a.repo, "worktree", "remove", "--force", a.path)
        else:
            ap.print_help()
    except subprocess.CalledProcessError as e:
        print(f"git failed: {e.stderr.strip() if e.stderr else e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
