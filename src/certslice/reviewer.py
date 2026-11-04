"""Review and interactive correction module for Certificate Slicer."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import pymupdf

from certslice.bulk import update_bulk_workbooks_from_groups
from certslice.config import load_config
from certslice.levels import clean_skp_peran, resolve_skp_level
from certslice.naming import NameBuilder, clean_field
from certslice.writer import write_workbooks


def get_review_items(run_dir: Path) -> list[dict[str, Any]]:
    """Load groups that need review or are unmatched from plan.json in run_dir or its subdirectories."""
    run_dir = Path(run_dir)
    review_items = []

    plan_files = []
    if (run_dir / "plan.json").exists():
        plan_files.append(run_dir / "plan.json")
    for p in sorted(run_dir.rglob("plan.json")):
        if p not in plan_files:
            plan_files.append(p)

    for plan_path in plan_files:
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            for g in data.get("groups", []):
                # Include unmatched groups or groups marked needing review
                if g.get("status") in ["unmatched", "review"] or g.get("status") != "accepted":
                    g["_plan_dir"] = str(plan_path.parent)
                    g["_plan_path"] = str(plan_path)
                    review_items.append(g)
        except Exception:
            continue

    return review_items


def render_page_image(
    pdf_path: Path,
    page_num_1based: int,
    max_width: int | None = None,
    zoom: float = 1.0,
    dpi: int = 150,
) -> bytes | None:
    """Render a 1-based page of a PDF as PNG image bytes with optional zoom and scaling."""
    if not pdf_path.exists():
        return None

    try:
        doc = pymupdf.open(pdf_path)
        page_idx = page_num_1based - 1
        if page_idx < 0 or page_idx >= len(doc):
            doc.close()
            return None

        page = doc.load_page(page_idx)
        rect = page.rect
        orig_w = rect.width
        if orig_w <= 0:
            doc.close()
            return None

        if max_width is not None and max_width > 0:
            base_scale = max_width / orig_w
        else:
            base_scale = dpi / 72.0

        scale = max(0.2, min(5.0, base_scale * zoom))
        mat = pymupdf.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception:
        return None


def approve_group(
    run_dir: Path,
    group_id: int,
    nid: str,
    nama: str,
    email: str = "",
    unit: str = "",
    custom_title: str | None = None,
    custom_year: str | None = None,
    plan_path: Path | str | None = None,
) -> str:
    """Approve an unmatched group, slice the PDF, save it, and update manifests."""
    run_dir = Path(run_dir)
    if plan_path:
        plan_path = Path(plan_path)
        run_dir = plan_path.parent
    else:
        plan_path = run_dir / "plan.json"
        if not plan_path.exists():
            for p in sorted(run_dir.rglob("plan.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    if any(g.get("id") == group_id and g.get("status") != "accepted" for g in d.get("groups", [])):
                        plan_path = p
                        run_dir = p.parent
                        break
                except Exception:
                    continue

            if not plan_path.exists():
                for p in sorted(run_dir.rglob("plan.json")):
                    try:
                        d = json.loads(p.read_text(encoding="utf-8"))
                        if any(g.get("id") == group_id for g in d.get("groups", [])):
                            plan_path = p
                            run_dir = p.parent
                            break
                    except Exception:
                        continue

    if not plan_path.exists():
        raise FileNotFoundError(f"plan.json not found in {run_dir}")

    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    groups = plan_data.get("groups", [])

    target_grp = None
    for g in groups:
        if g.get("id") == group_id:
            target_grp = g
            break

    if not target_grp:
        raise ValueError(f"Group ID {group_id} not found in plan.json")

    cfg = load_config(None)
    builder = NameBuilder(run_dir.parent, cfg)

    # Determine title & year
    judul = custom_title or target_grp.get("fields", {}).get("title", {}).get("value") or "Tanpa Judul"
    iss_date = target_grp.get("fields", {}).get("issue_date", {}).get("value")
    tahun = custom_year or (iss_date.split("-")[0] if iss_date else "0000")

    is_bulk = plan_data.get("mode") == "bulk"
    is_skp = bool(target_grp.get("fields", {}).get("is_skp", {}).get("value"))
    level = target_grp.get("fields", {}).get("level", {}).get("value") or ""
    peran = target_grp.get("fields", {}).get("peran", {}).get("value") or ""

    if is_bulk:
        clean_nid = clean_field(nid)
        clean_nama = clean_field(nama) or "Tanpa Nama"
        clean_judul = clean_field(judul) or "Tanpa Judul"
        if is_skp and (level or peran):
            resolved_level = resolve_skp_level(level)
            resolved_peran = clean_skp_peran(peran or judul)
            clean_level = clean_field(resolved_level)
            clean_peran = clean_field(resolved_peran)
            parts = [p for p in [clean_nid, clean_nama, clean_level, clean_peran] if p]
            base_name = "_".join(parts) or clean_nama
        else:
            # User requirement: non-SKP is {NID}_{Nama}_{Judul}
            parts = [p for p in [clean_nid, clean_nama, clean_judul] if p]
            base_name = "_".join(parts) or clean_nama

        base_key = base_name.lower()
        count = builder.seen_names.get(base_key, 0) + 1
        builder.seen_names[base_key] = count
        if count == 1:
            file_name = f"{base_name}.pdf"
        else:
            dup_suffix = builder.duplicate_suffix.format(n=count)
            file_name = f"{base_name}{dup_suffix}.pdf"
    elif not (nid and nid.strip()):
        clean_nama = clean_field(nama) or "Tanpa Nama"
        clean_judul = clean_field(judul) or "Tanpa Judul"
        clean_tahun = clean_field(tahun) or "0000"
        ren_val = target_grp.get("renewal")
        ren_suffix = builder.renewal_suffix.format(ke=ren_val) if ren_val is not None else ""
        base_name = f"{clean_nama}_{clean_judul}_{clean_tahun}{ren_suffix}"
        base_key = base_name.lower()
        count = builder.seen_names.get(base_key, 0) + 1
        builder.seen_names[base_key] = count
        if count == 1:
            file_name = f"{base_name}.pdf"
        else:
            dup_suffix = builder.duplicate_suffix.format(n=count)
            file_name = f"{base_name}{dup_suffix}.pdf"
    else:
        file_name = builder.build(
            nid=nid.strip(),
            nama=nama.strip(),
            judul=judul,
            tahun=tahun,
            renewal=target_grp.get("renewal"),
        )

    # Slice / Copy PDF if source PDF exists, or rename existing [REVIEW]_ file
    input_pdf_path = Path(target_grp.get("src_pdf") or plan_data.get("input_pdf", ""))
    pdf_dir = run_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    target_out_path = pdf_dir / file_name

    saved_from_src = False
    if input_pdf_path.exists():
        is_bundle_item = target_grp.get("is_bundle", not is_bulk)
        if is_bundle_item:
            try:
                doc = pymupdf.open(input_pdf_path)
                first_idx = target_grp["pages"][0] - 1
                last_idx = target_grp["pages"][-1] - 1
                new_doc = pymupdf.open()
                new_doc.insert_pdf(doc, from_page=first_idx, to_page=last_idx)
                new_doc.save(target_out_path)
                new_doc.close()
                doc.close()
                saved_from_src = True
            except Exception:
                pass
        else:
            try:
                shutil.copy2(input_pdf_path, target_out_path)
                saved_from_src = True
            except Exception:
                pass

    # Clean up old review files and ensure target_out_path exists
    old_names = set()
    if target_grp.get("review_file_name"):
        old_names.add(target_grp["review_file_name"])
    if target_grp.get("file_name"):
        old_names.add(target_grp["file_name"])

    for old_n in old_names:
        if old_n and old_n != file_name:
            old_p = pdf_dir / old_n
            if old_p.exists():
                if not saved_from_src and not target_out_path.exists():
                    try:
                        shutil.copy2(old_p, target_out_path)
                        saved_from_src = True
                    except Exception:
                        pass
                try:
                    old_p.unlink()
                except Exception:
                    pass

    # Extra safety: remove any [REVIEW]_ file matching the clean name
    stray_rev = pdf_dir / f"[REVIEW]_{file_name}"
    if stray_rev.exists() and stray_rev != target_out_path:
        try:
            stray_rev.unlink()
        except Exception:
            pass

    # Update group record in plan
    target_grp["status"] = "accepted"
    target_grp["reason"] = ""
    target_grp["nid"] = nid
    target_grp["nama"] = nama
    target_grp["email"] = email
    target_grp["unit"] = unit
    target_grp["file_name"] = file_name
    target_grp["review_file_name"] = None
    if "fields" not in target_grp:
        target_grp["fields"] = {}
    if "title" not in target_grp["fields"]:
        target_grp["fields"]["title"] = {}
    target_grp["fields"]["title"]["value"] = judul
    target_grp["fields"]["title"]["confidence"] = "high"
    if is_skp:
        if "level" not in target_grp["fields"]:
            target_grp["fields"]["level"] = {}
        target_grp["fields"]["level"]["value"] = resolve_skp_level(level)
        if "peran" not in target_grp["fields"]:
            target_grp["fields"]["peran"] = {}
        target_grp["fields"]["peran"]["value"] = clean_skp_peran(peran or judul)

    # Save updated plan.json
    plan_path.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Re-generate workbooks
    if is_bulk:
        update_bulk_workbooks_from_groups(run_dir, groups)
    else:
        write_workbooks(run_dir, groups, cfg)
        if (run_dir / "manifest_slice.xlsx").exists():
            try:
                shutil.copy2(run_dir / "manifest_slice.xlsx", run_dir / "data.xlsx")
            except Exception:
                pass

    return file_name
