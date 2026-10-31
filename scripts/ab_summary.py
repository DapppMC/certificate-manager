#!/usr/bin/env python3
"""ab_summary.py - paired A/B analysis for executor runs (standard library only, Python 3.9+, no network).

Input CSV columns (header required, extra columns ignored):
  ticket,arm,trial,passed,input_tokens,cache_read_tokens,output_tokens,thinking_tokens,turns,seconds
  arm is A (baseline) or B (baseline + ONE tool); passed is 1/0 (or true/false).
Cost per run = (input - cache_read) * price_in + cache_read * price_cache + (output + thinking) * price_out,
prices per MILLION tokens. Per ticket: median of trials per arm, then paired difference B - A.
Decision rule: ADOPT only if median cost is >=10% lower, >=8 of 12 tickets (or >=2/3 of tickets if
fewer than 12) are cheaper, and no ticket loses a pass; otherwise DROP, or EXTEND when borderline.
Usage: python ab_summary.py runs.csv --price-in 0.75 --price-cache 0.075 --price-out 3.75
"""
import argparse
import csv
import statistics
import sys


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "pass", "passed")


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Paired A/B analysis of executor runs.")
    ap.add_argument("csv_file")
    ap.add_argument("--price-in", type=float, required=True, help="USD per 1M uncached input tokens")
    ap.add_argument("--price-cache", type=float, required=True, help="USD per 1M cached input tokens")
    ap.add_argument("--price-out", type=float, required=True, help="USD per 1M output (+thinking) tokens")
    a = ap.parse_args(argv)
    runs = {}
    with open(a.csv_file, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                inp, cache = float(row["input_tokens"]), float(row["cache_read_tokens"])
                out, think = float(row["output_tokens"]), float(row["thinking_tokens"])
                cost = ((inp - cache) * a.price_in + cache * a.price_cache + (out + think) * a.price_out) / 1e6
                rec = (cost, float(row["turns"]), float(row["seconds"]), truthy(row["passed"]))
            except (KeyError, ValueError) as e:
                print("bad row %r: %s" % (row, e), file=sys.stderr)
                return 2
            runs.setdefault((row["ticket"], row["arm"].strip().upper()), []).append(rec)
    tickets = sorted({t for t, _ in runs})
    paired = []
    for t in tickets:
        if (t, "A") not in runs or (t, "B") not in runs:
            print("skip %s: needs both arms" % t)
            continue
        A, B = runs[(t, "A")], runs[(t, "B")]
        med = lambda rs, i: statistics.median(r[i] for r in rs)
        pa, pb = sum(r[3] for r in A), sum(r[3] for r in B)
        paired.append((t, med(B, 0) - med(A, 0), med(A, 0), med(B, 1) - med(A, 1), med(B, 2) - med(A, 2), pa, pb, len(A), len(B)))
        print("%-12s cost A=%.4f B=%.4f diff=%+.4f | turns diff=%+.1f | secs diff=%+.1f | passes A=%d/%d B=%d/%d"
              % (t, med(A, 0), med(B, 0), med(B, 0) - med(A, 0), med(B, 1) - med(A, 1), med(B, 2) - med(A, 2), pa, len(A), pb, len(B)))
    n = len(paired)
    if n == 0:
        print("no paired tickets")
        print("RESULT: EXTEND (no data)")
        return 1
    med_rel = statistics.median(d / c if c else 0.0 for _, d, c, *_ in paired)
    cheaper = sum(1 for p in paired if p[1] < 0)
    lost = [p[0] for p in paired if p[6] < p[5]]
    need = 8 if n >= 12 else -(-n * 2 // 3)
    print("tickets=%d median relative cost change=%+.1f%% cheaper=%d (need %d) tickets losing passes=%s"
          % (n, med_rel * 100, cheaper, need, lost or "none"))
    if n < 10:
        print("note: fewer than 10 paired tickets; treat as a pilot (small pilots overstate effects)")
    if med_rel <= -0.10 and cheaper >= need and not lost:
        print("RESULT: ADOPT")
        return 0
    if (med_rel <= -0.05 or cheaper >= need - 2) and not lost:
        print("RESULT: EXTEND (borderline: add more tickets, not more repeats)")
        return 1
    print("RESULT: DROP")
    return 1


if __name__ == "__main__":
    sys.exit(main())
