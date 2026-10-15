---
title: Requirements
date: 2026-10-15
tags: [spec, requirements]
when: Before design, tests or tickets; every R-xx is defined here and nowhere else.
---

# Requirements

Derived only from the approved [brief](00-brief.md). Style: EARS ("WHEN a trigger happens THE SYSTEM SHALL do something"; "IF an unwanted condition holds THEN THE SYSTEM SHALL respond safely"). Each requirement has an ID, a priority and an acceptance check that someone else can run. These requirements cover milestone 1 (the local engine). Milestones 2 and 3 are outlined at the end and get their own requirements when their gate opens.

Stable command interface used in the acceptance checks (details in [modules/interface.md](modules/interface.md)): `python -m certslice run --pdf <bundle.pdf> --db <database.xlsx> --out <folder> [--dry-run] [--pages-per-cert 1|2|auto] [--no-ocr] [--config <file.toml>]` and `python -m certslice evaluate --run <run folder> --gold <gold.csv>`.

## R-00: Safe and reproducible environment
Statement: THE SYSTEM SHALL be buildable and testable from a clean checkout using only the documented setup steps, with no secrets committed.
Priority: must
Acceptance: Given a clean clone, when the setup tickets are done, then the documented check commands run and the secret scan finds nothing.
Source: baseline (every project)

## Functional requirements

### R-01: Load the database and read every page
Statement: WHEN the user runs the tool with a bundle PDF and database.xlsx THE SYSTEM SHALL read the sheet named "Data Master" (configurable list, else the first sheet), find the header row within the first 10 rows, map the columns NID and name (required) and email, unit and jabatan (optional) through configurable header names, keep NIDs as text, and SHALL read every page of the PDF, recording for each page whether it has a usable text layer.
Priority: must
Acceptance: Given the synthetic database `db_basic.xlsx` (a decoy first sheet, then sheet "Data Master" with a title row, headers on row 3 written as `NID`, `Nama Lengkap` and `UNITKET`, NID "007123") and the 3-page `bundle_simple.pdf`, when the tool runs with `--dry-run`, then plan.json lists 3 pages with source "text" and the accepted NID reads "007123" (leading zeros kept).
Source: brief, Must (read PDF text per page), A-09, A-11

### R-02: Local OCR for pages without text
Statement: WHEN a page has no usable text layer, or only a short text layer over a page-sized background picture, THE SYSTEM SHALL read it with the bundled local OCR (merging the text layer for the second case); IF OCR is unavailable or fails THEN THE SYSTEM SHALL use what the text layer has, or mark the page "no text" and send it to the report instead of guessing.
Priority: must
Acceptance: Given `bundle_scan.pdf` (image-only pages), when the tool runs, then the pages have source `ocr` and are matched, and with OCR disabled they are reported with reason "no text"; given `bundle_k3_like.pdf` (labels only in a background picture), the pages have source `mixed` and title, number and dates are filled.
Source: brief, Must (OCR), risk 4

### R-03: Find the certificate holder by reverse lookup
Statement: WHEN a page has text THE SYSTEM SHALL search the normalised database names in the page text (titles removed, case and punctuation ignored, light typos tolerated) and SHALL propose candidates with a score, without relying on any layout or template; a one-word name contained in a longer confident name on the same line SHALL be dropped, and a shortened name on the certificate (omitted or initialled parts) SHALL be listed as a partial candidate that is never accepted automatically.
Priority: must
Acceptance: Given a page containing "Ir. BUDI SANTOSO, S.T., M.M." and a database row "Budi Santoso", when the tool runs, then that row is the top candidate with a score of at least 92; in `bundle_bnsp_like.pdf` "DEWI LESTARI" is accepted although a one-word employee "Lestari" exists, and "HANA PERTIWI" is reported with reason "partial name" and "Hana Pertiwi Kusumawardani" as candidate 1.
Source: brief, Must (reverse lookup), A-02

