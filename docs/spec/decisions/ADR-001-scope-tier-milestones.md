---
title: "ADR-001 Scope, tier M and three milestones"
id: ADR-001
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn why the project is tier M and why it is split into an engine, a GUI plus installer, and an optional LLM layer.
---

# ADR-001 Scope, tier M and three milestones

## Context
The idea began as tier S (a CLI script). Round 2 added a requirement that staff install a Windows program without installing dependencies, which needs a GUI and an installer, and an optional free LLM layer. See [brief](../00-brief.md), constraints and MVP scope.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A Tier S, CLI only | Smallest pack | Cannot meet the "easy install for staff" goal |
| B Tier M, three milestones* | Engine proven before GUI and installer; LLM stays optional | More documents; M2 and M3 stay outlines |
| C Tier L | Room for everything | Milestone 1 is far below 25 feature tickets; planning would drift |

## Decision
We choose B. M1 is the local engine (CLI, planned in full detail), M2 is a simple GUI plus installer (outline, planned when M1 passes the gold set), M3 is the optional LLM rescue (outline). The tier follows the size of M1. Evidence: stated (user, round 2 and 3).

## Consequences
- Good: the riskiest part (accuracy) is tested first, on the CLI, before any GUI work.
- Bad or costly: when M2 starts the planner must reopen Gate 2 for the GUI (`ui` becomes true) and add a design route.
- Revisit when: M1 needs more than about 25 feature tickets (then treat it as L), or the installer is blocked by IT.

Related: [brief](../00-brief.md), [ADR-004](ADR-004-stack-and-distribution.md)
