"""Resource Code resolution engine using 'Komparasi Kode Baru Sertifikasi NPS 0726.xlsx'."""
from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any

import numpy as np
import openpyxl
from rapidfuzz import fuzz, process

from certslice.levels import get_numeric_level
from certslice.normalize import fold

logger = logging.getLogger(__name__)

# Explicit overrides curated from MSO765_NPS.xlsx 'Tidak ada Resource Code '
EXPLICIT_OVERRIDES: dict[str, str] = {
    "ENGINEER WORK PLANNING & CONTROL": "B6P5",
    "ANLISIS MUDA PEMELIHARAAN COAL HANDLING & ASH HANDLING": "BAMT",
    "ANALISIS MUDA PEMELIHARAAN COAL HANDLING & ASH HANDLING": "BAMT",
    "ASSISTANT ENGINEER PERFORMANCE AND EFFICIENCY": "BAO1",
    "ANALIS MUDA PEMELIHARAAN PERALATAN KONTROL DAN INSTRUMEN": "BAML",
    "SUPERVISOR SENIOR PENGOPERASIAN UNIT PLTU BATU BARA": "AA03",
    "SUPERVISOR SENIOR PENGOPERASIAN UNIT PLTA BESAR": "AA03",
    "ASSISTANT ENGINEER RELIABILITY MANAGEMENT": "N010",
    "ASSISTANT ENGINEER WORK PLANNING AND CONTROL (MAINTENANCE PLANNER)": "N011",
    "ASSISTANT ENGINEER OUTAGE MANAGEMENT (OUTAGE PLANNER)": "N012",
    "MENGKOORDINIR PEMELIHARAAN PEMBANGKIT TENAGA LISTRIK - MEMELIHARA PERALATAN INSTRUMEN": "BAKL",
    "PENANGGUNG JAWAB K2 SISTEM MANAJEMEN KESELAMATAN KETENAGA LISTRIKAN PEMBANGKIT": "PK21",
    "PENANGGUNG JAWAB K2 SISTEM MANAJEMEN KESELAMATAN KETENAGALISTRIKAN PEMBANGKIT": "PK21",
    "PENANGGUNG JAWAB SMK2": "PK21",
    "TEKNISI PENGAWAS K2 SISTEM MANAJEMEN KESELAMATAN KETENAGALISTRIKAN PEMBANGKIT": "PK22",
    "TEKNISI PENGAWAS SMK2": "PK22",
    "ANALISI K2 SISTEM MANAJEMEN KESELAMATAN KETENAGALISTRIKAN PEMBANGKIT": "PK24",
    "ANALISIS K2 SISTEM MANAJEMEN KESELAMATAN KETENAGALISTRIKAN PEMBANGKIT": "PK24",
    "AUDITOR K2 SISTEM MANAJEMEN KESELAMATAN KETENAGALISTRIKAN PEMBANGKIT": "PK23",
    "PELAKSANA SENIOR PEMELIHARAAN GENERATOR": "BAKK",
    "PELAKSANA JUNIOR PEMELIHARAAN GENERATOR": "AA05",
    "SUPERVISOR SENIOR PENGOPERASIAN UNIT MESIN DIESEL": "A6A4",
    "SUPERVISOR SENIOR PEMELIHARAAN PERALATAN BOILER": "BAM9",
    "SUPERVISOR KIMIA": "AK06",
    "SUPERVISOR PENGOPERASIAN PLTS": "BAN8",
    "SUPERVISOR SENIOR PEMELIHARAAN PERALATAN KONTROL DAN INSTRUMEN": "BAMO",
    "SUPERVISOR SENIOR PEMELIHARAAN PERALATAN LISTRIK": "BAM8",
    "OPERATOR SENIOR BOILER": "AA05",
    "OPERATOR SENIOR TURBIN UAP": "AA05",
    "OPERATOR KONTROL INSTRUMEN": "BAKL",
}

DEFAULT_KOMPARASI_PATH = Path("Komparasi Kode Baru Sertifikasi NPS 0726.xlsx")


