"""Generate the synthetic test fixtures for Certificate Slicer (planner-owned, protected).

Usage:  python scripts/make_fixtures.py [--out tests/fixtures]
Needs pymupdf and openpyxl (project dependencies). Deterministic: same files every run
(byte-identical output is not guaranteed; tests compare content only).
No real employee data: every name and number here is invented.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

import pymupdf
from openpyxl import Workbook

PAGE_W, PAGE_H = 842, 595  # A4 landscape in points

# ---------------------------------------------------------------- employees
# (nid, nama, email, unit, jabatan). NIDs are text; one is written as a number on purpose.
CORE = [
    ("007123", "Budi Santoso", "budi.santoso@contoh.co.id", "UID Jakarta Raya", "Teknisi"),
    ("0045678", "Siti Rahmawati", "siti.rahmawati@contoh.co.id", "UID Jawa Barat", "Supervisor"),
    (8812345, "Dewi Lestari", "dewi.lestari@contoh.co.id", "UIW Kalimantan Barat", "Teknisi"),
    ("0011111", "Ahmad Fauzi", "ahmad.fauzi1@contoh.co.id", "UID Jakarta Raya", "Teknisi"),
    ("0022222", "Ahmad Fauzi", "ahmad.fauzi2@contoh.co.id", "UIW Sumatera Utara", "Operator"),
    ("0099001", "Agus Prasetyo", "agus.prasetyo@contoh.co.id", "Kantor Pusat", "Direktur"),
    ("0033333", "Rina Marlina", "rina.marlina@contoh.co.id", "UID Jawa Timur", "Analis"),
    ("0044444", "Muhammad Rizky Pratama", "rizky.pratama@contoh.co.id", "UIW Papua", "Teknisi"),
    ("0055001", "Andréas Siregar", "andreas.siregar@contoh.co.id", "UIW Sumatera Utara", "Teknisi"),
    ("0066001", "Fitri Handayani", "fitri.handayani@contoh.co.id", "UID Bali", "Analis"),
    ("0066002", "Fitri Handayanni", "fitri.handayanni@contoh.co.id", "UIW NTB", "Analis"),
    # patterns seen in the real master data (2026-10-15): one-word names, shortened names on certificates,
    # signatories whose names are close to an employee's name
    ("0077001", "Sutrisna", "sutrisna@contoh.co.id", "UID Bali", "Operator"),
    ("0077005", "Ilham Sutrisna", "ilham.sutrisna@contoh.co.id", "UIW NTB", "Teknisi"),
    ("0077002", "Sudarmaji", "sudarmaji@contoh.co.id", "UIW NTB", "Operator"),
    ("0077003", "Hana Pertiwi Kusumawardani", "hana.pk@contoh.co.id", "UID Banten", "Analis"),
    ("0077004", "Muhammad Alfi", "muhammad.alfi@contoh.co.id", "Kantor Pusat", "Staf"),
]
FIRST = ["Bayu", "Citra", "Eka", "Fajar", "Gilang", "Hendra", "Indah", "Kurnia", "Lukman", "Maya",
         "Nanda", "Oki", "Putri", "Qori", "Reza", "Sari", "Taufik", "Umar", "Vina", "Wahyu",
         "Yudi", "Zahra", "Arif", "Bella", "Candra", "Dimas", "Erlangga", "Farah", "Galih", "Hana"]
LAST = ["Saputra", "Wijaya", "Kusuma", "Nugroho", "Hidayat", "Permana", "Setiawan", "Gunadi",
        "Susanto", "Halim", "Firmansyah", "Ramadhan", "Syahputra", "Utami", "Anggraini",
        "Wulandari", "Purnomo", "Hakim", "Maulana", "Kurniawan"]


def filler_employees(n: int = 400) -> list[tuple]:
    rnd = random.Random(42)
    combos = [f"{f} {l}" for f in FIRST for l in LAST]
    rnd.shuffle(combos)
    out = []
    for i, name in enumerate(combos[:n]):
        nid = f"9{i:06d}"
        email = name.lower().replace(" ", ".") + "@contoh.co.id"
        out.append((nid, name, email, rnd.choice(["UID Bali", "UIW NTT", "UIP JBB", "UID Banten"]), "Staf"))
    return out


FILLER = filler_employees()


def write_db(path: Path, rows: list[tuple], *, title_row: bool, headers: list[str], extra_cols: bool,
             sheet: str = "Pegawai", decoy: bool = False) -> None:
    wb = Workbook()
    if decoy:
        # a first sheet that also has NID and name columns: the tool must use the "Data Master" sheet instead
        d = wb.active
        d.title = "Ringkasan"
        d.append(["NID", "Nama"])
        d.append(["WRONG001", "Budi Santoso"])
        ws = wb.create_sheet(sheet)
    else:
        ws = wb.active
        ws.title = sheet
    if title_row:
        ws.append(["DATA PEGAWAI (FIKTIF, UNTUK TES)"])
        ws.append([])
    hdr = (["No"] if extra_cols else []) + headers + (["Status"] if extra_cols else [])
    ws.append(hdr)
    for i, r in enumerate(rows, 1):
        vals = list(r)[: len(headers)]
        ws.append(([i] if extra_cols else []) + vals + (["Aktif"] if extra_cols else []))
    wb.save(path)


# ---------------------------------------------------------------- pdf helpers
def add_text_page(doc: pymupdf.Document, lines: list[str]) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = 60
    gap = 16 if len(lines) <= 16 else 8  # long pages are packed tighter so every line stays on the page
    for i, line in enumerate(lines):
        if line == "":
            y += 14
            continue
        size = 20 if i == 0 else 13
        page.insert_text((70, y), line, fontsize=size, fontname="helv")
        y += size + gap
    assert y < PAGE_H, "fixture page overflows"


def lsp_page(holder: str, number: str, issue: str | None, expiry: str | None,
             cue: bool = True, signatory: tuple[str, str] = ("Ketua LSP,", "Ir. Hartono Wibisono, M.T."),
             title: str = "Teknisi Distribusi Tenaga Listrik", extra: list[str] | None = None) -> list[str]:
    lines = ["SERTIFIKAT KOMPETENSI", f"Nomor: {number}", "LEMBAGA SERTIFIKASI PROFESI PLN", ""]
    lines += (["Diberikan kepada:"] if cue else ["Sertifikat ini menyatakan bahwa"]) + [holder]
    lines += ["Telah dinyatakan KOMPETEN pada skema:", title]
    lines += extra or []
    if issue:
        lines.append(f"Jakarta, {issue}")
    if expiry:
        lines.append(f"Berlaku s.d. {expiry}")
    lines += ["", signatory[0], "", signatory[1]]
    return lines


def save(doc: pymupdf.Document, path: Path) -> None:
    doc.set_metadata({})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


# ---------------------------------------------------------------- bundles
def bundle_simple(path: Path) -> None:
    d = pymupdf.open()
    add_text_page(d, lsp_page("Ir. BUDI SANTOSO, S.T., M.M.", "123/LSP-PLN/VIII/2025", "17 Agustus 2025", "16 Agustus 2028"))
    add_text_page(d, lsp_page("SITI RAHMAWATI, S.E.", "124/LSP-PLN/VIII/2025", "17 Agustus 2025", "16 Agustus 2028"))
    add_text_page(d, lsp_page("Dewi Lestari", "125/LSP-PLN/VIII/2025", "17 Agustus 2025", "16 Agustus 2028"))
    save(d, path)


def bundle_signatory(path: Path) -> None:
    d = pymupdf.open()
    for i in range(10):
        holder = FILLER[i][1].upper()
        add_text_page(d, lsp_page(holder, f"{300 + i}/LSP-PLN/IX/2025", "2 September 2025", "1 September 2028",
                                  cue=i < 5, signatory=("Direktur Utama,", "AGUS PRASETYO")))
    save(d, path)


def continuation_page(number: str, holder_line: str | None) -> list[str]:
    lines = ["LAMPIRAN SERTIFIKAT", f"Nomor: {number}", "Daftar Unit Kompetensi"]
    if holder_line:
        lines.append(holder_line)
    lines += ["1. Melaksanakan pekerjaan pemeliharaan jaringan", "2. Menerapkan prosedur K3 di tempat kerja",
              "", "Halaman 2/2"]
    return lines


def bundle_mixed(path: Path) -> None:
    d = pymupdf.open()
    add_text_page(d, lsp_page("BUDI SANTOSO", "401/LSP-PLN/X/2025", "1 Oktober 2025", "30 September 2028"))
    add_text_page(d, lsp_page("Siti Rahmawati", "402/LSP-PLN/X/2025", "1 Oktober 2025", "30 September 2028"))
    add_text_page(d, continuation_page("402/LSP-PLN/X/2025", None))
    add_text_page(d, lsp_page("DEWI LESTARI", "403/LSP-PLN/X/2025", "1 Oktober 2025", "30 September 2028"))
    add_text_page(d, lsp_page("RINA MARLINA", "404/LSP-PLN/X/2025", "1 Oktober 2025", "30 September 2028"))
    add_text_page(d, continuation_page("404/LSP-PLN/X/2025", "untuk RINA MARLINA"))
    add_text_page(d, lsp_page("MUHAMMAD RIZKY PRATAMA", "405/LSP-PLN/X/2025", None, None))
    save(d, path)


def bundle_unmatched(path: Path) -> None:
    d = pymupdf.open()
    add_text_page(d, lsp_page("AHMAD FAUZI", "501/LSP-PLN/XI/2025", "3 November 2025", "2 November 2028"))
    add_text_page(d, lsp_page("BAMBANG SUTRISNO", "502/LSP-PLN/XI/2025", "3 November 2025", "2 November 2028"))
    add_text_page(d, lsp_page("Rina Marlina, S.T.", "503/LSP-PLN/XI/2025", "3 November 2025", "2 November 2028"))
    # two different employees within 5 points of each other: must not be accepted
    add_text_page(d, lsp_page("FITRI HANDAYANI", "504/LSP-PLN/XI/2025", "3 November 2025", "2 November 2028"))
    save(d, path)


def bundle_scan(path: Path) -> None:
    src = pymupdf.open()
    add_text_page(src, lsp_page("MUHAMMAD RIZKY PRATAMA", "601/LSP-PLN/XII/2025", "5 Desember 2025", "4 Desember 2028"))
    add_text_page(src, lsp_page("DEWI LESTARI, A.Md.", "602/LSP-PLN/XII/2025", "5 Desember 2025", "4 Desember 2028"))
    out = pymupdf.open()
    for page in src:
        png = page.get_pixmap(dpi=150).tobytes("png")
        p = out.new_page(width=PAGE_W, height=PAGE_H)
        p.insert_image(p.rect, stream=png)
    src.close()
    save(out, path)


def bundle_djk(path: Path) -> None:
    d = pymupdf.open()
    for holder, num in (("BUDI SANTOSO", "DJK.0701.2025"), ("SITI RAHMAWATI", "DJK.0702.2025")):
        add_text_page(d, [
            "KEMENTERIAN ENERGI DAN SUMBER DAYA MINERAL", "DIREKTORAT JENDERAL KETENAGALISTRIKAN",
            "SERTIFIKAT KOMPETENSI TENAGA TEKNIK KETENAGALISTRIKAN", f"Nomor Registrasi: {num}", "",
            "Diberikan kepada:", holder, "Jabatan Kerja: Teknisi Gardu Distribusi", "",
            "Ditetapkan di Jakarta pada tanggal 17 Agustus 2025", "", "Direktur Jenderal Ketenagalistrikan"])
    save(d, path)


def bundle_internal(path: Path) -> None:
    d = pymupdf.open()
    for holder, num in (("Dewi Lestari", "CU/0801/2024"), ("Rina Marlina", "CU/0802/2024")):
        add_text_page(d, [
            "PLN CORPORATE UNIVERSITY", "SERTIFIKAT PELATIHAN", f"No. {num}", "",
            "Diberikan kepada:", holder, "telah mengikuti pelatihan", "Pemeliharaan Gardu Induk 150 kV", "",
            "Jakarta, 5 Maret 2024", "", "Kepala Udiklat"])
    save(d, path)


LONG_TITLE = ("Sertifikat Kompetensi: Pengoperasian/Pemeliharaan Sistem Proteksi Jaringan Tegangan Menengah "
              "20 kV Tingkat Lanjut")


def bundle_titles(path: Path) -> None:
    d = pymupdf.open()
    add_text_page(d, lsp_page("BUDI SANTOSO", "201/LSP-PLN/VII/2025", "10 Juli 2025", "9 Juli 2028", title=LONG_TITLE))
    add_text_page(d, lsp_page("BUDI SANTOSO", "202/LSP-PLN/VII/2025", "10 Juli 2025", "9 Juli 2028", title=LONG_TITLE))
    add_text_page(d, lsp_page("ANDRÉAS SIREGAR", "203/LSP-PLN/VII/2025", "10 Juli 2025", "9 Juli 2028",
                              title=LONG_TITLE, extra=["Perpanjangan ke-2"]))
    save(d, path)


def bnsp_first(holder: str, number: str) -> list[str]:
    return ["BADAN NASIONAL", "SERTIFIKASI PROFESI", "SERTIFIKAT KOMPETENSI", "CERTIFICATE OF COMPETENCE",
            f"No.:{number}", "Dengan ini menyatakan bahwa,", "This is to certify that,", holder,
            "Telah Kompeten pada :", "Is Competent for :", "Staf Sumber Daya Manusia", "Human Capital Staff",
            "Pada Bidang Pekerjaan :", "In the area of :", "Manajemen Sumber Daya Manusia",
            "Sertifikat ini berlaku untuk 3 (tiga) Tahun", "This certificate is valid for 3 (three) years",
            "Surabaya, 20 Agustus 2026", "Atas nama (on behalf of) BNSP", "Lembaga Sertifikasi Profesi",
            "Manajemen Sumber Daya Manusia NUSANTARA", "Ir. Hartono Wibisono", "Direktur (Director)"]


def bnsp_second(holder: str) -> list[str]:
    return ["Daftar Unit Kompetensi", "List of Competency Unit", "1. M.70SDM01.010.2 Menyusun Uraian Jabatan",
            "2. M.70SDM01.057.2 Melakukan Administrasi Pengupahan", "Surabaya, 20 Agustus 2026",
            "Lembaga Sertifikasi Profesi", holder, "Muhammad Al Fikri", "Tanda tangan pemilik", "Manajer Sertifikasi"]


def bundle_bnsp_like(path: Path) -> None:
    """Text-layer copy of the layout of a real BNSP bundle: 3 certificates x 2 pages."""
    d = pymupdf.open()
    for holder, num in (("ILHAM SUTRISNA", "11111.2222.0.0000101.2026"), ("HANA PERTIWI", "11111.2222.0.0000102.2026"),
                        ("BUDI SANTOSO", "11111.2222.0.0000103.2026")):
        add_text_page(d, bnsp_first(holder, num))
        add_text_page(d, bnsp_second(holder))
    save(d, path)


def bundle_k3_like(path: Path) -> None:
    """Like a real training certificate: labels live in a background picture, only the holder, the number and the
    signatory are in the text layer (fewer than 400 characters)."""
    out = pymupdf.open()
    for holder, num in (("RINA MARLINA", "9101/K3.TEKNISI-LISTRIK/TRN-XYZ/X-2026"),
                        ("SITI RAHMAWATI", "9102/K3.TEKNISI-LISTRIK/TRN-XYZ/X-2026")):
        bg = pymupdf.open()
        add_text_page(bg, ["CERTIFICATE", "CERTIFICATE NO", "", "THIS CERTIFICATE IS PRESENTED TO", "", "",
                           "has successfully completed training of", "TEKNISI K3 LISTRIK",
                           "SERTIFIKASI KEMENTERIAN KETENAGAKERJAAN RI.", "Held on October 05th - 12th, 2026",
                           "At PT Contoh Pelatihan Indonesia", "Surabaya - Indonesia"])
        png = bg[0].get_pixmap(dpi=150).tobytes("png")
        bg.close()
        p = out.new_page(width=PAGE_W, height=PAGE_H)
        p.insert_image(p.rect, stream=png)
        p.insert_text((185, 96), f": {num}", fontsize=12, fontname="helv")
        p.insert_text((70, 186), holder, fontsize=16, fontname="helv")
        p.insert_text((70, 520), "Sudarmaji", fontsize=12, fontname="helv")
        p.insert_text((70, 540), "Direktur", fontsize=12, fontname="helv")
    save(out, path)


def bundle_30(path: Path) -> None:
    d = pymupdf.open()
    for i in range(30):
        holder = FILLER[100 + i][1].upper()
        add_text_page(d, lsp_page(holder, f"{900 + i}/LSP-PLN/I/2026", "12 Januari 2026", "11 Januari 2029"))
    save(d, path)


def bad_pdfs(folder: Path) -> None:
    (folder / "corrupt.pdf").write_bytes(b"%PDF-1.7\nthis is not a real pdf\n" + bytes(range(256)))
    d = pymupdf.open()
    add_text_page(d, lsp_page("BUDI SANTOSO", "999/LSP/2025", "1 Januari 2025", "31 Desember 2027"))
    d.save(folder / "encrypted.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-x", user_pw="user-x")
    d.close()


def gold_files(folder: Path) -> None:
    header = ["page", "nid", "title", "number", "issue_date", "expiry_date"]
    rows = [
        ["1", "007123", "Teknisi Distribusi Tenaga Listrik", "123/LSP-PLN/VIII/2025", "17/08/2025", "16/08/2028"],
        ["2", "0045678", "Teknisi Distribusi Tenaga Listrik", "124/LSP-PLN/VIII/2025", "17/08/2025", "16/08/2028"],
        ["3", "8812345", "Teknisi Distribusi Tenaga Listrik", "125/LSP-PLN/VIII/2025", "17/08/2025", "16/08/2028"],
    ]
    for name, data in (("gold_simple.csv", rows),
                       ("gold_wrong.csv", [rows[0], ["2", "9999999"] + rows[1][2:], rows[2]])):
        with open(folder / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="tests/fixtures")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    std = ["NID", "Nama Lengkap", "Email Korporat", "UNITKET", "Jabatan"]
    write_db(out / "db_basic.xlsx", CORE + FILLER, title_row=True, headers=std, extra_cols=True,
             sheet="Data Master", decoy=True)
    write_db(out / "db_missing_nid.xlsx", [r[1:] for r in CORE], title_row=False, headers=std[1:], extra_cols=False)
    write_db(out / "db_empty.xlsx", [], title_row=False, headers=std, extra_cols=False)
    for fn in (bundle_simple, bundle_signatory, bundle_mixed, bundle_unmatched, bundle_scan, bundle_djk,
               bundle_internal, bundle_titles, bundle_30, bundle_bnsp_like, bundle_k3_like):
        fn(out / f"{fn.__name__}.pdf")
    bad_pdfs(out)
    gold_files(out)
    names = sorted(p.name for p in out.iterdir() if p.is_file())
    print("\n".join(names))
    print(f"RESULT: PASS ({len(names)} fixture files in {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
