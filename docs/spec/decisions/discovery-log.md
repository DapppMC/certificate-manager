---
title: Discovery log
date: 2026-10-15
tags: [spec, decisions, discovery]
when: To see which questions were asked, what was answered, what was challenged and how it was resolved.
---

# Discovery log

Chat language: Indonesian. Tier chosen: M because the project needs an engine (M1), a GUI plus installer (M2) and an optional LLM layer (M3). It started as S and was raised to M in round 2 (see ADR-001).

## Round 1: orient, problem, vision
| # | Question | Why it matters | Options offered (recommended marked *) | Answer | Evidence |
|---|----------|----------------|-----------------------------------------|--------|----------|
| 1 | Tell me about the last bundle you processed | Baseline for success numbers | open question | Intern, has not processed one; staff receive 6 to 7 PDFs per day, tens of pages each, one person per page | stated (second-hand) |
| 2 | How is metadata extracted | Accuracy without LLM depends on it | A per-issuer profiles*; B generic only; C local LLM | Superseded in round 2 and 4 (see below) | stated |
| 3 | Name matching policy | A wrong NID costs more than a miss | A conservative (>= 92, unique, margin >= 5)*; B >= 85; C interactive | A | stated |
| 4 | Real data and the cloud executor | Executor model runs in the cloud | A synthetic only*; B anonymised; C real in workspace | Real data in `samples/` accepted; AI use of company data is not a problem | stated |
| 5 | Stop criteria | When to stop or pivot | proposal a to d | Delegated to the planner; kill criteria in the brief | stated |
| 6 | Setup: Python, RAM, hours, admin rights, Claude models | Environment and tooling | open | 16 GB RAM, Ryzen 7 7840HS, RTX 4050; Python version and hours per week not answered; staff PCs unknown; picker shows Claude 4.6 but Flash high is preferred | stated / assumed |

## Round 2: scope, users, constraints
| # | Question | Why it matters | Options offered | Answer | Evidence |
|---|----------|----------------|-----------------|--------|----------|
| 7 | Use a cloud LLM if it is free? | Changes the architecture | open | Yes, Gemini Flash-Lite free tier is acceptable; data via AI is acceptable | stated |
| 8 | Final product form | Drives tier | open | A Windows EXE that staff install without installing dependencies | stated |
| 9 | API key for staff (K1) | Quota is per Google project | A each staff makes a key*; B shared key; C no API | None of these: staff will not manage keys. One key in a config file next to the app, set by the owner or IT; LLM default off | stated |
| 10 | Real sample files (K2) | Accuracy cannot be proven without them | A real*; B anonymised; C none yet | Real files allowed in `samples/` | stated |
| 11 | Installer behaviour (K3) | Staff PCs unknown | open | Installer should bundle whatever is needed and install nothing unneeded | stated |

## Round 3: challenge
### Critic block
A beginner with limited coding experience, zero budget and a Windows laptop proposes a local tool that cuts bundled certificate PDFs, reads them and looks employees up in a master Excel. Likeliest failures: (1) layouts differ per issuer and extraction rules break; (2) a wrong NID passes fuzzy matching and nobody notices until it is in the monitoring system; (3) the tool only works on the developer's machine because OCR and runtimes do not install on staff PCs.

### Premortem answer
The user did not choose from options; the planner listed the likeliest causes: wrong NID accepted, LLM limits or model names change, installer blocked by IT, and (added in round 4) layouts so varied that generic extraction fails. Each has a mitigation in the brief.

### Alternatives checked
- PDFsam and PDF24 (assumed, not searched): free page splitters, but they do not read content, match NIDs or name files. Verdict: build, because the value is in matching and naming.
- Cloud LLM for everything: rejected as the base, because free limits are uncertain and sources disagree; kept as optional rescue.
- Per-issuer rule profiles as the base: rejected in round 4 because the user cannot promise templates repeat.

### Devil's advocate
Strongest argument against: without templates, title, number and date extraction may stay weak, and the tool then only saves the NID lookup and the cutting. Evidence that would change the plan: per-field accuracy on the gold set below an agreed level, which triggers M3 or a semi-manual pivot.

### Pushback from the user and outcome
| Point raised by user | New evidence? | Outcome (risk kept / changed / accepted) |
|----------------------|---------------|------------------------------------------|
| Templates may all differ, assume the worst case | yes (domain knowledge, user will research) | Extraction redesigned template-free: reverse lookup from the database, agreement across pages, generic patterns (ADR-002) |
| Staff will not set up API keys | yes | One central key in a config file, LLM default off (ADR-005) |
| Do not drop the free LLM; combine local and LLM | yes | LLM stays as an optional fallback layer; M1 leaves a hook for it, M3 builds it |
| Reverse lookup should mean: read the name, find the NID | no (clarification) | Same flow; reverse lookup is only the technique that finds the name (ADR-003) |

## Real samples (2026-10-15, after Gate 3 was drafted)
The user shared the real master data and two real bundles (K3 training, 8 pages with text layer; BNSP, 12 scanned pages). Findings and the rules they changed are in [ADR-008](ADR-008-rules-tuned-on-real-samples.md); a new ticket T-007 was inserted (later tickets moved up by one number).

## Scope cuts made
- GUI, installer and LLM rescue moved out of M1 into M2 and M3.
- Per-issuer profiles demoted from required to optional.
- Renewal detection from history, upload to the web system and in-app review cut.

## Assumptions created
- A-01 to A-15 in [the brief](../00-brief.md) (mostly assumed; the stated ones are marked).

Related: [brief](../00-brief.md), [ADR-001](ADR-001-scope-tier-milestones.md)
