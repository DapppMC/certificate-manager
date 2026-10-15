---
title: "T-004 Database loader with header search and validation"
id: T-004
date: 2026-10-15
tags: [ticket]
when: Replaces the skeleton's database reading with database.py: header search, configurable column names, NID as text, clear errors.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-01, R-19]
depends_on: [T-003]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: true
risk: normal
---

# T-004 Database loader with header search and validation

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`database.load_employees(path, cfg)` returns `list[Employee]` (with `norm` filled) or raises `InputError` with a plain message; `pipeline.run` uses it before creating anything.

## Context to read first
- `AGENTS.md`
- `docs/spec/01-requirements.md` (R-01 and R-19 only)
- `src/certslice/default_config.toml` (table `[database]` only)
- `tests/acceptance/test_database.py` (read only)
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/database.py` (create)
- `src/certslice/pipeline.py` (modify: use `load_employees`)
- `tests/unit/test_database.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
def header_key(v) -> str:
    return re.sub(r"[\s.\-]+", "_", str(v).strip().casefold()) if v is not None else ""

def load_employees(path: Path, cfg: dict) -> list[Employee]: ...
```
Error messages (tests look for these words): `cannot open database file <path>: <reason>`; `database is missing the required column(s): nid` (or `nama`, or `nid, nama`); `database has no employee rows below the header`.

## Steps
1. Tests first: write `tests/unit/test_database.py` that builds small workbooks in `tmp_path` with openpyxl (header on row 1; header on row 3 below a title row; headers `NID`/`Nama Lengkap`/`UNITKET`; a decoy first sheet with NID and name columns before a sheet called `Data Master`; a numeric NID 8812345 and a text NID `007123`; a sheet without a NID column; a sheet with a header only; a file that is not an xlsx). Run it and confirm it fails because `certslice.database` does not exist.
2. Write `database.py`: open with `load_workbook(path, read_only=True, data_only=True)` (any exception -> `InputError` `cannot open database file ...`); use the first worksheet whose title, lowercased and stripped, is in `cfg["database"]["preferred_sheets"]`, else the first worksheet; read ONLY the mapped columns (the real master data holds personal columns that must never be read or copied); scan the first `cfg["database"]["header_search_rows"]` rows; in each row compute `header_key` for every cell; the header row is the first row that has a key from `cfg["database"]["nid"]` AND a key from `cfg["database"]["nama"]` (first alias in list order wins); also map `email`, `unit`, `jabatan` when present. If no row qualifies raise `InputError` naming the required field(s) that no scanned row contained. Read the rows below the header; skip rows whose NID or name cell is empty; NID: `int` or integer-valued `float` -> `str(int(v))`, otherwise `str(v).strip()`; strip the name; fill `norm` with `normalize_name(nama)`. No rows -> `InputError("database has no employee rows below the header")`.
3. In `pipeline.run` replace the skeleton database code with `employees = load_employees(Path(db_path), cfg)`. The existence checks and database loading must happen before the run folder is created. Keep the skeleton matching but compare against `Employee.nama`.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-004: database loader"`.

## Acceptance criteria
- AC1: `db_basic.xlsx` loads from its `Data Master` sheet (not the decoy first sheet), header on row 3; NIDs `007123` and `8812345` stay text; the unit comes from `UNITKET` (R-01).
- AC2: Missing NID column, an empty sheet or a missing file exit with code 2, a plain `ERROR:` line, and no output folder content (R-19).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t004 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-004
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

No mutants block: the acceptance tests cover each error path and the NID conversion; the ticket does not dictate code text a mutant could target reliably.

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

Files changed:
- `src/certslice/database.py` (created)
- `src/certslice/pipeline.py` (modified)
- `tests/unit/test_database.py` (created)
- `docs/spec/tickets/T-004-database-loader.md` (modified)

Commands run:
- `.\.venv\Scripts\python.exe -m pytest tests/unit/test_database.py`:
  EXIT=1 (confirmed failed before `certslice.database` existed with `ModuleNotFoundError: No module named 'certslice.database'`)
- `.\.venv\Scripts\python.exe scripts/run_check.py --name t004 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-004`:
  EXIT=0
  RESULT: PASS
- `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
  EXIT=0
  RESULT: PASS

Acceptance criteria:
- AC1: `db_basic.xlsx` loads from its `Data Master` sheet (not the decoy first sheet), header on row 3; NIDs `007123` and `8812345` stay text; the unit comes from `UNITKET`: PASS (verified via `test_header_found_below_title_row_and_nid_keeps_leading_zeros`).
- AC2: Missing NID column, an empty sheet or a missing file exit with code 2, a plain `ERROR:` line, and no output folder content: PASS (verified via `test_missing_nid_column_stops_before_writing`, `test_empty_database_stops_before_writing`, `test_missing_database_file`).

Deviations from the plan: none.
Open issues and questions for the planner: none.

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
