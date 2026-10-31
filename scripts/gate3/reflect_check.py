#!/usr/bin/env python3
"""reflect_check.py: block code edits until a reflection exists for the current failure (Reflexion).

Usage:
  reflect_check.py <signature> <attempt> [--log reflection_log.md]
  reflect_check.py --hook [--log reflection_log.md]      # checks the LAST entry (PreToolUse hook)

Log entry format (one per attempt):
  ## <signature> / attempt <n>
  Hypothesis: <falsifiable causal claim, >= 10 chars, different from earlier attempts>
Exit codes: 0 allowed, 2 blocked, 3 escalation (attempt > MAX_ATTEMPTS; ESCALATION.md written), 1 usage error.
It fails CLOSED: missing arguments never mean "allowed".
"""
import argparse, os, re, sys

LOG_FILE = "reflection_log.md"
MAX_ATTEMPTS = 3
MIN_HYPOTHESIS = 10
PLACEHOLDERS = {"tbd", "todo", "unknown", "n/a", "none", "fix it", "try again", "same as before"}
HEADER = re.compile(r"^##\s+([0-9a-f]{6,64})\s*/\s*attempt\s+(\d+)\s*$", re.M | re.I)


def parse_log(text):
    """Return list of (signature, attempt, body) so each entry only sees its own section."""
    heads = list(HEADER.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        # a later non-matching "## " heading also closes the section
        body = text[m.end():end]
        nxt = re.search(r"^##\s", body, re.M)
        if nxt:
            body = body[:nxt.start()]
        out.append((m.group(1).lower(), int(m.group(2)), body))
    return out


def hypothesis_of(body):
    m = re.search(r"^\s*Hypothesis:[ \t]*(.*)$", body, re.M | re.I)
    return m.group(1).strip() if m else ""


def read_log(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf8") as fh:
        return fh.read()


def check_entry(entries, signature, attempt):
    """Return (ok, message)."""
    match = [e for e in entries if e[0] == signature.lower() and e[1] == attempt]
    if not match:
        return False, f"No reflection entry found for signature {signature} attempt {attempt}."
    hyp = hypothesis_of(match[-1][2])
    if len(hyp) < MIN_HYPOTHESIS or hyp.lower().rstrip(".") in PLACEHOLDERS:
        return False, "Hypothesis is missing or too short. Provide a falsifiable causal claim."
    for sig, att, body in entries:
        if sig == signature.lower() and att < attempt and hypothesis_of(body).lower() == hyp.lower():
            return False, f"Hypothesis repeats attempt {att}. A new attempt needs a new theory of the cause."
    return True, "ok"


def write_escalation(log_dir, signature, entries):
    lines = [f"# ESCALATION: failure {signature}", "",
             f"The same failure survived {MAX_ATTEMPTS} attempts. HALT. Do not try a 4th variation.", "",
             "## Hypotheses tried"]
    for sig, att, body in sorted(entries, key=lambda e: e[1]):
        if sig == signature.lower():
            lines.append(f"- attempt {att}: {hypothesis_of(body) or '(none)'}")
    lines += ["", "## Next", "- A human or a higher-tier model replans (spec wrong / test wrong / environment?).",
              "- Classify with gates/gate3_verification.md before editing anything."]
    path = os.path.join(log_dir, "ESCALATION.md")
    with open(path, "w", encoding="utf8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reflexion gate for code edits")
    ap.add_argument("signature", nargs="?")
    ap.add_argument("attempt", nargs="?", type=int)
    ap.add_argument("--log", default=LOG_FILE)
    ap.add_argument("--hook", action="store_true")
    a = ap.parse_args(argv)
    text = read_log(a.log)
    if text is None:
        print(f"BLOCKED: {a.log} missing! Write a reflection entry before editing.", file=sys.stderr)
        return 2
    entries = parse_log(text)
    if a.hook:
        if not entries:
            print("BLOCKED: reflection log has no entries.", file=sys.stderr)
            return 2
        a.signature, a.attempt = entries[-1][0], entries[-1][1]
    elif a.signature is None or a.attempt is None:
        print("Usage: reflect_check.py <signature> <attempt> | --hook", file=sys.stderr)
        return 1
    if a.attempt > MAX_ATTEMPTS:
        path = write_escalation(os.path.dirname(os.path.abspath(a.log)), a.signature, entries)
        print(f"BLOCKED: maximum {MAX_ATTEMPTS} attempts exceeded. Wrote {path} and HALT.", file=sys.stderr)
        return 3
    ok, msg = check_entry(entries, a.signature, a.attempt)
    if not ok:
        print(f"BLOCKED: {msg}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
