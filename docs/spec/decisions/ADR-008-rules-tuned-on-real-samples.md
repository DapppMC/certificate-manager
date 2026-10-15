---
title: "ADR-008 Rules tuned on real samples"
id: ADR-008
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn what the first real master data and certificates showed, which rules changed because of it, and what stays conservative.
---

# ADR-008 Rules tuned on real samples

## Context
On 2026-10-15 the user shared the real master data (workbook with 90+ sheets; sheet "Data Master" with 5,088 employees, header on row 1: `NID`, `NAMA`, `UNITKET`, `JABATAN` and personal columns that the tool must ignore) and two real bundles: a K3 training bundle (8 pages, 1 page per certificate, text layer holds only name, number and signatory; every label is in a background picture) and a scanned BNSP bundle (12 pages, 6 certificates of 2 pages, the holder's name printed on both pages). A throwaway planner implementation of the M1 rules was run on them in the planner sandbox. The files were not copied into the pack; only invented look-alike fixtures were added.

## Findings and options
Names and numbers below are invented stand-ins with the same shape as the real ones; no real person's data is in this pack.

| Finding (real data) | Option chosen | Rejected |
|---|---|---|
| "Data Master" is not the first sheet; another sheet also has NID and name columns | `[database] preferred_sheets = ["data master"]`, else the first sheet | always the first sheet (would read the wrong list) |
| 610 one-word names ("SUTRISNA") tie with "ILHAM SUTRISNA" at 100 | subsumption: drop a name contained in a longer confident name on the same line | raise `min_tokens_fuzzy` (would never match real one-word employees) |
| 78 exact duplicate names | keep the homonym rule | none |
| Certificates shorten names ("HANA PERTIWI" for a 3-word employee, "... NIRMALA W") | partial candidate with score 90: reported as `partial name` with the likely employee as candidate 1, never auto-accepted | auto-accept unique partials (more automation, but a different person with the same first two names would get the NID) |
| A signatory ("Muhammad Al Fikri") scores about 91 against several employees on every other page | template counting uses `list_score` (80) instead of 92 | keep 92 (near-misses stay in the margin race) |
| Labels in a background picture, short text layer | OCR a page whose text layer is under 400 characters when images cover at least half of it; merge text layer lines | OCR only image-only pages (K3 title, number and dates would stay empty) |
| "Held on October 05th - 12nd, 2026"; "berlaku untuk 3 (tiga) Tahun"; "Telah Kompeten pada :" followed by an English line; "Lembaga Sertifikasi Profesi" split over two lines; the employer "PT PLN NUSANTARA POWER SERVICES" printed on certificates | ordinal and day-range dates (last day), printed duration before the rule table, title from the first suitable following line, issuer join-next and an employer exclusion list | leave these fields to manual review |

## Decision
Adopt the chosen options above in `default_config.toml`, [extraction rules](../modules/extraction-rules.md), requirements R-01 to R-08 and the affected tickets. Result of the planner trial (sandbox, 2026-10-15; not a gold-set measurement): K3 6 of 8 accepted, BNSP 5 of 6 accepted, every accepted name scored 100, all fields filled on accepted certificates; reported: 2 `partial name` (right employee as candidate 1), 1 `no confident holder` (the name is not in the master data). Time: 8 text pages with OCR merge about 35 s, 12 scanned pages about 56 s (about 3.5 s per OCR page). Evidence: verified in the sandbox, not on Windows; correctness of the NIDs was not checked against a gold file.

## Consequences
- Good: the rules now match two real issuers; 20 new acceptance assertions guard them (80 tests in total).
- Bad or costly: 2 of 14 real certificates still need a human click because of shortened names; an employee whose name is close to a signatory's may be excluded in that bundle (safe: reported, never wrong).
- Revisit when: the gold-set pilot (T-020) shows many `partial name` rows; then the user may decide to accept unique partials with a needs-review flag (a config switch and one ticket).

Related: [brief](../00-brief.md), [ADR-003](ADR-003-conservative-matching-and-page-accounting.md), [extraction rules](../modules/extraction-rules.md)
