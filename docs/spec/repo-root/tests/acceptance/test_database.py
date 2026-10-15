"""T-004 database loading (R-01, R-19)."""
from conftest import FIX, cli, load_plan


def test_header_found_below_title_row_and_nid_keeps_leading_zeros(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr", "--dry-run")
    assert code == 0, out
    plan = load_plan(rd)
    nids = [g["nid"] for g in plan["groups"]]
    assert nids == ["007123", "0045678", "8812345"]  # 8812345 is stored as a number in the sheet


def test_missing_nid_column_stops_before_writing(tmp_path, capsys):
    out = tmp_path / "o"
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(FIX / "db_missing_nid.xlsx"),
                        "--out", str(out), "--no-ocr"], capsys)
    assert code == 2
    assert "nid" in (so + se).lower()
    assert not out.exists() or not any(out.iterdir())


def test_empty_database_stops_before_writing(tmp_path, capsys):
    out = tmp_path / "o"
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(FIX / "db_empty.xlsx"),
                        "--out", str(out), "--no-ocr"], capsys)
    assert code == 2
    assert "no employee rows" in (so + se).lower()
    assert not out.exists() or not any(out.iterdir())


def test_missing_database_file(tmp_path, capsys):
    out = tmp_path / "o"
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(tmp_path / "nope.xlsx"),
                        "--out", str(out), "--no-ocr"], capsys)
    assert code == 2
    assert not out.exists() or not any(out.iterdir())
