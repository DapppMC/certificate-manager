from pathlib import Path
import pytest
from certslice import cli
from certslice.errors import AccountingError


def test_accounting_error_exits_3_and_writes_plan(tmp_path, monkeypatch):
    def bad_accounting(groups, n_pages):
        raise AccountingError("forced accounting error for test")

    monkeypatch.setattr("certslice.pipeline.check_accounting", bad_accounting)

    root = Path(__file__).resolve().parents[2]
    pdf_path = root / "tests" / "fixtures" / "bundle_simple.pdf"
    db_path = root / "tests" / "fixtures" / "db_basic.xlsx"
    out_dir = tmp_path / "out"

    code = cli.main([
        "run",
        "--pdf",
        str(pdf_path),
        "--db",
        str(db_path),
        "--out",
        str(out_dir),
        "--no-ocr",
    ])

    assert code == 3
    # Check that a run folder was created and contains plan.json
    run_folders = list(out_dir.glob("run-*"))
    assert len(run_folders) == 1
    assert (run_folders[0] / "plan.json").exists()
