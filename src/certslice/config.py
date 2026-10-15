from __future__ import annotations

import csv
from pathlib import Path
import tomllib
import unicodedata

from certslice.errors import InputError


def fold_text(s: str) -> str:
    """NFKD, drop combining marks, casefold(), collapse whitespace, strip."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    return " ".join(s.split()).strip()


def load_config(path: str | Path | None = None) -> dict:
    default_path = Path(__file__).with_name("default_config.toml")
    try:
        cfg = tomllib.loads(default_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise InputError(f"Failed to load default configuration: {e}")

    if path is None:
        cfg["_dir"] = default_path.parent.resolve()
        return cfg

    user_path = Path(path)
    if not user_path.exists():
        raise InputError(f"Config file not found: {user_path}")

    try:
        user_cfg = tomllib.loads(user_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise InputError(f"Invalid config file {user_path}: {e}")

    for key, val in user_cfg.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(val, dict):
            cfg[key].update(val)
        else:
            cfg[key] = val

    cfg["_dir"] = user_path.parent.resolve()
    return cfg


def load_aliases(cfg: dict) -> dict[str, str]:
    alias_file = cfg.get("naming", {}).get("alias_file", "")
    if not alias_file:
        return {}

    alias_path = Path(alias_file)
    if not alias_path.is_absolute():
        alias_path = Path(cfg["_dir"]) / alias_path

    if not alias_path.exists():
        raise InputError(f"Alias file not found: {alias_path}")

    aliases: dict[str, str] = {}
    with open(alias_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("raw_title")
            short = row.get("short_title")
            if raw is not None and short is not None:
                folded_raw = fold_text(raw)
                short_clean = short.strip()
                if folded_raw and short_clean:
                    aliases[folded_raw] = short_clean

    return aliases
