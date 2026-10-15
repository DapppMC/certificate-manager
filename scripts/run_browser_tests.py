#!/usr/bin/env python3
"""run_browser_tests.py - run a plain HTML test page in headless Chrome/Edge and print PASS/FAIL.

For no-build web apps (static HTML + JS, no Node). Standard library only, Python 3.9+. No network.
Copy it to <repo>/scripts/ with assets/tests.html (the skeleton) as <repo>/tests.html.

How it works: the test page runs its tests on load and writes lines into <pre id="result">:
  `PASS name` or `FAIL name: reason` per test, then one summary line `SUMMARY passed=N failed=M`.
This script opens the page with `--headless --dump-dom`, reads that element, prints failures and ends with
`EXIT=<code>` and `RESULT: PASS|FAIL` (the same signal as run_check.py). It also FAILs when no tests ran.

Usage:
  python scripts/run_browser_tests.py tests.html
  python scripts/run_browser_tests.py tests.html --chrome "C:/Program Files/Google/Chrome/Application/chrome.exe"
Browser lookup: --chrome, env CHROME_BIN, then common Chrome/Chromium/Edge names and Windows paths.
"""
import argparse
import urllib.parse
import html
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

CANDIDATES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge", "microsoft-edge"]
WIN_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser(explicit):
    for c in [explicit, os.environ.get("CHROME_BIN")]:
        if c:
            return c if os.path.exists(c) else shutil.which(c)
    for c in CANDIDATES:
        w = shutil.which(c)
        if w:
            return w
    for p in WIN_PATHS + ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
        if os.path.exists(p):
            return p
    return None


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Run an HTML test page headless and print RESULT: PASS|FAIL.")
    ap.add_argument("page", help="path to the test page, e.g. tests.html")
    ap.add_argument("--chrome", help="path to chrome/chromium/msedge")
    ap.add_argument("--group", help="run only the tests of this group (page is opened with ?group=NAME; see tests.html)")
    ap.add_argument("--allow-network", action="store_true", help="let the page reach external hosts (default: blocked, so tested code cannot send data out)")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--budget", type=int, default=10000, help="virtual time budget in ms for async tests")
    a = ap.parse_args(argv)
    exe = find_browser(a.chrome)
    if not exe:
        print("[run_browser_tests] no Chrome/Chromium/Edge found; install Chrome or pass --chrome PATH")
        print("EXIT=127\nRESULT: FAIL")
        return 127
    if not os.path.exists(a.page):
        print("[run_browser_tests] page not found: %s" % a.page)
        print("EXIT=2\nRESULT: FAIL")
        return 2
    url = pathlib.Path(os.path.abspath(a.page)).as_uri()
    if a.group:
        url += "?group=" + urllib.parse.quote(a.group)
    prof = tempfile.mkdtemp(prefix="rbt-")
    cmd = [exe, "--headless", "--disable-gpu", "--allow-file-access-from-files",
           ] + ([] if a.allow_network else ["--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost"]) + [
           "--user-data-dir=" + prof, "--virtual-time-budget=%d" % a.budget, "--dump-dom", url]
    if hasattr(os, "geteuid") and os.geteuid() == 0:   # Chrome refuses to start as root without this (containers only)
        cmd.insert(3, "--no-sandbox")
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=a.timeout)
        dom = p.stdout.decode("utf-8", errors="replace")
        code = p.returncode
    except subprocess.TimeoutExpired:
        print("[run_browser_tests] TIMEOUT after %ds" % a.timeout)
        print("EXIT=124\nRESULT: FAIL")
        return 124
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    m = re.search(r'<pre id="result"[^>]*>(.*?)</pre>', dom, re.S)
    if not m:
        print("[run_browser_tests] no <pre id=\"result\"> in the page output (did the test script crash?)")
        print("EXIT=1\nRESULT: FAIL")
        return 1
    lines = [ln for ln in html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).splitlines() if ln.strip()]
    fails = [ln for ln in lines if ln.startswith("FAIL")]
    summ = [ln for ln in lines if ln.startswith("SUMMARY")]
    n_pass = len([ln for ln in lines if ln.startswith("PASS")])
    for ln in fails:
        print(ln)
    print(summ[-1] if summ else "SUMMARY missing")
    group_ok = True
    if a.group:
        ran = [ln for ln in lines if ln.startswith("GROUP ")]
        group_ok = any(re.fullmatch(r"GROUP %s ran=([1-9][0-9]*)" % re.escape(a.group), ln) for ln in ran)
        if not group_ok:
            print("[run_browser_tests] group %r ran no tests (typo, or tests.html has no such group)" % a.group)
    ok = code == 0 and not fails and summ and n_pass > 0 and group_ok
    if not summ:
        print("[run_browser_tests] no SUMMARY line: the tests did not finish")
    if n_pass == 0:
        print("[run_browser_tests] no passing tests were reported (empty run counts as FAIL)")
    final = 0 if ok else 1
    print("EXIT=%d" % final)
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return final


if __name__ == "__main__":
    sys.exit(main())
