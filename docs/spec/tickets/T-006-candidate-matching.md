---
title: "T-006 Candidate scoring and the conservative decision"
id: T-006
date: 2026-10-15
tags: [ticket]
when: Replaces the skeleton match with fuzzy reverse lookup per line and the four-step accept rule.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-03, R-08]
depends_on: [T-005]
parallel: false
role: backend
effort: high
model_tier: standard
score: 4
tests_first: true
risk: high
---

# T-006 Candidate scoring and the conservative decision

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Each page gets scored candidates (rapidfuzz over line windows) and a decision `accepted`, or `unmatched` with reason `no confident holder`, `homonym` or `close candidates`; plan.json shows them.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (sections 3 and 6 only)
- `docs/spec/modules/interface.md` (section 5 only)
- `tests/acceptance/test_matching.py` (read only)
- `src/certslice/pipeline.py`
- `src/certslice/models.py`

## Files to create or modify
- `src/certslice/matcher.py` (create)
- `src/certslice/pipeline.py` (modify: use matcher, fill candidates)
- `tests/unit/test_matcher.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
Keep the comparison lines of `decide` exactly as written (the weak-test check targets them):
```python
def decide(cands: list[Candidate], employees: list[Employee], cfg: dict):
    m = cfg["matching"]
    if not cands or cands[0].score < m["accept_score"]:
        return "unmatched", None, "no confident holder"
    top = cands[0]
    if len(cands) > 1:
        second = cands[1]
        if employees[second.index].norm == employees[top.index].norm:
            return "unmatched", None, "homonym"
        if top.score - second.score < m["margin"]:
            return "unmatched", None, "close candidates"
    return "accepted", top, ""
```
rapidfuzz 3.14.6: `from rapidfuzz import fuzz`; `fuzz.ratio(a, b)` returns 0..100.

## Steps
1. Tests first: write `tests/unit/test_matcher.py` for `page_candidates` and `decide`: exact name with titles scores 100; a one-letter typo still scores at least 92; two rows with the same normalised name give `homonym`; two different names within 5 points give `close candidates`; a page without any name gives `no confident holder`. Confirm the tests fail before `matcher.py` exists.
2. Write `matcher.py` with `page_candidates(lines: list[str], employees: list[Employee], cfg: dict) -> list[Candidate]` following extraction-rules section 3 exactly (tokens of each line from `normalize.name_tokens`; window sizes k-1, k, k+1; names with fewer than `min_tokens_fuzzy` tokens need an exact token; keep scores at or above `list_score`; one Candidate per employee with its best score and line), sorted by score high first, ties by employee index; and `decide` exactly as the example.
3. In `pipeline.run` replace the skeleton matching: per page `cands = page_candidates(lines, employees, cfg)`, `status, top, reason = decide(cands, employees, cfg)`; plan.json `pages[].candidates` = first 3 as `{"nid", "nama", "score": round(score, 1)}`; group `candidates` = first 3 with `unit` added; accepted groups take `nid`, `nama`, `email`, `unit` from `employees[top.index]`.
4. Run the Verification. If `test_speed` is not yet in the stage, still time one run of `bundle_30.pdf` by hand (`Measure-Command { ... }`) and report the seconds.
5. Commit: `git add -A` then `git commit -m "T-006: candidate matching"`.

## Acceptance criteria
- AC1: `Ir. BUDI SANTOSO, S.T., M.M.` matches NID 007123 with score of at least 92 (R-03).
- AC2: `bundle_unmatched.pdf`: page 1 `homonym` listing both Ahmad Fauzi NIDs and units, page 2 `no confident holder`, page 3 accepted 0033333, page 4 `close candidates` (R-08).
- AC3: Every accepted group's first candidate is its NID and leads the next by at least 5 points.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t006 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-006
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

Weak-test check (the HUMAN runs it after acceptance; the executor does not):
````
```mutants
{"file": "src/certslice/matcher.py", "find": "top.score - second.score < m[\"margin\"]", "replace": "top.score - second.score < 0"}
{"file": "src/certslice/matcher.py", "find": "== employees[top.index].norm", "replace": "== \"never-equal\""}
```
````

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
- `src/certslice/matcher.py` (created)
- `src/certslice/pipeline.py` (modified)
- `tests/unit/test_matcher.py` (created)
- `docs/spec/tickets/T-006-candidate-matching.md` (modified)

Commands run:
- `.\.venv\Scripts\python.exe -m pytest tests/unit/test_matcher.py`:
  EXIT=1 (confirmed failed before `certslice.matcher` existed with `ModuleNotFoundError: No module named 'certslice.matcher'`)
- `Measure-Command { .\.venv\Scripts\python.exe -m certslice run --pdf tests/fixtures/bundle_30.pdf --db tests/fixtures/db_basic.xlsx --out out --no-ocr --dry-run }`:
  TotalSeconds = 1.144s
- `.\.venv\Scripts\python.exe scripts/run_check.py --name t006 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-006`:
  EXIT=0
  RESULT: PASS
- `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
  EXIT=0
  RESULT: PASS

Acceptance criteria:
- AC1: `Ir. BUDI SANTOSO, S.T., M.M.` matches NID 007123 with score of at least 92: PASS (verified via `test_titles_and_case_do_not_block_a_match`).
- AC2: `bundle_unmatched.pdf`: page 1 `homonym` listing both Ahmad Fauzi NIDs and units, page 2 `no confident holder`, page 3 accepted 0033333, page 4 `close candidates`: PASS (verified via `test_homonym_and_stranger_are_not_accepted`).
- AC3: Every accepted group's first candidate is its NID and leads the next by at least 5 points: PASS (verified via `test_every_accepted_group_has_a_clear_winner`).

Deviations from the plan: none.
Open issues and questions for the planner: none.

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
