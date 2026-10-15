---
title: "ADR-005 Optional LLM layer and data policy"
id: ADR-005
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn how and when the free Gemini API is used, where the key lives, and which data may leave the machine.
---

# ADR-005 Optional LLM layer and data policy

## Context
The user accepts free LLM use and data passing through AI, but wants the tool to work without it, wants a combination for safety, and does not want staff to manage API keys. Free limits are uncertain (sources disagree, checked 2026-10-14; re-check in the AI Studio dashboard). See [brief](../00-brief.md), risk 5.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A Each staff member has a key | Quota per person | Staff will not do it |
| B One shared key in a config file next to the app, default off* | No staff setup; tool still works with no key | Shared quota; key must stay out of the repo |
| C No LLM at all | Simplest | No rescue for hard pages |

## Decision
We choose B. M1 only leaves a clean hook (a "page reader" interface with a local implementation). M3 adds a Gemini Flash-Lite implementation that is off by default and is used only for pages the local pipeline marked doubtful. The key sits in a config file beside the app (set by the owner or IT), never in code, the repo or the installer. The project uses a Google AI Studio project without billing, so a leaked key can only exhaust quota. The model name is a config value, results are cached per page, and runs can resume the next day if the quota runs out. Only certificate pages (never database.xlsx) are sent. LLM output is accepted only if the name appears in the page text (for text-layer PDFs) and the date format validates; NIDs still come only from the local match. Real samples in `samples/` are gitignored; the executor may see them (accepted by the user) but unit tests use synthetic fixtures. Using the free tier at work must be confirmed with the supervisor before M3 goes live. Evidence: stated (user); limits verified 2026-10-14 with conflicting sources.

## Consequences
- Good: offline and free by default; the LLM can only help, never decide an NID.
- Bad or costly: shared quota; Google may use free-tier content for product improvement (accepted risk, stated).
- Revisit when: Google changes the free tier, the supervisor does not allow it, or the quota is exhausted regularly.

Related: [brief](../00-brief.md), [ADR-002](ADR-002-local-first-template-free-extraction.md)
