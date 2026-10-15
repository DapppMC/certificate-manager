from __future__ import annotations

import datetime as dt
import re

from certslice.normalize import fold

MONTH_MAP = {
    "januari": 1,
    "jan": 1,
    "january": 1,
    "februari": 2,
    "feb": 2,
    "february": 2,
    "maret": 3,
    "mar": 3,
    "march": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "jun": 6,
    "june": 6,
    "juli": 7,
    "jul": 7,
    "july": 7,
    "agustus": 8,
    "agu": 8,
    "agt": 8,
    "ags": 8,
    "aug": 8,
    "august": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "oct": 10,
    "october": 10,
    "november": 11,
    "nov": 11,
    "nopember": 11,
    "desember": 12,
    "des": 12,
    "dec": 12,
    "december": 12,
}

MONTHS_REGEX = "|".join(sorted(MONTH_MAP.keys(), key=len, reverse=True))
DAY_REGEX = r"(\d{1,2}(?:st|nd|rd|th)?)"


def _parse_day(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


DATE_PATTERNS = [
    # 1. Day range: d1 - d2 month yyyy
    (
        re.compile(
            r"\b" + DAY_REGEX + r"\s*[\-\u2013\u2014]\s*" + DAY_REGEX + r"\s+(" + MONTHS_REGEX + r")\s+(\d{4})\b"
        ),
        lambda m: (_parse_day(m.group(2)), MONTH_MAP[m.group(3)], int(m.group(4))),
    ),
    # 2. Day range with month first: month d1 - d2, yyyy
    (
        re.compile(
            r"\b(" + MONTHS_REGEX + r")\s+" + DAY_REGEX + r"\s*[\-\u2013\u2014]\s*" + DAY_REGEX + r",?\s+(\d{4})\b"
        ),
        lambda m: (_parse_day(m.group(3)), MONTH_MAP[m.group(1)], int(m.group(4))),
    ),
    # 3. d month yyyy
    (
        re.compile(r"\b" + DAY_REGEX + r"\s+(" + MONTHS_REGEX + r")\s+(\d{4})\b"),
        lambda m: (_parse_day(m.group(1)), MONTH_MAP[m.group(2)], int(m.group(3))),
    ),
    # 4. month d, yyyy
    (
        re.compile(r"\b(" + MONTHS_REGEX + r")\s+" + DAY_REGEX + r",?\s+(\d{4})\b"),
        lambda m: (_parse_day(m.group(2)), MONTH_MAP[m.group(1)], int(m.group(3))),
    ),
    # 5. yyyy-mm-dd
    (
        re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),
        lambda m: (int(m.group(3)), int(m.group(2)), int(m.group(1))),
    ),
    # 6. dd/mm/yyyy
    (
        re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"),
        lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3))),
    ),
]


def find_dates(line: str) -> list[tuple[int, dt.date]]:
    folded = fold(line)
    matched_spans: list[tuple[int, int]] = []
    found: list[tuple[int, dt.date]] = []

    for pattern, extractor in DATE_PATTERNS:
        for match in pattern.finditer(folded):
            start, end = match.start(), match.end()
            if any(s < end and match.start() < e for s, e in matched_spans):
                continue
            try:
                day, month, year = extractor(match)
                if not (1990 <= year <= 2100):
                    continue
                d = dt.date(year, month, day)
                matched_spans.append((start, end))
                found.append((start, d))
            except (ValueError, KeyError):
                continue

    found.sort(key=lambda x: x[0])
    return found


def extract_number(lines: list[str], cfg: dict) -> str | None:
    if not lines:
        return None

    num_keys = cfg.get("fields", {}).get(
        "number_keywords", ["nomor", "no", "number", "nr", "registrasi", "reg"]
    )
    sorted_keys = sorted(num_keys, key=len, reverse=True)
    keys_pat = "|".join(re.escape(k) for k in sorted_keys)
    keyword_regex = re.compile(r"\b(?:" + keys_pat + r")\b", re.IGNORECASE)

    skip_words = set(k.lower() for k in num_keys) | {"sertifikat", "certificate"}

    # First page lines only (lines passed are from first page)
    for line in lines:
        for m in keyword_regex.finditer(line):
            rest = line[m.end():]
            while True:
                rest = re.sub(r"^[\s.:#]+", "", rest)
                m_skip = re.match(r"^\b([a-z]+)\b", rest, re.IGNORECASE)
                if m_skip and m_skip.group(1).lower() in skip_words:
                    rest = rest[m_skip.end():]
                else:
                    break

            m_token = re.match(r"^([A-Za-z0-9][A-Za-z0-9./-]{3,})", rest)
            if m_token:
                token = m_token.group(1).rstrip(".-/")
                if any(c.isdigit() for c in token):
                    return token

    return None


