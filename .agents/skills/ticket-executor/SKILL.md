---
name: ticket-executor
description: Executes ONE spec-pack ticket as written: read AGENTS.md and the ticket, edit only listed files, run verification, stop after two failures, write the evidence report. Use when told to run a ticket.
---

# ticket-executor

You execute exactly one ticket from `docs/spec/tickets/`. The ticket is the plan; do not re-plan, and do not use planning mode, `/plan`, `/goal` or `/grill-me`.

1. Read `AGENTS.md`, then the ticket, then only the files it lists under "Context to read first".
2. Edit only the files under "Files to create or modify". Never touch protected paths or "Files NOT to touch". Add no dependencies unless a step says so. If `tests_first: true`, write the tests first, run them, show they fail for the right reason, then implement.
3. Follow the Steps in order with the exact commands. No guessing: if a path, API, package or command named in the ticket does not exist, stop and report.
4. Run every Verification command in this session, through `scripts/run_check.py`. If the output looks truncated or ambiguous, re-run the raw command and report its exit code. A line starting `GUARD:` before `EXIT=` is an order: do not run that check again, write the report.
5. Evidence before claims: never say done, fixed or passing until you have the exit code and the final PASS/FAIL line from this session. If a verification fails, load the `diagnose-failure` skill and follow it (read the full error, classify, one written hypothesis, one change, re-run); after two failed attempts stop and report.
5b. Commit your work in progress on the current branch (`git commit -m "T-xxx wip: <what>"`); never push, never switch or delete branches, never `git reset --hard`, `git clean` or `git stash`, and never run or edit `scripts/collect_evidence.py` (the human runs it).
5c. If the ticket has a `lessons:` list, read only those lines of `docs/spec/lessons.md` before you start. If the ticket names an `asset-operator`, `design-operator` skill, load it; otherwise never touch images, video or Open Design.
6. Write the Execution report at the bottom of the ticket in the mandatory format (status, files changed, commands with exit code and final line, each acceptance criterion with evidence, deviations, open issues, and one line `Proposed lesson: <trigger | rule>` only if something would have saved time). No terse style in the report. Set `status: done` only if verification passed; otherwise `blocked`.
7. The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside the ticket's scope.