### R-04: Keep signatories and template names out
Statement: THE SYSTEM SHALL treat a database name that is a candidate (score at least 80) on at least half of the pages (and on at least 3 pages) of the same bundle as template or signatory text and SHALL NOT use it as a holder anywhere in that bundle; WHEN a page has a holder cue ("diberikan kepada", "awarded to", "nama") and a confident candidate within the 3 lines after it THE SYSTEM SHALL drop the candidates found outside those lines before deciding.
Priority: must
Acceptance: Given a 10-page synthetic bundle where every page contains the director's name (also in the database) and a different holder name, when the tool runs, then no page is matched to the director.
Source: brief, risk 2

### R-05: Decide where each certificate starts and ends
Statement: WHEN the bundle mixes 1-page and 2-page certificates THE SYSTEM SHALL group pages by default in `auto` mode (a page with a confident new holder starts a certificate; a following page with no new holder and a continuation sign such as "2/2" or the same certificate number continues it), SHALL honour `--pages-per-cert 1` or `2` when given, and SHALL report ambiguous pages with their page numbers.
Priority: must
Acceptance: Given a synthetic bundle of three 1-page and two 2-page certificates, when the tool runs in `auto` mode, then it produces 5 groups with the right page ranges.
Source: brief, Must (1 or 2 pages)

### R-06: Extract title, issuer, number and dates with confidence
Statement: WHEN a certificate group is formed THE SYSTEM SHALL extract title, issuer, certificate number, issue date and expiry date using generic patterns (Indonesian and English dates, markers such as "berlaku s.d." and "valid until", numbers near "No", "Nomor" or "Number") and agreement across pages of the bundle, SHALL give each field a confidence, and SHALL mark a field "needs review" instead of guessing when confidence is low.
Priority: must
Acceptance: Given `bundle_simple.pdf` ("Jakarta, 17 Agustus 2025", "Nomor: 123/LSP-PLN/VIII/2025", "Berlaku s.d. 16 Agustus 2028"), when the tool runs, then the manifest has issue date 17/08/2025, expiry 16/08/2028 and number 123/LSP-PLN/VIII/2025; and the last certificate of `bundle_mixed.pdf`, which has no date, has an empty issue date flagged "needs review".
Source: brief, Must (extract fields), risk 3, A-02, A-03

### R-07: Compute expiry date
Statement: WHEN a certificate has no printed expiry date THE SYSTEM SHALL use a printed validity duration ("berlaku untuk 3 (tiga) Tahun") when present, else the rule table (DJK 5 years, K3 and BNSP 3 years, internal training none) from an editable config file, and SHALL leave the expiry cell empty for non-expiring documents; a printed date SHALL always win over the rule table.
Priority: must
Acceptance: Given a DJK certificate issued 2025-08-17 with no printed expiry, when the tool runs, then the expiry date is 17/08/2030; given an internal training certificate, then the expiry cell is empty; given `bundle_bnsp_like.pdf` (issued 20 Agustus 2026, "berlaku untuk 3 (tiga) Tahun"), then the expiry is 20/08/2029 with source `duration:3y`.
Source: brief, A-07, A-08

### R-08: Match conservatively and fetch NID and email
Statement: WHEN the best candidate scores at least 92, that top score is held by exactly one database row (no homonym), and it leads the next candidate by at least 5 points THE SYSTEM SHALL accept it and take NID and email from that database row; OTHERWISE THE SYSTEM SHALL NOT accept it and SHALL send the group to the unmatched report.
Priority: must
Acceptance: Given two employees named "Ahmad Fauzi" in different units (`bundle_unmatched.pdf`, page 1), when the tool runs, then no NID is accepted, the reason is "homonym" and the candidates list both NIDs with their unit.
Source: brief, success metric (zero wrong NIDs), risk 1

