import pytest
from certslice.normalize import normalize_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Prof. Dr. Ir. Budi Santoso, M.T., IPU", "budi santoso"),
        ("Hj. Siti Rahmawati, S.E., Ak., CPA", "siti rahmawati"),
        ("  Dewi   Lestari  ", "dewi lestari"),
        ("Élodie Noël", "elodie noel"),
        ("D'Angelo Pratama", "dangelo pratama"),
        ("Ahmad-Fauzi", "ahmad fauzi"),
        ("", ""),
        ("H. Hendra Wijaya", "hendra wijaya"),
    ],
)
def test_normalize_name_custom_cases(raw, expected):
    assert normalize_name(raw) == expected
