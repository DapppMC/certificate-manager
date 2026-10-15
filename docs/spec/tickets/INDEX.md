---
title: Index
date: 2026-10-15
tags: [index]
---

# Index

- [T-000a Safety baseline (human)](T-000a-safety-baseline.md): Selalu pertama. Membuat folder proyek, repo privat, mode izin aman, dan menyalin file repo-root sebelum sesi agent mana pun.
- [T-000b Environment bootstrap](T-000b-env-bootstrap.md): Second ticket. Creates the virtual environment, installs pinned packages, generates the synthetic fixtures and proves the check scripts work.
- [T-000c Siapkan sampel asli dan gold set (human)](T-000c-real-samples-and-gold-set.md): Bisa dikerjakan kapan saja setelah T-000a, paralel dengan tiket executor; dibutuhkan oleh T-017 dan T-020.
- [T-001 Walking skeleton: bundle in, named PDFs and plan.json out](T-001-walking-skeleton.md): First feature: the thinnest end-to-end run from one bundle and database to split PDFs and plan.json.
- [T-002 Configuration loader and shared data models](T-002-config-and-models.md): Loads default_config.toml plus a user override and the alias CSV; defines PageText, PageReader, Employee and Candidate.
- [T-003 Name normalisation](T-003-name-normalisation.md): Implements normalize_name exactly as the extraction rules say; titles, case, accents and punctuation stop mattering.
- [T-004 Database loader with header search and validation](T-004-database-loader.md): Replaces the skeleton's database reading with database.py: header search, configurable column names, NID as text, clear errors.
- [T-005 Page reader interface and PDF input checks](T-005-page-reader.md): Adds reader.py: open_pdf with clear errors and LocalPageReader (text layer); pipeline accepts any PageReader.
- [T-006 Candidate scoring and the conservative decision](T-006-candidate-matching.md): Replaces the skeleton match with fuzzy reverse lookup per line and the four-step accept rule.
- [T-007 One-word names and shortened names](T-007-one-word-and-shortened-names.md): Adds subsumption (one-word employee inside a longer name) and partial-name candidates seen in the real master data.
- [T-008 Template and signatory names, holder cue filter](T-008-signatory-filter.md): Adds the bundle-level template-name exclusion and the per-page holder cue filter before the decision.
- [T-009 Certificate number and date extraction](T-009-number-and-dates.md): Creates fields.py with the number rule and the Indonesian/English date rules; fills number, issue and expiry in plan.json.
- [T-010 Certificate boundaries: auto and fixed grouping](T-010-grouping.md): Adds grouping.py: 1/2-page certificates in auto mode, --pages-per-cert 1|2, reasons and the page-accounting check.
- [T-011 Title, issuer, document type and renewal](T-011-title-issuer-type.md): Extends fields.py with template-line detection, title, issuer, document type and renewal number.
- [T-012 Expiry from the validity rule table](T-012-expiry-rules.md): Adds expiry.py: printed expiry wins, then a printed duration, else DJK 5 years, K3/BNSP 3 years, internal training none.
- [T-013 Standard file names, aliases and path length](T-013-file-naming.md): Adds naming.py: {NID}_{Nama}_{Judul}_{Tahun}.pdf, renewal suffix, illegal characters, aliases, path budget, _dup counter.
- [T-014 Manifest and report workbooks](T-014-workbooks.md): Adds writer.py output for manifest_slice.xlsx (10 columns) and unmatched_report.xlsx (unmatched, needs_review).
- [T-015 Pipeline hardening: dry run, accounting exit, re-runs, speed, offline](T-015-pipeline-hardening.md): Finishes the engine: pure dry run, exit 3 with plan.json on accounting errors, timings, logging, entry point checks.
- [T-016 OCR spike script](T-016-ocr-spike-script.md): A small script that OCRs chosen pages of any PDF with RapidOCR and prints text, scores and seconds, for T-017.
- [T-017 Uji OCR pada 3 halaman scan asli (human)](T-017-ocr-spike-on-real-scans.md): Setelah T-016 dan T-000c. Anda menilai apakah RapidOCR cukup baik pada scan asli sebelum OCR dipasang ke mesin (T-018).
- [T-018 OCR fallback in the page reader](T-018-ocr-in-reader.md): Adds ocr.py; LocalPageReader OCRs pages without a text layer and merges OCR into sparse pages over a background picture.
- [T-019 Gold-set evaluation command](T-019-evaluate-command.md): Adds evaluate.py and wires `python -m certslice evaluate`; prints SET, FIELD and WRONG_NID lines and exits 4 on a wrong NID.
- [T-020 Pilot dengan data asli dan cek kill criteria (human)](T-020-real-data-pilot.md): Tiket terakhir M1. Anda menjalankan mesin pada bundel asli, mengukur akurasi dengan gold set, dan memutuskan lanjut ke M2, memperbaiki, atau pivot.

Related: [pack index](../INDEX.md)
