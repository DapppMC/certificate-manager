import datetime as dt
from certslice.config import load_config
from certslice.fields import extract_dates, extract_number, find_dates

D = dt.date


def test_find_dates_patterns():
    # 1. d month yyyy
    assert [d for _, d in find_dates("Jakarta, 17 Agustus 2025")] == [D(2025, 8, 17)]
    # 2. month d, yyyy
    assert [d for _, d in find_dates("Valid until March 4, 2027")] == [D(2027, 3, 4)]
    # 3. day range Indonesian
    assert [d for _, d in find_dates("Pelatihan 17 - 20 Agustus 2025")] == [D(2025, 8, 20)]
    # 4. day range English with ordinal suffixes and en-dash
    assert [d for _, d in find_dates("Held on October 05th \u2013 12nd, 2026")] == [D(2026, 10, 12)]
    # 5. yyyy-mm-dd
    assert [d for _, d in find_dates("Issued on 2025-08-17")] == [D(2025, 8, 17)]
    # 6. dd/mm/yyyy
    assert [d for _, d in find_dates("Tanggal 01/02/2023")] == [D(2023, 2, 1)]
    # 7. Short month names
    assert [d for _, d in find_dates("10 Agt 2024 and 15 Oct 2024")] == [D(2024, 8, 10), D(2024, 10, 15)]
    # 8. Invalid date 31/02/2025 rejected
    assert find_dates("Tanggal 31/02/2025") == []


def test_extract_dates_logic():
    cfg = load_config(None)

    # 9. s.d. range with two dates on expiry line
    lines_range = ["Berlaku 17 Agustus 2025 s.d. 16 Agustus 2028"]
    iss, iss_c, exp, exp_c = extract_dates(lines_range, cfg)
    assert iss == D(2025, 8, 17) and exp == D(2028, 8, 16) and exp_c == "high"

    # 10. Expiry marker with date on the next line
    lines_split = ["Berlaku sampai:", "16 Agustus 2028"]
    iss, iss_c, exp, exp_c = extract_dates(lines_split, cfg)
    assert exp == D(2028, 8, 16) and exp_c == "high"

    # 11. Ignored birth date
    lines_birth = ["Tanggal lahir: 17 Agustus 1995", "Diterbitkan 10 Januari 2025"]
    iss, iss_c, exp, exp_c = extract_dates(lines_birth, cfg)
    assert iss == D(2025, 1, 10) and iss_c == "high"

    # 12. Expiry before issue rejected
    lines_reversed = ["Diterbitkan 20 Agustus 2025", "Berlaku s.d. 10 Januari 2024"]
    iss, iss_c, exp, exp_c = extract_dates(lines_reversed, cfg)
    assert exp is None and exp_c == "low"


def test_extract_number_logic():
    cfg = load_config(None)

    # 13. Number skipping Registrasi
    lines_reg = ["Nomor Registrasi: DJK.0701.2025"]
    assert extract_number(lines_reg, cfg) == "DJK.0701.2025"

    # 14. Number without digits rejected
    lines_no_digits = ["Nomor Sertifikat: ABC-DEF"]
    assert extract_number(lines_no_digits, cfg) is None
