---
title: Verification plan
date: 2026-10-15
tags: [spec, verification]
when: To see how every requirement is proven, with exact commands, and which files the executor must not edit.
---

# Verification plan

Principles: the planner owns "done"; checks are mechanical (exit code plus a final PASS/FAIL line); acceptance tests and fixtures are planner-owned and protected ([ADR-007](decisions/ADR-007-planner-owned-acceptance-tests.md)); a fresh-context reviewer diff-checks that no test, threshold or config value was weakened.

## How to run checks
All commands run from the repo root in PowerShell, with the project interpreter running the wrapper (so `python` after `--` is the `.venv` interpreter):
```
.\.venv\Scripts\python.exe scripts/run_check.py --name <name> --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-0xx
```
Expected last two lines: `EXIT=0` and `RESULT: PASS`. `stage_tests.py --upto T-0xx` runs every acceptance file up to that ticket plus `tests/unit` (regression included). Converge check after each phase and before any release: `... -- python scripts/stage_tests.py --all`.
Proof level: on 2026-10-15 a throwaway planner implementation passed all 81 acceptance tests on Linux, Python 3.13.16, pinned packages, and was run on two real bundles with the real master data (ADR-008: 11 of 14 certificates accepted, 3 reported, no wrong NID seen); the repo setup (venv, editable install, `stage_tests.py`, `run_check.py`, `scan_secrets.py`) was rehearsed end to end in the sandbox. Not yet run on Windows.

## Traceability matrix
| Requirement | Check type | Command or manual check | Expected result | Ticket |
|-------------|-----------|-------------------------|-----------------|--------|
| R-00 | script | `python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py` | `RESULT: PASS` | T-000b |
| R-01 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t004 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-004` (test_t001_skeleton, test_database) | `RESULT: PASS` | T-001, T-004 |
| R-02 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t017 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-018` (test_ocr) | `RESULT: PASS` | T-018 |
| R-02 | manual | manual: OCR spike on 3 real scanned pages (T-017), owner human, pass = holder name and dates readable in the printed text for at least 2 of 3 pages | spike report | T-017 |
| R-03 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t007 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-007` (test_normalize_contract, test_matching, test_names_real_patterns) | `RESULT: PASS` | T-003, T-006, T-007 |
| R-04 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t007 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-008` (test_signatory) | `RESULT: PASS` | T-008 |
| R-05 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t009 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-010` (test_grouping) | `RESULT: PASS` | T-010 |
| R-06 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t010 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-011` (test_fields_dates, test_fields_text) | `RESULT: PASS` | T-009, T-011 |
| R-07 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t011 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-012` (test_expiry) | `RESULT: PASS` | T-012 |
| R-08 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t007 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-007` (homonym, close candidates, clear winner, partial name) | `RESULT: PASS` | T-006, T-007 |
| R-09 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t014 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-015` (dry run, real run) | `RESULT: PASS` | T-001, T-015 |
| R-10 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t012 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-013` (test_naming) | `RESULT: PASS` | T-013 |
| R-11 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t013 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-014` (manifest) | `RESULT: PASS` | T-014 |
| R-12 | acceptance | same command as R-11 (unmatched report) | `RESULT: PASS` | T-014 |
| R-13 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t014 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-015` (accounting on every bundle) | `RESULT: PASS` | T-010, T-015 |
| R-14 | acceptance | same command as R-13 (rerun never overwrites) | `RESULT: PASS` | T-015 |
| R-15 | acceptance | same command as R-11 (needs_review sheet) | `RESULT: PASS` | T-014 |
| R-16 | acceptance | same command as R-10 (alias table) | `RESULT: PASS` | T-013 |
| R-17 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t005 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-005` (test_reader_hook) | `RESULT: PASS` | T-005 |
| R-18 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t018 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-019` (test_evaluate) | `RESULT: PASS` | T-019 |
| R-18 | manual | manual: real-data pilot (T-020), owner human; pass = `WRONG_NID=0` on every sample bundle and the SET text line at least 80% | pasted evaluate lines | T-020 |
| R-19 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t005 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-005` (test_database, test_pdf_input) | `RESULT: PASS` | T-004, T-005 |
| R-20 | acceptance | `.\.venv\Scripts\python.exe scripts/run_check.py --name t014 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-015` (test_speed) | `RESULT: PASS` | T-015 |
| R-21 | acceptance | same command as R-20 (spaces in paths, module entry point) and R-10 (accented name) | `RESULT: PASS` | T-013, T-015 |
| R-22 | acceptance | same command as R-20 (test_offline) and R-02 (OCR offline) | `RESULT: PASS` | T-015, T-018 |
| R-22 | script | `git check-ignore samples/x.pdf` | prints `samples/x.pdf` | T-000b |

## Protected files (executor must not edit)
- `tests/acceptance/**`, `tests/fixtures/**` (generated once by T-000b, then committed), `tests/unit/**` after the ticket that created a file was accepted.
- `scripts/**` (run_check, scan_secrets, collect_evidence, check_mutants, make_fixtures, stage_tests).
- `src/certslice/default_config.toml` (thresholds and word lists are spec), `pyproject.toml`, `requirements*.txt`, `requirements.lock.txt` after T-000b.
- `docs/spec/**` except the ticket's `status:` line and Execution report; `AGENTS.md`; `.agents/**`; `.gitignore` after T-000b.

## Manual checks
- OCR spike (T-017): owner human; steps in the ticket; pass criteria above.
- Real-data pilot (T-020): owner human; steps in the ticket; pass criteria above and the brief's kill criteria.
- Reviewer pass (optional, fresh session, read-only): diff against `pre/T-xxx`, confirm no protected file changed and no assertion, threshold or skip marker was weakened.

## Weak-test check (human, after risky tickets)
Tickets T-003, T-006, T-007 and T-008 carry a `mutants` block (all 7 mutants were KILLED against code shaped like the tickets' dictated snippets in a planner trial). After they pass: `python scripts/check_mutants.py --ticket docs/spec/tickets/<file>.md -- .\.venv\Scripts\python.exe scripts/stage_tests.py --upto <id>`; every mutant should be KILLED. STALE means the planner guessed the code text wrong (not an executor failure).

## Security checks
- Secret scan: `python scripts/scan_secrets.py` (T-000b and every ticket's regression).
- Dependency control: pinned `requirements.txt`, `requirements.lock.txt` committed; no new package without a ticket naming it. (A dependency audit tool such as pip-audit is not pinned in M1; add it in M2 before distribution.)
- Network: `test_offline.py` and `test_ocr.py::test_ocr_run_offline` block sockets.
- Real data: `git check-ignore samples/x.pdf` must print the path (T-000b).

## UI checks
Not applicable in M1.

## Evals
Not applicable in M1 (no LLM). M3 will add an eval set for the LLM rescue.

Related: [requirements](01-requirements.md), [design](02-design.md), [tickets index](tickets/INDEX.md)
