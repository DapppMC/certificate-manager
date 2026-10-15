"""T-015 end-to-end pipeline rules (R-09, R-13, R-14, R-21)."""
import shutil
import subprocess
import sys

from conftest import DB, FIX, ROOT, SRC, assert_accounting, cli, manifest, run_dir_from

BUNDLES = ["bundle_simple", "bundle_signatory", "bundle_mixed", "bundle_unmatched", "bundle_djk",
           "bundle_internal", "bundle_titles", "bundle_30", "bundle_bnsp_like"]


def test_dry_run_writes_plan_only(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr", "--dry-run")
    assert code == 0
    assert sorted(p.name for p in rd.iterdir()) == ["plan.json"]


def test_real_run_writes_all_outputs(run_bundle):
    code, out, rd = run_bundle("bundle_simple", "--no-ocr")
    assert {p.name for p in rd.iterdir()} == {"plan.json", "pdf", "manifest_slice.xlsx", "unmatched_report.xlsx"}


def test_every_page_accounted_in_every_bundle(run_bundle):
    for b in BUNDLES:
        code, out, rd = run_bundle(b, "--no-ocr")
        assert code == 0, (b, out)
        assert_accounting(rd)


def test_rerun_never_overwrites(tmp_path, capsys):
    args = ["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(DB), "--out", str(tmp_path), "--no-ocr"]
    c1, o1, _ = cli(args, capsys)
    c2, o2, _ = cli(args, capsys)
    r1, r2 = run_dir_from(o1), run_dir_from(o2)
    assert c1 == c2 == 0 and r1 != r2 and r1.exists() and r2.exists()
    assert manifest(r1) == manifest(r2)


def test_paths_with_spaces_and_module_entry_point(tmp_path):
    folder = tmp_path / "Folder With Spaces"
    folder.mkdir()
    shutil.copy(FIX / "bundle_simple.pdf", folder / "bundel sertifikat.pdf")
    env = {**__import__("os").environ, "PYTHONPATH": str(SRC)}
    p = subprocess.run([sys.executable, "-m", "certslice", "run", "--pdf", str(folder / "bundel sertifikat.pdf"),
                        "--db", str(DB), "--out", str(folder / "hasil"), "--no-ocr"],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    rd = run_dir_from(p.stdout)
    assert len(list((rd / "pdf").glob("*.pdf"))) == 3
