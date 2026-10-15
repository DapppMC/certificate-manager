from __future__ import annotations

import re
import unicodedata

from certslice.config import load_config

_TITLES = frozenset(load_config(None)["matching"]["titles"])


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def name_tokens(s: str, titles=_TITLES) -> list[str]:
    s = fold(s).replace(".", "").replace("'", "")
    tokens = re.sub(r"[^a-z0-9]+", " ", s).split()
    return [t for t in tokens if t not in titles]


def normalize_name(s: str) -> str:
    return " ".join(name_tokens(s))
