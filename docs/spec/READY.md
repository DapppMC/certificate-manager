---
title: Readiness review
date: 2026-10-15
tags: [spec, readiness]
status: ready
when: Before starting the executor; shows the Definition of Ready results, the plan premortem and the size estimate.
---

# Readiness review (Definition of Ready)

Result of `python scripts/check_pack.py docs/spec` on 2026-10-15: RESULT: PASS (warnings=11) (warnings are the tier-M line budget, CLI usage placeholders in code blocks, and tickets that state why they carry no mutants)

## Checklist
- [x] Every R-xx has a verification entry in 04-verification.md (a command or a named manual check).
- [x] Every ticket references at least one R-xx and has an exact verification command with an expected result.
- [x] Every ticket is executor-proof: exact files, steps without implicit assumptions, stop conditions, report format.
- [x] No open questions remain in the brief (section says None).
- [x] All assumptions are listed in the brief and were accepted by the user (A-14, A-16, A-17, A-18 confirmed on 2026-10-15).
- [x] Every top risk has a mitigation; kill criteria are written.
- [x] Every milestone-1 integration has a setup ticket and a smoke-test ticket (M1 has no integrations; nothing is marked `Milestone: later`).
- [x] Human-only steps are separate tickets with owner: human and a checklist (T-000a, T-000c, T-017, T-020).
- [x] Secrets handling is defined (no secrets in M1; `.env`, `samples/`, `out/` gitignored; agent denied `read_file(samples/)`).
- [x] Size and cost estimate is given below (tickets, effort mix) and labelled unmeasured.
- [x] The first three tickets can start without any further planner input.
- [x] The plan premortem was run and its findings are fixed or accepted (see below).
- [x] Executor profile recorded; tickets satisfy the token-tools adaptation rules (wrapper lines, raw re-run rule, mandatory report).
- [x] Quality mode, premium budget and (UI) design route are recorded in INDEX and agreed by the user (economy, budget 0: the user chose Gemini 3.8 Flash only; no UI in M1).
- [x] `claude_models` (5.5, 4.6 or none) and `process` (full or lite) are recorded in INDEX; the Model ladder line in `repo-root/AGENTS.md` matches `claude_models`; with 4.6 the removal date (2026-11-02) is written down.
- [x] The user was told the per-ticket routine (`collect_evidence.py --start`, session, `--bundle`, own rerun of the verification, `--accept` or `--abandon`) in T-000a step 10 and the delivery message; process is full (no batches).
- [x] Every `risk: high` or `tests_first: true` feature ticket has a `mutants` block, or the reason it has none is written next to it (T-003, T-006, T-007, T-008 have blocks, all 7 mutants KILLED in a planner trial; T-004, T-009, T-010, T-012, T-013 state why not).
- [x] Images or video: no assets.

Assumptions added after the playback (A-14, A-16, A-17, A-18) were confirmed by the user on 2026-10-15; A-11 is stated by the user and verified on the real file.

## Plan premortem (what would make the executor fail or the project miss its goal?)
| # | Failure mode | Likelihood | Mitigation in this pack |
|---|--------------|------------|--------------------------|
| 1 | Windows differs from the Linux sandbox (PowerShell quoting of `python -c`, `.venv` paths, long paths, OCR wheels) | med | T-000b proves the toolchain first; commands use `.\.venv\Scripts\python.exe`; `core.longpaths`; wheels verified for win_amd64 cp313 |
| 2 | A planner acceptance test is wrong and blocks a ticket | low | all 81 tests passed on a throwaway reference; the staged run was rehearsed; triage class CHECK DEFECT, planner fixes, does not count on the ladder |
| 3 | Flash struggles with the larger logic tickets (T-006, T-009, T-010: score 4, effort high) | med | exact rules in extraction-rules.md, dictated snippets, tests first; the real-name rules were split into their own ticket (T-007); escalation ladder (economy: failure 2 means the planner rewrites or the user picks a mode) |
| 4 | Executor weakens or skips a protected test | low | protected list in AGENTS.md, `collect_evidence.py` bundle, optional reviewer diff check, mutants for the riskiest logic |
| 5 | Other issuers' layouts differ from the two real bundles | high | rules are layout-free; per-field confidence and needs-review; T-000c collects more issuers; T-020 pilot with a gold set; rule tuning only through the planner |
| 6 | Shortened names make many certificates manual | med | reported with the likely employee as candidate 1 (2 of 14 in the trial); an auto-accept switch is documented in ADR-008 if the pilot shows it is needed |
| 7 | OCR on other scans is poorer than on the BNSP sample | med | T-017 human check on Windows before T-018; fallback ADR (Tesseract or Windows OCR) or M3 |
| 8 | Speed with the 5,088-row master data | low | measured in the sandbox: about 1 s per text page, about 3.5 s per OCR page; `rapidfuzz.process.cdist` route documented |
| 9 | Real employee data leaks into git or the agent context | low | `samples/` gitignored, agent deny `read_file(samples/)`, pilot run by the human only, only mapped columns are read, tests use invented names |
| 10 | The user skips the checkpoint routine, so a bad session cannot be undone | med | T-000a step 10 rehearses it; repeated in the delivery message |
| 11 | Pack is over the tier-M line budget (about 3,600 lines vs 1,500) | accepted | the extra lines are exact contracts and rules for a weaker executor; no extra ceremony documents were added |
| 12 | Claude 4.6 disappears on 2026-11-02 | none for this plan | economy mode, no premium tickets; only the AGENTS.md ladder line would change |

## Estimate (unmeasured: no A/B run done; treat as a rough guide)
- Tickets: 23 (tier M): 19 executor tickets (18 feature tickets in milestone 1 plus T-000b) and 4 human tickets. Effort mix of executor tickets: low 5, medium 11, high 3.
- Human-only steps: 4 tickets (T-000a, T-000c, T-017, T-020).
- Expected sessions: about 19 to 25 Antigravity sessions (one ticket per fresh session, plus rewrites after failures).
- Calendar: at the assumed 5 to 8 hours per week, roughly 3 to 5 weeks for M1 (unmeasured).
- Cost: Rp0 in money; Antigravity quota use unmeasured. Measure with the A/B protocol in EFFICIENCY.md before trusting any figure.

## First three tickets to run
1. T-000a Safety baseline (human, you).
2. T-000b Environment bootstrap (executor).
3. T-001 Walking skeleton (executor). T-000c (your real samples and gold set) can run in parallel at any time.

## Known limits of this review
- Everything was proven on Linux (Python 3.13.16) in the planner sandbox, not on Windows; T-000b and T-001 are the first Windows proof.
- The extraction rules were proven on synthetic fixtures and on two real bundles from two issuers (11 of 14 accepted, no wrong NID seen, but no gold file yet); real accuracy across issuers is unknown until T-020.
- OCR was proven on one real scanned bundle (about 3.5 s per page on the sandbox CPU), not on Windows.
- Antigravity menu names, deny-list syntax and skills paths were last checked on 2026-10-05 by the kit, not today.
- Free Gemini limits (M3) were not re-checked; M3 is not planned yet.

Related: [index](INDEX.md), [verification](04-verification.md), [brief](00-brief.md)
