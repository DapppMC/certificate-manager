#!/usr/bin/env python3
"""ticketc.py: validate a ticket YAML and enforce its diff boundaries. Standard library only.

  ticketc.py validate <ticket.yml> [--root DIR]        schema + frozen sha256 check (exit 0 ok, 1 invalid)
  ticketc.py diff     <ticket.yml> [--repo DIR] [--base REF]   changed files vs allowed/forbidden + frozen check
  ticketc.py hash     <file>...                         print sha256 for the `frozen:` map

Ticket fields: id, goal, frozen (path: sha256), allowed_paths, forbidden_paths,
protected_tests, acceptance (commands), stop_if. Globs: `*` stays inside a folder, `**` crosses folders.
A tiny YAML subset is parsed here (scalars, block lists, one-level maps, [] and {}) so no PyYAML is needed.
"""
import argparse, hashlib, os, re, subprocess, sys

REQUIRED_FIELDS = ["id", "goal", "frozen", "allowed_paths", "forbidden_paths", "protected_tests", "acceptance", "stop_if"]
LIST_FIELDS = ["allowed_paths", "forbidden_paths", "protected_tests", "acceptance", "stop_if"]


class TicketError(ValueError):
    pass


# ---------- minimal YAML subset ----------
def _scalar(s):
    s = s.strip()
    if s in ("", "~", "null"):
        return ""
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    s = re.sub(r"\s+#.*$", "", s)  # trailing comment on plain scalars
    return s.strip()


def parse_yaml(text):
    lines = [l.rstrip("\n") for l in text.splitlines()]
    data, i = {}, 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith((" ", "\t")):
            raise TicketError(f"line {i+1}: unexpected indentation")
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", raw)
        if not m:
            raise TicketError(f"line {i+1}: expected 'key: value'")
        key, rest = m.group(1), m.group(2).strip()
        if key in data:
            raise TicketError(f"line {i+1}: duplicate key {key}")
        i += 1
        if rest in ("|", ">"):
            buf = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            data[key] = (" " if rest == ">" else "\n").join(buf).strip()
            continue
        if rest and not rest.startswith("#"):
            data[key] = _scalar(rest)
            continue
        # block collection (or empty)
        block = []
        while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#") or lines[i].startswith((" ", "\t", "-"))):
            if lines[i].strip() and not lines[i].lstrip().startswith("#"):
                block.append(lines[i])
            i += 1
        if not block:
            data[key] = ""
        elif block[0].lstrip().startswith("- ") or block[0].strip() == "-":
            data[key] = [_scalar(b.lstrip()[1:]) for b in block if b.lstrip().startswith("-")]
        else:
            d = {}
            for b in block:
                mm = re.match(r"^\s+(?:\"([^\"]+)\"|'([^']+)'|([^:\s][^:]*?))\s*:\s*(.*)$", b)
                if not mm:
                    raise TicketError(f"bad mapping line under {key}: {b.strip()}")
                d[mm.group(1) or mm.group(2) or mm.group(3)] = _scalar(mm.group(4))
            data[key] = d
    return data


# ---------- globbing ----------
def glob_to_regex(pat):
    pat = pat.replace("\\", "/").lstrip("./") if pat.startswith("./") else pat.replace("\\", "/")
    out, i = "", 0
    while i < len(pat):
        c = pat[i]
        if pat[i:i+3] == "**/":
            out += "(?:.*/)?"; i += 3
        elif pat[i:i+2] == "**":
            out += ".*"; i += 2
        elif c == "*":
            out += "[^/]*"; i += 1
        elif c == "?":
            out += "[^/]"; i += 1
        else:
            out += re.escape(c); i += 1
    return re.compile("^" + out + "$")


def matches(path, patterns):
    path = path.replace("\\", "/")
    return any(glob_to_regex(p).match(path) for p in patterns)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- validation ----------
