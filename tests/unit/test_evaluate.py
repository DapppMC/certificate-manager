from __future__ import annotations

import json
from pathlib import Path
import pytest

from certslice.errors import InputError
from certslice.evaluate import evaluate


def test_evaluate_missing_plan_json(tmp_path: Path) -> None:
    gold = tmp_path / "gold.csv"
    gold.write_text("page,nid,title,number,issue_date,expiry_date\n", encoding="utf-8")
    with pytest.raises(InputError, match="plan.json not found"):
        evaluate(tmp_path, gold)


def test_evaluate_missing_gold_file(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    with pytest.raises(InputError, match="Gold file not found"):
        evaluate(tmp_path, tmp_path / "nonexistent.csv")


def test_evaluate_wrong_nid_gives_exit_4(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = {
        "groups": [
            {
                "id": 1,
                "pages": [1],
                "status": "accepted",
                "nid": "1111111",
                "source": "text",
                "fields": {
                    "title": {"value": "Teknisi K3"},
                    "number": {"value": "123/ABC/2026"},
                    "issue_date": {"value": "2026-10-12"},
                    "expiry_date": {"value": "2029-10-12"},
                },
            }
        ],
        "pages": [{"page": 1, "source": "text"}],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    gold = tmp_path / "gold.csv"
    gold.write_text(
        "page,nid,title,number,issue_date,expiry_date\n"
        "1,2222222,Teknisi K3,123/ABC/2026,12/10/2026,12/10/2029\n",
        encoding="utf-8",
    )

    code = evaluate(tmp_path, gold)
    captured = capsys.readouterr()

    assert code == 4
    assert "WRONG_NID=1" in captured.out
    assert "SET text certificates=1 correct=0 pct=0.0" in captured.out


def test_evaluate_unmatched_group_with_empty_gold_nid_counts_as_correct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = {
        "groups": [
            {
                "id": 1,
                "pages": [1],
                "status": "unmatched",
                "nid": None,
                "source": "text",
                "fields": {
                    "title": {"value": None},
                    "number": {"value": None},
                    "issue_date": {"value": None},
                    "expiry_date": {"value": None},
                },
            }
        ],
        "pages": [{"page": 1, "source": "text"}],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    gold = tmp_path / "gold.csv"
    gold.write_text(
        "page,nid,title,number,issue_date,expiry_date\n"
        "1,,Teknisi K3,123/ABC/2026,12/10/2026,12/10/2029\n",
        encoding="utf-8",
    )

    code = evaluate(tmp_path, gold)
    captured = capsys.readouterr()

    assert code == 0
    assert "WRONG_NID=0" in captured.out
    assert "SET text certificates=1 correct=1 pct=100.0" in captured.out


def test_evaluate_gold_row_page_starts_no_group_counts_not_correct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Page 1 starts group 1 (covering pages [1, 2]). Page 2 does NOT start a group.
    plan = {
        "groups": [
            {
                "id": 1,
                "pages": [1, 2],
                "status": "accepted",
                "nid": "1111111",
                "source": "text",
                "fields": {},
            }
        ],
        "pages": [{"page": 1, "source": "text"}, {"page": 2, "source": "text"}],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    gold = tmp_path / "gold.csv"
    gold.write_text(
        "page,nid,title,number,issue_date,expiry_date\n"
        "2,1111111,,,,\n",
        encoding="utf-8",
    )

    code = evaluate(tmp_path, gold)
    captured = capsys.readouterr()

    assert code == 0
    assert "WRONG_NID=0" in captured.out
    assert "SET text certificates=1 correct=0 pct=0.0" in captured.out


def test_evaluate_scan_source_group_lands_in_scan_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = {
        "groups": [
            {
                "id": 1,
                "pages": [1],
                "status": "accepted",
                "nid": "3333333",
                "source": "ocr",
                "fields": {
                    "title": {"value": "Pelatihan K3"},
                    "number": {"value": "789/XYZ/2025"},
                    "issue_date": {"value": "2025-05-01"},
                    "expiry_date": {"value": None},
                },
            }
        ],
        "pages": [{"page": 1, "source": "ocr"}],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    gold = tmp_path / "gold.csv"
    gold.write_text(
        "page,nid,title,number,issue_date,expiry_date\n"
        "1,3333333,Pelatihan K3,789/XYZ/2025,01/05/2025,\n",
        encoding="utf-8",
    )

    code = evaluate(tmp_path, gold)
    captured = capsys.readouterr()

    assert code == 0
    assert "WRONG_NID=0" in captured.out
    assert "SET text certificates=0 correct=0 pct=n/a" in captured.out
    assert "SET scan certificates=1 correct=1 pct=100.0" in captured.out
    assert "FIELD title text=0/0 scan=1/1" in captured.out
    assert "FIELD number text=0/0 scan=1/1" in captured.out
    assert "FIELD issue_date text=0/0 scan=1/1" in captured.out
    assert "FIELD expiry_date text=0/0 scan=0/0" in captured.out
