import pytest
from certslice.config import load_config
from certslice.fields import (
    doc_type,
    extract_issuer,
    extract_title,
    renewal_number,
    template_lines,
)


@pytest.fixture
def cfg():
    return load_config(None)


def test_cue_with_text_on_same_line(cfg):
    lines = ["Telah dinyatakan kompeten pada skema: Teknisi Jaringan Tegangan Rendah"]
    title, conf = extract_title(lines, set(), True, 1, cfg)
    assert title == "Teknisi Jaringan Tegangan Rendah"
    assert conf == "high"


def test_cue_with_title_on_next_line(cfg):
    lines = ["Telah dinyatakan KOMPETEN pada skema:", "Teknisi Distribusi Tenaga Listrik"]
    title, conf = extract_title(lines, set(), False, 3, cfg)
    assert title == "Teknisi Distribusi Tenaga Listrik"
    assert conf == "medium"


def test_following_label_ending_with_colon_skipped(cfg):
    lines = [
        "Telah mengikuti pelatihan",
        "Pada Bidang Pekerjaan :",
        "Pemeliharaan Gardu Induk 150 kV",
    ]
    title, conf = extract_title(lines, set(), False, 3, cfg)
    assert title == "Pemeliharaan Gardu Induk 150 kV"


def test_following_line_with_another_cue_skipped(cfg):
    lines = [
        "Telah Kompeten pada :",
        "Is Competent for :",
        "Staf Sumber Daya Manusia",
    ]
    title, conf = extract_title(lines, set(), False, 3, cfg)
    assert title == "Staf Sumber Daya Manusia"


def test_generic_rest_skipped(cfg):
    lines = [
        "Telah mengikuti",
        "Pelatihan",
        "Teknik Pengelasan Logam",
    ]
    title, conf = extract_title(lines, set(), False, 3, cfg)
    assert title == "Teknik Pengelasan Logam"


def test_title_keyword_fallback(cfg):
    lines = [
        "Surat Keputusan",
        "Pelatihan Operator Boiler Kelas 1",
    ]
    title, conf = extract_title(lines, set(), False, 3, cfg)
    assert title == "Pelatihan Operator Boiler Kelas 1"
    assert conf == "low"


def test_issuer_keyword_priority_order(cfg):
    # 'direktorat jenderal' appears earlier in issuer_keywords than 'kementerian'
    lines = [
        "Kementerian Energi dan Sumber Daya Mineral",
        "DIREKTORAT JENDERAL KETENAGALISTRIKAN",
    ]
    issuer, conf = extract_issuer(lines, set(), cfg)
    assert issuer == "DIREKTORAT JENDERAL KETENAGALISTRIKAN"


def test_issuer_exclude_skips_employer(cfg):
    lines = [
        "PT PLN Nusantara Power Services",
        "LEMBAGA SERTIFIKASI PROFESI PLN",
    ]
    issuer, conf = extract_issuer(lines, set(), cfg)
    assert issuer == "LEMBAGA SERTIFIKASI PROFESI PLN"


def test_issuer_join_next(cfg):
    lines = [
        "Lembaga Sertifikasi Profesi",
        "Manajemen Sumber Daya Manusia NUSANTARA",
    ]
    issuer, conf = extract_issuer(lines, set(), cfg)
    assert issuer == "Lembaga Sertifikasi Profesi Manajemen Sumber Daya Manusia NUSANTARA"


def test_doc_type_classification(cfg):
    # Pelatihan
    dt1, c1 = doc_type("Pemeliharaan Gardu Induk", ["Sertifikat Pelatihan Kerja"], cfg)
    assert dt1 == "Pelatihan" and c1 == "medium"

    # Sertifikasi
    dt2, c2 = doc_type("Teknisi Distribusi", ["Sertifikat Kompetensi"], cfg)
    assert dt2 == "Sertifikasi" and c2 == "medium"

    # Default fallback
    dt3, c3 = doc_type(None, ["Surat Keterangan Kerja"], cfg)
    assert dt3 == "Sertifikasi" and c3 == "low"


def test_renewal_number(cfg):
    # Renewal ke-2
    lines_ren = ["Sertifikat Kompetensi", "Perpanjangan ke-2"]
    assert renewal_number(lines_ren, cfg) == 2

    # No renewal
    lines_no = ["Sertifikat Kompetensi", "Nomor: 123/2025"]
    assert renewal_number(lines_no, cfg) is None


def test_template_lines(cfg):
    pages_lines = [
        ["Header Tetap", "Page 1 Content"],
        ["Header Tetap", "Page 2 Content"],
        ["Header Tetap", "Page 3 Content"],
    ]
    tmpl = template_lines(pages_lines, cfg)
    assert "header tetap" in tmpl
