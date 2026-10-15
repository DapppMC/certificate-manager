from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import time

from certslice.config import fold_text, load_aliases, load_config
from certslice.database import load_employees
from certslice.errors import AccountingError, InputError
from certslice.expiry import apply_rules
from certslice.gemini import GeminiRescue, load_user_settings
from certslice.fields import (
    doc_type,
    extract_dates,
    extract_issuer,
    extract_number,
    extract_title,
    renewal_number,
    template_lines,
)
from certslice.grouping import PageInfo, check_accounting, group_pages
from certslice.matcher import cue_filter, decide, page_candidates, template_employees
from certslice.naming import NameBuilder, clean_field
from certslice.normalize import fold
from certslice.reader import LocalPageReader, open_pdf
from certslice.writer import write_workbooks
import pymupdf

logger = logging.getLogger("certslice")


@dataclass
class RunResult:
    run_dir: Path | None
    exit_code: int
    summary: dict


def run(
    pdf_path=None,
    db_path=None,
    out_dir=None,
    *,
    pdf_file=None,
    db_file=None,
    out_path=None,
    dry_run=False,
    pages_per_cert="auto",
    ocr=True,
    ocr_enabled=None,
    config_path=None,
    reader=None,
    use_ai=False,
    gemini_api_key=None,
    gemini_model="gemini-2.5-flash",
    run_name=None,
) -> RunResult:
    t0 = time.perf_counter()
    cfg = load_config(config_path)
    if not gemini_api_key:
        settings = load_user_settings()
        gemini_api_key = settings.get("gemini_api_key")
        if not use_ai and settings.get("use_ai"):
            use_ai = True
    if ocr_enabled is not None:
        ocr = ocr_enabled
    pdf_file = Path(pdf_path or pdf_file)
    db_file = Path(db_path or db_file)
    out_path = Path(out_dir or out_path)

    # (a) Database validation
    employees = load_employees(db_file, cfg)

    # (b) Open PDF
    doc = open_pdf(pdf_file)
    num_pages = len(doc)

    # (c) Reader setup
    if reader is None:
        rdr = LocalPageReader(cfg, ocr=ocr)
    else:
        rdr = reader

    all_page_pts = []
    all_page_lines = []
    all_page_raw_cands = []

    for page_idx in range(num_pages):
        page = doc[page_idx]
        pt = rdr.read(page)
        lines = [line for line in pt.text.splitlines() if line.strip()]
        cands = page_candidates(lines, employees, cfg)
        all_page_pts.append(pt)
        all_page_lines.append(lines)
        all_page_raw_cands.append(cands)

    t_read = time.perf_counter()
    logger.info("read %d pages", num_pages)

    template_indices = template_employees(all_page_raw_cands, num_pages, cfg)
    template_names = sorted(employees[i].nama for i in template_indices)

    page_records = []
    page_infos = []
    page_cands_list = []

    accept_score = cfg.get("matching", {}).get("accept_score", 92)
    continuation_patterns = cfg.get("grouping", {}).get("continuation_patterns", [])

    for page_idx in range(num_pages):
        page_num = page_idx + 1
        pt = all_page_pts[page_idx]
        lines = all_page_lines[page_idx]
        source = pt.source

        # Remove template employees
        cands = [c for c in all_page_raw_cands[page_idx] if c.index not in template_indices]
        # Apply cue filter
        cands = cue_filter(cands, lines, cfg)

        status, top, reason = decide(cands, employees, cfg)

        is_start = bool(cands and cands[0].score >= accept_score)
        page_num_str = extract_number(lines, cfg)
        folded_text = fold(pt.text)
        continuation = any(re.search(pat, folded_text, re.IGNORECASE) for pat in continuation_patterns)

        page_infos.append(
            PageInfo(
                decision=(status, top, reason),
                is_start=is_start,
                number=page_num_str,
                continuation=continuation,
                source=source,
            )
        )
        page_cands_list.append(cands)

        cand_page = []
        for c in cands[:3]:
            cd = {
                "nid": employees[c.index].nid,
                "nama": employees[c.index].nama,
                "score": round(c.score, 1),
            }
            if c.partial:
                cd["partial"] = True
            cand_page.append(cd)

        page_records.append({
            "page": page_num,
            "source": source,
            "candidates": cand_page,
        })

    # Group pages and check accounting
    raw_groups = group_pages(page_infos, employees, pages_per_cert, cfg)
    try:
        check_accounting(raw_groups, num_pages)
    except AccountingError as exc:
        out_path.mkdir(parents=True, exist_ok=True)
        base_name = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run_dir = out_path / base_name
        counter = 2
        while run_dir.exists():
            run_dir = out_path / f"{base_name}-{counter}"
            counter += 1
        run_dir.mkdir(parents=True, exist_ok=True)

        plan_groups = []
        for grp_idx, rg in enumerate(raw_groups):
            plan_groups.append({
                "id": grp_idx + 1,
                "pages": rg["pages"],
                "status": rg["status"],
                "reason": rg["reason"],
                "nid": None,
                "nama": None,
                "email": None,
                "unit": None,
                "candidates": [],
                "fields": {},
                "expiry_source": None,
                "renewal": None,
                "file_name": None,
            })

        ocr_times = getattr(rdr, "ocr_seconds", [])
        ocr_pages = len(ocr_times)
        ocr_seconds_per_page = round(sum(ocr_times) / ocr_pages, 3) if ocr_pages > 0 else None
        plan_data = {
            "version": 1,
            "input_pdf": str(pdf_file),
            "database": str(db_file),
            "page_count": num_pages,
            "dry_run": dry_run,
            "pages_per_cert": pages_per_cert,
            "template_names": template_names,
            "pages": page_records,
            "groups": plan_groups,
            "timings": {
                "read_seconds": round(t_read - t0, 3),
                "total_seconds": round(time.perf_counter() - t0, 3),
                "ocr_pages": ocr_pages,
                "ocr_seconds_per_page": ocr_seconds_per_page,
            },
        }
        plan_path = run_dir / "plan.json"
        plan_path.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")
        doc.close()
        exc.run_dir = run_dir
        raise exc

    tmpl_lines = template_lines(all_page_lines, cfg)

    groups = []
    for grp_idx, rg in enumerate(raw_groups):
        grp_id = grp_idx + 1
        grp_pages = rg["pages"]
        deciding_p = rg["deciding_page"]
        first_p = grp_pages[0]

        first_page_lines = all_page_lines[first_p - 1]
        group_lines = []
        for p in grp_pages:
            group_lines.extend(all_page_lines[p - 1])

        group_is_bundle = (len(grp_pages) == num_pages)

        status = rg["status"]
        reason = rg["reason"]
        cand = rg["candidate"]

        deciding_cands = page_cands_list[deciding_p - 1]
        cand_group = []
        for c in deciding_cands[:3]:
            cd = {
                "nid": employees[c.index].nid,
                "nama": employees[c.index].nama,
                "unit": employees[c.index].unit,
                "score": round(c.score, 1),
            }
            if c.partial:
                cd["partial"] = True
            cand_group.append(cd)

        num_val = extract_number(first_page_lines, cfg)
        num_conf = "high" if num_val is not None else "low"
        iss_val, iss_conf, exp_val, exp_conf = extract_dates(group_lines, cfg)
        group_text_folded = " ".join(fold(line) for line in group_lines)
        exp_val, exp_conf, expiry_source = apply_rules(iss_val, exp_val, exp_conf, group_text_folded, cfg)

        title_val, title_conf = extract_title(first_page_lines, tmpl_lines, group_is_bundle, num_pages, cfg)
        issuer_val, issuer_conf = extract_issuer(group_lines, tmpl_lines, cfg)
        type_val, type_conf = doc_type(title_val, group_lines, cfg)
        ren_val = renewal_number(group_lines, cfg)

        fields_dict = {
            "number": {"value": num_val, "confidence": num_conf},
            "issue_date": {"value": iss_val.isoformat() if iss_val else None, "confidence": iss_conf},
            "expiry_date": {"value": exp_val.isoformat() if exp_val else None, "confidence": exp_conf},
            "title": {"value": title_val, "confidence": title_conf},
            "issuer": {"value": issuer_val, "confidence": issuer_conf},
            "doc_type": {"value": type_val, "confidence": type_conf},
        }

        first_source = all_page_pts[first_p - 1].source

        if status != "accepted" and use_ai and gemini_api_key:
            first_page_obj = doc[first_p - 1]
            rescue_engine = GeminiRescue(api_key=gemini_api_key, model=gemini_model)
            rescue_res = rescue_engine.rescue_group(first_page_obj, deciding_cands, employees)
            if rescue_res and rescue_res.get("candidate") is not None:
                cand = rescue_res["candidate"]
                status = "accepted"
                reason = ""
                first_source = "ai_rescue"
                if rescue_res.get("number") and not num_val:
                    num_val = rescue_res["number"]
                    fields_dict["number"] = {"value": num_val, "confidence": "high"}
                if rescue_res.get("title") and not title_val:
                    title_val = rescue_res["title"]
                    fields_dict["title"] = {"value": title_val, "confidence": "high"}
                if rescue_res.get("issue_date") and not iss_val:
                    try:
                        iss_val = date.fromisoformat(rescue_res["issue_date"])
                        fields_dict["issue_date"] = {"value": rescue_res["issue_date"], "confidence": "high"}
                    except Exception:
                        pass
                if rescue_res.get("expiry_date") and not exp_val:
                    try:
                        exp_val = date.fromisoformat(rescue_res["expiry_date"])
                        fields_dict["expiry_date"] = {"value": rescue_res["expiry_date"], "confidence": "high"}
                    except Exception:
                        pass

        if status == "accepted" and cand is not None:
            matched_emp = employees[cand.index]
            group_entry = {
                "id": grp_id,
                "pages": grp_pages,
                "status": status,
                "reason": reason,
                "nid": matched_emp.nid,
                "nama": matched_emp.nama,
                "email": matched_emp.email,
                "unit": matched_emp.unit,
                "candidates": cand_group,
                "fields": fields_dict,
                "expiry_source": expiry_source,
                "renewal": ren_val,
                "file_name": None,
                "source": first_source,
            }
        else:
            group_entry = {
                "id": grp_id,
                "pages": grp_pages,
                "status": status,
                "reason": reason,
                "nid": None,
                "nama": None,
                "email": None,
                "unit": None,
                "candidates": cand_group,
                "fields": fields_dict,
                "expiry_source": expiry_source,
                "renewal": ren_val,
                "file_name": None,
                "source": first_source,
            }
        groups.append(group_entry)

    logger.info("grouped into %d certificates", len(groups))

    # (e.2) Apply aliases and standard file naming before creating output folder
    aliases = load_aliases(cfg)
    builder = NameBuilder(out_path, cfg)
    unknown_title = cfg.get("naming", {}).get("unknown_title", "Tanpa Judul")
    unknown_year = cfg.get("naming", {}).get("unknown_year", "0000")

    for grp in groups:
        raw_title = grp["fields"]["title"]["value"]
        if raw_title is not None:
            judul = aliases.get(fold_text(raw_title), raw_title)
        else:
            judul = unknown_title
        grp["fields"]["title"]["value"] = judul

        if grp["status"] == "accepted":
            iss_str = grp["fields"]["issue_date"]["value"]
            tahun = iss_str.split("-")[0] if iss_str else unknown_year
            grp["file_name"] = builder.build(
                grp["nid"],
                grp["nama"],
                judul,
                tahun,
                grp["renewal"],
            )
        else:
            grp["file_name"] = None

    # (f) Create run folder
    out_path.mkdir(parents=True, exist_ok=True)
    if run_name:
        base_name = run_name
    else:
        base_name = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir = out_path / base_name
    counter = 2
    while run_dir.exists():
        run_dir = out_path / f"{base_name}-{counter}"
        counter += 1
    run_dir.mkdir(parents=True, exist_ok=True)

    # (g) Write PDFs and workbooks unless dry_run
    if not dry_run:
        pdf_dir = run_dir / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        for grp in groups:
            first_idx = grp["pages"][0] - 1
            last_idx = grp["pages"][-1] - 1
            new_doc = pymupdf.open()
            new_doc.insert_pdf(doc, from_page=first_idx, to_page=last_idx)
            if grp["status"] == "accepted":
                new_doc.save(pdf_dir / grp["file_name"])
            else:
                cands = grp.get("candidates", [])
                cand_str = f"_{cands[0]['nid']}_{clean_field(cands[0]['nama'])}" if cands else ""
                pg_str = f"Hal{grp['pages'][0]}" if len(grp["pages"]) == 1 else f"Hal{grp['pages'][0]}-{grp['pages'][-1]}"
                review_fname = f"[REVIEW]_{pg_str}{cand_str}.pdf"
                grp["review_file_name"] = review_fname
                new_doc.save(pdf_dir / review_fname)
            new_doc.close()

        write_workbooks(run_dir, groups, cfg)
        logger.info("wrote outputs to %s", run_dir)

    doc.close()

    ocr_times = getattr(rdr, "ocr_seconds", [])
    ocr_pages = len(ocr_times)
    ocr_seconds_per_page = round(sum(ocr_times) / ocr_pages, 3) if ocr_pages > 0 else None

    # (h) Write plan.json
    plan_data = {
        "version": 1,
        "input_pdf": str(pdf_file),
        "database": str(db_file),
        "page_count": num_pages,
        "dry_run": dry_run,
        "pages_per_cert": pages_per_cert,
        "template_names": template_names,
        "pages": page_records,
        "groups": groups,
        "timings": {
            "read_seconds": round(t_read - t0, 3),
            "total_seconds": round(time.perf_counter() - t0, 3),
            "ocr_pages": ocr_pages,
            "ocr_seconds_per_page": ocr_seconds_per_page,
        },
    }

    plan_path = run_dir / "plan.json"
    plan_path.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # (i) Summary and return
    accepted_count = sum(1 for g in groups if g["status"] == "accepted")
    unmatched_count = sum(1 for g in groups if g["status"] == "unmatched")
    summary = {
        "pages": num_pages,
        "accepted": accepted_count,
        "unmatched": unmatched_count,
    }

    return RunResult(run_dir=run_dir, exit_code=0, summary=summary)
