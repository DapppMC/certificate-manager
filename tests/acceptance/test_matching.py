"""T-006 reverse lookup and the conservative decision (R-03, R-08)."""
from conftest import load_plan


def test_titles_and_case_do_not_block_a_match(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    g1 = plan["groups"][0]
    assert g1["status"] == "accepted" and g1["nid"] == "007123"
    assert plan["pages"][0]["candidates"][0]["nid"] == "007123"
    assert plan["pages"][0]["candidates"][0]["score"] >= 92


def test_homonym_and_stranger_are_not_accepted(run_bundle):
    code, out, rd = run_bundle("bundle_unmatched", "--no-ocr", "--dry-run")
    plan = load_plan(rd)
    by_first = {g["pages"][0]: g for g in plan["groups"]}
    hom = by_first[1]
    assert hom["status"] == "unmatched" and hom["nid"] is None
    assert hom["reason"] == "homonym"
    assert {c["nid"] for c in hom["candidates"]} >= {"0011111", "0022222"}
    assert {c["unit"] for c in hom["candidates"]} >= {"UID Jakarta Raya", "UIW Sumatera Utara"}
    stranger = by_first[2]
    assert stranger["status"] == "unmatched" and stranger["reason"] == "no confident holder"
    ok = by_first[3]
    assert ok["status"] == "accepted" and ok["nid"] == "0033333"
    close = by_first[4]
    assert close["status"] == "unmatched" and close["reason"] == "close candidates"
    assert {c["nid"] for c in close["candidates"][:2]} == {"0066001", "0066002"}


def test_every_accepted_group_has_a_clear_winner(run_bundle):
    for bundle in ("bundle_simple", "bundle_unmatched", "bundle_30"):
        code, out, rd = run_bundle(bundle, "--no-ocr", "--dry-run")
        plan = load_plan(rd)
        for g in plan["groups"]:
            if g["status"] != "accepted":
                continue
            c = g["candidates"]
            assert c[0]["nid"] == g["nid"] and c[0]["score"] >= 92
            if len(c) > 1:
                assert c[0]["score"] - c[1]["score"] >= 5
