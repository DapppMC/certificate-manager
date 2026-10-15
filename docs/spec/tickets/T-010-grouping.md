---
title: "T-010 Certificate boundaries: auto and fixed grouping"
id: T-010
date: 2026-10-15
tags: [ticket]
when: Adds grouping.py: 1/2-page certificates in auto mode, --pages-per-cert 1|2, reasons and the page-accounting check.
status: done
owner: executor
kind: feature
phase: 1
requirements: [R-05, R-13]
depends_on: [T-009]
parallel: false
role: backend
effort: high
model_tier: standard
score: 4
tests_first: true
risk: normal
---

# T-010 Certificate boundaries: auto and fixed grouping

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Pages are grouped into certificates by the auto rules (or fixed chunks), each group carries the decision of its deciding page, and a lost or doubled page raises `AccountingError`.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 7 only)
- `tests/acceptance/test_grouping.py` (read only)
- `src/certslice/pipeline.py`
- `src/certslice/fields.py` (function `extract_number` only)

## Files to create or modify
- `src/certslice/grouping.py` (create)
- `src/certslice/pipeline.py` (modify: group pages, write groups)
- `tests/unit/test_grouping.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
@dataclass
class PageInfo:              # put it in grouping.py
    decision: tuple       # (status, Candidate | None, reason) from matcher.decide
    is_start: bool        # first candidate scores >= accept_score
    number: str | None    # extract_number(page lines)
    continuation: bool    # a continuation_patterns regex matches the folded page text
    source: str

def group_pages(infos: list[PageInfo], employees, mode: str, cfg: dict) -> list[dict]: ...
# each dict: {"pages": [1-based...], "status", "candidate": Candidate | None, "reason", "deciding_page": 1-based}
def check_accounting(groups: list[dict], n_pages: int) -> None: ...   # raises AccountingError
```

## Steps
1. Tests first: write `tests/unit/test_grouping.py` with PageInfo lists (no PDFs): a holder-less page with `Halaman 2/2` joins the previous group; a page with the same holder and a new number starts a new group; a holder-less page without signs is its own unmatched group; the group limit `max_pages_per_cert` holds; fixed mode 2 with two different accepted NIDs in one chunk is `fixed chunk without one clear holder`; `check_accounting` raises on a missing and on a doubled page. Confirm they fail first.
2. Write `grouping.py` following extraction-rules section 7 exactly, including the `no text` reason.
3. In `pipeline.run`: build a `PageInfo` per page; `groups = group_pages(infos, employees, pages_per_cert, cfg)`; `check_accounting(groups, n_pages)`; build the plan groups from them (group candidates and fields come from the deciding page / all group lines; the number of a group is the number of its first page); PDFs contain all pages of the group (`insert_pdf(doc, from_page=first-1, to_page=last-1)`).
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-010: grouping"`.

## Acceptance criteria
- AC1: `bundle_mixed.pdf` auto: groups `[[1],[2,3],[4],[5,6],[7]]` with NIDs 007123, 0045678, 8812345, 0033333, 0044444 (R-05).
- AC2: `--pages-per-cert 1` gives 7 groups and page 3 is unmatched; `bundle_titles.pdf` gives 3 groups.
- AC3: `check_accounting` raises `AccountingError` on lost or doubled pages (R-13).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t010 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-010
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

No mutants block: the grouping acceptance tests caught a deliberately disabled continuation rule in planner trials; the code text is not dictated.

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
- Ticket: T-010, status: done
- Files changed:
  - `src/certslice/grouping.py` (created)
  - `src/certslice/pipeline.py` (modified)
  - `tests/unit/test_grouping.py` (created)
- Commands run:
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name t010 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-010`:
    EXIT=0
    RESULT: PASS
  - `.\.venv\Scripts\python.exe scripts/run_check.py --name secrets -- python scripts/scan_secrets.py`:
    EXIT=0
    RESULT: PASS
- Acceptance criteria:
  - AC1: pass (`bundle_mixed.pdf` auto: groups `[[1], [2, 3], [4], [5, 6], [7]]` with NIDs 007123, 0045678, 8812345, 0033333, 0044444)
  - AC2: pass (`--pages-per-cert 1` gives 7 groups and page 3 is unmatched; `bundle_titles.pdf` gives 3 groups)
  - AC3: pass (`check_accounting` raises `AccountingError` on lost or doubled pages)
- Deviations from the plan: none
- Open issues and questions for the planner: none

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
