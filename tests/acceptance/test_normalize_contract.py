"""T-003 name normalisation contract (R-03). Rules: docs/spec/modules/extraction-rules.md section 1."""
import pytest
from certslice.normalize import normalize_name

CASES = [
    ("Ir. BUDI SANTOSO, S.T., M.M.", "budi santoso"),
    ("SITI RAHMAWATI, S.E.", "siti rahmawati"),
    ("Dr. Ir. H. Ahmad Fauzi, M.Sc., Ph.D.", "ahmad fauzi"),
    ("DEWI LESTARI, A.Md.", "dewi lestari"),
    ("  rina   marlina  ", "rina marlina"),
    ("ANDRÉAS SIREGAR", "andreas siregar"),
    ("Muhammad Rizky Pratama, S.Kom", "muhammad rizky pratama"),
    ("O'Neil Saputra", "oneil saputra"),
    ("Budi-Santoso", "budi santoso"),
    ("", ""),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_title_words_inside_a_name_are_kept():
    # "Hendra" contains "h" but is not the title "H."; only whole tokens are removed.
    assert normalize_name("H. Hendra Saputra") == "hendra saputra"
