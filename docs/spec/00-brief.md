---
title: Idea Brief
date: 2026-10-15
tags: [spec, brief]
when: Start here to understand what is being built, for whom, and what is out of scope.
---

# Idea Brief: Certificate Slicer

Status: approved by the user on 2026-10-15 (playback confirmed; Gate 0 re-run the same day with all skills loaded, adding Python 3.13.13, the database header detection and the move of resume to M3; two clarifications: the NID flow is "read the name on the certificate, look it up in database.xlsx, take the NID"; the free Gemini Flash-Lite API stays as a combined fallback layer, not as the base). Language: chat Indonesian, artifacts English.

## Problem
The TDC (Technical Development and Certification) staff at PLN receive about 6 to 7 bundled PDFs per day (reported by staff, not measured). Each bundle has tens of pages, and each page is the same kind of certificate for a different employee. Today a person cuts the PDF per employee, looks up the NID in the master Excel and renames every file by hand, which takes hours and causes human errors. If nothing is built, the manual work and its error rate stay.

## Users and jobs to be done
- TDC staff (non-technical, Windows PCs not controlled by the project owner): "Give me one bundle PDF and the master Excel, and get back correctly named per-person PDFs plus a sheet I can import into the monitoring website."
- Project owner (intern, learning vibe coding): "Prove the work can be automated with zero cost and little maintenance."

## Success metrics (measurable)
- Zero wrong NIDs accepted automatically on the gold set (30 to 50 pages with known answers). This is a release gate, not a target.
- On PDFs with a text layer, local mode alone gets at least 80% of pages fully correct (name matched to the right NID). Scanned PDFs are measured and reported separately.
- One 30-page bundle finishes in under 5 minutes of tool time for a text-layer PDF (scan speed is measured and reported).
- Every input page ends in exactly one output PDF or exactly one report row (100%, checked by an automatic test).
- Per-field accuracy (title, certificate number, issue date, expiry date) is measured and reported; low-confidence fields are flagged "needs review" instead of guessed.
- First real trial (planner sandbox, 2026-10-15, no gold file yet): 11 of 14 certificates accepted automatically, 3 reported with reasons, no wrong NID seen (ADR-008). The 80% target is still measured in T-020.

## MVP scope (MoSCoW)
Milestone 1 (M1, the local engine, planned in detail), Milestone 2 (M2, GUI and installer, outline only), Milestone 3 (M3, optional LLM rescue, outline only).
- Must (M1): read PDF text per page; OCR for pages without a text layer; find the employee on each page by looking the database names up in the page text (no template needed); decide where each certificate starts and ends (1 or 2 pages); extract title, issuer, certificate number, issue date and expiry date with generic patterns plus agreement across pages; conservative matching to database.xlsx; write named PDFs, manifest_slice.xlsx (10 columns) and unmatched_report.xlsx; dry-run plan file; page-accounting invariant.
- Should (M1): editable alias table for titles; per-field confidence and a "needs review" sheet; no-overwrite behaviour on re-runs; a replaceable page-reader interface (the hook for M3).
- Must (M1, support): a gold-set evaluation command that proves the success metrics.
- Could (M1): optional per-issuer profile files as a speed-up (never required).
- Must (M2): simple GUI (pick PDF, pick database.xlsx, Start, open result folder) and a Windows installer that bundles everything.
- Should (M3): LLM rescue layer for doubtful pages, off by default, with resume of interrupted runs.
- Won't (this version): see non-goals.

## Non-goals
- Local LLMs (too heavy, hardware of staff PCs unknown).
- Docker in the final product (does not help a Windows EXE).
- Automatic upload to the web monitoring system.
- Detecting renewals from history (only from keywords printed on the certificate).
- Interactive review inside the app (the admin edits the Excel report).
- Using the GPU (the design is CPU-only).

