---
title: "T-008 Template and signatory names, holder cue filter"
id: T-008
date: 2026-10-15
tags: [ticket]
when: Adds the bundle-level template-name exclusion and the per-page holder cue filter before the decision.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-04]
depends_on: [T-007]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: true
risk: high
---

# T-008 Template and signatory names, holder cue filter

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
A name that appears on most pages of a bundle (a director, an assessor) is never a holder, and on pages with a holder cue only the candidates near the cue count.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (sections 4 and 5 only)
- `tests/acceptance/test_signatory.py` (read only)
- `src/certslice/matcher.py`
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/matcher.py` (modify: add two functions)
- `src/certslice/pipeline.py` (modify: apply them before `decide`)
- `tests/unit/test_matcher_filters.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
Keep the last two lines of `template_employees` exactly as written:
```python
def template_employees(per_page: list[list[Candidate]], n_pages: int, cfg: dict) -> set[int]:
    m = cfg["matching"]
    counts: dict[int, int] = {}
    for cands in per_page:
        for c in cands:
            if c.score >= m["list_score"]:
                counts[c.index] = counts.get(c.index, 0) + 1
    need = max(m["template_min_pages"], math.ceil(m["template_min_share"] * n_pages))
    return {i for i, count in counts.items() if count >= need}

def cue_filter(cands: list[Candidate], lines: list[str], cfg: dict) -> list[Candidate]: ...
```

## Steps
1. Tests first: write `tests/unit/test_matcher_filters.py`: a name on 4 of 5 pages is a template name; a name on 2 of 10 pages is not; with a cue line `Diberikan kepada:` and the holder on the next line, a candidate 8 lines lower is dropped; without any confident candidate near a cue nothing is dropped. Confirm they fail first.
2. Add `template_employees` exactly as the example and `cue_filter` per extraction-rules section 5 (cue lines found with a phrase match of `holder_cues` on folded lines; window = cue line index .. index + `cue_window_lines`; result sorted by score high first, ties by employee index).
3. In `pipeline.run`: compute candidates for all pages first; `template = template_employees(...)`; for each page remove candidates whose index is in `template`, apply `cue_filter`, then `decide`. plan.json `template_names` = sorted `nama` of the template employees.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-008: signatory filter"`.

## Acceptance criteria
- AC1: `bundle_signatory.pdf`: 10 groups, all accepted, none with NID 0099001, and `Agus Prasetyo` in `template_names` (R-04).
- AC2: `bundle_bnsp_like.pdf`: `Muhammad Alfi` (between 80 and 92 against the signatory `Muhammad Al Fikri`) is a template name.
- AC3: All earlier acceptance tests still pass.

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t008 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-008
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

Weak-test check (the HUMAN runs it after acceptance; the executor does not):
````
```mutants
{"file": "src/certslice/matcher.py", "find": "if count >= need}", "replace": "if count >= need + 100}"}
```
````

No mutants block: only one mutant: `cue_filter` is not dictated line by line, so a second mutant would likely be STALE.

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
