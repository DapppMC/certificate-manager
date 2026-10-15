from __future__ import annotations

from pathlib import Path

import pymupdf

from certslice.errors import InputError
from certslice.models import PageText


def open_pdf(path: Path) -> pymupdf.Document:
    path = Path(path)
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise InputError(f"cannot open PDF {path}: {exc}") from exc
    if doc.needs_pass or doc.is_encrypted:
        raise InputError(f"PDF {path} is encrypted (password protected)")
    if doc.page_count == 0:
        raise InputError(f"PDF {path} has no pages")
    return doc


class LocalPageReader:
    def __init__(self, cfg: dict, ocr: bool = True):
        self.cfg = cfg
        self.ocr = ocr

    def read(self, page: pymupdf.Page) -> PageText:
        text = page.get_text("text")
        min_chars = self.cfg.get("reader", {}).get("min_text_chars", 20)
        if len(text.strip()) >= min_chars:
            return PageText(index=page.number + 1, text=text, source="text")
        else:
            return PageText(index=page.number + 1, text=text, source="none")
