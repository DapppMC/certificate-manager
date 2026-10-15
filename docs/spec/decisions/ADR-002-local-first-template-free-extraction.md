---
title: "ADR-002 Local-first, template-free extraction"
id: ADR-002
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn how fields are found without knowing the certificate layout, and why LLM is a fallback and not the base.
---

# ADR-002 Local-first, template-free extraction

## Context
The user does not know whether certificate layouts repeat and wants the worst case assumed: every kind of certificate may look new. Free LLM limits are uncertain, so the tool must work with the LLM off. See [brief](../00-brief.md), risks 3 and 4.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A Per-issuer rule profiles as the base | Accurate when layouts repeat | Breaks on every new layout; needs samples per issuer |
| B Cloud LLM as the base | Handles any layout | Limits and model names change; needs a key and internet |
| C Template-free local extraction with an optional LLM layer* | Works offline for any layout; the LLM only helps on doubtful pages | Weaker on title, number and dates; needs per-field confidence |

## Decision
We choose C. The local pipeline does three things that do not depend on layout: (1) reverse lookup, which searches the database names inside the text of each page; (2) agreement across pages of one bundle, where text identical on most pages is template (title, issuer, fixed sentences) and text that differs is holder data; (3) generic patterns for certificate numbers and dates (Indonesian and English months, markers such as "berlaku s.d." and "valid until"). Pages without a text layer are OCR'd locally. Each field gets a confidence; low confidence becomes "needs review". Per-issuer profiles are an optional speed-up, never required. Evidence: stated (user, round 4); library fit assumed.

## Consequences
- Good: no dependence on templates, network or API limits; NID and name are the strongest fields.
- Bad or costly: title and issuer are the weakest fields and may be flagged often; a gold set is needed to measure each field.
- Revisit when: local accuracy stays below 80% after two improvement rounds (turn on M3) or the user finds that most layouts repeat (add profiles).

Related: [brief](../00-brief.md), [ADR-003](ADR-003-conservative-matching-and-page-accounting.md), [ADR-005](ADR-005-optional-llm-layer-and-data-policy.md)
