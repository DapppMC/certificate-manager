from __future__ import annotations

import calendar
import datetime as dt
import re

from certslice.normalize import fold


def add_years(d: dt.date, years: int) -> dt.date:
    y = d.year + years
    return dt.date(y, d.month, min(d.day, calendar.monthrange(y, d.month)[1]))


def _phrase_match(phrase: str, text: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(fold(phrase)) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def apply_rules(
    issue: dt.date | None,
    expiry: dt.date | None,
    expiry_conf: str,
    group_text_folded: str,
    cfg: dict,
) -> tuple[dt.date | None, str, str | None]:
    if expiry is not None:
        return expiry, expiry_conf, "printed"

    # Printed duration
    if issue is not None:
        duration_pattern = cfg.get("fields", {}).get(
            "duration_pattern",
            r"berlaku\s+(?:untuk|selama)\s+(\d+)\s*(?:\([a-z ]+\)\s*)?tahun|valid\s+for\s+(\d+)\s*(?:\([a-z ]+\)\s*)?years?",
        )
        m = re.search(duration_pattern, group_text_folded, re.IGNORECASE)
        if m:
            years = int(m.group(1) or m.group(2))
            return add_years(issue, years), "high", f"duration:{years}y"

    # Validity rules
    rules = cfg.get("validity_rules", [])
    for rule in rules:
        matches = rule.get("match", [])
        if any(_phrase_match(phrase, group_text_folded) for phrase in matches):
            years = rule.get("years", 0)
            if years == 0:
                return None, "medium", "rule:none"
            elif years > 0:
                if issue is not None:
                    return add_years(issue, years), "medium", f"rule:{years}y"
                else:
                    return None, "low", None

    return None, "low", None
