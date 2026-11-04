"""Global Certificate Manager level-to-job mappings and helper functions."""
from __future__ import annotations

import re

GLOBAL_SKP_LEVEL_TITLES: dict[int, str] = {
    1: "PELAKSANA MUDA",
    2: "PELAKSANA MADYA",
    3: "PELAKSANA UTAMA",
    4: "ANALIS MUDA",
    5: "ANALIS MADYA",
    6: "ANALIS UTAMA",
    7: "AHLI MUDA",
    8: "AHLI MADYA",
    9: "AHLI UTAMA",
}

_ROMAN_MAP = {
    "IX": 9,
    "VIII": 8,
    "VII": 7,
    "VI": 6,
    "IV": 4,
    "V": 5,
    "III": 3,
    "II": 2,
    "I": 1,
}


def get_numeric_level(raw_level: str | int | float | None) -> float | None:
    """Extract numeric level 1.0 - 9.0 from level string, integer, or title."""
    if raw_level is None:
        return None
    if isinstance(raw_level, (int, float)):
        val = float(raw_level)
        if 1.0 <= val <= 9.0:
            return val
    s = str(raw_level).strip().upper()
    # Check standard title match
    for num, title in GLOBAL_SKP_LEVEL_TITLES.items():
        if title in s:
            return float(num)
    # Check Roman numerals
    for rom, num in _ROMAN_MAP.items():
        if re.search(rf"\b(?:LEVEL|LV|JENJANG|KUALIFIKASI)?\s*[\.:\-]?\s*{rom}\b", s):
            return float(num)
    # Check digits 1-9
    m = re.search(r"(?:LEVEL|LV|JENJANG|KUALIFIKASI|\b)\s*[\.:\-]?\s*([1-9])\b", s)
    if m:
        return float(m.group(1))
    return None


def resolve_skp_level(raw_level: str | int | None, full_text: str = "") -> str:
    """Resolve SKP Level number or text into standardized global title:
    LEVEL 1 : PELAKSANA MUDA
    LEVEL 2 : PELAKSANA MADYA
    LEVEL 3 : PELAKSANA UTAMA
    LEVEL 4 : ANALIS MUDA
    LEVEL 5 : ANALIS MADYA
    LEVEL 6 : ANALIS UTAMA
    LEVEL 7 : AHLI MUDA
    LEVEL 8 : AHLI MADYA
    LEVEL 9 : AHLI UTAMA
    """
    if isinstance(raw_level, int) and 1 <= raw_level <= 9:
        return GLOBAL_SKP_LEVEL_TITLES[raw_level]

    s = (str(raw_level) if raw_level is not None else "").strip()
    s_upper = s.upper()

    # 1. Exact or partial match with the standard titles
    for lvl_num, title in GLOBAL_SKP_LEVEL_TITLES.items():
        if title in s_upper:
            return title

    # 2. Check for level number digit (1-9) in raw_level
    m_num = re.search(r"(?:level|kualifikasi|jenjang|lv|\b)\s*[\.:\-]?\s*([1-9])\b", s, re.IGNORECASE)
    if m_num:
        val = int(m_num.group(1))
        if 1 <= val <= 9:
            return GLOBAL_SKP_LEVEL_TITLES[val]

    # 3. Check for Roman numerals in raw_level
    for rom, num in _ROMAN_MAP.items():
        if re.search(rf"\b(?:level|kualifikasi|jenjang|lv)?\s*[\.:\-]?\s*{rom}\b", s, re.IGNORECASE):
            return GLOBAL_SKP_LEVEL_TITLES[num]

    # 4. Keyword heuristic on role text (e.g. "Ahli Muda", "Pelaksana Madya", "Analis Muda")
    if "AHLI" in s_upper:
        if "MUDA" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[7]
        if "MADYA" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[8]
        if "UTAMA" in s_upper or "SENIOR" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[9]
    elif "ANALIS" in s_upper:
        if "MUDA" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[4]
        if "MADYA" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[5]
        if "UTAMA" in s_upper or "SENIOR" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[6]
    else:
        if "MUDA" in s_upper or "JUNIOR" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[1]
        if "MADYA" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[2]
        if "UTAMA" in s_upper or "SENIOR" in s_upper:
            return GLOBAL_SKP_LEVEL_TITLES[3]

    # 5. If raw_level didn't match, check full_text
    if full_text:
        ft_upper = full_text.upper()
        for lvl_num, title in GLOBAL_SKP_LEVEL_TITLES.items():
            if title in ft_upper:
                return title

        m_ft = re.search(r"(?:level|kualifikasi|jenjang|lv)\s*[\.:\-]?\s*([1-9])\b", full_text, re.IGNORECASE)
        if m_ft:
            val = int(m_ft.group(1))
            if 1 <= val <= 9:
                return GLOBAL_SKP_LEVEL_TITLES[val]

        for rom, num in _ROMAN_MAP.items():
            if re.search(rf"(?:level|kualifikasi|jenjang|lv)\s*[\.:\-]?\s*{rom}\b", full_text, re.IGNORECASE):
                return GLOBAL_SKP_LEVEL_TITLES[num]

    return s


def clean_skp_peran(peran: str) -> str:
    """Clean and standardize SKP role/position description.
    Example: 'Melaksanakan pekerjaan supervisi pengoperasian unit PLTD besar'
    -> 'Mengoperasian unit PLTD besar'.
    """
    if not peran:
        return ""
    p = peran.strip(" :-_.\t\r\n")

    # Match user's specific example:
    # "Melaksanakan pekerjaan supervisi pengoperasian unit PLTD besar" -> "Mengoperasian unit PLTD besar"
    p = re.sub(
        r"^Melaksanakan\s+pekerjaan\s+supervisi\s+(?:pengoperasian|mengoperasikan|mengoperasian)",
        "Mengoperasian",
        p,
        flags=re.IGNORECASE,
    )
    p = re.sub(
        r"^Melaksanakan\s+pekerjaan\s+supervisi\s+",
        "Supervisi ",
        p,
        flags=re.IGNORECASE,
    )
    p = re.sub(
        r"^Melaksanakan\s+pekerjaan\s+(?:pengoperasian|mengoperasikan|mengoperasian)",
        "Mengoperasian",
        p,
        flags=re.IGNORECASE,
    )
    p = re.sub(
        r"^Melaksanakan\s+pekerjaan\s+(?:pemeliharaan|memelihara)",
        "Pemeliharaan",
        p,
        flags=re.IGNORECASE,
    )
    p = re.sub(
        r"^Melaksanakan\s+pekerjaan\s+",
        "",
        p,
        flags=re.IGNORECASE,
    )
    p = re.sub(
        r"^Melaksanakan\s+",
        "",
        p,
        flags=re.IGNORECASE,
    )
    # Strip unwanted renewal prefixes
    p = re.sub(r"^(?:Hasil\s+)?Perpanjangan\s+(?:Lisensi\s+|Sertifikasi\s+|Sertifikat\s+)?", "", p, flags=re.IGNORECASE)
    # Expand abbreviations to full names
    p = re.sub(r"\bAK3\s*Umum\b", "Ahli K3 Umum", p, flags=re.IGNORECASE)
    p = re.sub(r"\bAK3\b", "Ahli K3", p, flags=re.IGNORECASE)
    p = re.sub(r"\bOP\s+Batu\s*Bara\b", "Operator Batu Bara", p, flags=re.IGNORECASE)
    p = re.sub(r"\bOP\b", "Operator", p, flags=re.IGNORECASE)
    return p.strip(" :-_.\t\r\n")
