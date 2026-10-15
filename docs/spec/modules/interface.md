---
title: Interface and file contracts
date: 2026-10-15
tags: [spec, module, interface]
when: When a ticket touches the command line, the Python API, the configuration, plan.json or the output workbooks; the acceptance tests rely on every line here.
---

# Interface and file contracts

Serves: R-01, R-09, R-11, R-12, R-13, R-14, R-15, R-17, R-18, R-19, R-21. The acceptance tests in `tests/acceptance/` check this contract; a change here needs an ADR.

## 1. Command line
```
python -m certslice run --pdf <bundle.pdf> --db <database.xlsx> --out <folder>
                        [--dry-run] [--pages-per-cert 1|2|auto] [--no-ocr] [--config <file.toml>]
python -m certslice evaluate --run <run folder> --gold <gold.csv>
```
- `--pages-per-cert` default `auto`. `--no-ocr` turns OCR off for this run. `--config` is optional.
- stdout of `run` ends with exactly two lines: `PAGES=<n> ACCEPTED=<a> UNMATCHED=<u>` (u = unmatched groups) and `RUN_DIR=<absolute path of the run folder>`.
- Errors go to stderr as one line starting `ERROR: ` in plain language (for example `ERROR: database is missing the required column(s): nid ...`).
- `--help` works for both subcommands (argparse).

| Exit code | Meaning |
|---|---|
| 0 | run finished (unmatched groups are normal) / evaluate found no wrong NID |
| 1 | unexpected error (bug); traceback on stderr |
| 2 | input error found BEFORE anything is written: file missing, database missing `nid` or name column, no employee rows, PDF corrupt, encrypted or empty, output folder path too long |
| 3 | page accounting failed (R-13); plan.json is still written to the run folder |
| 4 | evaluate: at least one wrong NID was accepted |

## 2. Python API (used by tests and later by the GUI and LLM layer)
- `certslice.cli.main(argv: list[str] | None = None) -> int`: the CLI; returns the exit code (never calls `sys.exit` itself). `certslice/__main__.py` does `sys.exit(main())`.
- `certslice.pipeline.run(pdf_path, db_path, out_dir, *, dry_run=False, pages_per_cert="auto", ocr=True, config_path=None, reader=None) -> RunResult`; `RunResult` has `run_dir: Path | None`, `exit_code: int`, `summary: dict` with keys `pages`, `accepted`, `unmatched`. Input errors raise `certslice.errors.InputError`; accounting errors raise `certslice.errors.AccountingError` (the CLI maps them to 2 and 3).
- `certslice.models.PageText`: dataclass `index: int` (1-based page number), `text: str` (lines separated by `\n`), `source: str` (`"text"`, `"ocr"`, `"none"`, or any name a custom reader chooses).
- `certslice.models.PageReader`: `typing.Protocol` with one method `read(self, page: pymupdf.Page) -> PageText`. The pipeline uses `reader` when given, else `certslice.reader.LocalPageReader`.
- `certslice.normalize.normalize_name(s: str) -> str` (rules in [extraction rules](extraction-rules.md) section 1).

## 3. Configuration
- Defaults: `src/certslice/default_config.toml` (planner-owned, protected). Read with `tomllib` (standard library).
- Database sheet: the first sheet whose name (case-insensitive, trimmed) is in `[database] preferred_sheets` (default `data master`), else the first sheet. Only the mapped columns (NID, name, email, unit, jabatan) are read; every other column (birth date, religion, address and so on in the real master data) is ignored and never copied anywhere.
- `--config <file>`: a TOML file whose tables override the default tables key by key (for example only `[naming] alias_file = "aliases.csv"`). `[[validity_rules]]` in a user file replaces the whole default list. Relative paths inside it (alias file) are relative to that file's folder.
- Alias CSV: UTF-8 (BOM allowed), header `raw_title,short_title`; matching of `raw_title` is case- and accent-insensitive with collapsed spaces.

## 4. Output folder layout
```
<out>/run-YYYYMMDD-HHMMSS/          (if it exists: run-YYYYMMDD-HHMMSS-2, -3, ...)
  plan.json                          always (dry-run writes ONLY this file)
  pdf/<file name>.pdf                one per accepted group (not in dry-run)
  manifest_slice.xlsx                not in dry-run
  unmatched_report.xlsx              not in dry-run; sheets "unmatched" and "needs_review"
```
`<out>` is created if missing. Nothing outside the new run folder is written or deleted. Input errors (exit 2) are detected before the run folder is created.

