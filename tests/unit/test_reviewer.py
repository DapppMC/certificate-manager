import json
from pathlib import Path
import pymupdf
import pytest

from certslice.reviewer import get_review_items, render_page_image, approve_group


def test_get_review_items_empty(tmp_path: Path):
    assert get_review_items(tmp_path) == []


def test_get_review_and_approve_group(tmp_path: Path):
    # Create dummy input PDF with 2 pages
    pdf_path = tmp_path / "bundle.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    doc.new_page(width=300, height=400)
    doc.save(pdf_path)
    doc.close()

    run_dir = tmp_path / "run-test"
    run_dir.mkdir()

    plan_data = {
        "input_pdf": str(pdf_path),
        "groups": [
            {
                "id": 1,
                "pages": [1, 2],
                "status": "unmatched",
                "reason": "partial name",
                "nid": None,
                "nama": None,
                "candidates": [
                    {"nid": "12345", "nama": "BUDI SANTOSO", "score": 90.0}
                ],
                "fields": {
                    "title": {"value": "Pelatihan K3", "confidence": "high"},
                    "issue_date": {"value": "2024-05-10", "confidence": "high"},
                    "expiry_date": {"value": "2027-05-10", "confidence": "high"},
                    "issuer": {"value": "BNSP", "confidence": "high"},
                    "number": {"value": "123/K3/2024", "confidence": "high"},
                    "doc_type": {"value": "sertifikasi", "confidence": "high"},
                },
                "renewal": None,
                "file_name": None,
            }
        ]
    }
    (run_dir / "plan.json").write_text(json.dumps(plan_data), encoding="utf-8")

    # 1. Test get_review_items
    items = get_review_items(run_dir)
    assert len(items) == 1
    assert items[0]["id"] == 1

    # 2. Test render_page_image
    img_bytes = render_page_image(pdf_path, 1, max_width=200)
    assert img_bytes is not None
    assert len(img_bytes) > 0

    # 3. Test approve_group
    file_name = approve_group(
        run_dir=run_dir,
        group_id=1,
        nid="12345",
        nama="BUDI SANTOSO",
        unit="KANTOR PUSAT",
    )
    assert file_name.endswith(".pdf")
    assert "12345" in file_name
    assert "BUDI SANTOSO" in file_name

    # Check generated files
    assert (run_dir / "pdf" / file_name).exists()
    assert (run_dir / "manifest_slice.xlsx").exists()
    assert (run_dir / "unmatched_report.xlsx").exists()

    # Re-check get_review_items should be empty now
    assert len(get_review_items(run_dir)) == 0


def test_approve_group_empty_nid(tmp_path: Path):
    pdf_path = tmp_path / "bundle2.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    doc.save(pdf_path)
    doc.close()

    run_dir = tmp_path / "run-test-empty-nid"
    run_dir.mkdir()

    plan_data = {
        "input_pdf": str(pdf_path),
        "groups": [
            {
                "id": 1,
                "pages": [1],
                "status": "unmatched",
                "reason": "no confident holder",
                "nid": None,
                "nama": None,
                "raw_name": "RAYHAN FADLILLAH",
                "candidates": [],
                "fields": {
                    "title": {"value": "Pelatihan K3", "confidence": "high"},
                    "issue_date": {"value": "2024-05-10", "confidence": "high"},
                    "expiry_date": {"value": "2027-05-10", "confidence": "high"},
                    "issuer": {"value": "BNSP", "confidence": "high"},
                    "number": {"value": "123/K3/2024", "confidence": "high"},
                    "doc_type": {"value": "sertifikasi", "confidence": "high"},
                },
                "renewal": None,
                "file_name": None,
            }
        ]
    }
    (run_dir / "plan.json").write_text(json.dumps(plan_data), encoding="utf-8")

    file_name = approve_group(
        run_dir=run_dir,
        group_id=1,
        nid="",
        nama="RAYHAN FADLILLAH",
        unit="",
    )
    assert file_name == "RAYHAN FADLILLAH_Pelatihan K3_2024.pdf"
    assert (run_dir / "pdf" / file_name).exists()
    assert (run_dir / "data.xlsx").exists()
    assert len(get_review_items(run_dir)) == 0


