from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

import rapidfuzz

from certslice.config import fold_text
from certslice.errors import InputError


def format_date_ddmmyyyy(val: str) -> str:
    val = val.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val, fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return val


def evaluate(run_dir: Path, gold_path: Path) -> int:
    run_dir = Path(run_dir)
    gold_path = Path(gold_path)

    if not run_dir.exists() or not run_dir.is_dir():
        raise InputError(f"Run directory not found: {run_dir}")

    plan_path = run_dir / "plan.json"
    if not plan_path.exists():
        raise InputError(f"plan.json not found in run directory: {run_dir}")

    if not gold_path.exists() or not gold_path.is_file():
        raise InputError(f"Gold file not found: {gold_path}")

    try:
        plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise InputError(f"Failed to read plan.json: {e}") from e

    try:
        with open(gold_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            gold_rows = list(reader)
    except Exception as e:
        raise InputError(f"Failed to read gold file {gold_path}: {e}") from e

    groups = plan.get("groups", [])
    plan_pages = plan.get("pages", [])

    groups_by_first_page: dict[int, dict[str, Any]] = {}
    for g in groups:
        pages = g.get("pages", [])
        if pages:
            groups_by_first_page[pages[0]] = g

    cert_totals = {"text": 0, "scan": 0}
    cert_correct = {"text": 0, "scan": 0}
    wrong_nid = 0

    fields = ["title", "number", "issue_date", "expiry_date"]
    field_totals = {f: {"text": 0, "scan": 0} for f in fields}
    field_correct = {f: {"text": 0, "scan": 0} for f in fields}

    for row in gold_rows:
        page_str = (row.get("page") or "").strip()
        if not page_str:
            continue
        try:
            page_num = int(page_str)
        except ValueError:
            continue

        gold_nid = (row.get("nid") or "").strip()
        group = groups_by_first_page.get(page_num)

        if group is not None:
            src = group.get("source")
        elif 1 <= page_num <= len(plan_pages):
            src = plan_pages[page_num - 1].get("source")
        else:
            src = "text"

        set_name = "text" if src == "text" else "scan"
        cert_totals[set_name] += 1

        if group is not None:
            status = group.get("status")
            pred_nid = (group.get("nid") or "").strip()

            if gold_nid:
                if status == "accepted" and pred_nid == gold_nid:
                    cert_correct[set_name] += 1
                elif status == "accepted" and pred_nid != gold_nid:
                    wrong_nid += 1
            else:
                if status != "accepted":
                    cert_correct[set_name] += 1
                else:
                    wrong_nid += 1
        else:
            # Group not found for starting page: counts as not correct
            pass

        # Field evaluation
        for field_name in fields:
            gold_val = (row.get(field_name) or "").strip()
            if not gold_val:
                continue

            field_totals[field_name][set_name] += 1

            if group is None:
                continue

            pred_val = group.get("fields", {}).get(field_name, {}).get("value")
            if pred_val is None or not str(pred_val).strip():
                continue

            if field_name == "title":
                r = rapidfuzz.fuzz.token_sort_ratio(fold_text(str(pred_val)), fold_text(gold_val))
                if r >= 90:
                    field_correct[field_name][set_name] += 1
            elif field_name == "number":
                clean_pred = re.sub(r"\s+", "", str(pred_val)).casefold()
                clean_gold = re.sub(r"\s+", "", gold_val).casefold()
                if clean_pred == clean_gold:
                    field_correct[field_name][set_name] += 1
            elif field_name in ("issue_date", "expiry_date"):
                if format_date_ddmmyyyy(str(pred_val)) == format_date_ddmmyyyy(gold_val):
                    field_correct[field_name][set_name] += 1

    for s in ["text", "scan"]:
        t = cert_totals[s]
        c = cert_correct[s]
        pct = f"{c / t * 100:.1f}" if t > 0 else "n/a"
        print(f"SET {s} certificates={t} correct={c} pct={pct}")

    for field_name in fields:
        c1 = field_correct[field_name]["text"]
        t1 = field_totals[field_name]["text"]
        c2 = field_correct[field_name]["scan"]
        t2 = field_totals[field_name]["scan"]
        print(f"FIELD {field_name} text={c1}/{t1} scan={c2}/{t2}")

    print(f"WRONG_NID={wrong_nid}")
    return 4 if wrong_nid > 0 else 0
