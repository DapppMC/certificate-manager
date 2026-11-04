# Gate 1: EARS Requirements Specification (Planner System 4.0 Modernization)

## 1. Output & Contract Preservation (Zero Regression)
- **REQ-INV-01:** WHEN any CLI, Bulk, Multi-Folder, or FTP workflow executes, THE SYSTEM SHALL produce output PDFs, `data.xlsx`, `manifest_slice.xlsx`, `unmatched_report.xlsx`, `For NPS 0726.xlsx`, `rekap_lampiran_dokumen.xlsx`, `rekap_seluruh_sertifikat.xlsx`, and `plan.json` with 100% identical schemas, column headers, and naming patterns as the Golden Master specification.
- **REQ-INV-02:** WHEN `pytest` or `python scripts/stage_tests.py --all` is executed, THE SYSTEM SHALL collect and pass all acceptance, unit, and golden master test suites without import collisions.

## 2. High-Throughput Engine & Algorithmic Optimization
- **REQ-PERF-01 (Indexed Vectorized Matching):** WHEN matching certificate lines or queries against the employee master database, THE SYSTEM SHALL use pre-computed employee token/length indices and C-accelerated RapidFuzz batch distance (`cdist`) with mathematical length-ratio pruning to achieve up to 10x faster candidate evaluation while returning identical candidate scores and rankings.
- **REQ-PERF-02 (Database & Catalog Caching):** WHEN `load_employees` is invoked with an unchanged `.xlsx` path (verified by resolved path, `mtime_ns`, and file size), THE SYSTEM SHALL serve pre-normalized `Employee` records from an in-memory cache in $O(1)$ time.
- **REQ-PERF-03 (Lock-Free Thread-Local OCR & Pre-compiled Regex):** WHEN multiple worker threads perform OCR extraction or regex field parsing, THE SYSTEM SHALL use thread-local `RapidOCR` instances (avoiding global lock serialization) and pre-compiled regular expressions.

## 3. Web Edition v3.0 Overhaul (Offline-First, Design Constitution Compliant, Feature-Complete)
- **REQ-WEB-01 (Anti-Slop Design Constitution):** WHEN rendering the Web Edition UI (`/`), THE SYSTEM SHALL pass `scripts/design_lint.py --strict` with 0 failures (Archetype 1 Precision Dark & Industrial Warm theme toggle, zero purple/indigo slop, CSS custom tokens, concentric border radius $R_o = R_i + p$, `font-variant-numeric: tabular-nums`, and hardware-accelerated `transform`/`opacity` transitions).
- **REQ-WEB-02 (100% Offline Zero-CDN Operation):** WHEN running in an air-gapped corporate intranet without internet access, THE SYSTEM SHALL render all styles, icons (inline SVG icon system), and interactive components without any external CDN requests.
- **REQ-WEB-03 (Full Workbook & MSO765 Sync on Approval):** WHEN a user approves an item or executes Batch Approve in the Web UI, THE SYSTEM SHALL immediately re-slice the PDF, resolve SKP Level & Resource Code, and regenerate `data.xlsx`, `manifest_slice.xlsx`, `For NPS 0726.xlsx`, and `plan.json` on disk.
- **REQ-WEB-04 (Multi-Page Preview, Batch Approval & Analytics):** WHEN inspecting results in the Web UI, THE SYSTEM SHALL support multi-page PDF navigation (Page 1..N, zoom, pan, fit), one-click Batch Approve All / Selected, live search & filtering (by Status, SKP/Non-SKP, Level 1–9, Resource Code, Unit), downloadable `For NPS 0726.xlsx` and `rekap_lampiran_dokumen.xlsx`, and an Executive Analytics view (SKP Level distribution, Resource Code mapping rate, Unit breakdown, Expiry alerts).
- **REQ-WEB-05 (Local Directory / Multi-Folder / FTP Fast-Rekap via Web API):** WHEN running locally, THE SYSTEM SHALL allow processing local folder paths (Bulk, Multi-Folder Tree, and FTP Pre-formatted Instant Rekap) directly from the Web UI in addition to drag-and-drop file uploads.

## 4. Desktop GUI (`gui.py`) Ergonomics & Modernization
- **REQ-GUI-01:** WHEN using the Tkinter Desktop GUI (`CertSliceApp` and `ReviewDialog`), THE SYSTEM SHALL provide live search/filtering in the Review Dialog, keyboard shortcuts (`Ctrl+Return` to approve, `Left`/`Right` for page navigation, `+`/`-` for zoom), determinate progress feedback, and a 1-click button to launch the Web Dashboard.
