---
title: "T-019 Gold-set evaluation command"
id: T-019
date: 2026-10-15
tags: [ticket]
when: Adds evaluate.py and wires `python -m certslice evaluate`; prints SET, FIELD and WRONG_NID lines and exits 4 on a wrong NID.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-18]
depends_on: [T-015]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: false
risk: normal
---

# T-019 Gold-set evaluation command

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
The owner can measure accuracy on real samples with one command whose output follows interface section 8.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/interface.md` (sections 1 and 8 only)
- `tests/acceptance/test_evaluate.py` (read only)
- `src/certslice/cli.py`

## Files to create or modify
- `src/certslice/evaluate.py` (create)
- `src/certslice/cli.py` (modify: replace the `evaluate` placeholder)
- `tests/unit/test_evaluate.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
def evaluate(run_dir: Path, gold_path: Path) -> int:   # returns the exit code
    ...
    print(f"SET text certificates={t} correct={c} pct={pct}")   # pct like 100.0, or n/a when t == 0
    print(f"FIELD issue_date text={c1}/{t1} scan={c2}/{t2}")
    print(f"WRONG_NID={wrong}")
```

## Steps
1. Write `tests/unit/test_evaluate.py` with a hand-written plan.json and gold CSV in `tmp_path`: one wrong NID gives exit 4; an unmatched group with an empty gold NID counts as correct; a gold row whose page starts no group counts as not correct; a scan-source group lands in the scan set.
2. Write `evaluate.py` per interface section 8 (read gold with `encoding="utf-8-sig"`; missing plan.json or gold file -> `InputError`).
3. In `cli.py` call `evaluate(Path(args.run), Path(args.gold))` and return its code; map `InputError` to 2.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-019: evaluate"`.

## Acceptance criteria
- AC1: With `gold_wrong.csv`: `WRONG_NID=1` and exit 4 (R-18).
- AC2: With `gold_simple.csv`: `WRONG_NID=0`, exit 0, `SET text certificates=3 correct=3`, `SET scan certificates=0`, `FIELD issue_date text=3/3`.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t019 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-019
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