def check_frozen(frozen, root="."):
    errs = []
    for rel, want in frozen.items():
        p = os.path.join(root, rel)
        if not re.fullmatch(r"[0-9a-fA-F]{8,64}", str(want)):
            errs.append(f"frozen hash for {rel} is not hex (8-64 chars): {want!r}")
        elif not os.path.isfile(p):
            errs.append(f"frozen file missing: {rel}")
        elif not sha256_file(p).startswith(str(want).lower()):
            errs.append(f"frozen file changed or hash broken: {rel}")
    return errs


def validate_data(data, root="."):
    errs = []
    for f in REQUIRED_FIELDS:
        if f not in data:
            errs.append(f"Missing required field: {f}")
    if errs:
        return errs
    for f in ("id", "goal"):
        if not isinstance(data[f], str) or not data[f].strip():
            errs.append(f"{f} must be a non-empty string")
    for f in LIST_FIELDS:
        v = data[f]
        if v == "":
            v = []
        if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
            errs.append(f"{f} must be a list of non-empty strings")
    if isinstance(data["acceptance"], list) and not data["acceptance"]:
        errs.append("Acceptance must be a non-empty list of commands")
    if isinstance(data["allowed_paths"], list) and not data["allowed_paths"]:
        errs.append("allowed_paths must not be empty")
    if isinstance(data["stop_if"], list) and not data["stop_if"]:
        errs.append("stop_if must not be empty (tell the executor when to halt)")
    allowed = data["allowed_paths"] if isinstance(data["allowed_paths"], list) else []
    for p in allowed + (data["forbidden_paths"] if isinstance(data["forbidden_paths"], list) else []):
        if isinstance(p, str) and (p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.replace("\\", "/").split("/")):
            errs.append(f"path must be relative and stay inside the repo: {p}")
    for p in allowed:
        if p.strip() in ("*", "**", "**/*", "./**"):
            errs.append(f"allowed_paths too broad: {p}")
    if isinstance(data["protected_tests"], list):
        for t in data["protected_tests"]:
            if matches(t, allowed):
                errs.append(f"protected test is inside allowed_paths (executor could edit it): {t}")
    fr = data["frozen"]
    if fr == "" or fr == []:
        fr = {}
    if not isinstance(fr, dict):
        errs.append("frozen must be a map of path: sha256")
    else:
        errs += check_frozen(fr, root)
    return errs


def load(path):
    with open(path, encoding="utf8") as fh:
        return parse_yaml(fh.read())


def validate_ticket(path, root="."):
    data = load(path)
    errs = validate_data(data, root)
    if errs:
        raise ValueError("; ".join(errs))
    return data


# ---------- diff guard ----------
def changed_files(repo=".", base="HEAD"):
    def run(*a):
        return subprocess.check_output(["git", "-C", repo] + list(a), text=True, stderr=subprocess.PIPE).splitlines()
    files = set(run("diff", "--name-only", base))
    files |= set(run("ls-files", "--others", "--exclude-standard"))
    return sorted(f.replace("\\", "/") for f in files if f)


def verify_diff(allowed_patterns, forbidden_patterns, repo_root=".", base="HEAD"):
    violations = []
    for f in changed_files(repo_root, base):
        if matches(f, forbidden_patterns):
            violations.append(f"Diff touched forbidden path: {f}")
        elif not matches(f, allowed_patterns):
            violations.append(f"Diff touched file outside allowed_paths: {f}")
    if violations:
        raise PermissionError("; ".join(violations))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ticket validator and diff guard")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate"); v.add_argument("ticket"); v.add_argument("--root", default=".")
    d = sub.add_parser("diff"); d.add_argument("ticket"); d.add_argument("--repo", default="."); d.add_argument("--base", default="HEAD")
    h = sub.add_parser("hash"); h.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "validate":
            validate_ticket(a.ticket, a.root)
            print("OK: ticket is valid")
        elif a.cmd == "diff":
            data = validate_ticket(a.ticket, a.repo)
            verify_diff(data["allowed_paths"], data["forbidden_paths"], a.repo, a.base)
            print("OK: diff is inside allowed_paths")
        elif a.cmd == "hash":
            for f in a.files:
                print(f"{f}: {sha256_file(f)}")
        else:
            ap.print_help()
    except (ValueError, PermissionError, OSError, subprocess.CalledProcessError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
