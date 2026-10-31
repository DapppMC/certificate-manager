"""Unit tests for Resource Code lookup and catalog indexing."""
from __future__ import annotations

import pytest

from certslice.resource_codes import lookup_resource_code, get_resource_code_catalog
from certslice.levels import GLOBAL_SKP_LEVEL_TITLES, resolve_skp_level, get_numeric_level


def test_level_extension_up_to_level_9():
    assert len(GLOBAL_SKP_LEVEL_TITLES) == 9
    assert GLOBAL_SKP_LEVEL_TITLES[7] == "AHLI MUDA"
    assert GLOBAL_SKP_LEVEL_TITLES[8] == "AHLI MADYA"
    assert GLOBAL_SKP_LEVEL_TITLES[9] == "AHLI UTAMA"

    assert resolve_skp_level("Level 7") == "AHLI MUDA"
    assert resolve_skp_level("Level 8") == "AHLI MADYA"
    assert resolve_skp_level("Level 9") == "AHLI UTAMA"
    assert resolve_skp_level("Level VII") == "AHLI MUDA"
    assert resolve_skp_level("Level VIII") == "AHLI MADYA"
    assert resolve_skp_level("Level IX") == "AHLI UTAMA"
    assert resolve_skp_level("Ahli Muda") == "AHLI MUDA"
    assert resolve_skp_level("Ahli Madya") == "AHLI MADYA"
    assert resolve_skp_level("Ahli Utama") == "AHLI UTAMA"

    assert get_numeric_level("Pelaksana Muda") == 1.0
    assert get_numeric_level("Analis Muda") == 4.0
    assert get_numeric_level("Ahli Muda") == 7.0
    assert get_numeric_level("Ahli Utama") == 9.0


def test_resource_code_catalog_loading():
    catalog = get_resource_code_catalog()
    catalog.load()
    assert len(catalog.code_entries) > 1000
    assert "A100" in catalog.code_entries
    assert "AA02" in catalog.code_entries
    assert "PK21" in catalog.code_entries
    assert "E028" in catalog.code_entries


def test_resource_code_lookup_exact_token():
    res = lookup_resource_code("File with code A100 inside filename")
    assert res["code"] == "A100"
    assert res["matched_by"] == "exact_code_token"


def test_resource_code_lookup_explicit_override():
    res = lookup_resource_code("Penanggung jawab SMK2", level="Level 4")
    assert res["code"] == "PK21"
    assert res["institute"] == "D001"


def test_resource_code_lookup_fuzzy():
    res = lookup_resource_code("Pengoperasian unit PLTU Batubara", level="Analis Utama")
    assert res["code"] in ["A100", "H16A"]
    assert res["confidence"] > 0.6
