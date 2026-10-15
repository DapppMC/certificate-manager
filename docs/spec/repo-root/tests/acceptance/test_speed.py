"""T-015 speed on a 30-page text bundle with a 400-row database (R-20)."""
import time
from conftest import DB, FIX, cli, load_plan, run_dir_from


def test_thirty_pages_under_five_minutes(tmp_path, capsys):
    t = time.perf_counter()
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_30.pdf"), "--db", str(DB), "--out", str(tmp_path),
                        "--no-ocr"], capsys)
    elapsed = time.perf_counter() - t
    assert code == 0, so + se
    assert elapsed < 300, elapsed
    plan = load_plan(run_dir_from(so))
    assert plan["timings"]["total_seconds"] > 0
    assert sum(1 for g in plan["groups"] if g["status"] == "accepted") == 30
