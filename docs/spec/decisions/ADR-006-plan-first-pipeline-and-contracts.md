---
title: "ADR-006 Plan-first pipeline and fixed file contracts"
id: ADR-006
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn why every run builds plan.json before writing, why the CLI and file formats are frozen, and why resume moved to M3.
---

# ADR-006 Plan-first pipeline and fixed file contracts

## Context
A weaker executor builds the engine ticket by ticket, so each ticket needs a stable target; the GUI (M2) and the LLM layer (M3) will reuse the same engine. Users must be able to inspect decisions, and no page may disappear. See [requirements](../01-requirements.md) R-09, R-13, R-17.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A Stream: decide and write page by page | Simple | No accounting before writing; hard to inspect; partial outputs on errors |
| B Plan first (plan.json), write after the accounting check, frozen CLI, API and workbook formats* | Inspectable; dry-run is free; tests can target files; M2 and M3 plug in | More up-front contract text |

## Decision
We choose B. The contract lives in [modules/interface.md](../modules/interface.md) and the rules in [modules/extraction-rules.md](../modules/extraction-rules.md); acceptance tests encode both (ADR-007). Input errors are detected before the run folder exists. Resume of an interrupted run is moved to M3, where the LLM quota can interrupt a run; a local run takes minutes. Evidence: assumed (design reasoning), contracts proven on synthetic fixtures by a throwaway planner implementation on 2026-10-15.

## Consequences
- Good: every ticket has an observable target; evaluate reads plan.json only.
- Bad or costly: any change to a header name, reason string or exit code needs an ADR and a test update by the planner.
- Revisit when: the monitoring website needs a different manifest format, or M2/M3 need a field plan.json lacks.

Related: [design](../02-design.md), [ADR-007](ADR-007-planner-owned-acceptance-tests.md)
