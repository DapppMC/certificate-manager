from pathlib import Path
import pytest

from certslice.config import load_aliases, load_config
from certslice.errors import InputError


def test_defaults_load():
    cfg = load_config(None)
    assert cfg["matching"]["accept_score"] == 92
    assert cfg["naming"]["max_path_chars"] == 250
    assert len(cfg["validity_rules"]) > 0


def test_user_file_partial_override(tmp_path):
    user_toml = tmp_path / "custom.toml"
    user_toml.write_text('[naming]\nalias_file = "a.csv"\n', encoding="utf-8")
    cfg = load_config(user_toml)
    assert cfg["naming"]["alias_file"] == "a.csv"
    assert cfg["naming"]["max_path_chars"] == 250
    assert cfg["matching"]["accept_score"] == 92


def test_user_validity_rules_replaces_list(tmp_path):
    user_toml = tmp_path / "custom.toml"
    user_toml.write_text(
        '[[validity_rules]]\nmatch = ["custom"]\nyears = 10\n',
        encoding="utf-8",
    )
    cfg = load_config(user_toml)
    assert len(cfg["validity_rules"]) == 1
    assert cfg["validity_rules"][0]["match"] == ["custom"]
    assert cfg["validity_rules"][0]["years"] == 10


def test_load_aliases_with_bom(tmp_path):
    csv_file = tmp_path / "aliases.csv"
    # Write with utf-8-sig (with BOM)
    content = "raw_title,short_title\nTeknisi Distribusi Tenaga Listrik,Teknisi Distribusi\n"
    csv_file.write_text(content, encoding="utf-8-sig")

    cfg = load_config(None)
    cfg["naming"]["alias_file"] = str(csv_file)
    aliases = load_aliases(cfg)
    assert "teknisi distribusi tenaga listrik" in aliases
    assert aliases["teknisi distribusi tenaga listrik"] == "Teknisi Distribusi"


def test_missing_user_file_raises_input_error(tmp_path):
    missing_path = tmp_path / "nonexistent.toml"
    with pytest.raises(InputError) as exc_info:
        load_config(missing_path)
    assert str(missing_path) in str(exc_info.value)