## Constraints
- Budget: 100% free software and APIs (Rp0). No credit card, no paid API, no billing-enabled Google project.
- User skill and time: beginner to some experience, learning vibe coding; hours per week not known by the user, assumed about 5 to 8 (used only for the rough schedule in READY.md).
- Hardware and OS: developer laptop Windows 11, 16 GB RAM, Ryzen 7 7840HS, RTX 4050 6 GB (unused); PowerShell 5.1 (no `&&`). Staff PCs are unknown, assumed Windows without admin rights.
- Platforms and hosting: local desktop only, no server.
- Legal, privacy, data: employee data (NID, email). Sending certificate pages to the Google free tier is accepted by the user (`accepted risk (stated)`); database.xlsx itself never leaves the machine. Real samples may be placed in `samples/` (gitignored). Free-tier use at the company must be confirmed with the supervisor before M3 is switched on in production.
- Scale expectation: about 60 to 350 pages per day, one machine at a time.

## Integrations needed
- None for development (executor runs with no MCP servers). The optional Gemini API in M3 is a runtime feature, not a development integration.

## Executor profile
Recorded in [executor-profile.md](executor-profile.md): Antigravity with Gemini 3.8 Flash, thinking level high, no MCP servers, no token tools. Claude 4.6 is visible in the picker but not used.

## UI direction (UI projects only)
Not a UI project in M1 (`ui: false`). M2 adds a simple GUI; when M2 starts, the planner reopens Gate 2 for the GUI and flips `ui` to true.

## Top risks and mitigations
| # | Risk | Impact (1-3) | Probability (1-3) | Mitigation | Evidence |
|---|------|--------------|-------------------|------------|----------|
| 1 | A wrong NID passes automatic matching and reaches the monitoring system | 3 | 2 | Conservative policy (score >= 92, unique candidate, margin >= 5 over the second); homonyms go to the report with unit_kerja shown; gold set must show zero wrong NIDs | assumed |
| 2 | Reverse lookup picks a signatory, assessor or trainer who is also in the database instead of the certificate holder | 3 | 2 | Names that recur across many pages of the same bundle are treated as template or signatory and excluded; prefer names near holder cues such as "diberikan kepada" or "awarded to"; page-accounting and report | assumed |
| 3 | Certificate layouts differ for every certificate, so generic extraction of title, number and date is weak | 2 | 3 | Design assumes the worst case (no templates); cross-page consensus; low confidence goes to "needs review"; NID and name stay strongest because they come from the database | stated |
| 4 | OCR quality on scans is too low or RapidOCR fails to bundle | 2 | 2 | Spike on real scans before committing; scans are reported separately and never auto-accepted below the score gate; LLM rescue in M3 | assumed |
| 5 | Gemini free-tier limits or model names change | 1 | 3 | LLM is optional and default off; model name in config; per-page cache and checkpoint | verified (2026-10-14, sources disagree) |
| 6 | Installer blocked by SmartScreen or company antivirus | 2 | 2 | Portable folder fallback; test on a clean Windows; code signing is paid so not used | assumed |
| 7 | Real names differ from the master data (shortened, one-word names, 78 duplicates, signatories close to employees) | 2 | 3 | Subsumption, partial-name reporting, homonym rule, template counting at 80 (ADR-008) | verified on 2 real bundles |

## Kill criteria
- After 6 tickets there is no sample PDF that went all the way to output: stop and re-plan.
- Any wrong NID accepted automatically on the gold set: do not release; tighten thresholds first.
- Local mode below 80% on text-layer PDFs after 2 improvement rounds: enable M3 (LLM rescue); if still below, pivot to a semi-manual tool.
- Installer blocked by IT: ship the portable folder version.

