---
title: Index
date: 2026-10-15
tags: [index]
---

# Index

- [ADR-001 Scope, tier M and three milestones](ADR-001-scope-tier-milestones.md): To learn why the project is tier M and why it is split into an engine, a GUI plus installer, and an optional LLM layer.
- [ADR-002 Local-first, template-free extraction](ADR-002-local-first-template-free-extraction.md): To learn how fields are found without knowing the certificate layout, and why LLM is a fallback and not the base.
- [ADR-003 Conservative matching and page accounting](ADR-003-conservative-matching-and-page-accounting.md): To learn the auto-accept rule for NIDs, how signatories are kept out, and the rule that no page may disappear.
- [ADR-004 Technology stack and distribution](ADR-004-stack-and-distribution.md): To learn which libraries are used, which were rejected, and how the app reaches staff PCs.
- [ADR-005 Optional LLM layer and data policy](ADR-005-optional-llm-layer-and-data-policy.md): To learn how and when the free Gemini API is used, where the key lives, and which data may leave the machine.
- [ADR-006 Plan-first pipeline and fixed file contracts](ADR-006-plan-first-pipeline-and-contracts.md): To learn why every run builds plan.json before writing, why the CLI and file formats are frozen, and why resume moved to M3.
- [ADR-007 Planner-owned acceptance tests and synthetic fixtures](ADR-007-planner-owned-acceptance-tests.md): To learn who writes which tests, why the executor may not edit them, and how far the tests were proven.
- [ADR-008 Rules tuned on real samples](ADR-008-rules-tuned-on-real-samples.md): To learn what the first real master data and certificates showed, which rules changed because of it, and what stays conservative.
- [Discovery log](discovery-log.md): To see which questions were asked, what was answered, what was challenged and how it was resolved.

Related: [pack index](../INDEX.md)
