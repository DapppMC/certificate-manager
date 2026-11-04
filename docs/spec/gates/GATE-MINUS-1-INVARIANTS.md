# Gate -1: Pinning & Behavioral Invariants (`cert_slicer`)

- **Project Mode:** `BROWNFIELD`
- **Golden Master Hash:** `a543a8b2f93671c2cc7545192acf4b17070483f3c19b04cd1c77acb59fb94a72` (`tests/golden/golden_invariants.json`)

## 1. Core Output & Behavioral Invariants (`[INVARIANT]`)

1. **`[INVARIANT]` [ref: src/certslice/writer.py:L9-L60]: CLI Workbook Schemas**
   - `manifest_slice.xlsx` MUST contain sheet `"manifest"` with exact 10 columns (`MANIFEST_HEADERS`).
   - `unmatched_report.xlsx` MUST contain sheet `"unmatched"` with exact 19 columns (`UNMATCHED_HEADERS`) and sheet `"needs_review"` with exact 6 columns (`NEEDS_REVIEW_HEADERS`).
2. **`[INVARIANT]` [ref: src/certslice/pipeline.py:L251-L268, L475-L495]: CLI `plan.json` & Console Contract**
   - `certslice run` MUST write `plan.json` (version 1) and print `PAGES=<p> ACCEPTED=<a> UNMATCHED=<u>` and `RUN_DIR=<path>`.
   - Every page $1 \dots N$ MUST belong to exactly one group (`check_accounting`).
3. **`[INVARIANT]` [ref: src/certslice/naming.py:L24-L92]: PDF Naming Contract**
   - CLI mode pattern: `{nid}_{nama}_{judul}_{tahun}[_PERPANJANGAN_{ke}][_dup{n}].pdf` within `max_path_chars = 250`.
   - Bulk mode pattern:
     - SKP: `{NID}_{Nama}_{Jabatan Level}_{Peran}.pdf` (or `[REVIEW]_...` when status != `"accepted"`).
     - Non-SKP: `{NID}_{Nama}_{Judul}.pdf` (or `[REVIEW]_...` when status != `"accepted"`).
     - Roster & Auxiliary: `Lampiran_Surat_Pengantar*.pdf`, `[LAMPIRAN]_{Kategori}_{Stem}.pdf`.
4. **`[INVARIANT]` [ref: src/certslice/bulk.py:L1794-L1910, L2095-L2219, L2860-L3060]: Bulk, Multi-Folder & FTP Workbook Schemas**
   - `data.xlsx` & `manifest_slice.xlsx` (Bulk mode, sheet `"Rekap Sertifikat"`, 17 columns).
   - `rekap_lampiran_dokumen.xlsx` (sheet `"Rekap Lampiran Dokumen"`, 6 columns).
   - `rekap_seluruh_sertifikat.xlsx` (Multi-folder mode: 18 columns; FTP mode: 16 columns).
   - `rekap_seluruh_lampiran.xlsx` (7 columns).
5. **`[INVARIANT]` [ref: src/certslice/mso.py:L19-L310]: MSO765 (`For NPS 0726.xlsx`) Upload Schema**
   - Exact 10 columns (`HEADER_COLUMNS`: `NID`, `RESOURCE CLASS`, `RESOURCE CODE`, `COMPETENCY LEVEL`, `EFFECTIVE DATE`, `GAINED DATE`, `EXP DATE`, `REQ TYPE`, `INSTITUTE CODE`, `PRIMARY`).
   - Segregation into Organic 2-letter NID sheets, `(Bukan 2 Huruf)` sheets, `Tidak ada NID `, and `Tidak ada Resource Code `.
6. **`[INVARIANT]` [ref: src/certslice/levels.py:L6-L187, src/certslice/resource_codes.py:L18-L312]: SKP Level & Resource Code Resolution**
   - `GLOBAL_SKP_LEVEL_TITLES` (Levels 1–9), `resolve_skp_level`, `get_numeric_level`, `clean_skp_peran`, and `lookup_resource_code` MUST preserve exact mapping behavior.

## 2. Legacy Anomalies & Bottlenecks Identified (`[LEGACY_ANOMALY]`)

1. **`[LEGACY_ANOMALY]` [ref: src/certslice/matcher.py:L93-L196, src/certslice/bulk.py:L635-L688]: Brute-Force $O(N \times M \times W)$ Candidate Matching**
   - Re-tokenizes employee norms and runs nested Python loops per line window without length-bound pruning or C++ batch scoring (`rapidfuzz.process.cdist`).
2. **`[LEGACY_ANOMALY]` [ref: src/certslice/database.py:L24-L129]: Uncached Excel Database Parsing**
   - Re-opens and parses multi-megabyte `Data Master SDM` Excel workbooks on every subfolder in `process_multi_folder_tree`.
3. **`[LEGACY_ANOMALY]` [ref: src/certslice/ocr.py:L11-L43]: Global Thread Lock on OCR Inference**
   - `self._lock = threading.Lock()` serializes all worker threads during RapidOCR inference, negating multi-threading gains.
4. **`[LEGACY_ANOMALY]` [ref: src/certslice/web.py:L363-L441]: Web Approval Does Not Sync Workbooks**
   - `approve_candidate` in `web.py` slices the PDF in-memory but fails to update `data.xlsx`, `manifest_slice.xlsx`, `For NPS 0726.xlsx`, and `plan.json` on disk.
5. **`[LEGACY_ANOMALY]` [ref: src/certslice/web.py:L331-L361]: Web Preview Ignores `page` Parameter**
   - `get_item_preview` hardcodes `target_item.pages[0] + 1` instead of allowing multi-page navigation across pages of a certificate.
6. **`[LEGACY_ANOMALY]` [ref: src/certslice/web.py:L510-L955]: Design Constitution Violations (6/9 Rules Failing)**
   - Uses banned AI slop indigo/purple palette (`#6366f1`, `#4f46e5`, `bg-indigo-*`), external CDN dependencies that fail offline, `transition-all`, missing CSS custom property tokens, missing concentric radius formula, and missing `tabular-nums`.
