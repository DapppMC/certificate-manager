---
title: "T-009 Certificate number and date extraction"
id: T-009
date: 2026-10-15
tags: [ticket]
when: Creates fields.py with the number rule and the Indonesian/English date rules; fills number, issue and expiry in plan.json.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-06]
depends_on: [T-008]
parallel: false
role: backend
effort: high
model_tier: standard
score: 4
tests_first: true
risk: normal
---

# T-009 Certificate number and date extraction

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Every group in plan.json has `fields.number`, `fields.issue_date` and `fields.expiry_date` (ISO dates) with a confidence, extracted without any template.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 9: Number and Dates only)
- `docs/spec/modules/interface.md` (section 5 only)
- `src/certslice/default_config.toml` (table `[fields]` only)
- `tests/acceptance/test_fields_dates.py` and `test_dates_contract.py` (read only)
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/fields.py` (create)
- `src/certslice/pipeline.py` (modify: fill three fields per group)
- `tests/unit/test_fields_dates.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
def extract_number(lines: list[str], cfg: dict) -> str | None: ...
def find_dates(line: str) -> list[tuple[int, datetime.date]]: ...   # (position, date), sorted
def extract_dates(lines: list[str], cfg: dict) -> tuple[date | None, str, date | None, str]: ...
# returns (issue, issue_confidence, expiry, expiry_confidence); confidence is "high", "medium" or "low"
```
Expected: `find_dates("Jakarta, 17 Agustus 2025")` -> one date 2025-08-17; `find_dates("Valid until March 4, 2027")` -> 2027-03-04; `find_dates("Held on October 05th - 12nd, 2026")` -> 2026-10-12 (a day range gives its last day; ordinal suffixes st/nd/rd/th are allowed; the dash may be `-`, an en dash or an em dash); `extract_number(["Nomor Registrasi: DJK.0701.2025"], cfg)` -> `DJK.0701.2025`.

## Steps
1. Tests first: write `tests/unit/test_fields_dates.py` with at least 12 cases from extraction-rules section 9 (each date pattern, short and long month names in both languages, `s.d.` ranges with two dates, an expiry marker with the date on the next line, an ignored birth date, an invalid date 31/02/2025, expiry before issue, the number skipping `Registrasi`, a number without digits rejected). Confirm they fail first.
2. Write `fields.py` with the three functions following section 9 exactly. Read every word list from `cfg["fields"]`.
3. In `pipeline.run`, per group (one page per group until T-010): number from the first page's lines; dates from all group lines; set `fields["number"]`, `fields["issue_date"]`, `fields["expiry_date"]` as `{"value": <str or None>, "confidence": ...}` with ISO dates; number confidence `high` when found else value None and `low`; set `expiry_source` to `printed` when an expiry was found, else null (T-012 adds rules).
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-009: number and dates"`.

## Acceptance criteria
- AC1: `bundle_simple.pdf` page 1: number `123/LSP-PLN/VIII/2025`, issue `2025-08-17`, expiry `2028-08-16` high (R-06).
- AC2: Every case of `tests/acceptance/test_dates_contract.py` passes (patterns seen on real certificates).
- AC3: `bundle_mixed.pdf` page 7 (no date): issue date null with confidence `low`.
- AC4: `bundle_djk.pdf`: issue `2025-08-17` high from `Ditetapkan ...`; number `DJK.0701.2025`.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t009 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-009
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

No mutants block: parsing is covered by many acceptance assertions and by the unit tests this ticket writes first; the code text is not dictated, so planner mutants would likely be STALE.

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
- Ticket: T-009, status: done
- Files changed:
  - `src/certslice/fields.py` (created)
  - `src/certslice/pipeline.py` (modified)
  - `tests/unit/test_fields_dates.py` (created)
- Commands run:
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name t009 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-009`:
    EXIT=0
    RESULT: PASS
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
    EXIT=0
    RESULT: PASS
- Acceptance criteria:
  - AC1: pass (bundle_simple.pdf page 1: number 123/LSP-PLN/VIII/2025, issue 2025-08-17, expiry 2028-08-16 high)
  - AC2: pass (Every case of tests/acceptance/test_dates_contract.py passes)
  - AC3: pass (bundle_mixed.pdf page 7: issue date null with confidence low)
  - AC4: pass (bundle_djk.pdf: issue 2025-08-17 high from Ditetapkan ...; number DJK.0701.2025)
- Deviations from the plan: none
- Open issues and questions for the planner: none

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