### R-09: Plan first, then write
Statement: THE SYSTEM SHALL always build a human-readable plan file before writing outputs, and WHEN `--dry-run` is given SHALL write the plan only and no PDFs or spreadsheets.
Priority: must
Acceptance: Given a bundle, when run with `--dry-run`, then the output folder contains plan.json only; when run without it, then the PDFs and both spreadsheets also exist.
Source: brief, Must (dry-run plan file)

### R-10: Name the output PDFs
Statement: WHEN a group is accepted THE SYSTEM SHALL write a PDF named `{NID}_{NamaPegawai}_{JudulSertifikasi}_{Tahun}.pdf`, adding `_PERPANJANGAN_{ke}` when a renewal marker is printed, replacing characters Windows does not allow, shortening long titles so the full path stays at or under 250 characters, and adding a counter when the same name would repeat.
Priority: must
Acceptance: Given a certificate titled with `/` and `:` characters and an output folder path of 200 characters, when the tool runs, then the file name has no illegal characters and the full path is at most 250 characters; given the same employee twice with the same title and year, then the second file ends with a counter and nothing is overwritten.
Source: brief, Must (standard names), A-10, A-12, A-14

### R-11: Write the 10-column manifest
Statement: THE SYSTEM SHALL write manifest_slice.xlsx with exactly these columns in this order: No, NID, Nama Pegawai, Judul Sertifikasi/Pelatihan, Jenis Dokumen (Sertifikasi/Pelatihan), Lembaga/Penyelenggara, Nomor Sertifikat, Tanggal Terbit, Tanggal Expired, Nama File PDF, with NID stored as text and dates in the configured format.
Priority: must
Acceptance: Given a run with 3 accepted certificates, when manifest_slice.xlsx is opened, then it has 3 data rows, the 10 headers exactly as written, NIDs as text and the date format from the config.
Source: brief, Must (manifest), A-08, A-09

### R-12: Write the unmatched report
Statement: THE SYSTEM SHALL write unmatched_report.xlsx with one row per group or page that was not accepted, giving the page numbers, the reason (for example "no confident holder", "homonym", "no text"), the top three candidates with scores and unit_kerja, and the fields it did extract.
Priority: must
Acceptance: Given a bundle with one page naming a person who is not in the database, when the tool runs, then the report has one row for that page with reason "no confident holder" and the page number.
Source: brief, Must (unmatched report)

### R-13: No page may disappear
Statement: THE SYSTEM SHALL end every input page in exactly one output PDF or exactly one report row, and IF the counts do not add up THEN THE SYSTEM SHALL stop with an error and keep the plan file.
Priority: must
Acceptance: Given any test bundle of N pages, when the tool runs, then pages in output PDFs plus pages in report rows equal N and no page is counted twice.
Source: brief, success metric (100% page accounting)

### R-14: Re-run safely
Statement: WHEN the tool runs again THE SYSTEM SHALL write to a new timestamped run folder inside the output folder and SHALL NOT overwrite or delete earlier results.
Priority: should
Acceptance: Given two consecutive runs on the same bundle, then two separate result folders exist with identical content apart from timestamps.
Source: brief, Should (no-overwrite), A-14

### R-15: Needs-review sheet and field confidence
Statement: THE SYSTEM SHOULD list in a "needs review" sheet of the report every accepted certificate that has at least one low-confidence field, with the field name and its confidence.
Priority: should
Acceptance: Given a certificate with a clear holder but an unreadable date, when the tool runs, then it is in the manifest with the date cell empty and appears in the needs-review sheet with the field "Tanggal Terbit".
Source: brief, Should (per-field confidence)

### R-16: Editable alias table for titles
Statement: THE SYSTEM SHOULD normalise certificate titles through an editable CSV alias table (raw title to short title) and SHALL use the raw title, shortened only if needed for path length, when no alias exists.
Priority: should
Acceptance: Given a config file whose alias table maps "Teknisi Distribusi Tenaga Listrik" to "Teknisi Distribusi", when the tool runs on `bundle_simple.pdf` with that config, then the file names and the manifest title column use "Teknisi Distribusi".
Source: brief, Should (alias table), A-10

