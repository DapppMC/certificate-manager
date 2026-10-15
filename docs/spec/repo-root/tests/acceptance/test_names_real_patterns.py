"""T-007 one-word names and shortened names (R-03, R-08). Patterns from the real master data (2026-10-15):
610 one-word names such as "SUTRISNA", and certificates that print only part of a long name."""
from conftest import load_plan


def test_one_word_name_inside_a_longer_name_is_dropped(run_bundle):
    code, out, rd = run_bundle("bundle_bnsp_like", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    by_first = {g["pages"][0]: g for g in plan["groups"]}
    assert by_first[1]["status"] == "accepted" and by_first[1]["nid"] == "0077005"
    assert "0077001" not in [c["nid"] for c in plan["pages"][0]["candidates"]]


def test_shortened_name_is_reported_with_the_likely_employee(run_bundle):
    code, out, rd = run_bundle("bundle_bnsp_like", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    short = {g["pages"][0]: g for g in plan["groups"]}[3]
    assert short["status"] == "unmatched" and short["reason"] == "partial name"
    assert short["candidates"][0]["nid"] == "0077003"
    assert short["candidates"][0]["score"] < 92
