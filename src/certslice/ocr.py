from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np


class OcrEngine:
    def __init__(self, dpi: int = 130):
        self.dpi = dpi
        self._engine: Any = None
        self._lock = threading.Lock()
        self.seconds: list[float] = []

    def page_text(self, page: Any) -> str:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    from rapidocr import RapidOCR

                    self._engine = RapidOCR()
        t = time.perf_counter()
        pix = page.get_pixmap(dpi=self.dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        with self._lock:
            result = self._engine(img)

        self.seconds.append(time.perf_counter() - t)
        txts = getattr(result, "txts", None)
        if txts is None and isinstance(result, (list, tuple)) and len(result) > 0:
            txts = result[0]
        elif txts is None and hasattr(result, "boxes") and hasattr(result, "txts"):
            txts = result.txts
        return "\n".join(txts or [])
