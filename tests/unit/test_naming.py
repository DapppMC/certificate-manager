from pathlib import Path
import pytest
from certslice.config import load_config
from certslice.errors import InputError
from certslice.naming import NameBuilder, clean_field


@pytest.fixture
def cfg():
    return load_config(None)


def test_clean_field():
    # Illegal characters and _ become -
    assert clean_field('Teknisi: <Distribusi> "Tenaga" / Listrik | ? * _') == "Teknisi- -Distribusi- -Tenaga- - Listrik - - - -"

    # Trailing dots and spaces removed
    assert clean_field("...Budi Santoso... ") == "Budi Santoso"

    # Accents kept
    assert clean_field("Andréas Siregar") == "Andréas Siregar"


def test_standard_naming_and_renewal(tmp_path, cfg):
    builder = NameBuilder(tmp_path, cfg)
    # Standard name
    name1 = builder.build("007123", "Budi Santoso", "Teknisi Distribusi", "2025", None)
    assert name1 == "007123_Budi Santoso_Teknisi Distribusi_2025.pdf"

    # Renewal suffix
    name2 = builder.build("0055001", "Andréas Siregar", "Teknisi Distribusi", "2025", 2)
    assert name2 == "0055001_Andréas Siregar_Teknisi Distribusi_2025_PERPANJANGAN_2.pdf"


def test_duplicates_counter(tmp_path, cfg):
    builder = NameBuilder(tmp_path, cfg)
    n1 = builder.build("007123", "Budi Santoso", "Teknisi Distribusi", "2025", None)
    n2 = builder.build("007123", "Budi Santoso", "Teknisi Distribusi", "2025", None)
    n3 = builder.build("007123", "Budi Santoso", "Teknisi Distribusi", "2025", None)

    assert n1 == "007123_Budi Santoso_Teknisi Distribusi_2025.pdf"
    assert n2 == "007123_Budi Santoso_Teknisi Distribusi_2025_dup2.pdf"
    assert n3 == "007123_Budi Santoso_Teknisi Distribusi_2025_dup3.pdf"


def test_long_title_shortened_to_fit_path(tmp_path, cfg):
    # Set out_dir length to 150 chars total
    pad = 150 - len(str(tmp_path.resolve())) - 1
    if pad < 1:
        out_dir = tmp_path
    else:
        out_dir = tmp_path / ("x" * pad)

    builder = NameBuilder(out_dir, cfg)
    long_title = "A" * 150
    n1 = builder.build("007123", "Budi Santoso", long_title, "2025", None)
    n2 = builder.build("007123", "Budi Santoso", long_title, "2025", None)

    # Both must fit within max_path_chars
    full_path1 = str(out_dir / "run-20261015-120000-01" / "pdf" / n1)
    full_path2 = str(out_dir / "run-20261015-120000-01" / "pdf" / n2)
    assert len(full_path1) <= 250
    assert len(full_path2) <= 250

    # Same shortened title
    assert n1[:-len("_2025.pdf")] == n2[:-len("_2025_dup2.pdf")]


def test_too_long_folder_raises_input_error(tmp_path, cfg):
    out_dir = tmp_path / ("x" * 240)
    builder = NameBuilder(out_dir, cfg)
    with pytest.raises(InputError, match="too long"):
        builder.build("007123", "Budi Santoso", "Teknisi", "2025", None)
