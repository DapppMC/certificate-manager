"""T-001 walking skeleton: one real bundle in, named PDFs and plan.json out (R-01, R-09, R-13)."""
from conftest import load_plan


def test_skeleton_writes_one_pdf_per_holder(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    assert code == 0, out
    names = sorted(p.name for p in (rd / "pdf").glob("*.pdf"))
    assert len(names) == 3
    assert any(n.startswith("007123_Budi Santoso") for n in names), names
    assert any(n.startswith("0045678_Siti Rahmawati") for n in names), names
    assert any(n.startswith("8812345_Dewi Lestari") for n in names), names


def test_skeleton_plan_accounts_for_every_page(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    plan = load_plan(rd)
    assert plan["page_count"] == 3
    assert sorted(p for g in plan["groups"] for p in g["pages"]) == [1, 2, 3]
    assert [p["page"] for p in plan["pages"]] == [1, 2, 3]


def test_skeleton_prints_summary_lines(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    assert "PAGES=3 ACCEPTED=3 UNMATCHED=0" in out
