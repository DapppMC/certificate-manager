"""T-013 output file names (R-10, R-16, R-21)."""
import re

import pytest
from conftest import FIX, DB, cli, load_plan, manifest, run_dir_from

ILLEGAL = re.compile(r'[<>:"/\\|?*]')


def test_standard_names(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    names = sorted(p.name for p in (rd / "pdf").iterdir())
    assert names == [
        "0045678_Siti Rahmawati_Teknisi Distribusi Tenaga Listrik_2025.pdf",
        "007123_Budi Santoso_Teknisi Distribusi Tenaga Listrik_2025.pdf",
        "8812345_Dewi Lestari_Teknisi Distribusi Tenaga Listrik_2025.pdf",
    ]


def test_long_title_illegal_chars_duplicates_renewal_and_accent(tmp_path, capsys):
    # an output folder whose absolute path is 150 characters long: the long title must be shortened
    pad = 150 - len(str(tmp_path.resolve())) - 1
    if pad < 1:
        pytest.skip("temporary folder path is already longer than 150 characters")
    deep = tmp_path / ("x" * pad)
    assert len(str(deep.resolve())) == 150
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_titles.pdf"), "--db", str(DB), "--out", str(deep),
                        "--no-ocr"], capsys)
    assert code == 0, so + se
    rd = run_dir_from(so)
    files = sorted((rd / "pdf").iterdir())
    assert len(files) == 3
    for f in files:
        assert len(str(f.resolve())) <= 250, len(str(f.resolve()))
        assert not ILLEGAL.search(f.name)
    names = [f.name for f in files]
    budi = [n for n in names if n.startswith("007123_Budi Santoso_")]
    assert len(budi) == 2
    first = [n for n in budi if n.endswith("_2025.pdf")]
    dup = [n for n in budi if n.endswith("_2025_dup2.pdf")]
    assert len(first) == 1 and len(dup) == 1
    assert first[0][: -len("_2025.pdf")] == dup[0][: -len("_2025_dup2.pdf")]  # same shortened title
    andreas = [n for n in names if n.startswith("0055001_Andréas Siregar_")]
    assert len(andreas) == 1 and andreas[0].endswith("_2025_PERPANJANGAN_2.pdf")


def test_alias_table(tmp_path, capsys):
    (tmp_path / "aliases.csv").write_text("raw_title,short_title\nTeknisi Distribusi Tenaga Listrik,Teknisi Distribusi\n",
                                          encoding="utf-8")
    (tmp_path / "my.toml").write_text('[naming]\nalias_file = "aliases.csv"\n', encoding="utf-8")
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(DB), "--out", str(tmp_path / "o"),
                        "--no-ocr", "--config", str(tmp_path / "my.toml")], capsys)
    assert code == 0, so + se
    rd = run_dir_from(so)
    names = sorted(p.name for p in (rd / "pdf").iterdir())
    assert all("_Teknisi Distribusi_2025.pdf" in n for n in names), names
    if (rd / "manifest_slice.xlsx").exists():  # written from T-014 on
        assert {r["Judul Sertifikasi/Pelatihan"] for r in manifest(rd)} == {"Teknisi Distribusi"}


def test_output_folder_too_long_stops_before_writing(tmp_path, capsys):
    pad = 240 - len(str(tmp_path.resolve())) - 1
    if pad < 1:
        pytest.skip("temporary folder path is already longer than 240 characters")
    deep = tmp_path / ("x" * min(pad, 200)) / ("y" * max(1, pad - 201))
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(DB), "--out", str(deep),
                        "--no-ocr"], capsys)
    assert code == 2, so + se
    assert "too long" in (so + se).lower()
    assert not deep.exists() or not any(deep.iterdir())
