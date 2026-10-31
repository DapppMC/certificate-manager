"""T-005 PDF input errors (R-19)."""
from conftest import DB, FIX, cli


def _run(pdf, tmp_path, capsys):
    out = tmp_path / "o"
    code, so, se = cli(["run", "--pdf", str(pdf), "--db", str(DB), "--out", str(out), "--no-ocr"], capsys)
    return code, (so + se).lower(), out


def test_corrupt_pdf(tmp_path, capsys):
    code, text, out = _run(FIX / "corrupt.pdf", tmp_path, capsys)
    assert code == 2
    assert "pdf" in text
    assert not out.exists() or not any(out.iterdir())


def test_encrypted_pdf(tmp_path, capsys):
    code, text, out = _run(FIX / "encrypted.pdf", tmp_path, capsys)
    assert code == 2
    assert "encrypted" in text
    assert not out.exists() or not any(out.iterdir())


def test_missing_pdf(tmp_path, capsys):
    code, text, out = _run(tmp_path / "nope.pdf", tmp_path, capsys)
    assert code == 2
    assert not out.exists() or not any(out.iterdir())
