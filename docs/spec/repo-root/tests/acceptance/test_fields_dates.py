"""T-009 certificate number and dates (R-06). Plan stores ISO dates (YYYY-MM-DD)."""
from conftest import load_plan


def F(g, k):
    return g["fields"][k]["value"], g["fields"][k]["confidence"]


def test_indonesian_dates_and_number(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert F(g, "number")[0] == "123/LSP-PLN/VIII/2025"
    assert F(g, "issue_date")[0] == "2025-08-17"
    assert F(g, "expiry_date") == ("2028-08-16", "high")


def test_missing_date_is_empty_and_low(run_bundle):
    code, out, rd = run_bundle("bundle_mixed", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][-1]
    assert g["pages"] == [7]
    assert F(g, "issue_date") == (None, "low")


def test_marked_issue_date_and_registration_number(run_bundle):
    code, out, rd = run_bundle("bundle_djk", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert F(g, "issue_date") == ("2025-08-17", "high")
    assert F(g, "number")[0] == "DJK.0701.2025"