def test_approve_group_replaces_review_file(tmp_path: Path):
    """Test that approving a review item cleanly replaces the [REVIEW]_ file on disk."""
    run_dir = tmp_path / "run-replace-test"
    pdf_dir = run_dir / "pdf"
    pdf_dir.mkdir(parents=True)

    # Create dummy initial [REVIEW]_ file
    old_review_name = "[REVIEW]_8109060PT_RAKHMAT HIDAYAT_Sertifikat.pdf"
    old_pdf_path = pdf_dir / old_review_name
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    doc.save(old_pdf_path)
    doc.close()
    assert old_pdf_path.exists()

    plan_data = {
        "mode": "bulk",
        "input_pdf": str(old_pdf_path),
        "groups": [
            {
                "id": 1,
                "pages": [1],
                "status": "review",
                "reason": "Judul sertifikat tidak dapat dideteksi secara spesifik",
                "nid": "8109060PT",
                "nama": "RAKHMAT HIDAYAT",
                "raw_name": "RAKHMAT HIDAYAT",
                "candidates": [
                    {"nid": "8109060PT", "nama": "RAKHMAT HIDAYAT", "score": 100.0, "unit": "UPK SEBALANG"}
                ],
                "fields": {
                    "title": {"value": "Ahli K3 Umum", "confidence": "high"},
                    "issue_date": {"value": "2025-05-15", "confidence": "high"},
                    "expiry_date": {"value": "2028-05-15", "confidence": "high"},
                    "number": {"value": "5/123/AS.02.04/V/2025", "confidence": "high"},
                    "is_skp": {"value": False, "confidence": "high"},
                    "level": {"value": "", "confidence": "high"},
                    "peran": {"value": "", "confidence": "high"},
                },
                "file_name": old_review_name,
                "review_file_name": old_review_name,
                "src_pdf": str(old_pdf_path),
                "is_bundle": False,
            }
        ],
    }
    (run_dir / "plan.json").write_text(json.dumps(plan_data, indent=2), encoding="utf-8")

    # Approve item with custom title Ahli K3 Umum
    approved_name = approve_group(
        run_dir=run_dir,
        group_id=1,
        nid="8109060PT",
        nama="RAKHMAT HIDAYAT",
        unit="UPK SEBALANG",
        custom_title="Ahli K3 Umum",
    )

    assert approved_name == "8109060PT_RAKHMAT HIDAYAT_Ahli K3 Umum.pdf"
    assert "[REVIEW]" not in approved_name
    assert (pdf_dir / approved_name).exists()
    # The old [REVIEW]_ file must be completely deleted/replaced!
    assert not old_pdf_path.exists()
    assert not (pdf_dir / f"[REVIEW]_{approved_name}").exists()
    # Review items list is now empty
    assert len(get_review_items(run_dir)) == 0


def test_get_review_items_nested_subdirectories(tmp_path: Path):
    root_run = tmp_path / "Hasil_Test_Multi"
    root_run.mkdir()

    # Create depth 1 subfolder
    sub1 = root_run / "Folder1"
    sub1.mkdir()
    p1 = {
        "mode": "bulk",
        "groups": [
            {"id": 1, "status": "accepted", "pages": [1]},
            {"id": 2, "status": "unmatched", "pages": [2], "reason": "test1", "candidates": []},
        ],
    }
    (sub1 / "plan.json").write_text(json.dumps(p1), encoding="utf-8")

    # Create depth 3 nested subfolder
    sub_nested = root_run / "Nested" / "Deep" / "Folder2"
    sub_nested.mkdir(parents=True)
    p2 = {
        "mode": "bulk",
        "groups": [
            {"id": 1, "status": "unmatched", "pages": [1], "reason": "test2", "candidates": []},
            {"id": 2, "status": "accepted", "pages": [2]},
            {"id": 3, "status": "review", "pages": [3], "reason": "test3", "candidates": []},
        ],
    }
    (sub_nested / "plan.json").write_text(json.dumps(p2), encoding="utf-8")

    items = get_review_items(root_run)
    assert len(items) == 3

    # Check that _plan_dir and _plan_path are properly assigned
    dirs = [it["_plan_dir"] for it in items]
    assert str(sub1) in dirs
    assert str(sub_nested) in dirs



