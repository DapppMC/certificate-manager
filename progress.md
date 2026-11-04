# Project Progress & Continuity Log

- **Project:** Certificate Manager v3.1 (Precision Studio Web & Desktop Edition — `cert-slicer`)
- **Last Updated:** 2026-11-04 10:59:00
- **Current Status:** COMPLETED
- **Current Objective:** Wave 4 (Fase 5 Otomasi Magang TDC + Koreksi.md Compatibility) — FTP Duplicate Guard, Auto-Extract `.ZIP` & Merge Companion PDF (SK + Sertifikat), Per-Participant Excel (`Excel_Per_Peserta/*.xlsx`), Direct Sync to Website Monitoring (`Program_1`), and Desktop / Application Launcher Shortcuts on Linux and Windows.

## 1. Completed Work & Changes Made
- [x] `src/certslice/bulk.py`:
  - Added `check_ftp_duplicate(nid, nama, judul, year, catalog_path)` with in-memory cached index over `synology_ftp_catalog.json` (82,989 FTP files) and `ftp_duplicate` / `ftp_existing_path` fields on `BulkItem`.
  - Added `expand_pdf_or_zip_inputs()` so `process_bulk_files()` and `/api/process` automatically extract `.pdf` files from uploaded `.zip` archives.
  - Added `merge_companion_pdfs()` and `merge_companion_items_in_run()` to combine companion SK + Sertifikat PDFs per participant into a single PDF and resync workbooks.
  - Updated `_write_bulk_workbooks()` to also generate individual per-participant Excel files in `Excel_Per_Peserta/{stem}.xlsx` while keeping `data.xlsx`, `manifest_slice.xlsx`, and `For NPS 0726.xlsx` 100% compliant with Golden Invariants.
- [x] `src/certslice/web.py`:
  - Updated `/api/process` and `WEB_HTML_CONTENT` dropzone to accept `.pdf` and `.zip` archives.
  - Updated `/api/jobs/{job_id}/download/zip` to package both combined Excel workbooks (`data.xlsx`, `manifest_slice.xlsx`, `For NPS 0726.xlsx`) and individual participant Excel workbooks (`Excel_Per_Peserta/*.xlsx`).
  - Added `POST /api/jobs/{job_id}/merge-companion` and `POST /api/jobs/{job_id}/sync-monitoring` (direct sync bridge to Website Monitoring `Program_1`), plus UI buttons `"Gabung SK + Sertifikat"`, `"Kirim ke Website Monitoring"`, and `"Sudah Ada di FTP"` badge.
- [x] Application Launcher & Desktop Shortcuts (Linux & Windows):
  - Linux Application Launcher (`~/.local/share/applications/` & `~/Desktop/`):
    - `Certificate Manager v3.1 (Web Studio)` -> executes `/mnt/windows/dev/cert-slicer/launch_app.sh`
    - `Certificate Manager v3.1 (Desktop GUI)` -> executes `/mnt/windows/dev/cert-slicer/buka_certificate_manager.sh`
  - Windows Desktop (`C:\Users\lenovo\Desktop\` / `/mnt/windows/Users/lenovo/Desktop/`):
    - `Certificate_Manager_Web_Studio_v3.1.bat` -> executes `C:\dev\cert-slicer\Buka_Web_Studio_v3.bat` (Port 8500)
    - `Certificate_Manager_Desktop_GUI_v3.1.bat` -> executes `C:\dev\cert-slicer\Buka_Certificate_Manager.bat`
- [x] `tests/test_wave4_fase5.py`:
  - Added comprehensive Wave 4 test suite; full test suite passed: **213 passed in 12.86s** (100% PASS, including Golden Invariants and Design Lint checks).

## 2. System State & Invariants
- **Golden Invariants:** 100% intact (`213 passed`).
- **Website Monitoring Compatibility:** `data.xlsx`, `manifest_slice.xlsx`, and `Excel_Per_Peserta/*.xlsx` are 100% compatible with Website Monitoring (`Program_1`) Bulk Import and `/api/jobs/{job_id}/sync-monitoring`.

## 3. Issues Encountered & Root Causes (If Any)
- None. All 213 tests pass cleanly.

## 4. Next Actionable Steps (Resume Point for Next Model/Agent)
- [x] All Fase 5 (Wave 4) objectives and `Koreksi.md` compatibility requirements are complete.
- **Resume Command:** `PYTHONPATH=src /home/dapp/Projects/cert_slicer/.venv/bin/python -m pytest -q`