### R-17: Page-reader hook for M3
Statement: THE SYSTEM SHOULD read pages through one replaceable "page reader" interface so that a later milestone can add the LLM rescue without changing the matching, naming or writing code.
Priority: should
Acceptance: Given a test that replaces the page reader with a fake one returning fixed text, when the tool runs, then the rest of the pipeline produces the expected outputs without code changes.
Source: brief, Should (M3 hook), [ADR-005](decisions/ADR-005-optional-llm-layer-and-data-policy.md)

### R-18: Measure accuracy against a gold set
Statement: WHEN the user runs `python -m certslice evaluate` with a run folder and gold.csv (first page of each certificate, correct NID, title, number, issue date, expiry date) THE SYSTEM SHALL report the number of wrong accepted NIDs, the share of certificates correct, and the accuracy per field, with text-layer and scanned certificates reported separately, and SHALL exit with a non-zero code when any wrong NID was accepted.
Priority: must
Acceptance: Given a run on `bundle_simple.pdf` and a gold file with one deliberately wrong expected NID, when evaluate runs, then it prints `WRONG_NID=1` and exits with code 4; with the correct gold file it prints `WRONG_NID=0`, exits 0 and shows the text and scan sets separately.
Source: brief, success metrics (gold set, scans reported separately)

### R-19: Fail clearly on bad input
Statement: IF the database lacks a required column, the PDF is encrypted or corrupt, or the database has no rows THEN THE SYSTEM SHALL stop before writing anything and print a message that names the problem in plain language.
Priority: should
Acceptance: Given a database without the column nid, when the tool runs, then it exits with a non-zero code, names the missing column and creates no output files.
Source: brief, users (non-technical staff), constraints

## Non-functional requirements

### R-20: Speed on text-layer bundles
Statement: THE SYSTEM SHALL process a 30-page text-layer bundle in under 5 minutes on the developer laptop; scan speed SHALL be measured and reported, not gated.
Priority: must
Acceptance: Given the 30-page synthetic `bundle_30.pdf` and a 400-row database, when the tool runs with `--no-ocr`, then it finishes in under 300 seconds; for the scanned fixture plan.json records the OCR seconds per page.
Source: brief, success metrics

### R-21: Windows 11 and PowerShell
Statement: THE SYSTEM SHALL run on Windows 11 with Python 3.10 or newer, CPU only, with input paths that contain spaces and employee names with non-ASCII characters.
Priority: must
Acceptance: Given a PDF at `C:\Temp\Folder With Spaces\bundel.pdf` and a name containing an accented letter, when the tool runs in PowerShell 5.1, then it completes and the file name keeps the letter or a documented ASCII form.
Source: brief, constraints (hardware and OS)

### R-22: Privacy and repository hygiene
Statement: THE SYSTEM SHALL keep real samples, run outputs and any API key out of the repository (`samples/` and the output folders are gitignored) and SHALL NOT send database.xlsx anywhere over the network.
Priority: must
Acceptance: Given the repository, when the secret scan and `git check-ignore samples/x.pdf` run, then the scan finds nothing and the path is ignored; a test with network access blocked passes in full.
Source: brief, constraints (legal, privacy), A-13

## Later milestones (outline only, no R-ids until their gate opens)
- M2: simple GUI (pick PDF, pick database.xlsx, Start, open result folder) and a Windows installer that bundles Python, libraries and OCR models; check for the Visual C++ runtime; portable fallback; clean-Windows test.
- M3: optional LLM rescue for doubtful pages, off by default, one key in a config file, per-page cache and resume, model name in config, output validated against the page text.

## Out of scope
- Local LLMs, Docker in the product, automatic upload to the web monitoring system, renewal detection from history, interactive review inside the app, GPU use.

Related: [brief](00-brief.md), [ADR-003](decisions/ADR-003-conservative-matching-and-page-accounting.md), [verification](04-verification.md)
