---
title: "T-016 OCR spike script"
id: T-016
date: 2026-10-15
tags: [ticket]
when: A small script that OCRs chosen pages of any PDF with RapidOCR and prints text, scores and seconds, for T-017.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-02]
depends_on: [T-000b]
parallel: true
role: spike
effort: low
model_tier: standard
score: 2
tests_first: false
risk: normal
---

# T-016 OCR spike script

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`spikes/ocr_spike.py --pdf <file> --pages 1,2 --dpi 200` prints per page the seconds, every recognised line with its score, and writes the text to `spikes/output/` (gitignored).

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 2 only)

## Files to create or modify
- `spikes/ocr_spike.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.
- - Everything under `src/` (this ticket is independent of the engine).

## Canonical example
```python
from rapidocr import RapidOCR   # rapidocr 3.10.0, models ship inside the package
engine = RapidOCR()
png = page.get_pixmap(dpi=dpi).tobytes("png")
result = engine(png)
for text, score in zip(result.txts or [], result.scores or []):
    print(f"{score:.3f}  {text}")
```

## Steps
1. Write `spikes/ocr_spike.py` with argparse flags `--pdf` (required), `--pages` (comma list of 1-based numbers, default `1`), `--dpi` (default 200). For each page: render, OCR, print `PAGE <n> seconds=<s>` then the lines as in the example, write the lines to `spikes/output/<pdf stem>-p<n>.txt` (create the folder). The first OCR call includes model loading: print `engine_init_seconds=<s>` separately. End with `RESULT: PASS` when every page produced at least one line, else `RESULT: FAIL` and exit 1.
2. Run the Verification. Commit: `git add -A` then `git commit -m "T-016: OCR spike script"`.

## Acceptance criteria
- AC1: On `tests/fixtures/bundle_scan.pdf` page 1 the output contains `MUHAMMAD RIZKY PRATAMA` (R-02).
- AC2: Seconds per page are printed.

## Verification
Run exactly this and paste the last lines:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t015 --require "MUHAMMAD RIZKY PRATAMA" -- python spikes/ocr_spike.py --pdf tests/fixtures/bundle_scan.pdf --pages 1
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: both end with `EXIT=0` and `RESULT: PASS`. If the output looks truncated or ambiguous, re-run the raw command and report its exit code.

## Stop conditions
- If a verification fails twice, stop and report; do not keep guessing.
- If a file outside the list above is needed, stop and report.
- If a named API, package, command or path does not exist, stop and report; do not invent one.
- If an acceptance test seems wrong, stop and report the test name and the failing assertion; never edit or skip it (the planner fixes test defects).
- If the plan is impossible or contradicts the requirements, stop and report; never deviate from the plan.

## Report format
Append to the "Execution report" section below (this format is mandatory and is not subject to any terse or brevity style):
- Ticket, status (done / blocked / failed)
- Files changed (path, created or modified)
- Commands run, each with exit code and the final PASS/FAIL line
- Each acceptance criterion: pass or fail with the evidence
- Deviations from the plan (none, or list)
- Open issues and questions for the planner

## Constraints (repeated)
- Touch only the files listed above. Never edit `tests/acceptance/**`, `tests/fixtures/**`, `scripts/**`, `src/certslice/default_config.toml` or `docs/spec/**` beyond this ticket's report.
- No new dependencies, no network calls, no real data, no secrets. Read thresholds and word lists from the config dict, never hard-code them.
- Never say done until the verification commands ran in this session and their exit codes and final lines are pasted.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
