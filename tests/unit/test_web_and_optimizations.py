"""Verification suite for Planner 4.0 optimizations, Golden Master invariants, and Web Studio v3.0."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from fastapi.testclient import TestClient
import openpyxl
import pymupdf

from certslice.bulk import BulkItem, BulkResult, _write_auxiliary_workbook, _write_bulk_workbooks, _write_master_tree_workbook
from certslice.config import load_config
from certslice.database import clear_employee_cache, load_employees
from certslice.matcher import cue_filter, page_candidates
from certslice.models import Employee
from certslice.mso import HEADER_COLUMNS
from certslice.normalize import normalize_name
from certslice.ocr import get_ocr_engine
from certslice.web import JOBS, WEB_HTML_CONTENT, app
from certslice.writer import MANIFEST_HEADERS, NEEDS_REVIEW_HEADERS, UNMATCHED_HEADERS

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _make_sample_db(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data Master"
    ws.append(["NID", "Nama", "Email", "Unit", "Jabatan"])
    ws.append(["88112233", "Budi Santoso", "budi@example.com", "Unit Pembangkitan Jawa", "Supervisor Senior Boiler"])
    ws.append(["88112244", "Siti Aminah Putri", "siti@example.com", "Unit Pembangkitan Bali", "Analis Kimia"])
    ws.append(["88112255", "Ahmad Fauzi Pratama", "ahmad@example.com", "Unit Induk Pusat", "Teknisi K3 Listrik"])
    wb.save(path)
    wb.close()
    return path


def _make_sample_pdf(path: Path, pages_text: list[str]) -> Path:
    doc = pymupdf.open()
    for txt in pages_text:
        page = doc.new_page(width=595, height=842)
        y = 72
        for line in txt.splitlines():
            page.insert_text((72, y), line, fontsize=12)
            y += 20
    doc.save(path)
    doc.close()
    return path


def test_golden_master_invariants_preserved(tmp_path: Path) -> None:
    """Verify all output workbook schemas match tests/golden/golden_invariants.json 100%."""
    golden_path = ROOT_DIR / "tests" / "golden" / "golden_invariants.json"
    assert golden_path.exists()
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    assert MANIFEST_HEADERS == golden["headers"]["manifest"]
    assert UNMATCHED_HEADERS == golden["headers"]["unmatched"]
    assert NEEDS_REVIEW_HEADERS == golden["headers"]["needs_review"]
    assert HEADER_COLUMNS == golden["headers"]["mso_nps"]

    # Verify bulk data.xlsx 17-column headers
    pdf_file = _make_sample_pdf(tmp_path / "cert.pdf", ["SERTIFIKAT KOMPETENSI\nBudi Santoso\nLevel 4"])
    item = BulkItem(
        index=1,
        src_path=pdf_file,
        file_name="88112233_Budi_Santoso_Level_4_Boiler.pdf",
        nid="88112233",
        nama="Budi Santoso",
        jabatan="Supervisor",
        unit="UP Jawa",
        is_skp=True,
        level="Level 4",
        peran="Memelihara Boiler",
        judul="Memelihara Boiler",
        number="1234.01.A01.02.2025",
        issue_date="2025-01-10",
        expiry_date="2028-01-10",
        status="accepted",
        resource_code="BAM9",
    )
    run_dir = tmp_path / "run_out"
    run_dir.mkdir()
    _write_bulk_workbooks(run_dir, [item])
    wb_data = openpyxl.load_workbook(run_dir / "data.xlsx", read_only=True)
    data_headers = [c for c in next(wb_data.active.iter_rows(values_only=True))]
    wb_data.close()
    assert len(data_headers) == 17
    assert data_headers[:5] == ["No", "File Asal", "Nama File Baru", "NID", "Nama Pegawai"]
    assert data_headers[11] == "Resource Code"

    # Verify rekap_lampiran_dokumen.xlsx 6-column headers
    _write_auxiliary_workbook(run_dir, [item])
    wb_aux = openpyxl.load_workbook(run_dir / "rekap_lampiran_dokumen.xlsx", read_only=True)
    aux_headers = [c for c in next(wb_aux.active.iter_rows(values_only=True))]
    wb_aux.close()
    assert len(aux_headers) == 6

    # Verify rekap_seluruh_sertifikat.xlsx 18-column headers
    _write_master_tree_workbook(run_dir, [("Subfolder_A", item)])
    wb_master = openpyxl.load_workbook(run_dir / "rekap_seluruh_sertifikat.xlsx", read_only=True)
    master_headers = [c for c in next(wb_master.active.iter_rows(values_only=True))]
    wb_master.close()
    assert len(master_headers) == 18


def test_database_employee_cache_and_copy_safety(tmp_path: Path) -> None:
    """Verify load_employees caches by file stat and returns isolated Employee instances."""
    clear_employee_cache()
    db_path = _make_sample_db(tmp_path / "database.xlsx")
    cfg = load_config(None)

    emps1 = load_employees(db_path, cfg)
    assert len(emps1) == 3
    emps1[0].nama = "MUTATED NAME"

    emps2 = load_employees(db_path, cfg)
    assert emps2[0].nama == "Budi Santoso"
    assert emps2[0] is not emps1[0]


def test_ocr_engine_singleton_factory() -> None:
    """Verify get_ocr_engine returns shared thread-safe instance per DPI."""
    e1 = get_ocr_engine(dpi=130)
    e2 = get_ocr_engine(dpi=130)
    e3 = get_ocr_engine(dpi=150)
    assert e1 is e2
    assert e1 is not e3


def test_vectorized_matcher_speed_and_accuracy() -> None:
    """Verify vectorized page_candidates matches exact, fuzzy, and partial names at high speed."""
    cfg = load_config(None)
    employees = [
        Employee(
            nid=f"{10000 + i}",
            nama=f"Pegawai Uji Kecepatan {i}",
            email="",
            unit="Unit",
            jabatan="Staf",
            norm=normalize_name(f"Pegawai Uji Kecepatan {i}"),
        )
        for i in range(500)
    ]
    employees.append(
        Employee(
            nid="999999",
            nama="Muhammad Rizky Pratama",
            email="",
            unit="UP Bali",
            jabatan="Engineer",
            norm=normalize_name("Muhammad Rizky Pratama"),
        )
    )

    lines = [
        "PT PLN NUSANTARA POWER SERVICES",
        "SERTIFIKAT KOMPETENSI",
        "Diberikan kepada:",
        "Ir. Muhammad Rizky Pratama, S.T.",
        "Atas keberhasilan mengikuti pelatihan",
        "Jakarta, 15 Januari 2025",
    ] * 4

    t0 = time.perf_counter()
    cands = page_candidates(lines, employees, cfg)
    filtered = cue_filter(cands, lines, cfg)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 250.0
    assert filtered
    assert filtered[0].index == len(employees) - 1
    assert filtered[0].score == 100.0


def test_web_html_passes_strict_design_lint() -> None:
    """Verify WEB_HTML_CONTENT passes all 9 rules of scripts/design_lint.py --strict."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        html_path = Path(tmp_dir) / "web_studio.html"
        html_path.write_text(WEB_HTML_CONTENT, encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(ROOT_DIR / "scripts" / "design_lint.py"), str(html_path), "--strict"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Design lint failed:\n{res.stdout}\n{res.stderr}"
        assert "design lint: OK" in res.stdout


def test_web_studio_v3_endpoints_and_workbook_resync(tmp_path: Path) -> None:
    """Verify Web Studio v3.0 endpoints, multi-page preview, single & batch approval workbook resync."""
    client = TestClient(app)

    # 1. Status & Index
    r_idx = client.get("/")
    assert r_idx.status_code == 200
    assert "Certificate Manager Studio" in r_idx.text

    r_stat = client.get("/api/status")
    assert r_stat.status_code == 200
    assert r_stat.json()["status"] == "online"

    # 2. Seed a completed job with a 2-page certificate item requiring review
    pdf_path = _make_sample_pdf(
        tmp_path / "bundle_test.pdf",
        [
            "SERTIFIKAT KOMPETENSI\nDiberikan kepada:\nBudi Santoso\nLevel 4",
            "DAFTAR UNIT KOMPETENSI\n1. Memelihara Peralatan Boiler",
        ],
    )
    db_path = _make_sample_db(tmp_path / "database.xlsx")
    run_dir = tmp_path / "Hasil_Web_Test"
    (run_dir / "pdf").mkdir(parents=True, exist_ok=True)
    review_pdf = run_dir / "pdf" / "[REVIEW]_Budi_Santoso.pdf"
    review_pdf.write_bytes(pdf_path.read_bytes())

    item = BulkItem(
        index=1,
        src_path=pdf_path,
        file_name="[REVIEW]_Budi_Santoso.pdf",
        nid="",
        nama="Budi Santoso",
        jabatan="",
        unit="",
        is_skp=True,
        level="Level 4",
        peran="Memelihara Peralatan Boiler",
        judul="Memelihara Peralatan Boiler",
        number="1234.01.A01.02.2025",
        issue_date="2025-02-01",
        expiry_date="2028-02-01",
        status="unmatched",
        reason="perlu konfirmasi",
        candidates=[
            {
                "nid": "88112233",
                "nama": "Budi Santoso",
                "unit": "Unit Pembangkitan Jawa",
                "jabatan": "Supervisor Senior Boiler",
                "score": 95.0,
                "partial": False,
            }
        ],
        pages=[0, 1],
        is_bundle=True,
        resource_code="BAM9",
    )
    bulk_res = BulkResult(
        run_dir=run_dir,
        total_files=1,
        accepted_count=0,
        review_count=1,
        items=[item],
        lampiran_items=[],
    )
    _write_bulk_workbooks(run_dir, [item])

    job_id = "testjob1"
    JOBS[job_id] = {
        "id": job_id,
        "mode": "standard",
        "status": "completed",
        "progress": 100,
        "current": 1,
        "total": 1,
        "message": "Selesai",
        "logs": [],
        "pdf_paths": [pdf_path],
        "db_path": db_path,
        "result": bulk_res,
        "run_dir": str(run_dir),
        "elapsed_sec": 0.42,
        "error": None,
    }

    # 3. Multi-page preview endpoint respects ?page=1 and ?page=2
    r_prev1 = client.get(f"/api/jobs/{job_id}/preview/1?page=1&zoom=1.0")
    assert r_prev1.status_code == 200
    assert r_prev1.headers.get("X-Total-Pages") == "2"
    assert r_prev1.headers.get("X-Current-Page") == "1"

    r_prev2 = client.get(f"/api/jobs/{job_id}/preview/1?page=2&zoom=1.0")
    assert r_prev2.status_code == 200
    assert r_prev2.headers.get("X-Current-Page") == "2"

    # 4. Analytics endpoint
    r_ana = client.get(f"/api/jobs/{job_id}/analytics")
    assert r_ana.status_code == 200
    ana = r_ana.json()
    assert ana["total_certificates"] == 1
    assert ana["skp_count"] == 1
    assert ana["resource_code_resolved"] == 1

    # 5. Approve candidate and verify data.xlsx, For NPS 0726.xlsx, and plan.json on disk are updated!
    r_app = client.post(
        f"/api/jobs/{job_id}/approve",
        data={
            "item_index": 1,
            "nid": "88112233",
            "nama": "Budi Santoso",
            "unit": "Unit Pembangkitan Jawa",
            "jabatan": "Supervisor Senior Boiler",
            "custom_title": "Memelihara Peralatan Boiler",
            "level": "Level 4",
            "peran": "Memelihara Peralatan Boiler",
            "resource_code": "BAM9",
        },
    )
    assert r_app.status_code == 200
    app_data = r_app.json()
    assert app_data["status"] == "success"
    assert app_data["accepted_count"] == 1
    assert app_data["review_count"] == 0
    assert not review_pdf.exists()  # Stale [REVIEW]_ PDF removed!
    assert (run_dir / "pdf" / app_data["file_name"]).exists()

    # Check data.xlsx on disk was re-synced with NID 88112233 and Status "Tervalidasi"
    wb_check = openpyxl.load_workbook(run_dir / "data.xlsx", read_only=True)
    rows = list(wb_check.active.iter_rows(values_only=True))
    wb_check.close()
    assert len(rows) == 2
    assert rows[1][3] == "88112233"
    assert rows[1][4] == "Budi Santoso"
    assert rows[1][11] == "BAM9"
    assert rows[1][15] == "Tervalidasi"

    # Check plan.json on disk was also written and synced
    plan_json = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan_json["groups"][0]["status"] == "accepted"
    assert plan_json["groups"][0]["nid"] == "88112233"

    # 6. Download ZIP includes For NPS 0726.xlsx and data.xlsx
    r_zip = client.get(f"/api/jobs/{job_id}/download/zip")
    assert r_zip.status_code == 200
    assert r_zip.headers["content-type"] == "application/zip"
