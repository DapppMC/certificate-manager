"""Unit tests for MSO765 workbook generation ('For NPS 0726.xlsx') and NID validation."""
from __future__ import annotations

from pathlib import Path
import pytest
import openpyxl

from certslice.mso import (
    is_valid_mso_nid,
    is_organic_2letter_nid,
    parse_date_to_int,
    calculate_exp_date,
    build_for_nps_workbook,
    extract_batch_name,
)


def test_is_valid_mso_nid():
    # Organic PLN employees (2 letter suffix)
    assert is_valid_mso_nid("9016033PL") is True
    assert is_valid_mso_nid("8310238RB") is True
    assert is_valid_mso_nid("8512525ID") is True
    assert is_valid_mso_nid("8914146KP") is True
    assert is_valid_mso_nid("7909020CC") is True

    # Subsidiary / Penugasan PLN employees (3-letter suffix like APN, SPA, NAS)
    assert is_valid_mso_nid("9620028APN") is True
    assert is_valid_mso_nid("9317048SPA") is True
    assert is_valid_mso_nid("9825010NAS") is True
    assert is_valid_mso_nid("8919011APN") is True

    # Pure numeric employee ID (>= 5 digits)
    assert is_valid_mso_nid("10240212") is True

    # Placeholders / non-numeric (invalid)
    assert is_valid_mso_nid("BMT") is False
    assert is_valid_mso_nid("MKP") is False
    assert is_valid_mso_nid("-") is False
    assert is_valid_mso_nid("") is False
    assert is_valid_mso_nid(None) is False


def test_is_organic_2letter_nid():
    assert is_organic_2letter_nid("9016033PL") is True
    assert is_organic_2letter_nid("8310238RB") is True
    assert is_organic_2letter_nid("9620028APN") is False
    assert is_organic_2letter_nid("BMT") is False


def test_extract_batch_name():
    assert extract_batch_name("127") == "127"
    assert extract_batch_name("Hasil_127_21-Oct-2026_11.00") == "127"
    assert extract_batch_name("Hasil_PDF_Mentah/127") == "127"
    assert extract_batch_name("127_2") == "127"
    assert extract_batch_name("060 & 062") == "060 & 062"


def test_date_parsing_and_exp_calculation():
    assert parse_date_to_int("2026-05-22") == 20260522
    assert parse_date_to_int("22-05-2026") == 20260522
    assert parse_date_to_int(20260522) == 20260522

    # DJK: +3 years
    assert calculate_exp_date(20260211, validity_years=3) == 20290211
    # K3: +5 years
    assert calculate_exp_date(20260509, validity_years=5) == 20310509


def test_build_for_nps_workbook_subfolder_sheets(tmp_path: Path):
    items = [
        {
            "nid": "9016033PL",  # Organic PLN NID (2-letter suffix) -> goes into primary '127' sheet
            "nama": "HAMSAH K. SUNDUSEN",
            "level": "Level 3",
            "judul": "Pelaksana Senior Pemeliharaan",
            "resource_code": "BAKL",
            "institute_code": "D002",
            "effective_date": "2026-08-19",
            "exp_date": "2029-08-19",
            "subfolder_name": "127",
        },
        {
            "nid": "9620028APN",  # Non-organic / Subsidiary NID (3-letter suffix) -> goes into '127 (Non-Organik)'
            "nama": "FALDI LALENOH",
            "level": "Level 4",
            "judul": "Operator control room PLTU",
            "resource_code": "AA02",
            "institute_code": "D002",
            "effective_date": "2026-08-19",
            "exp_date": "2029-08-19",
            "subfolder_name": "127",
        },
        {
            "nid": "BMT",  # Invalid placeholder NID
            "nama": "PESERTA BMT",
            "level": "Level 3",
            "judul": "Peralatan Turbin",
            "resource_code": "A117",
            "subfolder_name": "127",
        },
        {
            "nid": "9320030APN",  # Valid NID but missing Resource Code
            "nama": "IMRAN",
            "level": "Level 3",
            "judul": "Skill Belum Terdaftar",
            "resource_code": "",
            "subfolder_name": "127",
        },
        {
            "nid": "8410288KP",
            "nama": "DANANG",
            "level": None,  # Non-level certificate! Must be excluded!
            "judul": "General Seminar",
            "resource_code": "",
            "subfolder_name": "127",
        },
    ]

    out_file = tmp_path / "For NPS 0726.xlsx"
    build_for_nps_workbook(items, out_file)
    assert out_file.exists()

    wb = openpyxl.load_workbook(out_file, data_only=True)
    
    # Primary sheet for organic 2-letter NID
    assert "127" in wb.sheetnames
    # Separate sheet for bukan 2 huruf (3-letter, numeric, etc.)
    assert "127 (Bukan 2 Huruf)" in wb.sheetnames
    # Empty K3 & BNSP and DJK sheets must NOT be created
    assert "K3 & BNSP" not in wb.sheetnames
    assert "DJK" not in wb.sheetnames
    
    # Review sheets created only because items exist
    assert "Tidak ada NID " in wb.sheetnames
    assert "Tidak ada Resource Code " in wb.sheetnames

    # Check 127 sheet rows (organic 2-letter NID)
    s127_rows = list(wb["127"].iter_rows(values_only=True))
    assert len(s127_rows) == 2  # Header + 1 organic row
    assert s127_rows[1][0] == "9016033PL"
    assert s127_rows[1][2] == "BAKL"
    assert s127_rows[1][4] == 20260819
    assert s127_rows[1][5] == 20260819
    assert s127_rows[1][6] == 20290819
    assert s127_rows[1][8] == "D002"

    # Check 127 (Bukan 2 Huruf) sheet rows
    s127_non_org = list(wb["127 (Bukan 2 Huruf)"].iter_rows(values_only=True))
    assert len(s127_non_org) == 2  # Header + 1 non-organic row
    assert s127_non_org[1][0] == "9620028APN"
    assert s127_non_org[1][2] == "AA02"
    assert s127_non_org[1][4] == 20260819
    assert s127_non_org[1][6] == 20290819

    # Check Tidak ada NID sheet: pure NID in cell, never 'NID tidak standar (...)'
    no_nid_rows = list(wb["Tidak ada NID "].iter_rows(values_only=True))
    assert len(no_nid_rows) == 2  # Header + 1 row
    assert no_nid_rows[1][0] == "PESERTA BMT"
    assert no_nid_rows[1][1] == "BMT"  # Pure value!
    assert "NID tidak standar" not in str(no_nid_rows[1][1])

    # Check Tidak ada Resource Code sheet
    no_rc_rows = list(wb["Tidak ada Resource Code "].iter_rows(values_only=True))
    assert len(no_rc_rows) == 2
    assert no_rc_rows[1][3] == "9320030APN"

    wb.close()