## Assumptions (all accepted by the user in playback)
- A-01 (stated): volume is 6 to 7 PDFs per day, tens of pages each, one person per page; the manual-time baseline is not measured.
- A-02 (assumed): worst case for layout: every certificate may look different, so no per-issuer template is required; issuer profiles are an optional later speed-up.
- A-03 (assumed): a page is "correct" when the NID and name are right; title, number and dates are reported per field and flagged when unsure.
- A-04 (stated): Python 3.13.13 on the developer laptop (answered 2026-10-15); T-000a and T-000b re-check it. Windows wheels for every pinned package exist for Python 3.13 (verified 2026-10-15).
- A-05 (verified on a synthetic scan, assumed on real scans): RapidOCR 3.10.0 installs with pip, ships its models inside the package (no download at run time) and read a synthetic scanned certificate correctly at about 3 s per page (planner sandbox, 2026-10-15). It must still pass a spike on 3 real scanned pages (T-017), otherwise Tesseract or Windows OCR is evaluated.
- A-06 (stated): sending certificate pages to Google AI Studio free tier is accepted; using it at work needs the supervisor's confirmation before M3 goes live.
- A-07 (assumed): expiry date uses the printed date first, then a rule table (DJK 5 years, K3 and BNSP 3 years, internal training none); non-expiring documents leave the expiry cell empty.
- A-08 (assumed): the monitoring website accepts dates as DD/MM/YYYY text; the format is a config value and is confirmed at Gate 1.
- A-09 (assumed): NIDs are read and written as text so leading zeros survive.
- A-10 (assumed): certificate titles are normalised through an editable alias table (CSV).
- A-11 (stated and verified on the real file 2026-10-15): the master data is the sheet "Data Master" of a large workbook (header row 1: `NID`, `NAMA`, `UNITKET`, `JABATAN`, no email column). The tool picks that sheet by name (configurable), finds the header row within the first 10 rows, maps columns by configurable header names, and reads only NID, name, unit, jabatan (and email when present); personal columns are never read.
- A-12 (assumed): `_PERPANJANGAN_{ke}` is added only when a renewal keyword or number is printed on the certificate.
- A-13 (stated): real PDFs and the real database may sit in `samples/`, which is gitignored; the executor may see them (accepted), but unit tests still use synthetic fixtures.
- A-14 (accepted 2026-10-15; assumed): an employee can appear several times in one bundle; file names stay unique with a `_dup2` counter, and a re-run writes to a new timestamped folder and never overwrites. Resuming an interrupted run is moved to M3 (it matters when the LLM quota runs out; a local run takes minutes).
- A-15 (stated): executor is Gemini 3.8 Flash with thinking high (different from the profile default of medium); `claude_models: 4.6` is not used and is removed from Antigravity on 2026-11-02.
- A-16 (accepted 2026-10-15; assumed): a bundle where one person holds every certificate is rare; such a name looks like a signatory and those pages go to the report (safe failure, see ADR-003).
- A-17 (accepted 2026-10-15; assumed, from the real samples): a shortened name on a certificate (missing or initialled parts) is never accepted automatically; it is reported as `partial name` with the likely employee as candidate 1 (2 of 14 real certificates). Auto-accepting it can be added later as a switch (ADR-008).
- A-18 (accepted 2026-10-15; assumed, from the real samples): pages whose text layer is short while a background picture covers the page are also read by OCR (about 3.5 s per page); the employer name `PT PLN NUSANTARA POWER SERVICES` is never taken as the issuer.

## Key decisions
- Scope, tier M and the three milestones -> [ADR-001](decisions/ADR-001-scope-tier-milestones.md)
- Template-free, local-first extraction -> [ADR-002](decisions/ADR-002-local-first-template-free-extraction.md)
- Conservative matching and page accounting -> [ADR-003](decisions/ADR-003-conservative-matching-and-page-accounting.md)
- Technology stack and distribution -> [ADR-004](decisions/ADR-004-stack-and-distribution.md)
- Rules tuned on real samples -> [ADR-008](decisions/ADR-008-rules-tuned-on-real-samples.md)
- Optional LLM layer and data policy -> [ADR-005](decisions/ADR-005-optional-llm-layer-and-data-policy.md)

## Glossary
- NID: employee identification number in database.xlsx, always handled as text.
- Bundle: one PDF containing many certificates.
- Gold set: 30 to 50 pages with hand-checked correct answers, used to prove accuracy.
- Reverse lookup: finding the employee by searching the database names inside the page text.
- Needs review: a field or page the tool is not confident about; it is written to the report instead of guessed.

## Open questions
None.

Related: [requirements](01-requirements.md), [discovery log](decisions/discovery-log.md)
