---
title: "ADR-007 Planner-owned acceptance tests and synthetic fixtures"
id: ADR-007
date: 2026-10-15
tags: [decision, adr, verification]
when: To learn who writes which tests, why the executor may not edit them, and how far the tests were proven.
status: accepted
---

# ADR-007 Planner-owned acceptance tests and synthetic fixtures

## Context
The executor is a cheaper model; executors are known to weaken the tests that grade them. A wrong NID is the costliest failure. Real samples exist but must not be committed and cannot be shared with the planner. See [brief](../00-brief.md) risk 1 and A-13.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A Executor writes all tests | No planner effort | Tests can be bent to fit the code |
| B Planner writes black-box acceptance tests plus a fixture generator; executor adds unit tests per ticket* | Mechanical, protected "done"; rules checked end to end | The planner's tests can contain mistakes (check defects) |
| C Tests on real samples only | Most realistic | Cannot be committed; changes over time; privacy |

## Decision
We choose B. `scripts/make_fixtures.py` creates synthetic PDFs, databases and gold files (invented names only); `tests/acceptance/` checks the CLI, plan.json and workbooks; both are protected. Executors write unit tests in `tests/unit/` (protected after review). Real samples are used only in the human pilot (Flow 4) through `evaluate`. Proof so far: on 2026-10-15 a throwaway planner implementation (never shipped) passed all acceptance tests on Linux with Python 3.13.16 and the pinned packages (60 at first, 81 after the real-sample patterns of ADR-008 were added), and 11 of 14 deliberate rule breaks were caught (margin, homonym, template names, grouping, expiry years, forced acceptance, subsumption, partial names, template counting, sparse-page OCR, printed duration); the three that survived (single-token exact match, expiry-before-issue guard, a defensive title label rule) are left to unit tests. Windows runs are unproven until T-000b.

## Consequences
- Good: the executor cannot declare success without the planner's checks passing.
- Bad or costly: a planner test bug blocks a ticket; that is triaged as CHECK DEFECT, fixed by the planner, and does not count on the escalation ladder.
- Revisit when: the real pilot shows rules that the fixtures do not cover (add fixtures, never loosen thresholds).

Related: [verification](../04-verification.md), [ADR-006](ADR-006-plan-first-pipeline-and-contracts.md)
