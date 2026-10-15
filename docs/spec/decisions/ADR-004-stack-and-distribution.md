---
title: "ADR-004 Technology stack and distribution"
id: ADR-004
date: 2026-10-15
tags: [decision, adr]
status: accepted
when: To learn which libraries are used, which were rejected, and how the app reaches staff PCs.
---

# ADR-004 Technology stack and distribution

## Context
Everything must be free and local on Windows. Staff PCs are unknown and probably have no admin rights, so the final product must install without extra setup. The user suggested pypdf or PyMuPDF, pdfplumber, pytesseract, pandas, rapidfuzz and rich. See [brief](../00-brief.md), constraints.

## Options considered
| Option | Pros | Cons |
|--------|------|------|
| A User's list as is | Familiar | Duplicate tools; Tesseract needs a separate installer and PATH |
| B PyMuPDF, RapidOCR, rapidfuzz, openpyxl, argparse*| Few dependencies; pip only; OCR models can be bundled | PyMuPDF is AGPL (fine for internal use); RapidOCR must pass a spike |
| C Docker | Reproducible dev | Does not help a Windows EXE |

## Decision
We choose B for M1: PyMuPDF (slicing, text with coordinates, page rendering for OCR), RapidOCR (OCR, subject to a spike on real scans, with Tesseract or Windows OCR as the fallback), rapidfuzz (matching), openpyxl (reading and writing xlsx), argparse and logging (CLI). pdfplumber, pandas and rich are dropped. Python 3.10 or newer; CPU only. For M2: PyInstaller in one-folder mode plus an Inno Setup installer, a portable folder as the fallback, tested on a clean Windows (Sandbox or VM). The Visual C++ runtime is installed only if missing. Evidence: assumed (library choices from the planner's knowledge, not benchmarked); AGPL note to re-check if the tool is distributed outside the company.

## Consequences
- Good: one PDF library for three jobs; fewer things to bundle.
- Bad or costly: the OCR spike is a gate before any scan-related ticket; the unsigned EXE may trigger SmartScreen.
- Revisit when: the OCR spike fails, the AGPL licence becomes a problem, or IT blocks the installer.

Related: [brief](../00-brief.md), [ADR-001](ADR-001-scope-tier-milestones.md)
