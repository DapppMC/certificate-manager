"""Wave 4 (Fase 5 Otomasi Magang TDC) Verification Suite for Certificate Manager v3.1."""
from __future__ import annotations

from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import openpyxl
import pymupdf

from certslice.bulk import (
    BulkItem,
    check_ftp_duplicate,
    expand_pdf_or_zip_inputs,
    merge_companion_items_in_run,
    merge_companion_pdfs,
    process_bulk_files,
)
from certslice.web import app


def _make_sample_pdf(path: Path, text: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), text, fontsize=14)
    doc.save(path)
    doc.close()
    return path


def _make_sample_db(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pegawai"
    ws.append(["NID", "Nama Lengkap", "Jabatan", "Unit Kerja"])
    ws.append(["8307168Z", "SAHBUDIN MAHIFA", "TEKNISI SENIOR", "UP KALTIMRA"])
    ws.append(["9016033PL", "ANDI PRATAMA", "ENGINEER", "UP GRESIK"])
    wb.save(path)
    wb.close()
    return path


def test_ftp_duplicate_guard_and_zip_auto_extract(tmp_path: Path) -> None:
    # 1. Test FTP duplicate guard with custom catalog
    catalog_file = tmp_path / "custom_ftp_catalog.json"
    catalog_file.write_text(
        '{"files": [{"name": "8307168Z_Sahbudin Mahifa_Ahli K3 Umum_2025.pdf", "path": "/FTP/Sertifikat/8307168Z_Sahbudin Mahifa_Ahli K3 Umum_2025.pdf", "nid": "8307168Z"}]}',
        encoding="utf-8",
    )
    dup = check_ftp_duplicate("8307168Z", "SAHBUDIN MAHIFA", "Ahli K3 Umum", catalog_path=catalog_file)
    assert dup["is_duplicate"] is True
    assert "8307168Z" in dup["ftp_path"]

    non_dup = check_ftp_duplicate("9999999XX", "TIDAK ADA", "Ahli K3 Umum", catalog_path=catalog_file)
    assert non_dup["is_duplicate"] is False

    # 2. Test .ZIP auto-extraction + Per-Participant Excel generation
    pdf1 = _make_sample_pdf(
        tmp_path / "cert1.pdf",
        "SERTIFIKAT KOMPETENSI\nDiberikan kepada:\nNama : SAHBUDIN MAHIFA\nNID : 8307168Z\nBidang : Ahli K3 Umum\nTanggal Terbit : 10 Januari 2025",
    )
    zip_in = tmp_path / "batch_input.zip"
    with zipfile.ZipFile(zip_in, "w") as zf:
        zf.write(pdf1, arcname="cert1.pdf")

    expanded = expand_pdf_or_zip_inputs([zip_in], extract_dir=tmp_path / "unzipped")
    assert len(expanded) == 1
    assert expanded[0].suffix == ".pdf"

    db_path = _make_sample_db(tmp_path / "db.xlsx")
    res = process_bulk_files(
        pdf_paths=[zip_in],
        db_path=db_path,
        out_dir=tmp_path / "out",
        use_ai=False,
        use_ocr=False,
    )
    assert res.total_files == 1
    assert (res.run_dir / "data.xlsx").exists()
    per_peserta_files = list((res.run_dir / "Excel_Per_Peserta").glob("*.xlsx"))
    assert len(per_peserta_files) == 1


def test_merge_companion_pdfs_and_web_sync_endpoints(tmp_path: Path) -> None:
    p_sk = _make_sample_pdf(tmp_path / "SK_8307168Z.pdf", "SURAT KEPUTUSAN PENUNJUKAN\nSAHBUDIN MAHIFA\n8307168Z")
    p_cert = _make_sample_pdf(tmp_path / "Sertifikat_8307168Z.pdf", "SERTIFIKAT AHLI K3 UMUM\nSAHBUDIN MAHIFA\n8307168Z")

    merged_out = merge_companion_pdfs([p_sk, p_cert], tmp_path / "merged.pdf")
    assert merged_out.exists()
    doc = pymupdf.open(merged_out)
    assert len(doc) == 2
    doc.close()

    # Test merge_companion_items_in_run and Web API endpoints
    db_path = _make_sample_db(tmp_path / "db2.xlsx")
    res = process_bulk_files(
        pdf_paths=[p_sk, p_cert],
        db_path=db_path,
        out_dir=tmp_path / "out2",
        use_ai=False,
        use_ocr=False,
    )
    assert len(res.items) == 2

    from certslice.web import JOBS
    job_id = "testw4"
    JOBS[job_id] = {
        "id": job_id,
        "mode": "standard",
        "status": "completed",
        "progress": 100,
        "current": 2,
        "total": 2,
        "message": "Selesai",
        "logs": [],
        "db_path": db_path,
        "result": res,
        "run_dir": res.run_dir,
        "elapsed_sec": 0.2,
        "error": None,
    }

    client = TestClient(app)
    r_merge = client.post(f"/api/jobs/{job_id}/merge-companion", json={"item_indices": [1, 2]})
    assert r_merge.status_code == 200
    assert r_merge.json()["merged_count"] == 1
    assert r_merge.json()["new_total"] == 1

    r_zip = client.get(f"/api/jobs/{job_id}/download/zip")
    assert r_zip.status_code == 200
    with zipfile.ZipFile(
        Path(tmp_path / "dl.zip").write_bytes(r_zip.content) and (tmp_path / "dl.zip"), "r"
    ) as zf_dl:
        names = zf_dl.namelist()
        assert "data.xlsx" in names
        assert any(n.startswith("Excel_Per_Peserta/") for n in names)

    r_sync = client.post(f"/api/jobs/{job_id}/sync-monitoring", json={})
    assert r_sync.status_code == 200
    assert r_sync.json()["status"] == "success"
