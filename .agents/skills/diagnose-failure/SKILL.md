---
name: diagnose-failure
description: Use when a ticket's verification fails, a command errors or a GUARD line appears, before changing code again. Six-step diagnosis ending in one justified fix or an escalation report. Not for planning.
---

# diagnose-failure

A cheap model fails by guessing, by editing the wrong thing and by looping. This skill forces one measured step at a time. Stay inside the ticket's file list; the ticket is still the plan.

## 1. Freeze
Do not edit anything yet. Copy the exact first error line and the last 20 lines of the output (from the terminal or `.check-logs/<name>.log`). If a line starts with `GUARD:`, obey it now: `STOP`, `NO PROGRESS` or `BUDGET` means do not run that check again; go to step 6.

## 2. Reproduce once
Run the same command once more, raw (no output filter, no extra flags), and note its exit code. It passes this time: report "flaky, passed on rerun" with both outputs and stop; do not "fix" it.

## 3. Classify by the FIRST error line
| Class | Signs | You do |
|---|---|---|
| quota or tool | 429, 503, "quota", "capacity", model or session errors | stop, report, do not retry in a loop |
| environment | command not found, "No module named", wrong Python, PATH, long path, encoding | do not install anything globally; report the exact line |
| MCP or daemon | "daemon not reachable", server not connected, tool missing, `od` exit 3 | stop and report; never work around it with another server |
| permission | denied by rule, needs approval, sandbox | stop and report; never look for a flag that skips approval |
| plan or ticket | a named file, API or package does not exist; steps contradict; a file outside the list is needed | stop and report |
| check defect | `0 passed`, "no tests ran", the command tests something else than the criterion | stop and report; never edit the test or the check |
| code | an assertion or traceback that points into files you wrote or changed | go on to step 4 |

## 4. One hypothesis, written down
Write one line before touching a file: `Cause: <X> because <Y> (evidence: <file:line or message>)`. Look first at the file and line named in the traceback, then at `git diff` (what did I change), then at the inputs and assumptions. Do not read the rest of the repo. Two hypotheses at once means you have not looked closely enough.

## 5. One change, one run
Make exactly one change that tests the hypothesis, then re-run the ticket's verification through `scripts/run_check.py`. Never weaken a test or assertion, add `skip` or `xfail`, catch an exception only to hide it, loosen a threshold, or edit a protected path. The first change failed, so the hypothesis was wrong: write the next one-line hypothesis or stop.

## 6. Stop and report
After the second failed attempt, or a non-code class, or a GUARD line: stop. Write in the ticket's Execution report: ticket id; class from step 3; first error line; last 20 lines; files changed (`git diff --stat`); each hypothesis tried and what the run showed; your best guess; and one line `Proposed lesson: <trigger | rule>` if something would have saved time (the planner decides whether it becomes a lesson). Set `status: blocked`. Do not commit a half-working state as done; a `wip` commit is fine. Never run `scripts/collect_evidence.py` (the human does).
