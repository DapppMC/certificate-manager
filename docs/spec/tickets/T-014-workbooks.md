---
title: "T-014 Manifest and report workbooks"
id: T-014
date: 2026-10-15
tags: [ticket]
when: Adds writer.py output for manifest_slice.xlsx (10 columns) and unmatched_report.xlsx (unmatched, needs_review).
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-11, R-12, R-15]
depends_on: [T-013]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: false
risk: normal
---

# T-014 Manifest and report workbooks

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
A real run writes both workbooks exactly as interface sections 6 and 7 say, from the plan groups.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/interface.md` (sections 6 and 7 only)
- `tests/acceptance/test_outputs.py` (read only)
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/writer.py` (create)
- `src/certslice/pipeline.py` (modify: call the writer when not dry-run)
- `tests/unit/test_writer.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
MANIFEST_HEADERS = ["No", "NID", "Nama Pegawai", "Judul Sertifikasi/Pelatihan",
    "Jenis Dokumen (Sertifikasi/Pelatihan)", "Lembaga/Penyelenggara", "Nomor Sertifikat",
    "Tanggal Terbit", "Tanggal Expired", "Nama File PDF"]

def text_cell(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value="" if value is None else str(value))
    cell.number_format = "@"

def write_workbooks(run_dir: Path, groups: list[dict], cfg: dict) -> None: ...
```

## Steps
1. Write `tests/unit/test_writer.py` that calls `write_workbooks` with two hand-made group dicts (one accepted with a low-confidence issue date, one unmatched with two candidates) and checks headers, text NID cells, the `5-6` page label, `DD/MM/YYYY` dates and one needs_review row.
2. Write `writer.py`: manifest sheet `manifest` (accepted groups in page order, `No` integer, other cells text, dates formatted with `cfg["output"]["date_format"]`); report sheets `unmatched` and `needs_review` with the exact headers of interface section 7; needs_review lists every field with confidence `low` of accepted groups, using the manifest header name in `Kolom`.
3. In `pipeline.run` call `write_workbooks(run_dir, plan_groups, cfg)` after the PDFs when not `dry_run`.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-014: workbooks"`.

## Acceptance criteria
- AC1: `manifest_slice.xlsx` of `bundle_simple.pdf` has the 10 headers and row 1 values of the acceptance test, NIDs as text (R-11).
- AC2: `unmatched_report.xlsx` of `bundle_unmatched.pdf` has rows for pages 1, 2 and 4 with reasons and candidates (R-12).
- AC3: `bundle_mixed.pdf` page 7 appears in needs_review with `Tanggal Terbit`; file name ends `_0000.pdf` (R-15).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t014 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-014
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
Status: done.
- Ticket: T-014, status: done
- Files changed:
  - `src/certslice/writer.py` (created)
  - `src/certslice/pipeline.py` (modified)
  - `tests/unit/test_writer.py` (created)
- Commands run:
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name t014 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-014`:
    EXIT=0
    RESULT: PASS
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
    EXIT=0
    RESULT: PASS
- Acceptance criteria:
  - AC1: pass (`manifest_slice.xlsx` of `bundle_simple.pdf` has the 10 headers and row 1 values, NIDs as text)
  - AC2: pass (`unmatched_report.xlsx` of `bundle_unmatched.pdf` has rows for pages 1, 2 and 4 with reasons and candidates)
  - AC3: pass (`bundle_mixed.pdf` page 7 appears in needs_review with `Tanggal Terbit`; file name ends `_0000.pdf`)
- Deviations from the plan: none
- Open issues and questions for the planner: none

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
