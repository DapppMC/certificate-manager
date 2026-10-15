---
title: "T-012 Expiry from the validity rule table"
id: T-012
date: 2026-10-15
tags: [ticket]
when: Adds expiry.py: printed expiry wins, then a printed duration, else DJK 5 years, K3/BNSP 3 years, internal training none.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-07]
depends_on: [T-011]
parallel: false
role: backend
effort: low
model_tier: standard
score: 2
tests_first: true
risk: normal
---

# T-012 Expiry from the validity rule table

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Groups without a printed expiry get one from `[[validity_rules]]`, with `expiry_source` explaining where it came from.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 9: Expiry rule only)
- `tests/acceptance/test_expiry.py` (read only)
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/expiry.py` (create)
- `src/certslice/pipeline.py` (modify: apply after date extraction)
- `tests/unit/test_expiry.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
def add_years(d: date, years: int) -> date:
    y = d.year + years
    return date(y, d.month, min(d.day, calendar.monthrange(y, d.month)[1]))

def apply_rules(issue, expiry, expiry_conf, group_text_folded, cfg) -> tuple[date | None, str, str | None]: ...
# returns (expiry, confidence, expiry_source)
```
Order: printed expiry date (`printed`), then `[fields] duration_pattern` with an issue date (`duration:<n>y`, confidence high), then `[[validity_rules]]`.

## Steps
1. Tests first: write `tests/unit/test_expiry.py`: printed expiry kept with source `printed`; DJK text adds 5 years; `k3` as a whole word adds 3 years but `k30` does not; `corporate university` gives None with confidence `medium` and source `rule:none`; 2024-02-29 plus 5 years is 2029-02-28; no rule gives None, `low`, null; `sertifikat ini berlaku untuk 3 (tiga) tahun` with issue 2026-08-20 gives 2029-08-20, `high`, `duration:3y` even when `bnsp` also occurs. Confirm they fail first.
2. Write `expiry.py` (`add_years` as the example; `apply_rules` per section 9; phrase match with no letter or digit directly before or after).
3. In `pipeline.run` call `apply_rules` for every group after the dates and store `expiry_date` and `expiry_source`.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-012: expiry rules"`.

## Acceptance criteria
- AC1: `bundle_djk.pdf`: expiry `2030-08-17`, source `rule:5y` (R-07).
- AC2: `bundle_internal.pdf`: expiry null, confidence not `low`, source `rule:none`; `bundle_simple.pdf` keeps the printed date.
- AC3: `bundle_bnsp_like.pdf`: expiry `2029-08-20`, confidence high, source `duration:3y`.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t012 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-012
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

No mutants block: the acceptance tests caught a deliberate off-by-one-year break in planner trials.

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
Status: done.
- Ticket: T-012, status: done
- Files changed:
  - `src/certslice/expiry.py` (created)
  - `src/certslice/pipeline.py` (modified)
  - `tests/unit/test_expiry.py` (created)
- Commands run:
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name t012 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-012`:
    EXIT=0
    RESULT: PASS
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
    EXIT=0
    RESULT: PASS
- Acceptance criteria:
  - AC1: pass (`bundle_djk.pdf`: expiry `2030-08-17`, source `rule:5y`)
  - AC2: pass (`bundle_internal.pdf`: expiry null, confidence not `low`, source `rule:none`; `bundle_simple.pdf` keeps the printed date)
  - AC3: pass (`bundle_bnsp_like.pdf`: expiry `2029-08-20`, confidence high, source `duration:3y`)
- Deviations from the plan: none
- Open issues and questions for the planner: none

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
