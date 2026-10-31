from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pymupdf
import rapidfuzz

from certslice.errors import InputError
from certslice.models import PageText
from certslice.normalize import fold
from certslice.ocr import OcrEngine

logger = logging.getLogger(__name__)


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


def compute_image_cover(page: pymupdf.Page) -> float:
    area = page.rect.get_area()
    if area <= 0:
        return 0.0
    img_area = sum(
        (pymupdf.Rect(info["bbox"]) & page.rect).get_area()
        for info in page.get_image_info()
        if "bbox" in info
    )
    return min(1.0, img_area / area)


class LocalPageReader:
    def __init__(self, cfg: dict, ocr: bool = True):
        self.cfg = cfg
        self.ocr = ocr
        self._engine: OcrEngine | None = None

    @property
    def ocr_engine(self) -> OcrEngine:
        if self._engine is None:
            dpi = self.cfg.get("reader", {}).get("ocr_dpi", 200)
            self._engine = OcrEngine(dpi=dpi)
        return self._engine

    @property
    def ocr_seconds(self) -> list[float]:
        if self._engine is not None:
            return self._engine.seconds
        return []

    def read(self, page: pymupdf.Page) -> PageText:
        text = page.get_text("text")
        text_stripped_len = len(text.strip())
        min_chars = self.cfg.get("reader", {}).get("min_text_chars", 20)
        ocr_enabled = self.ocr and self.cfg.get("reader", {}).get("ocr_enabled", True)

        if text_stripped_len >= min_chars:
            sparse_below = self.cfg.get("reader", {}).get("ocr_sparse_below_chars", 400)
            sparse_cover = self.cfg.get("reader", {}).get("ocr_sparse_image_cover", 0.5)

            if ocr_enabled and text_stripped_len < sparse_below:
                cover = compute_image_cover(page)
                if cover >= sparse_cover:
                    try:
                        ocr_text = self.ocr_engine.page_text(page)
                        ocr_lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]
                        text_lines = [l.strip() for l in text.splitlines() if l.strip()]
                        folded_ocr = [fold(l) for l in ocr_lines]
                        merged_lines = list(ocr_lines)
                        for tl in text_lines:
                            ftl = fold(tl)
                            if all(rapidfuzz.fuzz.ratio(ftl, fol) < 90 for fol in folded_ocr):
                                merged_lines.append(tl)
                        return PageText(
                            index=page.number + 1,
                            text="\n".join(merged_lines),
                            source="mixed",
                        )
                    except Exception as exc:
                        logger.warning("OCR failed on page %d: %s", page.number + 1, exc)
                        return PageText(index=page.number + 1, text=text, source="text")

            return PageText(index=page.number + 1, text=text, source="text")

        # Text layer is too short (< min_chars)
        if ocr_enabled:
            try:
                ocr_text = self.ocr_engine.page_text(page)
                if len(ocr_text.strip()) >= min_chars:
                    return PageText(index=page.number + 1, text=ocr_text, source="ocr")
                else:
                    return PageText(index=page.number + 1, text=ocr_text, source="none")
            except Exception as exc:
                logger.warning("OCR failed on page %d: %s", page.number + 1, exc)
                return PageText(index=page.number + 1, text="", source="none")

        return PageText(index=page.number + 1, text=text, source="none")