## 5. plan.json (UTF-8, `ensure_ascii=False`, indent 2)
```json
{
  "version": 1, "input_pdf": "...", "database": "...", "page_count": 3, "dry_run": false,
  "pages_per_cert": "auto", "template_names": ["Agus Prasetyo"],
  "pages": [{"page": 1, "source": "text", "candidates": [{"nid": "007123", "nama": "Budi Santoso", "score": 100.0}]}],
  "groups": [{
    "id": 1, "pages": [1], "status": "accepted", "reason": "",
    "nid": "007123", "nama": "Budi Santoso", "email": "...", "unit": "...",
    "candidates": [{"nid": "007123", "nama": "Budi Santoso", "unit": "UID Jakarta Raya", "score": 100.0}],
    "fields": {
      "title": {"value": "Teknisi Distribusi Tenaga Listrik", "confidence": "high"},
      "issuer": {"value": "...", "confidence": "high"}, "doc_type": {"value": "Sertifikasi", "confidence": "medium"},
      "number": {"value": "123/LSP-PLN/VIII/2025", "confidence": "high"},
      "issue_date": {"value": "2025-08-17", "confidence": "medium"},
      "expiry_date": {"value": "2028-08-16", "confidence": "high"}},
    "expiry_source": "printed", "renewal": null, "file_name": "007123_Budi Santoso_..._2025.pdf"}],
  "timings": {"read_seconds": 0.1, "total_seconds": 0.4, "ocr_pages": 0, "ocr_seconds_per_page": null}
}
```
- `pages[].candidates`: up to 3, after the template and cue filters, sorted by score (high first, then database row order), score rounded to 1 decimal.
- `pages[].source`: `text`, `ocr`, `mixed` (text layer plus OCR of a background picture), `none`, or a custom reader's name. Candidate objects may carry `"partial": true` for a shortened name.
- `groups[].status`: `accepted` or `unmatched`. `reason` (unmatched only): `no text`, `no confident holder`, `partial name`, `homonym`, `close candidates`, `fixed chunk without one clear holder`. Accepted groups have `reason: ""`; unmatched groups have `nid`, `nama`, `email`, `unit`, `file_name` = null.
- `groups[].candidates`: the first 3 candidates of the group's deciding page, with `unit`.
- Field values are strings or null; dates are ISO `YYYY-MM-DD`; confidence is `high`, `medium` or `low`. `title.value` of an accepted group is the alias when one applies.
- `expiry_source`: `printed`, `duration:<n>y`, `rule:<n>y`, `rule:none`, or null. `renewal`: integer or null.
- `timings.ocr_seconds_per_page`: mean seconds per OCR'd page, null when no page was OCR'd.

## 6. manifest_slice.xlsx (sheet "manifest")
Header row exactly: `No`, `NID`, `Nama Pegawai`, `Judul Sertifikasi/Pelatihan`, `Jenis Dokumen (Sertifikasi/Pelatihan)`, `Lembaga/Penyelenggara`, `Nomor Sertifikat`, `Tanggal Terbit`, `Tanggal Expired`, `Nama File PDF`. One row per accepted group in page order. `No` is an integer from 1; every other cell is a text cell (`number_format = "@"`, value a `str`); dates formatted with `[output] date_format` (default `DD/MM/YYYY`); unknown values are empty strings.

## 7. unmatched_report.xlsx
- Sheet `unmatched`, header: `Halaman`, `Alasan`, then for k = 1..3 `Kandidat k Nama`, `Kandidat k NID`, `Kandidat k Unit`, `Kandidat k Skor`, then `Judul`, `Nomor Sertifikat`, `Tanggal Terbit`, `Tanggal Expired`, `Sumber Teks`. One row per unmatched group. `Halaman` is `"5"` or `"5-6"` (text). `Sumber Teks` is the source of the group's first page.
- Sheet `needs_review`, header: `Nama File PDF`, `NID`, `Halaman`, `Kolom`, `Nilai`, `Keyakinan`. One row per field with confidence `low` in an accepted group; `Kolom` uses the manifest header name of the field (for example `Tanggal Terbit`).
- Both sheets always exist (header only when empty). All cells are text.

## 8. Evaluate
- gold.csv: UTF-8, header `page,nid,title,number,issue_date,expiry_date`; one row per certificate, `page` = its first page; `nid` empty means "should not be accepted"; dates `DD/MM/YYYY`; empty field = not checked.
- A certificate is correct when its group (the group whose first page is `page`) is accepted with that NID, or is unmatched when `nid` is empty. A wrong NID is an accepted group whose NID differs from `nid` (including a non-empty NID when `nid` is empty).
- Field checks: title correct when `rapidfuzz.fuzz.token_sort_ratio` of the case- and accent-folded strings is at least 90; number equal ignoring spaces and case; dates equal after formatting as `DD/MM/YYYY`.
- Set = `text` when the group's first page source is `text`, else `scan` (`ocr`, `mixed` and `none` count as scan).
- stdout lines, in this order: `SET text certificates=<t> correct=<c> pct=<p>`, `SET scan ...` (`pct=n/a` when 0), `FIELD <title|number|issue_date|expiry_date> text=<c>/<t> scan=<c>/<t>`, then `WRONG_NID=<k>`. Exit 4 when k > 0, else 0.

Related: [design](../02-design.md), [extraction rules](extraction-rules.md), [requirements](../01-requirements.md)
