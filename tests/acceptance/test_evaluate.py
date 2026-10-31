"""T-019 gold-set evaluation (R-18)."""
from conftest import FIX, cli


def test_wrong_nid_is_counted_and_fails(run_bundle, capsys):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    c, so, se = cli(["evaluate", "--run", str(rd), "--gold", str(FIX / "gold_wrong.csv")], capsys)
    assert c == 4, so + se
    assert "WRONG_NID=1" in so


def test_correct_gold_passes_and_splits_sets(run_bundle, capsys):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    c, so, se = cli(["evaluate", "--run", str(rd), "--gold", str(FIX / "gold_simple.csv")], capsys)
    assert c == 0, so + se
    assert "WRONG_NID=0" in so
    assert "SET text certificates=3 correct=3" in so
    assert "SET scan certificates=0" in so
    assert "FIELD issue_date text=3/3" in so
