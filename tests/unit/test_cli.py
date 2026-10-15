from certslice.cli import main


def test_cli_run_missing_files_returns_2(tmp_path):
    missing_pdf = tmp_path / "missing.pdf"
    missing_db = tmp_path / "missing.xlsx"
    code = main(["run", "--pdf", str(missing_pdf), "--db", str(missing_db), "--out", str(tmp_path)])
    assert code == 2


def test_cli_evaluate_stub_returns_1():
    code = main(["evaluate", "--run", "x", "--gold", "y"])
    assert code == 1
