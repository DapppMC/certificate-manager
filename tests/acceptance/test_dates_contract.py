"""T-009 date parsing contract (R-06). Rules: docs/spec/modules/extraction-rules.md section 9 (Dates).
Patterns taken from real certificates seen on 2026-10-15."""
import datetime as dt

import pytest
from certslice.fields import find_dates

D = dt.date
CASES = [
    ("Jakarta, 17 Agustus 2025", [D(2025, 8, 17)]),
    ("Surabaya, 20 Agustus 2026", [D(2026, 8, 20)]),
    ("Valid until March 4, 2027", [D(2027, 3, 4)]),
    ("Issued 5th March 2024", [D(2024, 3, 5)]),
    ("Held on October 05th \u2013 12nd, 2026", [D(2026, 10, 12)]),
    ("Held on October 05th - 12th, 2026", [D(2026, 10, 12)]),
    ("Pelatihan 17 - 20 Agustus 2025", [D(2025, 8, 20)]),
    ("Tanggal 01/02/2023", [D(2023, 2, 1)]),
    ("2025-08-17", [D(2025, 8, 17)]),
    ("Berlaku 17 Agustus 2025 s.d. 16 Agustus 2028", [D(2025, 8, 17), D(2028, 8, 16)]),
    ("Tanggal 31/02/2025", []),
    ("Nomor 123/LSP/2025", []),
]


@pytest.mark.parametrize("line,expected", CASES)
def test_find_dates(line, expected):
    assert [d for _, d in find_dates(line)] == expected
