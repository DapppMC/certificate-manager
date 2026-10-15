---
title: "T-003 Name normalisation"
id: T-003
date: 2026-10-15
tags: [ticket]
when: Implements normalize_name exactly as the extraction rules say; titles, case, accents and punctuation stop mattering.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-03]
depends_on: [T-002]
parallel: false
role: backend
effort: low
model_tier: standard
score: 2
tests_first: true
risk: high
---

# T-003 Name normalisation

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`certslice.normalize.normalize_name` turns any printed or stored name into the canonical form used for matching.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 1 and the definition of fold only)
- `tests/acceptance/test_normalize_contract.py` (read only)

## Files to create or modify
- `src/certslice/normalize.py` (create)
- `tests/unit/test_normalize.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
import re, unicodedata
from certslice.config import load_config

_TITLES = frozenset(load_config(None)["matching"]["titles"])

def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()

def name_tokens(s: str, titles=_TITLES) -> list[str]:
    s = fold(s).replace(".", "").replace("'", "")
    tokens = re.sub(r"[^a-z0-9]+", " ", s).split()
    return [t for t in tokens if t not in titles]

def normalize_name(s: str) -> str:
    return " ".join(name_tokens(s))
```

## Steps
1. Tests first: write `tests/unit/test_normalize.py` with at least 8 cases of your own (titles before and after the name, commas, double spaces, accents, apostrophes, hyphens, empty string, a name containing a title-like substring such as `Hendra`). Run it and confirm it FAILS because `certslice.normalize` does not exist yet.
2. Create `src/certslice/normalize.py` exactly as the canonical example (keep the comprehension line `return [t for t in tokens if t not in titles]` as written).
3. Run the Verification. Commit: `git add -A` then `git commit -m "T-003: name normalisation"`.

## Acceptance criteria
- AC1: Every case in `tests/acceptance/test_normalize_contract.py` passes (R-03).
- AC2: Your unit tests failed before the module existed and pass now (say so in the report).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t003 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-003
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

Weak-test check (the HUMAN runs it after acceptance; the executor does not):
````
```mutants
{"file": "src/certslice/normalize.py", "find": "if t not in titles]", "replace": "if t]"}
{"file": "src/certslice/normalize.py", "find": ".casefold()", "replace": ".upper()"}
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
