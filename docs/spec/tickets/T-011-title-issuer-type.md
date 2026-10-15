---
title: "T-011 Title, issuer, document type and renewal"
id: T-011
date: 2026-10-15
tags: [ticket]
when: Extends fields.py with template-line detection, title, issuer, document type and renewal number.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-06, R-15]
depends_on: [T-010]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: false
risk: normal
---

# T-011 Title, issuer, document type and renewal

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Every group has `fields.title`, `fields.issuer`, `fields.doc_type` with confidence and `renewal` in plan.json.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (sections 8 and 9: Title, Issuer, Document type, Renewal only)
- `tests/acceptance/test_fields_text.py` (read only)
- `src/certslice/fields.py`
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/fields.py` (modify: add four functions)
- `src/certslice/pipeline.py` (modify: fill the fields)
- `tests/unit/test_fields_text.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
def template_lines(pages_lines: list[list[str]], cfg: dict) -> set[str]: ...   # folded lines
def extract_title(first_lines, template, group_is_bundle, n_pages, cfg) -> tuple[str | None, str]: ...
def extract_issuer(group_lines, template, cfg) -> tuple[str | None, str]: ...
def doc_type(title, group_lines, cfg) -> tuple[str, str]: ...
def renewal_number(group_lines, cfg) -> int | None: ...
```
Expected on the fixtures: LSP title `Teknisi Distribusi Tenaga Listrik`, issuer `LEMBAGA SERTIFIKASI PROFESI PLN`; training title `Pemeliharaan Gardu Induk 150 kV` (cue `telah mengikuti pelatihan` with an empty rest takes the next line); BNSP layout: after `Telah Kompeten pada :` the line `Is Competent for :` is skipped and the title is `Staf Sumber Daya Manusia`; issuer `Lembaga Sertifikasi Profesi` is joined with its next line.

## Steps
1. Write `tests/unit/test_fields_text.py` with at least 10 cases (cue with text on the same line, cue with the title on the next line, a following label line ending with `:` skipped, a following line that contains another cue skipped, generic rest like `pelatihan` skipped, keyword fallback, issuer keyword priority order, `issuer_exclude` skipping the employer line, `issuer_join_next` joining the next line, `Pelatihan` vs `Sertifikasi`, renewal `Perpanjangan ke-2` -> 2, no renewal -> None).
2. Add the functions to `fields.py` following sections 8 and 9 exactly.
3. In `pipeline.run` compute `template_lines` once per bundle and fill `title`, `issuer`, `doc_type` and group `renewal`. Unmatched groups get the fields too.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-011: title, issuer, type, renewal"`.

## Acceptance criteria
- AC1: LSP, DJK, training and BNSP-like fixtures give the expected title, issuer and document type (R-06).
- AC2: `bundle_titles.pdf` renewals are `[None, None, 2]`; low-confidence fields are marked `low` for T-014's needs-review sheet (R-15).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t011 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-011
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
- Ticket: T-011, status: done
- Files changed:
  - `src/certslice/fields.py` (modified)
  - `src/certslice/pipeline.py` (modified)
  - `tests/unit/test_fields_text.py` (created)
- Commands run:
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name t011 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-011`:
    EXIT=0
    RESULT: PASS
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
    EXIT=0
    RESULT: PASS
- Acceptance criteria:
  - AC1: pass (LSP, DJK, training and BNSP-like fixtures give the expected title, issuer and document type)
  - AC2: pass (`bundle_titles.pdf` renewals are `[None, None, 2]`; low-confidence fields marked `low`)
- Deviations from the plan: none
- Open issues and questions for the planner: none

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
