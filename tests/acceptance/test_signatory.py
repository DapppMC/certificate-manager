"""T-008 signatory and template names are never holders (R-04)."""
from conftest import load_plan


def test_director_on_every_page_is_never_the_holder(run_bundle):
    code, out, rd = run_bundle("bundle_signatory", "--no-ocr", "--dry-run")
    assert code == 0, out
    plan = load_plan(rd)
    assert len(plan["groups"]) == 10
    assert all(g["nid"] != "0099001" for g in plan["groups"])
    assert all(g["status"] == "accepted" for g in plan["groups"])
    assert "Agus Prasetyo" in plan["template_names"]


def test_signatory_close_to_an_employee_name_is_template(run_bundle):
    # "Muhammad Al Fikri" signs every second page; employee "Muhammad Alfi" scores between 80 and 92 there.
    code, out, rd = run_bundle("bundle_bnsp_like", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    assert "Muhammad Alfi" in plan["template_names"]
    assert all(g["nid"] != "0077004" for g in plan["groups"])
