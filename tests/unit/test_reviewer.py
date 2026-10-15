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
