#!/usr/bin/env python3
"""ui_check.py - cheap, deterministic UI hygiene checks for the executor (anti 'AI slop').

Standard library only, Python 3.9+. No network. Copy to <repo>/scripts/ui_check.py (design-system ticket).

Flags, per line (suppress one line with the comment  ui-check-ignore ):
  1. hard-coded colors (#rgb, #rrggbb, rgb(), hsl()) outside the tokens file(s)
  2. font-family values that are not in the allowed list and not var(--...)
  3. <img> without an alt attribute (alt="" is allowed for decorative images)
  4. 'outline: none' / 'outline: 0' (removes the keyboard focus ring) - warning only

Usage:
  python scripts/ui_check.py [PATH ...] [--tokens FILE ...] [--fonts "Inter,JetBrains Mono"]
Defaults: PATH=src (or . if missing); tokens files = any file named tokens.* / *.tokens.css /
design-tokens.* / tailwind.theme.css; allowed fonts = every font name found inside tokens files
(--font-* custom properties) plus --fonts. Last line is RESULT: PASS|FAIL (warnings do not fail).
"""
import argparse
import os
import re
import sys

EXT = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".mdx"}
SKIP = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", ".svelte-kit", "coverage", "__pycache__", ".check-logs"}
GENERIC = {"serif", "sans-serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace",
           "inherit", "initial", "unset", "revert", "cursive", "fantasy", "-apple-system", "blinkmacsystemfont"}
HEX = re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
FUNC = re.compile(r"\b(?:rgba?|hsla?)\(")
FONT = re.compile(r"font-family\s*:\s*([^;}{]+)", re.I)
IMG = re.compile(r"<img\b[^>]{0,2000}>", re.I)
JS_SELECTOR = re.compile(r"(querySelector(All)?|getElementById|closest|matches)\(\s*[\"'`][^\"'`]*$")


def hex_is_color(line):
    """True when a #abc-looking token is a color, False when it is an id selector (#add, #fade) or a JS selector string."""
    for m in HEX.finditer(line):
        before = line[: m.start()]
        seg = re.split(r"[;{}]", before)[-1]
        if JS_SELECTOR.search(before):
            continue
        if not re.search(r"[:(=]", seg):
            continue  # selector context: no property, call or attribute before it
        return True
    return False


def is_tokens(path):
    b = os.path.basename(path).lower()
    return b.startswith("tokens.") or b.startswith("design-tokens.") or b.endswith(".tokens.css") or b == "tailwind.theme.css"


def families(value):
    out = []
    for part in value.split(","):
        p = part.strip().strip("'\"").strip()
        if p:
            out.append(p)
    return out


def walk(paths, skip_dirs=()):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for dp, dns, fns in os.walk(p):
            dns[:] = [d for d in dns if d not in SKIP and os.path.abspath(os.path.join(dp, d)) not in skip_dirs]
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in EXT:
                    yield os.path.join(dp, fn)


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Flag hard-coded colors, off-token fonts and missing alt text.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--tokens", action="append", default=[], help="tokens file (repeatable); exempt from color rules")
    ap.add_argument("--fonts", default="", help="comma separated extra allowed font names")
    a = ap.parse_args(argv)
    paths = a.paths or (["src"] if os.path.isdir("src") else ["."])
    # default scan of a static site: the reference screens and the spec pack are not app code
    skip_dirs = set() if a.paths else {os.path.abspath("design"), os.path.abspath("docs")}
    files = list(walk(paths, skip_dirs))
    token_files = {os.path.abspath(t) for t in a.tokens}
    token_files |= {os.path.abspath(f) for f in files if is_tokens(f)}
    for dp, dns, fns in os.walk("."):
        dns[:] = [d for d in dns if d not in SKIP]
        for fn in fns:
            if is_tokens(fn):
                token_files.add(os.path.abspath(os.path.join(dp, fn)))
    allowed = {f.lower() for f in families(a.fonts)}
    for t in token_files:
        try:
            txt = open(t, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"--font[\w-]*\s*:\s*([^;}\n]+)", txt):
            allowed.update(x.lower() for x in families(m.group(1)))
        for m in FONT.finditer(txt):
            allowed.update(x.lower() for x in families(m.group(1)))
    bad, warn = 0, 0
    for path in sorted(set(files)):
        if os.path.abspath(path) in token_files:
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if "ui-check-ignore" in line:
                continue
            if hex_is_color(line) or FUNC.search(line):
                if not re.search(r"href=|url\(|&#\d+", line) or FUNC.search(line):
                    print("%s:%d: hard-coded color; use a token (var(--...) or theme class)" % (path, i))
                    bad += 1
            for m in FONT.finditer(line):
                for fam in families(m.group(1)):
                    if fam.lower() in GENERIC or fam.startswith("var(") or fam.startswith("--"):
                        continue
                    if fam.lower() not in allowed:
                        print("%s:%d: font %r is not in the token fonts; use the font token" % (path, i, fam))
                        bad += 1
            if re.search(r"outline\s*:\s*(none|0)\b", line, re.I) and "focus-visible" not in line:
                print("%s:%d: WARN outline removed; make sure a visible focus style exists" % (path, i))
                warn += 1
        for m in IMG.finditer(text):
            if "ui-check-ignore" in m.group(0):
                continue
            if not re.search(r"\balt\s*=|\balt\b\s*[}/>]|\{\.\.\.", m.group(0)):
                line_no = text.count("\n", 0, m.start()) + 1
                print("%s:%d: <img> without alt attribute (use alt=\"\" only for decorative images)" % (path, line_no))
                bad += 1
    print("checked %d file(s); violations=%d warnings=%d; tokens files: %s" % (
        len(set(files)), bad, warn, ", ".join(sorted(os.path.relpath(t) for t in token_files)) or "none found"))
    print("RESULT: %s" % ("PASS" if bad == 0 else "FAIL"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
