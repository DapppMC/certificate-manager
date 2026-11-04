#!/usr/bin/env python3
"""failure_signature.py: Deterministic SHA-256 signature for a test failure (12 hex chars)."""
import hashlib, sys


def compute_signature(test_id, error_type, stack_frame=""):
    raw = f"{test_id.strip()}|{error_type.strip()}|{stack_frame.strip()}"
    return hashlib.sha256(raw.encode("utf8")).hexdigest()[:12]


USAGE = "Usage: failure_signature.py <test_id> <error_type> [stack_frame]"

if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(USAGE + "\nPrints a stable 12-hex signature for one failure; the same failure always gives the same signature.")
        sys.exit(0)
    if len(sys.argv) < 3:
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    print(compute_signature(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
