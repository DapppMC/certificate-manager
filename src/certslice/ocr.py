from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np

_OCR_ENGINES: dict[int, "OcrEngine"] = {}
_OCR_ENGINES_LOCK = threading.Lock()


class OcrEngine:
    def __init__(self, dpi: int = 130):
        self.dpi = dpi
        self._engine: Any = None
        self._local = threading.local()
        self._lock = threading.Lock()
        self.seconds: list[float] = []

    def _get_thread_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        eng = getattr(self._local, "engine", None)
        if eng is None:
            from rapidocr import RapidOCR

            eng = RapidOCR()
            self._local.engine = eng
        return eng

    def page_text(self, page: Any) -> str:
        eng = self._get_thread_engine()
        t = time.perf_counter()
        pix = page.get_pixmap(dpi=self.dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        del pix

        result = eng(img)

        self.seconds.append(time.perf_counter() - t)
        txts = getattr(result, "txts", None)
        if txts is None and isinstance(result, (list, tuple)) and len(result) > 0:
            txts = result[0]
        elif txts is None and hasattr(result, "boxes") and hasattr(result, "txts"):
            txts = result.txts
        return "\n".join(txts or [])


def get_ocr_engine(dpi: int = 130) -> OcrEngine:
    """Return a shared thread-safe OcrEngine instance for the requested DPI."""
    with _OCR_ENGINES_LOCK:
        eng = _OCR_ENGINES.get(dpi)
        if eng is None:
            eng = OcrEngine(dpi=dpi)
            _OCR_ENGINES[dpi] = eng
        return eng
