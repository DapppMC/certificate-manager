"""Workbook generator for 'For NPS 0726.xlsx' (MSO765 NPS standard)."""
from __future__ import annotations

import datetime as dt
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from certslice.levels import get_numeric_level

logger = logging.getLogger(__name__)

HEADER_COLUMNS = [
    "NID",
    "RESOURCE CLASS",
    "RESOURCE CODE",
    "COMPETENCY LEVEL",
    "EFFECTIVE DATE",
    "GAINED DATE",
    "EXP DATE",
    "REQ TYPE",
    "INSTITUTE CODE",
    "PRIMARY",
]


def is_organic_2letter_nid(nid: str | None) -> bool:
    """Check if NID has at least 5 digits and ends with exactly 2 alphabetic characters (organic PLN employee NID).
    Example:
      '9016033PL' -> True
      '8310238RB' -> True
      '8109060PT' -> True
      '9620028APN' -> False (3 letters)
      '8109060P' -> False (1 letter)
      'BMT' -> False
    """
    if not nid:
        return False
    s = str(nid).strip()
    return bool(re.search(r"^\d{5,10}[A-Za-z]{2}$", s))


def is_valid_mso_nid(nid: str | None) -> bool:
    """Check if NID is a valid PLN employee NID.
    Accepts:
      - Organic staff (2-letter suffix like 8214011KD, 9016033PL, 8109060PT)
      - Subsidiary / Penugasan staff (3-4 letter suffix like 9620028APN, 9317048SPA, 9825010NAS, 1-letter P)
      - Pure numeric employee IDs with >= 5 digits (e.g. 10240212)
    Rejects:
      - Non-numeric strings / placeholders without numbers ('BMT', 'MKP', '-', '', None)
    """
    if not nid:
        return False
    s = str(nid).strip()
    if s in ["-", "None", ""]:
        return False
    return bool(re.search(r"\d{5,}", s))


def extract_batch_name(name: str) -> str:
    """Clean folder / batch name like 'Hasil_127_...' or '127_2' or 'Hasil_PDF_Mentah/127' to '127'."""
    s = str(name).strip().replace("\\", "/")
    parts = [p for p in s.split("/") if p and not p.startswith("Hasil_") and p != "."]
    if parts:
        cand = parts[-1] if len(parts) > 1 and parts[0].startswith("Hasil_") else parts[0]
    else:
        cand = s
    cand = re.sub(r"^Hasil_", "", cand, flags=re.IGNORECASE)
    cand = re.sub(r"_\d{2}-[A-Za-z]{3}-\d{4}.*$", "", cand)
    cand = re.sub(r"_\d+$", "", cand)
    cand = sanitize_sheet_title(cand.strip())
    return cand if cand else "Sertifikasi"


def parse_date_to_int(date_val: Any, default_year: int = 2026) -> int:
    """Convert various date representations to YYYYMMDD integer (e.g. 20260522)."""
    if date_val is None or str(date_val).strip() in ["", "-", "None"]:
        return int(f"{default_year}0101")

    if isinstance(date_val, (datetime, dt.date)):
        return int(f"{date_val.year:04d}{date_val.month:02d}{date_val.day:02d}")

    if isinstance(date_val, (int, float)):
        val = int(date_val)
        if 19900101 <= val <= 20991231:
            return val

    s = str(date_val).strip()
    # YYYYMMDD format
    if re.match(r"^(?:19|20)\d{6}$", s):
        return int(s)

    # YYYY-MM-DD format
    m_iso = re.match(r"^((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        return int(f"{y:04d}{m:02d}{d:02d}")

    # DD-MM-YYYY format
    m_dmy = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/]((?:19|20)\d{2})", s)
    if m_dmy:
        d, m, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        return int(f"{y:04d}{m:02d}{d:02d}")

    # Year only
    m_yr = re.search(r"\b((?:19|20)\d{2})\b", s)
    if m_yr:
        y = int(m_yr.group(1))
        return int(f"{y:04d}0101")

    # Current year fallback
    now = datetime.now()
    return int(f"{now.year:04d}{now.month:02d}{now.day:02d}")


def calculate_exp_date(gained_date_int: int, validity_years: int = 3) -> int:
    """Calculate EXP DATE by adding validity_years to gained_date_int (YYYYMMDD)."""
    s = str(gained_date_int)
    if len(s) == 8:
        year = int(s[:4]) + validity_years
        return int(f"{year}{s[4:]}")
    return gained_date_int + (validity_years * 10000)


