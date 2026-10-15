#!/usr/bin/env python3
"""OCR spike script using RapidOCR."""
import argparse
from pathlib import Path
import sys
import time

import pymupdf
from rapidocr import RapidOCR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR spike script")
    parser.add_argument("--pdf", required=True, type=Path, help="Path to PDF file")
    parser.add_argument(
        "--pages",
        default="1",
        help="Comma-separated 1-based page numbers to OCR (default: 1)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="DPI for rendering PDF pages (default: 200)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        print("RESULT: FAIL")
        sys.exit(1)

    t_init0 = time.perf_counter()
    engine = RapidOCR()
    t_init = time.perf_counter() - t_init0
    print(f"engine_init_seconds={t_init:.3f}")

    doc = pymupdf.open(pdf_path)
    page_numbers = [int(p.strip()) for p in args.pages.split(",") if p.strip()]

    out_dir = Path("spikes/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_passed = True
    if not page_numbers:
        all_passed = False

    for p in page_numbers:
        if p < 1 or p > len(doc):
            print(f"Error: Page {p} out of range (1..{len(doc)})", file=sys.stderr)
            all_passed = False
            continue

        page = doc[p - 1]
        t_page0 = time.perf_counter()
        png = page.get_pixmap(dpi=args.dpi).tobytes("png")
        result = engine(png)
        t_page = time.perf_counter() - t_page0

        print(f"PAGE {p} seconds={t_page:.3f}")

        txts = result.txts or []
        scores = result.scores or []

        if len(txts) == 0:
            all_passed = False

        lines = []
        for text, score in zip(txts, scores):
            print(f"{score:.3f}  {text}")
            lines.append(text)

        out_file = out_dir / f"{pdf_path.stem}-p{p}.txt"
        out_file.write_text("\n".join(lines), encoding="utf-8")

    if all_passed:
        print("RESULT: PASS")
        sys.exit(0)
    else:
        print("RESULT: FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
