#!/usr/bin/env python3
"""scan_secrets.py - small secret scanner for a repo (standard library only, Python 3.9+, no network).

Copy to <repo>/scripts/scan_secrets.py. Scans text files under PATH (default .), skipping
.git, node_modules, top-level dist/ and build/ output and binaries. UTF-16 files (what PowerShell 5.1 `>` writes) are
decoded and scanned; whole files are read (up to 20 MB each). A token is ignored only when the token itself looks like a
placeholder (xxx, example, your, dummy ...) or is wrapped like <TOKEN> or ${TOKEN}; the rest of the line
never excuses it. The local secrets files `.env` and `.env.*` (but not `.env.example`) are skipped because
they are meant to hold dev keys and must be gitignored (T-000a checks that). It is a cheap safety net, NOT
a replacement for GitHub push protection.

Usage: python scripts/scan_secrets.py [PATH ...]
Last line: RESULT: PASS (no findings) or RESULT: FAIL (findings=N). Exit 0 / 1.
"""
import os
import re
import sys

SKIP = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv", ".check-logs", ".agent-run", ".obsidian"}
SKIP_TOP = {"dist", "build"}   # build output is skipped only at the top of a scanned folder, so src/build/ is still scanned
MAX_READ = 20_000_000
PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{30,}", "GitHub fine-grained token"),
    (r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}", "API key (sk-...)"),     # not inside a word such as desk-hologram-...
    (r"sbp_[a-f0-9]{30,}", "Supabase access token"),
    (r"sb_secret_[A-Za-z0-9_-]{10,}", "Supabase secret key"),
    (r"AIza[0-9A-Za-z_-]{30,}", "Google API key"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"figd_[A-Za-z0-9_-]{20,}", "Figma token"),
    (r"ctx7sk-[A-Za-z0-9-]{10,}", "Context7 API key"),
    (r"[sr]k_(?:live|test)_[A-Za-z0-9]{10,}", "Stripe key"),
    (r"(?i)\b[a-z][a-z0-9+.-]{0,20}://[^\s:@/'\"<>]{1,100}:(?![^\s@]{0,100}[$<{])[^\s:@/'\"<>]{4,100}@", "URL with a password"),
]
URL_PW_PLACEHOLDER = re.compile(r"(?i):(password|pass|passwd|pwd|secret|user|username|\*+|x+)@")
GENERIC = re.compile(r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]{0,40}?(?:api[_-]?key|apikey|secret|token|passw(?:or)?d|pwd|private[_-]?key))(?![A-Za-z])(?:[_.-][A-Za-z0-9_.-]{0,40})?[\"']?\s*[:=]\s*['\"]?([A-Za-z0-9/+_\-.~=]{16,})")
PLACEHOLDER_WORDS = re.compile(r"(?i)(your|xxx|example|placeholder|changeme|\.\.\.|redacted|dummy|fake|process\.env|os\.environ|getenv|env\.)")


def is_placeholder(line, m):
    """True only if the matched token itself is a placeholder or is wrapped like <TOKEN> / ${TOKEN}."""
    tok = m.group(0)
    if PLACEHOLDER_WORDS.search(tok) or URL_PW_PLACEHOLDER.search(tok):
        return True
    before, after = line[max(0, m.start() - 3):m.start()], line[m.end():m.end() + 3]
    return before.endswith(("<", "${", "$")) or after.startswith((">", "}"))


def is_local_env(fn):
    return fn == ".env" or (fn.startswith(".env.") and fn != ".env.example")


def files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for dp, dns, fns in os.walk(p):
            dns[:] = [d for d in dns if d not in SKIP and not (d in SKIP_TOP and os.path.normpath(dp) == os.path.normpath(p))]
            for fn in fns:
                if is_local_env(fn):
                    continue
                yield os.path.join(dp, fn)


def decode_text(raw):
    """Return text, or None for a real binary file. UTF-16 (BOM or the usual NUL pattern) is decoded, not skipped."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    nul = raw.count(b"\x00")
    if nul == 0:
        return raw.decode("utf-8", errors="replace")
    if nul <= 4:
        return raw.replace(b"\x00", b"").decode("utf-8", errors="replace")      # a stray NUL must not hide a file
    half = len(raw) // 2
    if half and raw[1::2].count(0) > half * 0.4:
        return raw.decode("utf-16-le", errors="replace")
    if half and raw[0::2].count(0) > half * 0.4:
        return raw.decode("utf-16-be", errors="replace")
    return None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = [a for a in (argv if argv is not None else sys.argv[1:])]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    n = 0
    scanned = 0
    for path in files(args or ["."]):
        try:
            with open(path, "rb") as fh:
                raw = fh.read(MAX_READ)
                more = bool(fh.read(1))
        except OSError:
            continue
        text = decode_text(raw)
        if text is None:
            continue
        scanned += 1
        if more:
            print("%s: NOTE only the first %d MB were scanned" % (path, MAX_READ // 1_000_000))
        for i, line in enumerate(text.splitlines(), 1):
            hit = None
            for pat, label in PATTERNS:
                m = re.search(pat, line)
                if m and not is_placeholder(line, m):
                    hit = label
                    break
            if not hit:
                m = GENERIC.search(line)
                if m and not PLACEHOLDER_WORDS.search(m.group(2)) and not m.group(2).startswith(("$", "<")) \
                        and not re.fullmatch(r"[a-z_.]+", m.group(2)):
                    hit = "hard-coded %s" % m.group(1)
            if hit:
                print("%s:%d: possible secret (%s)" % (path, i, hit))
                n += 1
    print("scanned %d file(s); findings=%d" % (scanned, n))
    print("RESULT: %s" % ("PASS" if n == 0 else "FAIL (findings=%d)" % n))
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
