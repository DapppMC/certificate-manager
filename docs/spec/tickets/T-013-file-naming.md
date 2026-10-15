---
title: "T-013 Standard file names, aliases and path length"
id: T-013
date: 2026-10-15
tags: [ticket]
when: Adds naming.py: {NID}_{Nama}_{Judul}_{Tahun}.pdf, renewal suffix, illegal characters, aliases, path budget, _dup counter.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-10, R-16, R-21]
depends_on: [T-012]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: true
risk: normal
---

# T-013 Standard file names, aliases and path length

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Accepted groups get their final file names before anything is written; an output folder that is too long stops the run with exit 2.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 10 only)
- `docs/spec/modules/interface.md` (section 3, alias CSV)
- `tests/acceptance/test_naming.py` (read only)
- `src/certslice/pipeline.py`
- `src/certslice/config.py` (function `load_aliases` only)

## Files to create or modify
- `src/certslice/naming.py` (create)
- `src/certslice/pipeline.py` (modify: names before creating the run folder)
- `tests/unit/test_naming.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
RUN_PART = 28     # len("/run-YYYYMMDD-HHMMSS-NN/pdf/")
DUP_RESERVE = 6   # len("_dup99")

class NameBuilder:
    def __init__(self, out_dir: Path, cfg: dict): ...   # computes room once
    def build(self, nid, nama, judul, tahun, renewal) -> str: ...   # raises InputError when it cannot fit
```
Expected: `007123_Budi Santoso_Teknisi Distribusi Tenaga Listrik_2025.pdf`; third Budi copy `..._2025_dup3.pdf`; `0055001_Andréas Siregar_<short title>_2025_PERPANJANGAN_2.pdf`.

## Steps
1. Tests first: write `tests/unit/test_naming.py` (illegal characters `<>:"/\|?*` and `_` inside fields become `-`; trailing dots removed; renewal suffix; duplicates `_dup2`, `_dup3` with the same shortened title; a long title cut so the full path is at most 250; a too-long folder raises `InputError` containing `too long`; accents kept). Confirm they fail first.
2. Write `naming.py` per section 10 (`room = max_path_chars - len(str(out_dir.resolve())) - RUN_PART - DUP_RESERVE`).
3. In `pipeline.run`, before the run folder is created: `aliases = load_aliases(cfg)`; for each accepted group `judul = aliases.get(fold_text(title), title)` (or `unknown_title` when title is None), store it as `fields.title.value`; `tahun` = issue year or `unknown_year`; `file_name = builder.build(...)`. Remove the skeleton `{nid}_{nama}.pdf` naming.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-013: file naming"`.

## Acceptance criteria
- AC1: `bundle_simple.pdf` gives exactly the three standard names (R-10).
- AC2: Long title, duplicates, renewal and the accented name pass; every path is at most 250 characters (R-10, R-21).
- AC3: An alias CSV given through `--config` changes the title in the names (R-16); a 240-character `--out` exits 2.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t013 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-013
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

No mutants block: naming has 4 acceptance tests with exact expected names (including duplicates, renewal and path length); the code text is not dictated.

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
