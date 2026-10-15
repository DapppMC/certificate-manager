"""T-012 expiry rule table (R-07)."""
from conftest import load_plan


def test_djk_gets_five_years(run_bundle):
    code, out, rd = run_bundle("bundle_djk", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert g["fields"]["expiry_date"]["value"] == "2030-08-17"
    assert g["expiry_source"] == "rule:5y"


def test_internal_training_does_not_expire(run_bundle):
    code, out, rd = run_bundle("bundle_internal", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert g["fields"]["expiry_date"]["value"] is None
    assert g["fields"]["expiry_date"]["confidence"] != "low"
    assert g["expiry_source"] == "rule:none"


def test_printed_expiry_wins(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert g["fields"]["expiry_date"]["value"] == "2028-08-16"
    assert g["expiry_source"] == "printed"


def test_printed_duration_wins_over_the_rule_table(run_bundle):
    code, out, rd = run_bundle("bundle_bnsp_like", "--no-ocr", "--dry-run")
    g = load_plan(rd)["groups"][0]
    assert g["fields"]["issue_date"]["value"] == "2026-08-20"
    assert g["fields"]["expiry_date"]["value"] == "2029-08-20"
    assert g["fields"]["expiry_date"]["confidence"] == "high"
    assert g["expiry_source"] == "duration:3y"
