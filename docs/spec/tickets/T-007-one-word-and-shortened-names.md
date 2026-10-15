---
title: "T-007 One-word names and shortened names"
id: T-007
date: 2026-10-15
tags: [ticket]
when: Adds subsumption (one-word employee inside a longer name) and partial-name candidates seen in the real master data.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-03, R-08]
depends_on: [T-006]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: true
risk: high
---

# T-007 One-word names and shortened names

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`page_candidates` drops a name contained in a longer confident name on the same line, and lists a shortened printed name as a partial candidate (score 90, never accepted) so `decide` reports it as `partial name`.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 3, the paragraphs "Shortened name" and "Subsumption", and section 6 step 1 only)
- `tests/acceptance/test_names_real_patterns.py` (read only)
- `src/certslice/matcher.py`
- `src/certslice/models.py`

## Files to create or modify
- `src/certslice/matcher.py` (modify: `partial_cover`, subsumption, one branch in `decide`)
- `src/certslice/models.py` (modify: add `partial: bool = False` as the last field of `Candidate`)
- `src/certslice/pipeline.py` (modify only to write `"partial": true` into plan candidates)
- `tests/unit/test_matcher_names.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
Patterns seen in the real data on 2026-10-15, shown with invented names: employee `SUTRISNA` (one word) scored 100 inside `ILHAM SUTRISNA`; certificates print `HANA PERTIWI` for employee `Hana Pertiwi Kusumawardani` and `SRI AYU DEWI K` for `Sri Ayu Dewi Kartika`.
```python
def partial_cover(emp_tokens: list[str], line_tokens: list[str]) -> bool:
    m = len(emp_tokens)
    for s in range(len(line_tokens)):
        if line_tokens[s] != emp_tokens[0]:
            continue
        full, i, j = 1, s + 1, 1
        while i < len(line_tokens) and j < m:
            if line_tokens[i] == emp_tokens[j]:
                full += 1
                i += 1
                j += 1
            elif len(line_tokens[i]) == 1 and line_tokens[i] == emp_tokens[j][0]:
                i += 1
                j += 1
            elif line_tokens[i] in emp_tokens[j + 1:]:
                j += 1
            else:
                break
        if full >= 2 and full < m:
            return True
    return False
```
The new first lines of `decide` (keep the rest of T-006's function exactly):
```python
    if not cands:
        return "unmatched", None, "no confident holder"
    if cands[0].score < m["accept_score"]:
        return "unmatched", None, "partial name" if cands[0].partial else "no confident holder"
```

## Steps
1. Tests first: write `tests/unit/test_matcher_names.py`: `partial_cover` true for the two examples above and false for `["budi"]` vs `budi santoso wijaya`, for a line that starts with a different first name, and for a two-word employee; a page line `ilham sutrisna` with employees `Sutrisna` and `Ilham Sutrisna` keeps only the second; a page line `hana pertiwi` with employee `Hana Pertiwi Kusumawardani` gives one partial candidate with score 90 and `decide` returns `partial name`. Confirm they fail first.
2. Add `partial_cover` exactly as the example. In `page_candidates`, after the fuzzy score of an employee on a line: when that score is below `accept_score`, the employee has at least 2 tokens and `partial_cover` is true, use score `cfg["matching"]["partial_score"]` and mark the candidate partial. After all employees are scored, apply the subsumption rule of section 3 (only non-partial candidates scoring at least `accept_score` can subsume).
3. Change the first lines of `decide` as the example; keep the homonym and margin lines from T-006 unchanged.
4. Write `"partial": true` into plan.json candidate objects of partial candidates (omit the key otherwise).
5. Run the Verification. Commit: `git add -A` then `git commit -m "T-007: one-word and shortened names"`.

## Acceptance criteria
- AC1: `bundle_bnsp_like.pdf` page 1 is accepted as `0077005` (Ilham Sutrisna) and the one-word `0077001` is not a candidate there (R-03).
- AC2: The group starting at page 3 is unmatched with reason `partial name` and candidate 1 `0077003` scoring below 92 (R-08).
- AC3: All earlier acceptance tests still pass (no new wrong acceptance).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t007 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-007
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

Weak-test check (the HUMAN runs it after acceptance; the executor does not):
````
```mutants
{"file": "src/certslice/matcher.py", "find": "if full >= 2 and full < m:", "replace": "if False:"}
{"file": "src/certslice/matcher.py", "find": "\"partial name\" if cands[0].partial else", "replace": "\"x\" if False else"}
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
Status: not started.

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
