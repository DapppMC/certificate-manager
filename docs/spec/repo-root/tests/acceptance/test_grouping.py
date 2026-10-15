"""T-010 certificate boundaries (R-05)."""
from conftest import groups_pages, load_plan


def test_auto_mode_finds_one_and_two_page_certificates(run_bundle):
    code, out, rd = run_bundle("bundle_mixed", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    assert groups_pages(plan) == [[1], [2, 3], [4], [5, 6], [7]]
    assert [g["nid"] for g in plan["groups"]] == ["007123", "0045678", "8812345", "0033333", "0044444"]


def test_fixed_one_page_mode(run_bundle):
    code, out, rd = run_bundle("bundle_mixed", "--no-ocr", "--dry-run", "--pages-per-cert", "1")
    plan = load_plan(rd)
    assert groups_pages(plan) == [[i] for i in range(1, 8)]
    assert plan["groups"][2]["status"] == "unmatched"  # page 3 has no holder of its own


def test_same_holder_with_a_new_number_starts_a_new_certificate(run_bundle):
    code, out, rd = run_bundle("bundle_titles", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    assert groups_pages(plan) == [[1], [2], [3]]


def test_group_number_comes_from_its_first_page(run_bundle):
    code, out, rd = run_bundle("bundle_mixed", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][1]
    assert g["pages"] == [2, 3] and g["fields"]["number"]["value"] == "402/LSP-PLN/X/2025"


def test_two_page_certificates_with_the_name_on_both_pages(run_bundle):
    code, out, rd = run_bundle("bundle_bnsp_like", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    assert groups_pages(plan) == [[1, 2], [3, 4], [5, 6]]
    assert [g["status"] for g in plan["groups"]] == ["accepted", "unmatched", "accepted"]
