from __future__ import annotations

from pathlib import Path
import pymupdf
import pytest

from certslice.config import load_config
from certslice.ocr import OcrEngine
from certslice.reader import LocalPageReader

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def test_reader_ocr_disabled_on_image_page() -> None:
    cfg = load_config()
    doc = pymupdf.open(FIXTURES_DIR / "bundle_scan.pdf")
    page = doc[0]
    reader = LocalPageReader(cfg, ocr=False)
    pt = reader.read(page)
    doc.close()

    assert pt.source == "none"
    assert pt.index == 1


def test_ocr_engine_with_fake_callable() -> None:
    engine = OcrEngine(dpi=200)

    class FakeResult:
        txts = ("A", "B")

    engine._engine = lambda img: FakeResult()

    doc = pymupdf.open(FIXTURES_DIR / "bundle_scan.pdf")
    page = doc[0]
    text = engine.page_text(page)
    doc.close()

    assert text == "A\nB"
    assert len(engine.seconds) == 1
    assert engine.seconds[0] >= 0


def test_reader_ocr_enabled_uses_ocr_fallback() -> None:
    cfg = load_config()
    doc = pymupdf.open(FIXTURES_DIR / "bundle_scan.pdf")
    page = doc[0]

    reader = LocalPageReader(cfg, ocr=True)

    class FakeEngine:
        def __init__(self) -> None:
            self.seconds: list[float] = [0.123]

        def page_text(self, p) -> str:
            return "A very long recognized text exceeding minimum characters threshold"

    reader._engine = FakeEngine()  # type: ignore[assignment]
    pt = reader.read(page)
    doc.close()

    assert pt.source == "ocr"
    assert reader.ocr_seconds == [0.123]


def test_reader_ocr_exception_gives_source_none() -> None:
    cfg = load_config()
    doc = pymupdf.open(FIXTURES_DIR / "bundle_scan.pdf")
    page = doc[0]

    reader = LocalPageReader(cfg, ocr=True)

    class BrokenEngine:
        def __init__(self) -> None:
            self.seconds: list[float] = []

        def page_text(self, p) -> str:
            raise RuntimeError("Engine crashed")

    reader._engine = BrokenEngine()  # type: ignore[assignment]
    pt = reader.read(page)
    doc.close()

    assert pt.source == "none"
    assert pt.text == ""


def test_reader_sparse_page_gives_source_mixed() -> None:
    cfg = load_config()
    doc = pymupdf.open(FIXTURES_DIR / "bundle_k3_like.pdf")
    page = doc[0]

    reader = LocalPageReader(cfg, ocr=True)

    class FakeEngine:
        def __init__(self) -> None:
            self.seconds: list[float] = [0.456]

        def page_text(self, p) -> str:
            return "CERTIFICATE\nTHIS IS A NEW OCR LINE"

    reader._engine = FakeEngine()  # type: ignore[assignment]
    pt = reader.read(page)
    doc.close()

    assert pt.source == "mixed"
    assert "THIS IS A NEW OCR LINE" in pt.text
