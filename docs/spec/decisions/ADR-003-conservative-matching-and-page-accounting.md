---
title: "ADR-003 Conservative matching and page accounting"
id: ADR-003
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn the auto-accept rule for NIDs, how signatories are kept out, and the rule that no page may disappear.
---

# ADR-003 Conservative matching and page accounting

## Context
A wrong NID is worse than a miss: it reaches the monitoring system unnoticed. The name on a certificate may carry titles ("Ir. Budi Santoso, S.T., M.M.") or typos, and other people (signatories, assessors, trainers) may also be in the database. See [brief](../00-brief.md), risks 1 and 2.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A Auto-accept at score >= 92, unique candidate, margin >= 5* | Few wrong NIDs | More pages go to the report |
| B Auto-accept at >= 85 | Less manual work | More wrong NIDs |
| C Interactive confirmation | Safest | Slow, cannot run unattended |

## Decision
We choose A (the user chose it in round 1). Names are normalised first (remove titles, casefold, strip punctuation), then exact match, then token-sort ratio from rapidfuzz; partial ratio is not used because "Budi" would match "Budi Santoso Wijaya". A name that appears on most pages of the bundle is treated as template or signatory and excluded; a name near a holder cue ("diberikan kepada", "awarded to", "nama") is preferred. Pages with no confident holder, or with two equally good candidates (homonyms), go to `unmatched_report.xlsx` with the top three candidates and their unit_kerja. Invariant: every input page ends in exactly one output PDF or exactly one report row; an automatic test checks this. Evidence: stated (user), thresholds assumed.

## Consequences
- Good: a wrong NID needs several independent rules to fail at once; nothing disappears silently.
- Bad or costly: the share of pages in the report may be higher than the user hopes; thresholds are tuned on the gold set, never loosened to hit a percentage.
- Revisit when: the gold set shows a wrong NID (tighten) or the report is above about 30% of text-layer pages (investigate the cues before loosening).

Related: [brief](../00-brief.md), [ADR-002](ADR-002-local-first-template-free-extraction.md)