def sanitize_sheet_title(name: str) -> str:
    """Ensure Excel sheet name is <= 31 chars and contains no invalid characters."""
    clean = re.sub(r"[\\/*?:\[\]]", "_", name).strip()
    return clean[:31] if clean else "Sheet"


def build_for_nps_workbook(
    items: list[dict[str, Any]],
    output_path: Path,
    template_path: Path | str | None = None,
) -> Path:
    """Build 'For NPS 0726.xlsx' containing only level-based certificates (SKP/Kompetensi).
    Separates into sheets:
      - Primary: Subfolder/Batch specific sheets for Organic 2-letter NIDs (e.g. '127')
      - Non-Organic: Subfolder/Batch sheets for 1-letter/3-letter/numeric NIDs (e.g. '127 (Non-Organik)')
      - 'Tidak ada NID ' ONLY if items with missing/unidentified NID exist (pure NID in cell, explanation in header)
      - 'Tidak ada Resource Code ' ONLY if items with missing Resource Code exist
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if template_path and Path(template_path).exists():
        logger.info("Membangun For NPS 0726.xlsx merujuk pada template: %s", Path(template_path).name)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Styles
    font_header = Font(name="Calibri", size=11, bold=True, color="000000")
    font_data = Font(name="Calibri", size=11, color="000000")
    fill_header = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    fill_warn = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    # Filter items: only those with level (SKP / Kompetensi) or resource_code
    level_items = []
    for it in items:
        lvl = it.get("level")
        is_skp = it.get("is_skp", False)
        rc = str(it.get("resource_code") or "").strip()
        num_lvl = get_numeric_level(lvl)
        if is_skp or num_lvl is not None or (lvl and str(lvl).strip() not in ["-", "None", ""]) or (rc and rc != "-"):
            level_items.append(it)

    no_nid_rows: list[list[Any]] = []
    no_rc_rows: list[list[Any]] = []
    subfolder_batches: dict[str, list[list[Any]]] = {}

    for it in level_items:
        nid = str(it.get("nid") or "").strip()
        nama = str(it.get("nama") or "").strip()
        lvl = it.get("level")
        num_lvl = get_numeric_level(lvl)
        title = str(it.get("judul") or it.get("peran") or "").strip()
        rc = str(it.get("resource_code") or "").strip()
        inst = str(it.get("institute_code") or "").strip()
        raw_sub = str(it.get("subfolder_name") or it.get("batch_name") or "").strip()
        subfolder_key = extract_batch_name(raw_sub)

        is_k3 = (
            inst == "D001"
            or "K3" in title.upper()
            or "BNSP" in title.upper()
            or "SMK2" in title.upper()
            or "KEMNAKER" in title.upper()
            or str(it.get("tipe_dokumen", "")).upper() in ["K3", "BNSP"]
        )

        effective_date_int = parse_date_to_int(it.get("effective_date") or it.get("tanggal_terbit"))
        validity = 5 if is_k3 else 3
        raw_exp = it.get("exp_date") or it.get("tanggal_expired")
        if raw_exp and str(raw_exp).strip() not in ["-", "None", ""]:
            exp_date_int = parse_date_to_int(raw_exp)
        else:
            exp_date_int = calculate_exp_date(effective_date_int, validity)

        if exp_date_int <= effective_date_int:
            exp_date_int = calculate_exp_date(effective_date_int, validity)

        inst_code = "D001" if is_k3 else "D002"

        # Check NID validity: must have digits
        if not is_valid_mso_nid(nid):
            # Put pure NID in data column so user can copy-paste directly to NPS web
            pure_nid_val = nid if nid and nid != "-" else "-"
            no_nid_rows.append([
                nama,
                pure_nid_val,
                it.get("file_name") or it.get("src_file") or "-",
                f"Level {int(num_lvl)}" if num_lvl else (lvl or "-"),
                title,
                rc or "-",
            ])
            continue

        # Check Resource Code
        if not rc or rc == "-":
            no_rc_rows.append([
                title,
                "",  # KODE blank
                f"Level {int(num_lvl)}" if num_lvl else (lvl or "-"),
                nid,
                nama,
                it.get("file_name") or it.get("src_file") or "-",
            ])
            continue

        # Valid standard MSO row
        standard_row = [
            nid,
            "C",  # RESOURCE CLASS
            rc,   # RESOURCE CODE
            num_lvl,  # COMPETENCY LEVEL
            effective_date_int,  # EFFECTIVE DATE
            effective_date_int,  # GAINED DATE
            exp_date_int,        # EXP DATE
            None,                # REQ TYPE
            inst_code,           # INSTITUTE CODE
            "N",                 # PRIMARY
        ]

        # Segregation: 2-letter suffix vs bukan 2 huruf (3-letter, 1-letter, numeric)
        is_organic = is_organic_2letter_nid(nid)
        if subfolder_key in ["Batch", "Sertifikasi", ""]:
            base_key = "K3 & BNSP" if is_k3 else "DJK"
        else:
            base_key = subfolder_key

        target_sheet_title = base_key if is_organic else f"{base_key} (Bukan 2 Huruf)"
        target_sheet_title = sanitize_sheet_title(target_sheet_title)

        if target_sheet_title not in subfolder_batches:
            subfolder_batches[target_sheet_title] = []
        subfolder_batches[target_sheet_title].append(standard_row)

    # 1. Primary sheets: 2-letter suffix sheets first, then Bukan 2 Huruf sheets
    sheets_created = set()
    sorted_sheet_keys = sorted(subfolder_batches.keys(), key=lambda k: (1 if "(Bukan 2 Huruf)" in k else 0, k))
    for batch_title in sorted_sheet_keys:
        batch_rows = subfolder_batches[batch_title]
        if batch_rows:
            ws_batch = wb.create_sheet(title=batch_title)
            _write_standard_sheet(ws_batch, HEADER_COLUMNS, batch_rows, font_header, font_data, fill_header, thin_border)
            sheets_created.add(batch_title)

    # 2. Sheet: Tidak ada NID (ONLY created if there are rows)
    if no_nid_rows:
        ws_no_nid = wb.create_sheet(title="Tidak ada NID ")
        no_nid_headers = [
            "Nama",
            "Keterangan NID (NID Tidak Terdaftar / Tidak Standar)",
            "File Sertifikat",
            "Level",
            "Judul / Peran",
            "Resource Code",
        ]
        _write_standard_sheet(ws_no_nid, no_nid_headers, no_nid_rows, font_header, font_data, fill_warn, thin_border)
        sheets_created.add("Tidak ada NID ")

    # 3. Sheet: Tidak ada Resource Code (ONLY created if there are rows)
    if no_rc_rows:
        ws_no_rc = wb.create_sheet(title="Tidak ada Resource Code ")
        no_rc_headers = ["JUDUL", "KODE", "COMP LEVEL", "NID", "Nama Pegawai", "File Asal"]
        _write_standard_sheet(ws_no_rc, no_rc_headers, no_rc_rows, font_header, font_data, fill_warn, thin_border)
        sheets_created.add("Tidak ada Resource Code ")

    # Fallback if completely empty
    if not sheets_created:
        fallback_title = extract_batch_name(output_path.parent.name) or "Sertifikasi"
        ws_fallback = wb.create_sheet(title=fallback_title)
        _write_standard_sheet(ws_fallback, HEADER_COLUMNS, [], font_header, font_data, fill_header, thin_border)

    wb.save(output_path)
    logger.info("Saved For NPS 0726 workbook to %s (Sheets=%s, NoNID=%d, NoRC=%d)", output_path, list(wb.sheetnames), len(no_nid_rows), len(no_rc_rows))
    return output_path


def _write_standard_sheet(
    ws: Any,
    headers: list[str],
    rows: list[list[Any]],
    font_header: Font,
    font_data: Font,
    fill_header: PatternFill,
    border: Border,
) -> None:
    ws.views.sheetView[0].showGridLines = True
    ws.row_dimensions[1].height = 26

    # Write headers
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(1, c_idx, h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    # Write rows
    for r_idx, row in enumerate(rows, 2):
        ws.row_dimensions[r_idx].height = 20
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(r_idx, c_idx, val)
            cell.font = font_data
            cell.border = border
            if c_idx in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Auto column widths
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            v_str = str(cell.value or "")
            if len(v_str) > max_len:
                max_len = len(v_str)
        ws.column_dimensions[col_letter].width = max(12, min(max_len + 4, 50))