class ResourceCodeCatalog:
    """In-memory catalog of Resource Codes loaded from Komparasi Kode Baru Sertifikasi NPS 0726.xlsx."""

    def __init__(self, excel_path: Path | str | None = None):
        self.excel_path = Path(excel_path) if excel_path else DEFAULT_KOMPARASI_PATH
        self.code_entries: dict[str, dict[str, Any]] = {}
        self.entries_list: list[dict[str, Any]] = []
        self._is_loaded = False

    def load(self, force: bool = False) -> None:
        if self._is_loaded and not force:
            return

        if not self.excel_path.exists():
            # Try looking in parent, cwd, or executable directory
            alt_candidates = [
                Path.cwd() / "Komparasi Kode Baru Sertifikasi NPS 0726.xlsx",
                Path(__file__).resolve().parent.parent.parent / "Komparasi Kode Baru Sertifikasi NPS 0726.xlsx",
            ]
            for cand in alt_candidates:
                if cand.exists():
                    self.excel_path = cand
                    break

        if not self.excel_path.exists():
            logger.warning("Komparasi file not found at %s. Resource codes will use fallback rules.", self.excel_path)
            self._is_loaded = True
            return

        try:
            wb = openpyxl.load_workbook(self.excel_path, read_only=True, data_only=True)
        except Exception as exc:
            logger.error("Failed to open %s: %s", self.excel_path, exc)
            self._is_loaded = True
            return

        try:
            if "Kode Baru" in wb.sheetnames:
                ws = wb["Kode Baru"]
                self._load_kode_baru_sheet(ws)

            if "Kode Jabatan" in wb.sheetnames:
                ws_kj = wb["Kode Jabatan"]
                self._load_kode_jabatan_sheet(ws_kj)
        finally:
            wb.close()

        self._is_loaded = True
        logger.info("Loaded %d Resource Code entries from %s", len(self.code_entries), self.excel_path)

    def _load_kode_baru_sheet(self, ws: Any) -> None:
        block_defs = [
            ("A", 1, 2, 3, 4, 5, 6),
            ("B", 8, 9, 10, 11, 12, 13),
            ("C", 15, 16, None, None, None, None),
            ("D", 19, 20, None, None, None, None),
            ("E", 23, 24, None, None, None, None),
            ("F", 27, 28, None, None, None, None),
            ("G", 31, 32, None, None, None, None),
            ("H", 35, 36, None, None, None, None),
            ("I", 39, 40, None, None, None, None),
            ("J", 44, 45, None, None, None, None),
            ("K", 48, 49, None, None, None, None),
            ("L", 52, 53, None, None, None, None),
            ("M", 56, 57, None, None, None, None),
            ("N", 59, 60, None, None, None, None),
            ("P", 62, 63, None, None, None, None),
            ("Q", 65, 66, None, None, None, None),
        ]

        for r_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            if r_idx < 23:
                continue

            for bname, c_code, c_judul, c_lvl, c_jab, c_kem, c_mso in block_defs:
                if len(row) > c_code and row[c_code]:
                    code = str(row[c_code]).strip()
                    if not code or code.upper() in ["KODE", "NONE"]:
                        continue

                    judul = str(row[c_judul]).strip() if c_judul is not None and len(row) > c_judul and row[c_judul] else ""
                    lvl: float | None = None
                    if c_lvl is not None and len(row) > c_lvl and row[c_lvl] is not None:
                        try:
                            lvl = float(row[c_lvl])
                        except (ValueError, TypeError):
                            lvl = None

                    jab = str(row[c_jab]).strip() if c_jab is not None and len(row) > c_jab and row[c_jab] else ""
                    kem = str(row[c_kem]).strip() if c_kem is not None and len(row) > c_kem and row[c_kem] else ""
                    mso = str(row[c_mso]).strip() if c_mso is not None and len(row) > c_mso and row[c_mso] else ""

                    institute = "D001" if bname in ["E", "P"] or "K3" in judul.upper() or "SMK2" in judul.upper() or "BNSP" in judul.upper() else "D002"

                    entry = {
                        "code": code,
                        "block": bname,
                        "judul": judul,
                        "level": lvl,
                        "jabatan": jab,
                        "kemungkinan": kem,
                        "mso": mso,
                        "institute": institute,
                        "search_text": fold(f"{code} {judul} {jab} {kem} {mso}").lower(),
                    }

                    if code not in self.code_entries:
                        self.code_entries[code] = entry
                        self.entries_list.append(entry)
                    else:
                        # Append any richer search text
                        self.code_entries[code]["search_text"] += " " + fold(f"{judul} {jab} {kem}").lower()

    def _load_kode_jabatan_sheet(self, ws: Any) -> None:
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            if r_idx >= 21 and len(row) > 6 and row[3]:
                mso_code = str(row[3]).strip()
                jab = str(row[4] or "").strip()
                kem = str(row[5] or "").strip()
                desc = str(row[6] or "").strip()
                extra = fold(f"{jab} {kem} {desc}").lower()

                # Add extra search text to entries that match this mso_code
                for e in self.entries_list:
                    if e.get("mso") == mso_code:
                        e["search_text"] += " " + extra

    def lookup(
        self,
        title: str = "",
        level: str | int | float | None = None,
        jabatan: str = "",
        raw_text: str = "",
    ) -> dict[str, Any]:
        """Look up Resource Code given title, level, employee jabatan, or raw text."""
        self.load()

        combined_input = f"{title} {jabatan} {raw_text}".strip()
        num_level = get_numeric_level(level)

        # 1. Check if an explicit Resource Code (e.g. A100, PK21, AA02, E028, BAMT, BAKK) is in text/title
        for token in re.findall(r"\b[A-Z0-9]{3,5}\b", combined_input.upper()):
            if token in self.code_entries:
                entry = self.code_entries[token]
                return {
                    "code": token,
                    "level": num_level or entry.get("level"),
                    "institute": entry.get("institute", "D002"),
                    "description": entry.get("judul") or entry.get("jabatan"),
                    "confidence": 1.0,
                    "matched_by": "exact_code_token",
                }

        # 2. Check explicit high-priority overrides (normalized)
        norm_title = re.sub(r"\s+", " ", fold(title).upper()).strip()
        if norm_title in EXPLICIT_OVERRIDES:
            code = EXPLICIT_OVERRIDES[norm_title]
            entry = self.code_entries.get(code, {})
            return {
                "code": code,
                "level": num_level or entry.get("level"),
                "institute": entry.get("institute", "D002"),
                "description": entry.get("judul") or norm_title,
                "confidence": 0.99,
                "matched_by": "explicit_override",
            }

        for exp_key, code in EXPLICIT_OVERRIDES.items():
            if exp_key in norm_title:
                entry = self.code_entries.get(code, {})
                return {
                    "code": code,
                    "level": num_level or entry.get("level"),
                    "institute": entry.get("institute", "D002"),
                    "description": entry.get("judul") or exp_key,
                    "confidence": 0.95,
                    "matched_by": "explicit_override_partial",
                }

        # 3. Fuzzy match against catalog entries
        if not self.entries_list:
            return {
                "code": "",
                "level": num_level,
                "institute": "D002",
                "description": title,
                "confidence": 0.0,
                "matched_by": "none",
            }

        q = fold(f"{title} {jabatan}").lower().strip()
        if not q:
            return {
                "code": "",
                "level": num_level,
                "institute": "D002",
                "description": "",
                "confidence": 0.0,
                "matched_by": "empty_query",
            }

        search_texts = [entry["search_text"] for entry in self.entries_list]
        scores_arr = process.cdist(
            [q],
            search_texts,
            scorer=fuzz.token_set_ratio,
            dtype=np.float64,
        )[0]

        best_entry = None
        best_score = -1.0

        for idx, entry in enumerate(self.entries_list):
            score = float(scores_arr[idx])

            # Level bonus if level matches
            if num_level is not None and entry.get("level") is not None:
                if entry["level"] == num_level:
                    score += 15.0
                else:
                    score -= 10.0

            if score > best_score:
                best_score = score
                best_entry = entry

        # Threshold for acceptable match
        if best_entry and best_score >= 60.0:
            return {
                "code": best_entry["code"],
                "level": num_level or best_entry.get("level"),
                "institute": best_entry.get("institute", "D002"),
                "description": best_entry.get("judul") or best_entry.get("jabatan"),
                "confidence": round(min(1.0, best_score / 100.0), 2),
                "matched_by": "catalog_fuzzy",
            }

        return {
            "code": "",
            "level": num_level,
            "institute": "D002",
            "description": title,
            "confidence": 0.0,
            "matched_by": "unmatched",
        }


# Global singleton instance for fast reuse
_CATALOG_INSTANCE: ResourceCodeCatalog | None = None


def get_resource_code_catalog(excel_path: Path | str | None = None) -> ResourceCodeCatalog:
    global _CATALOG_INSTANCE
    if _CATALOG_INSTANCE is None or (excel_path and Path(excel_path) != _CATALOG_INSTANCE.excel_path):
        _CATALOG_INSTANCE = ResourceCodeCatalog(excel_path)
    return _CATALOG_INSTANCE


def lookup_resource_code(
    title: str = "",
    level: str | int | float | None = None,
    jabatan: str = "",
    raw_text: str = "",
    excel_path: Path | str | None = None,
) -> dict[str, Any]:
    catalog = get_resource_code_catalog(excel_path)
    return catalog.lookup(title=title, level=level, jabatan=jabatan, raw_text=raw_text)
