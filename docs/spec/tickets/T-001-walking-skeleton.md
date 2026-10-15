---
title: "T-001 Walking skeleton: bundle in, named PDFs and plan.json out"
id: T-001
date: 2026-10-15
tags: [ticket]
when: First feature: the thinnest end-to-end run from one bundle and database to split PDFs and plan.json.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-01, R-09, R-13]
depends_on: [T-000b]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 4
tests_first: false
risk: normal
---

# T-001 Walking skeleton: bundle in, named PDFs and plan.json out

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`python -m certslice run --pdf tests/fixtures/bundle_simple.pdf --db tests/fixtures/db_basic.xlsx --out <folder>` creates a run folder with one PDF per page named `{NID}_{Nama}.pdf` and a plan.json, using deliberately simple matching that later tickets replace.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/interface.md` (sections 1, 2, 4 and 5 only)
- `tests/acceptance/test_t001_skeleton.py` (read only)

## Files to create or modify
- `src/certslice/__init__.py` (create: one line `__version__ = "0.1.0"`)
- `src/certslice/__main__.py` (create)
- `src/certslice/errors.py` (create)
- `src/certslice/cli.py` (create)
- `src/certslice/pipeline.py` (create)
- `tests/unit/test_cli.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
# errors.py
class InputError(Exception):
    """Bad input found before anything is written (exit code 2)."""

class AccountingError(Exception):
    """Pages lost or duplicated (exit code 3)."""

# __main__.py
import sys
from certslice.cli import main
sys.exit(main())

# pipeline.py (shape)
@dataclass
class RunResult:
    run_dir: Path | None
    exit_code: int
    summary: dict

def run(pdf_path, db_path, out_dir, *, dry_run=False, pages_per_cert="auto", ocr=True, config_path=None,
        reader=None) -> RunResult: ...
```
PyMuPDF usage (version 1.28.2, import name `pymupdf`): `doc = pymupdf.open(path)`; `page.get_text("text")`; `new = pymupdf.open(); new.insert_pdf(doc, from_page=i, to_page=i); new.save(path); new.close()`. openpyxl 3.1.5: `load_workbook(path, read_only=True, data_only=True)`, `ws.iter_rows(values_only=True)`.

## Steps
1. Create `errors.py` and `__main__.py` exactly as in the canonical example, and `__init__.py` with the one line.
2. In `cli.py` write `main(argv: list[str] | None = None) -> int` with argparse subcommands `run` (flags `--pdf`, `--db`, `--out` required; `--dry-run`; `--pages-per-cert` choices `1`, `2`, `auto`, default `auto`; `--no-ocr`; `--config`) and `evaluate` (`--run`, `--gold` required). For `evaluate` print `ERROR: evaluate is not implemented yet` to stderr and return 1 (T-019 replaces it). For `run` call `pipeline.run(...)` (pass `ocr=not args.no_ocr`); catch `InputError` -> print `ERROR: <message>` to stderr, return 2; catch `AccountingError` -> same, return 3; on success print `PAGES=<pages> ACCEPTED=<accepted> UNMATCHED=<unmatched>` then `RUN_DIR=<absolute run folder>` and return `result.exit_code`. `main` never calls `sys.exit`.
3. In `pipeline.py` write `RunResult` and `run(...)` with the exact signature above. Skeleton behaviour, in this order: (a) if the PDF or database path does not exist raise `InputError("... not found: <path>")`; (b) read the sheet whose name, lowercased and stripped, is `data master` (else the first sheet) of the database; the header row is the first of the first 10 rows that contains a cell equal to `nid` after `str(v).strip().lower()`; the name column is the cell equal to `nama_lengkap` or `nama lengkap` or `nama`; NID values that are `int`, or `float` with an integer value, become `str(int(v))`, others `str(v).strip()`; skip rows with an empty NID or name; (c) open the PDF and read `page.get_text("text")` for every page; (d) simple match: fold a string as lowercase with every non-alphanumeric character replaced by a space and spaces collapsed; an employee matches a page when `" " + folded name + " "` is inside `" " + folded page text + " "`; a page is accepted when exactly one distinct NID matches, else unmatched with reason `no confident holder`; (e) one group per page; (f) create the run folder `<out>/run-YYYYMMDD-HHMMSS` (`datetime.now().strftime`), adding `-2`, `-3` while the name exists, creating `<out>` if missing; (g) unless `dry_run`: create `pdf/` and save each accepted page as `{nid}_{nama}.pdf` (nama from the database row); (h) write `plan.json` (UTF-8, `ensure_ascii=False`, indent 2) with the keys of interface section 5: `pages` entries `{"page", "source": "text", "candidates": []}`, group entries with `id`, `pages`, `status`, `reason`, `nid`, `nama`, `email`, `unit`, `candidates` (`[]`), `fields` (`{}`), `expiry_source`, `renewal`, `file_name` (null when unmatched), `template_names` `[]`, `timings` `{}`; (i) return `RunResult(run_dir, 0, {"pages": n, "accepted": a, "unmatched": u})`.
4. In `tests/unit/test_cli.py` write two tests: `main(["run", "--pdf", <missing file in tmp_path>, "--db", <missing>, "--out", <tmp_path>])` returns 2; `main(["evaluate", "--run", "x", "--gold", "y"])` returns 1. Do not create `tests/unit/conftest.py`.
5. Re-run the editable install so the package is found: `.\.venv\Scripts\python.exe -m pip install -e .`
6. Try it by hand: `.\.venv\Scripts\python.exe -m certslice run --pdf tests/fixtures/bundle_simple.pdf --db tests/fixtures/db_basic.xlsx --out out --no-ocr` (the `out/` folder is gitignored). Expect three PDFs.
7. Run the Verification. Commit: `git add -A` then `git commit -m "T-001: walking skeleton"`.

## Acceptance criteria
- AC1: `bundle_simple.pdf` gives three PDFs whose names start with `007123_Budi Santoso`, `0045678_Siti Rahmawati` and `8812345_Dewi Lestari` (R-01, R-09).
- AC2: plan.json lists pages 1 to 3 and every page in exactly one group (R-13).
- AC3: stdout contains `PAGES=3 ACCEPTED=3 UNMATCHED=0` and a `RUN_DIR=` line.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t001 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-001
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

Files changed:
- `src/certslice/__init__.py` (created)
- `src/certslice/__main__.py` (created)
- `src/certslice/errors.py` (created)
- `src/certslice/cli.py` (created)
- `src/certslice/pipeline.py` (created)
- `tests/unit/test_cli.py` (created)
- `docs/spec/tickets/T-001-walking-skeleton.md` (modified)

Commands run:
- `.\.venv\Scripts\python.exe scripts/run_check.py --name t001 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-001`:
  EXIT=0
  RESULT: PASS
- `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
  EXIT=0
  RESULT: PASS

Acceptance criteria:
- AC1: `bundle_simple.pdf` gives three PDFs whose names start with `007123_Budi Santoso`, `0045678_Siti Rahmawati` and `8812345_Dewi Lestari`: PASS (verified via `test_skeleton_writes_one_pdf_per_holder`).
- AC2: plan.json lists pages 1 to 3 and every page in exactly one group: PASS (verified via `test_skeleton_plan_accounts_for_every_page`).
- AC3: stdout contains `PAGES=3 ACCEPTED=3 UNMATCHED=0` and a `RUN_DIR=` line: PASS (verified via `test_skeleton_prints_summary_lines`).

Deviations from the plan: none.
Open issues and questions for the planner: none.

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
