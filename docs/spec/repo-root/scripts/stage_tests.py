"""Run the acceptance tests that must pass after a given ticket, plus all unit tests (planner-owned, protected).

Usage (through the wrapper, from the repo root, with the project's virtual environment):
  .\\.venv\\Scripts\\python.exe scripts/run_check.py --name t006 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-006
  python scripts/stage_tests.py --only T-006      # only that ticket's acceptance files
  python scripts/stage_tests.py --list            # show the stages
Exit code = pytest's exit code. Standard library only (pytest must be installed in the same interpreter).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = "tests/acceptance/"
STAGES: list[tuple[str, list[str]]] = [
    ("T-001", [A + "test_t001_skeleton.py"]),
    ("T-002", []),
    ("T-003", [A + "test_normalize_contract.py"]),
    ("T-004", [A + "test_database.py"]),
    ("T-005", [A + "test_pdf_input.py", A + "test_reader_hook.py"]),
    ("T-006", [A + "test_matching.py"]),
    ("T-007", [A + "test_names_real_patterns.py"]),
    ("T-008", [A + "test_signatory.py"]),
    ("T-009", [A + "test_fields_dates.py", A + "test_dates_contract.py"]),
    ("T-010", [A + "test_grouping.py"]),
    ("T-011", [A + "test_fields_text.py"]),
    ("T-012", [A + "test_expiry.py"]),
    ("T-013", [A + "test_naming.py"]),
    ("T-014", [A + "test_outputs.py"]),
    ("T-015", [A + "test_pipeline.py", A + "test_speed.py", A + "test_offline.py"]),
    ("T-016", []),
    ("T-018", [A + "test_ocr.py"]),
    ("T-019", [A + "test_evaluate.py"]),
]
IDS = [s for s, _ in STAGES]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--upto", choices=IDS, help="all acceptance files up to and including this ticket")
    g.add_argument("--only", choices=IDS, help="only this ticket's acceptance files (no unit tests)")
    g.add_argument("--all", action="store_true", help="every acceptance file and every unit test")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for sid, files in STAGES:
            print(sid, " ".join(files) or "(unit tests only)")
        return 0
    files: list[str] = []
    if a.only:
        files = dict(STAGES)[a.only]
    else:
        stop = IDS.index(a.upto) if a.upto else len(IDS) - 1
        for sid, fl in STAGES[: stop + 1]:
            files += fl
        if (ROOT / "tests" / "unit").is_dir():
            files.append("tests/unit")
    if not files:
        print("no tests ran (this stage has no acceptance files and tests/unit does not exist)")
        return 5
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
    print("RUN:", " ".join(cmd[1:]))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
