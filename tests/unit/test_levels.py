"""Unit tests for SKP Level mapping and peran description normalization."""
from __future__ import annotations

import pytest

from certslice.levels import GLOBAL_SKP_LEVEL_TITLES, clean_skp_peran, resolve_skp_level


def test_global_skp_levels_defined():
    assert GLOBAL_SKP_LEVEL_TITLES[1] == "PELAKSANA MUDA"
    assert GLOBAL_SKP_LEVEL_TITLES[2] == "PELAKSANA MADYA"
    assert GLOBAL_SKP_LEVEL_TITLES[3] == "PELAKSANA UTAMA"
    assert GLOBAL_SKP_LEVEL_TITLES[4] == "ANALIS MUDA"
    assert GLOBAL_SKP_LEVEL_TITLES[5] == "ANALIS MADYA"
    assert GLOBAL_SKP_LEVEL_TITLES[6] == "ANALIS UTAMA"


def test_resolve_skp_level_by_int_and_string_digits():
    for num in range(1, 7):
        assert resolve_skp_level(num) == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(str(num)) == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(f"Level {num}") == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(f"LEVEL {num}") == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(f"LV {num}") == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(f"LV{num}") == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(f"KUALIFIKASI {num}") == GLOBAL_SKP_LEVEL_TITLES[num]
        assert resolve_skp_level(f"Jenjang Kualifikasi {num}") == GLOBAL_SKP_LEVEL_TITLES[num]


def test_resolve_skp_level_roman_numerals():
    assert resolve_skp_level("Level I") == "PELAKSANA MUDA"
    assert resolve_skp_level("Level II") == "PELAKSANA MADYA"
    assert resolve_skp_level("Level III") == "PELAKSANA UTAMA"
    assert resolve_skp_level("Level IV") == "ANALIS MUDA"
    assert resolve_skp_level("Level V") == "ANALIS MADYA"
    assert resolve_skp_level("Level VI") == "ANALIS UTAMA"


def test_resolve_skp_level_text_aliases():
    assert resolve_skp_level("Pelaksana Muda") == "PELAKSANA MUDA"
    assert resolve_skp_level("PELAKSANA MADYA") == "PELAKSANA MADYA"
    assert resolve_skp_level("pelaksana utama") == "PELAKSANA UTAMA"
    assert resolve_skp_level("Analis Muda") == "ANALIS MUDA"
    assert resolve_skp_level("ANALIS MADYA") == "ANALIS MADYA"
    assert resolve_skp_level("Analis Utama") == "ANALIS UTAMA"


def test_resolve_skp_level_from_full_text_context():
    text = "SERTIFIKAT KETENAGALISTRIKAN\nJenjang Kualifikasi 2\nBidang Pembangkitan"
    assert resolve_skp_level("", full_text=text) == "PELAKSANA MADYA"

    text2 = "Diberikan kepada peserta Level 3 untuk Pengoperasian Turbin"
    assert resolve_skp_level("", full_text=text2) == "PELAKSANA UTAMA"


def test_clean_skp_peran():
    # User's exact example
    raw_1 = "Melaksanakan pekerjaan supervisi pengoperasian unit PLTD besar"
    assert clean_skp_peran(raw_1) == "Mengoperasian unit PLTD besar"

    raw_2 = "Melaksanakan pekerjaan pengoperasian unit PLTD besar"
    assert clean_skp_peran(raw_2) == "Mengoperasian unit PLTD besar"

    raw_3 = "Melaksanakan pekerjaan pemeliharaan generator"
    assert clean_skp_peran(raw_3) == "Pemeliharaan generator"

    raw_4 = "Pengoperasian unit PLTD besar"
    assert clean_skp_peran(raw_4) == "Pengoperasian unit PLTD besar"

    raw_5 = "Operator Madya Sistem Bahan Bakar"
    assert clean_skp_peran(raw_5) == "Operator Madya Sistem Bahan Bakar"
