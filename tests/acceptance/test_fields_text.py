"""T-011 title, issuer, document type and renewal (R-06)."""
from conftest import load_plan


def V(g, k):
    return g["fields"][k]["value"]


def test_title_and_issuer_from_lsp_bundle(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr", "--dry-run")
    for g in load_plan(rd)["groups"]:
        assert V(g, "title") == "Teknisi Distribusi Tenaga Listrik"
        assert g["fields"]["title"]["confidence"] in ("high", "medium")
        assert V(g, "issuer") == "LEMBAGA SERTIFIKASI PROFESI PLN"
        assert V(g, "doc_type") == "Sertifikasi"


def test_training_bundle(run_bundle):
    code, out, rd = run_bundle("bundle_internal", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert V(g, "title") == "Pemeliharaan Gardu Induk 150 kV"
    assert V(g, "issuer") == "PLN CORPORATE UNIVERSITY"
    assert V(g, "doc_type") == "Pelatihan"


def test_djk_title_and_issuer(run_bundle):
    code, out, rd = run_bundle("bundle_djk", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert V(g, "title") == "Teknisi Gardu Distribusi"
    assert V(g, "issuer") == "DIREKTORAT JENDERAL KETENAGALISTRIKAN"


def test_renewal_number(run_bundle):
    code, out, rd = run_bundle("bundle_titles", "--no-ocr", "--dry-run")
    gs = load_plan(rd)["groups"]
    assert [g["renewal"] for g in gs] == [None, None, 2]


def test_bnsp_layout_title_issuer_number(run_bundle):
    code, out, rd = run_bundle("bundle_bnsp_like", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert V(g, "title") == "Staf Sumber Daya Manusia"
    assert V(g, "issuer") == "Lembaga Sertifikasi Profesi Manajemen Sumber Daya Manusia NUSANTARA"
    assert V(g, "number") == "11111.2222.0.0000101.2026"
    assert V(g, "doc_type") == "Sertifikasi"
