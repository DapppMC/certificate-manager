from pathlib import Path
import pytest
import pymupdf

from certslice.config import load_config
from certslice.errors import InputError
from certslice.reader import LocalPageReader, open_pdf

FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def test_reader_text_and_empty_page():
    cfg = load_config(None)
    reader = LocalPageReader(cfg, ocr=False)

    doc = pymupdf.open()
    # Page with text (> min_text_chars = 20)
    p1 = doc.new_page()
    p1.insert_text((50, 50), "This is a certificate page with enough text to exceed the threshold.")
    pt1 = reader.read(p1)
    assert pt1.source == "text"
    assert "certificate page" in pt1.text
    assert pt1.index == 1

    # Empty page (< 20 chars)
    p2 = doc.new_page()
    pt2 = reader.read(p2)
    assert pt2.source == "none"
    assert pt2.index == 2
    doc.close()


def test_open_pdf_corrupt():
    corrupt_pdf = FIX / "corrupt.pdf"
    with pytest.raises(InputError) as exc_info:
        open_pdf(corrupt_pdf)
    assert "cannot open pdf" in str(exc_info.value).lower()


def test_open_pdf_encrypted():
    encrypted_pdf = FIX / "encrypted.pdf"
    with pytest.raises(InputError) as exc_info:
        open_pdf(encrypted_pdf)
    assert "encrypted" in str(exc_info.value).lower()
