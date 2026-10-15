---
title: "T-015 Pipeline hardening: dry run, accounting exit, re-runs, speed, offline"
id: T-015
date: 2026-10-15
tags: [ticket]
when: Finishes the engine: pure dry run, exit 3 with plan.json on accounting errors, timings, logging, entry point checks.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-09, R-13, R-14, R-20, R-21, R-22]
depends_on: [T-014]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: false
risk: normal
---

# T-015 Pipeline hardening: dry run, accounting exit, re-runs, speed, offline

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
The full M1 acceptance set (without OCR and evaluate) passes: dry run writes plan.json only, real runs write exactly four entries, every bundle balances, re-runs never overwrite, 30 pages run in under 5 minutes, no network is touched.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/interface.md` (sections 1, 4 and 5 only)
- `tests/acceptance/test_pipeline.py`, `test_speed.py`, `test_offline.py` (read only)
- `src/certslice/pipeline.py`
- `src/certslice/cli.py`

## Files to create or modify
- `src/certslice/pipeline.py` (modify)
- `src/certslice/cli.py` (modify: logging setup only)
- `tests/unit/test_pipeline.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
# pipeline.run, end of the function (shape)
timings = {"read_seconds": round(t_read - t0, 3), "total_seconds": round(time.perf_counter() - t0, 3),
           "ocr_pages": len(ocr_times), "ocr_seconds_per_page": round(sum(ocr_times) / len(ocr_times), 3) if ocr_times else None}
```

## Steps
1. Make sure the run folder holds ONLY `plan.json` in dry-run, and exactly `plan.json`, `pdf/`, `manifest_slice.xlsx`, `unmatched_report.xlsx` otherwise (no log files).
2. On `AccountingError`: create the run folder, write plan.json, then re-raise (the CLI returns 3).
3. Fill `timings` as in the example (`ocr_times` is an empty list until T-018).
4. In `cli.py` configure `logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")` once in `main`, and set `logging.getLogger("RapidOCR").setLevel(logging.WARNING)`. Log one INFO line per stage (pages read, groups, written). Never log page text or e-mail addresses.
5. Write `tests/unit/test_pipeline.py`: a monkeypatched `check_accounting` that raises gives exit 3 through `cli.main` and plan.json exists in the run folder.
6. Run the Verification. If `test_speed` takes more than 60 seconds, report the time; then group employees by token count and use `rapidfuzz.process.cdist` as extraction-rules section 3 allows (only inside `matcher.py`, add it to your report as a deviation needing approval, and stop before changing it).
7. Commit: `git add -A` then `git commit -m "T-015: pipeline hardening"`.

## Acceptance criteria
- AC1: Dry run and real run folder contents match exactly (R-09).
- AC2: Accounting holds for all 8 text bundles; an accounting error exits 3 with plan.json kept (R-13).
- AC3: Two runs give two folders with equal manifests (R-14); 30 pages under 300 s (R-20); paths with spaces and `python -m certslice` work (R-21); a run with sockets blocked succeeds (R-22).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t015 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-015
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

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
