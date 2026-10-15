"""T-018 local OCR for pages without a text layer (R-02, R-20, R-22)."""
import socket

from conftest import DB, FIX, cli, load_plan, run_dir_from


def test_scanned_pages_are_read_by_ocr(run_bundle):
    code, out, rd = run_bundle("bundle_scan")
    assert code == 0, out
    plan = load_plan(rd)
    assert [p["source"] for p in plan["pages"]] == ["ocr", "ocr"]
    assert [g["nid"] for g in plan["groups"]] == ["0044444", "8812345"]
    assert plan["timings"]["ocr_seconds_per_page"] > 0


def test_scanned_pages_without_ocr_are_reported(run_bundle):
    code, out, rd = run_bundle("bundle_scan", "--no-ocr")
    plan = load_plan(rd)
    assert [p["source"] for p in plan["pages"]] == ["none", "none"]
    assert [g["reason"] for g in plan["groups"]] == ["no text", "no text"]


def test_ocr_run_offline(monkeypatch, tmp_path, capsys):
    def boom(*a, **k):
        raise OSError("network blocked by test_ocr")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_scan.pdf"), "--db", str(DB), "--out", str(tmp_path)], capsys)
    assert code == 0, so + se
    assert len(list((run_dir_from(so) / "pdf").glob("*.pdf"))) == 2


def test_background_picture_labels_are_read_by_ocr(run_bundle):
    # Like the real K3 training bundle: only name, number and signatory are in the text layer.
    code, out, rd = run_bundle("bundle_k3_like")
    assert code == 0, out
    plan = load_plan(rd)
    assert [p["source"] for p in plan["pages"]] == ["mixed", "mixed"]
    g1, g2 = plan["groups"]
    assert (g1["nid"], g2["nid"]) == ("0033333", "0045678")  # the cue line removes signatory "Sudarmaji"
    F = g1["fields"]
    assert F["title"]["value"] == "TEKNISI K3 LISTRIK"
    assert F["number"]["value"] == "9101/K3.TEKNISI-LISTRIK/TRN-XYZ/X-2026"
    assert F["issue_date"]["value"] == "2026-10-12"  # last day of "October 05th - 12th, 2026"
    assert F["expiry_date"]["value"] == "2029-10-12" and g1["expiry_source"] == "rule:3y"
    assert F["doc_type"]["value"] == "Pelatihan"


def test_background_picture_bundle_without_ocr_is_not_guessed(run_bundle):
    code, out, rd = run_bundle("bundle_k3_like", "--no-ocr")
    plan = load_plan(rd)
    assert [p["source"] for p in plan["pages"]] == ["text", "text"]
    assert plan["groups"][0]["status"] == "unmatched"  # "Sudarmaji" (an employee) competes; no cue in the text layer
