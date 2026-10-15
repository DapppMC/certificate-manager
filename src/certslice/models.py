from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf


@dataclass
class PageText:
    index: int      # 1-based page number
    text: str       # lines separated by \n
    source: str     # "text" | "ocr" | "none" | reader-defined


class PageReader(Protocol):
    def read(self, page: pymupdf.Page) -> PageText: ...


@dataclass
class Employee:
    nid: str
    nama: str
    email: str = ""
    unit: str = ""
    jabatan: str = ""
    norm: str = ""   # normalize_name(nama)


@dataclass
class Candidate:
    index: int      # position of the employee in the employee list
    score: float
    line: int       # index of the page line with the best score
    partial: bool = False
