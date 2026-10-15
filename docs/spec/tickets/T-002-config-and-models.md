---
title: "T-002 Configuration loader and shared data models"
id: T-002
date: 2026-10-15
tags: [ticket]
when: Loads default_config.toml plus a user override and the alias CSV; defines PageText, PageReader, Employee and Candidate.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-07, R-16, R-17]
depends_on: [T-001]
parallel: false
role: backend
effort: low
model_tier: standard
score: 2
tests_first: false
risk: normal
---

# T-002 Configuration loader and shared data models

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`config.load_config()` returns the merged configuration dict and `models.py` holds the dataclasses every later ticket uses; `pipeline.run` loads the configuration first.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/interface.md` (sections 2 and 3 only)
- `src/certslice/default_config.toml` (read only)
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/config.py` (create)
- `src/certslice/models.py` (create)
- `src/certslice/pipeline.py` (modify: first line of `run` loads the config into `cfg`)
- `tests/unit/test_config.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
# models.py
@dataclass
class PageText:
    index: int      # 1-based page number
    text: str       # lines separated by \n
    source: str     # "text" | "ocr" | "none" | reader-defined

class PageReader(Protocol):
    def read(self, page: "pymupdf.Page") -> PageText: ...

@dataclass
class Employee:
    nid: str
    nama: str
    email: str = ""
    unit: str = ""
    jabatan: str = ""
    norm: str = ""   # normalize_name(nama)

@dataclass
class Candidate:
    index: int      # position of the employee in the employee list
    score: float
    line: int       # index of the page line with the best score
```

## Steps
1. Write `models.py` exactly as the canonical example (add the imports `dataclasses.dataclass` and `typing.Protocol`).
2. Write `config.py` with `load_config(path: str | Path | None) -> dict`: read `Path(__file__).with_name("default_config.toml")` with `tomllib.loads(text)`; when `path` is given, read it the same way and merge: for every top-level key, if both values are dicts call `default[key].update(user[key])`, otherwise replace the value (so a user `validity_rules` list replaces the default list). Store the folder used for relative paths in `cfg["_dir"]` (the user file's folder, else the package folder). A missing or invalid user file raises `InputError` with the path in the message.
3. In `config.py` add `fold_text(s: str) -> str` (NFKD, drop combining marks, `casefold()`, collapse whitespace, strip) and `load_aliases(cfg: dict) -> dict[str, str]`: when `cfg["naming"]["alias_file"]` is empty return `{}`; else open it (relative to `cfg["_dir"]`) with `encoding="utf-8-sig"`, read with `csv.DictReader`, map `fold_text(raw_title)` to `short_title.strip()` for rows where both are non-empty.
4. In `pipeline.run` call `cfg = load_config(config_path)` before anything else; keep all skeleton behaviour.
5. Write `tests/unit/test_config.py`: defaults load (`cfg["matching"]["accept_score"] == 92`); a user file with only `[naming] alias_file = "a.csv"` keeps `cfg["naming"]["max_path_chars"] == 250`; a user `[[validity_rules]]` replaces the list; the alias loader reads a CSV saved with a BOM; a missing user file raises `InputError`.
6. Run the Verification. Commit: `git add -A` then `git commit -m "T-002: config and models"`.

## Acceptance criteria
- AC1: `load_config(None)` returns the defaults and the merge rules above hold (R-07, R-16).
- AC2: `PageText` and `PageReader` exist in `certslice.models` with the fields shown (R-17).
- AC3: All earlier acceptance tests still pass.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t002 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-002
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
