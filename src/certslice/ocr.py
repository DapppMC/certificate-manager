from __future__ import annotations

import time
from typing import Any


class OcrEngine:
    def __init__(self, dpi: int = 200):
        self.dpi = dpi
        self._engine: Any = None
        self.seconds: list[float] = []

    def page_text(self, page: Any) -> str:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        t = time.perf_counter()
        result = self._engine(page.get_pixmap(dpi=self.dpi).tobytes("png"))
        self.seconds.append(time.perf_counter() - t)
        txts = getattr(result, "txts", None)
        if txts is None and isinstance(result, (list, tuple)) and len(result) > 0:
            txts = result[0]
        return "\n".join(txts or [])
