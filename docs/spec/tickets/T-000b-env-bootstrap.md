---
title: "T-000b Environment bootstrap"
id: T-000b
date: 2026-10-15
tags: [ticket, setup]
when: Second ticket. Creates the virtual environment, installs pinned packages, generates the synthetic fixtures and proves the check scripts work.
status: todo
owner: executor
kind: setup
phase: 0
requirements: [R-00, R-22]
depends_on: [T-000a]
parallel: false
role: devops
effort: low
model_tier: standard
score: 2
tests_first: false
risk: normal
---

# T-000b Environment bootstrap

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
A clean checkout has `.venv` with the pinned packages, the project installed in editable mode, committed synthetic fixtures in `tests/fixtures/`, a lock file, and a README; the secret scan passes.

## Context to read first
- `AGENTS.md`
- `docs/spec/05-tooling.md` (sections "Toolchain" and "Security notes" only)

## Files to create or modify
- `.venv/` (create; it is gitignored)
- `tests/fixtures/*` (create by running `scripts/make_fixtures.py`; commit them)
- `requirements.lock.txt` (create with `pip freeze`)
- `README.md` (create: 6 lines)

## Files NOT to touch
- `docs/spec/**`, `scripts/**`, `tests/acceptance/**`, `src/**`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `AGENTS.md`.

## Canonical example
README shape:
```
# Certificate Slicer
Splits bundled certificate PDFs, matches holders to database.xlsx and names the files (local, offline).
Setup: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt ; .\.venv\Scripts\python.exe -m pip install -e .
Run: .\.venv\Scripts\python.exe -m certslice run --pdf <bundle.pdf> --db <database.xlsx> --out <folder>
Checks: .\.venv\Scripts\python.exe scripts/stage_tests.py --all
Spec: docs/spec/INDEX.md
```

## Steps
1. Check Python (expected 3.13.x; if not, stop and report):
   ```
   python --version
   ```
2. Create the environment and install, one command per line:
   ```
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   .\.venv\Scripts\python.exe -m pip install -e .
   ```
   `src/certslice/` has no Python files yet; the editable install still succeeds (verified in the planner sandbox) and picks up the package once T-001 creates it.
3. Generate the fixtures ONCE: `.\.venv\Scripts\python.exe scripts/make_fixtures.py` (last line `RESULT: PASS (18 fixture files ...)`). The files are not byte-identical between runs, so never run it again after committing them.
4. Write the lock file: `.\.venv\Scripts\python.exe -m pip freeze --exclude-editable | Out-File -Encoding utf8 requirements.lock.txt`.
5. Create `README.md` exactly like the canonical example.
6. Run the Verification commands.
7. Commit, one command per line: `git add -A` then `git commit -m "T-000b: environment bootstrap"`.

## Acceptance criteria
- AC1: `.\.venv\Scripts\python.exe -c "import pymupdf, rapidfuzz, openpyxl, rapidocr, pytest; print('imports ok')"` prints `imports ok`.
- AC2: `tests/fixtures/` holds 18 files (11 bundles, 2 bad PDFs, 3 databases, 2 gold CSVs) and they are committed.
- AC3: the secret scan passes; `samples/` is ignored by git.

## Verification
```
.\.venv\Scripts\python.exe scripts/run_check.py --name imports -- python -c "import pymupdf, rapidfuzz, openpyxl, rapidocr, pytest; print('imports ok')"
.\.venv\Scripts\python.exe scripts/run_check.py --name fixtures --require "fixtures=18" -- python -c "import pathlib; print('fixtures=%d' % sum(1 for p in pathlib.Path('tests/fixtures').iterdir() if p.is_file()))"
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
git check-ignore samples/x.pdf
```
Expected: the first three end with `EXIT=0` and `RESULT: PASS`; the last prints `samples/x.pdf`. If output looks truncated or ambiguous, re-run the raw command and report its exit code.

## Stop conditions
- If a verification fails twice, stop and report.
- If pip cannot find a pinned version, stop and report the exact error; do not change any version.
- If a file outside the list is needed, stop and report. Never deviate from the plan.

## Report format
Append to "Execution report" (mandatory, never shortened): status; files changed; each command with exit code and final PASS/FAIL line; acceptance criteria pass/fail with evidence; deviations; open issues.

## Constraints (repeated)
- Touch only the listed files. Do not edit scripts, tests, requirements or docs. No real data anywhere.
- Never say done until the verification commands ran in this session and their results are pasted.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [safety baseline](T-000a-safety-baseline.md), [tickets index](INDEX.md)