def _phrase_match(phrase: str, text: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(fold(phrase)) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def extract_dates(lines: list[str], cfg: dict) -> tuple[dt.date | None, str, dt.date | None, str]:
    f_cfg = cfg.get("fields", {})
    ignore_markers = f_cfg.get("ignore_date_markers", [])
    expiry_markers = f_cfg.get("expiry_markers", [])
    issue_markers = f_cfg.get("issue_markers", [])
    range_words = f_cfg.get("range_words", [])

    issue_date: dt.date | None = None
    issue_conf: str = "low"
    expiry_date: dt.date | None = None
    expiry_conf: str = "low"
    unmarked_dates: list[dt.date] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        folded_line = fold(line)

        # Skip line with phrase match of ignore_date_markers
        if any(_phrase_match(marker, folded_line) for marker in ignore_markers):
            i += 1
            continue

        dates = [d for _, d in find_dates(line)]

        # Check expiry_markers (folded substring) or range_words with 2 dates
        is_expiry_line = any(fold(marker) in folded_line for marker in expiry_markers)
        if not is_expiry_line and len(dates) >= 2:
            if any(_phrase_match(rw, folded_line) or fold(rw) in folded_line for rw in range_words):
                is_expiry_line = True

        if is_expiry_line:
            if not dates and i + 1 < len(lines):
                next_dates = [d for _, d in find_dates(lines[i + 1])]
                if next_dates:
                    dates = next_dates
                    i += 1
            if len(dates) >= 2:
                if issue_date is None:
                    issue_date = dates[0]
                    issue_conf = "medium"
                expiry_date = dates[-1]
                expiry_conf = "high"
            elif len(dates) == 1:
                expiry_date = dates[0]
                expiry_conf = "high"
            i += 1
            continue

        # Check issue_markers
        is_issue_line = any(fold(marker) in folded_line for marker in issue_markers)
        if is_issue_line and dates:
            if issue_date is None or issue_conf != "high":
                issue_date = dates[0]
                issue_conf = "high"
        elif dates:
            unmarked_dates.extend(dates)

        i += 1

    if issue_date is None:
        if unmarked_dates:
            issue_date = unmarked_dates[0]
            issue_conf = "medium" if len(unmarked_dates) == 1 else "low"
        else:
            issue_date = None
            issue_conf = "low"

    if expiry_date is not None:
        if issue_date is not None and expiry_date <= issue_date:
            expiry_date = None
            expiry_conf = "low"

    if issue_date is None:
        issue_conf = "low"

    return issue_date, issue_conf, expiry_date, expiry_conf


def template_lines(pages_lines: list[list[str]], cfg: dict) -> set[str]:
    n_pages = len(pages_lines)
    if n_pages < 3:
        return set()

    share_threshold = cfg.get("fields", {}).get("template_line_share", 0.6)
    min_pages = share_threshold * n_pages

    counts: dict[str, int] = {}
    for page_lines in pages_lines:
        seen_on_page: set[str] = set()
        for line in page_lines:
            collapsed = " ".join(fold(line).split())
            if collapsed:
                seen_on_page.add(collapsed)
        for line_key in seen_on_page:
            counts[line_key] = counts.get(line_key, 0) + 1

    return {k for k, count in counts.items() if count >= min_pages}


def extract_title(
    first_lines: list[str],
    template: set[str],
    group_is_bundle: bool,
    n_pages: int,
    cfg: dict,
) -> tuple[str | None, str]:
    f_cfg = cfg.get("fields", {})
    title_cues = sorted(f_cfg.get("title_cues", []), key=len, reverse=True)
    title_generic = set(fold(g) for g in f_cfg.get("title_generic", []))
    title_keywords = f_cfg.get("title_keywords", [])

    found_title: str | None = None
    confidence: str = "low"

    # Search for cues
    for i, line in enumerate(first_lines):
        folded_line = fold(line)
        matched_cue = None
        for cue in title_cues:
            if fold(cue) in folded_line:
                matched_cue = cue
                break

        if matched_cue is None:
            continue

        # Found a line with a cue
        cue_folded = fold(matched_cue)
        m = re.search(re.escape(matched_cue), line, re.IGNORECASE)
        if m:
            unstripped_rest = line[m.end():]
        else:
            pos = folded_line.find(cue_folded)
            unstripped_rest = line[pos + len(cue_folded):]

        stripped_rest = unstripped_rest.strip(" \t\r\n:\"'-")

        if (
            len(stripped_rest) >= 4
            and fold(stripped_rest) not in title_generic
            and not unstripped_rest.rstrip().endswith(":")
        ):
            found_title = stripped_rest
        else:
            for j in range(i + 1, len(first_lines)):
                nxt_line = first_lines[j]
                stripped_nxt = nxt_line.strip(" \t\r\n\"'-")
                if (
                    len(stripped_nxt) >= 4
                    and not nxt_line.rstrip().endswith(":")
                    and not any(fold(c) in fold(nxt_line) for c in title_cues)
                    and fold(stripped_nxt) not in title_generic
                ):
                    found_title = stripped_nxt
                    break

        if found_title is not None:
            is_tmpl = " ".join(fold(found_title).split()) in template
            if is_tmpl or n_pages < 3 or group_is_bundle:
                confidence = "high"
            else:
                confidence = "medium"
            return found_title, confidence

    # Without a cue: check title_keywords
    for line in first_lines:
        folded_line = fold(line)
        stripped = line.strip(" \t\r\n\"'-")
        if any(_phrase_match(kw, folded_line) for kw in title_keywords):
            if fold(stripped) not in title_generic and len(stripped) >= 4:
                found_title = stripped
                is_tmpl = " ".join(fold(found_title).split()) in template
                confidence = "medium" if is_tmpl else "low"
                return found_title, confidence

    # Generic fallback
    for line in first_lines:
        stripped = line.strip(" \t\r\n\"'-")
        if fold(stripped) in title_generic:
            return stripped, "low"

    return None, "low"


def extract_issuer(
    group_lines: list[str],
    template: set[str],
    cfg: dict,
) -> tuple[str | None, str]:
    f_cfg = cfg.get("fields", {})
    issuer_keywords = f_cfg.get("issuer_keywords", [])
    issuer_exclude = f_cfg.get("issuer_exclude", [])
    issuer_join_next = set(fold(x) for x in f_cfg.get("issuer_join_next", []))

    for kw in issuer_keywords:
        kw_folded = fold(kw)
        for idx, line in enumerate(group_lines):
            line_folded = fold(line)
            line_folded_spaced = line_folded + " "
            if kw_folded in line_folded_spaced:
                if any(fold(ex) in line_folded for ex in issuer_exclude):
                    continue
                issuer = line.strip()
                if fold(issuer) in issuer_join_next and idx + 1 < len(group_lines):
                    issuer = f"{issuer} {group_lines[idx + 1].strip()}"

                is_tmpl = (
                    " ".join(fold(line).split()) in template
                    or " ".join(fold(issuer).split()) in template
                )
                conf = "high" if is_tmpl else "medium"
                return issuer, conf

    return None, "low"


def doc_type(title: str | None, group_lines: list[str], cfg: dict) -> tuple[str, str]:
    f_cfg = cfg.get("fields", {})
    training_words = f_cfg.get("training_words", [])
    certification_words = f_cfg.get("certification_words", [])

    title_text = fold(title or "")
    group_text = " ".join(fold(line) for line in group_lines)

    if any(_phrase_match(w, title_text) or _phrase_match(w, group_text) for w in training_words):
        return "Pelatihan", "medium"
    if any(_phrase_match(w, title_text) or _phrase_match(w, group_text) for w in certification_words):
        return "Sertifikasi", "medium"
    return "Sertifikasi", "low"


def renewal_number(group_lines: list[str], cfg: dict) -> int | None:
    pattern = cfg.get("fields", {}).get("renewal_pattern", r"perpanjangan(?:\s+ke)?[\s\-]*(\d+)")
    group_text = " ".join(fold(line) for line in group_lines)
    m = re.search(pattern, group_text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None

