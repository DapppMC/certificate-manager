"""Certificate Manager — Precision Studio Web Edition (FastAPI + Self-Contained UI).

Provides a 100% offline-capable, high-throughput web studio for automated PDF splitting,
lock-free OCR extraction, vectorized fuzzy database matching, SKP & Resource Code resolution,
batch interactive review, multi-folder / FTP fast-rekap processing, and executive analytics.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
import uuid
import zipfile

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
import pymupdf

from certslice.bulk import (
    BulkItem,
    BulkResult,
    MultiFolderResult,
    process_bulk_files,
    process_multi_folder_tree,
    process_preformatted_ftp_folder,
    resync_bulk_outputs,
)
from certslice.gemini import load_user_settings, save_user_settings
from certslice.levels import clean_skp_peran, resolve_skp_level
from certslice.naming import clean_field
from certslice.resource_codes import lookup_resource_code
from certslice.reviewer import render_page_image

logger = logging.getLogger("certslice.web")

app = FastAPI(title="Certificate Manager — Precision Studio", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global in-memory job store
JOBS: dict[str, dict[str, Any]] = {}
BASE_DIR = Path(__file__).resolve().parent.parent.parent


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    """System health and default configuration paths."""
    sample_db = BASE_DIR / "templates" / "sample_database.xlsx"
    default_out = BASE_DIR / "out"
    settings = load_user_settings()
    return {
        "status": "online",
        "version": "3.0.0",
        "platform": sys.platform,
        "sample_db_available": sample_db.exists(),
        "sample_db_path": str(sample_db.resolve()) if sample_db.exists() else "",
        "default_out_dir": str(default_out.resolve()),
        "active_jobs": len(JOBS),
        "has_api_key": bool(settings.get("gemini_api_key")),
        "default_model": settings.get("model", "gemini-flash-lite-latest"),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """Retrieve stored user settings from ~/.certslice_settings.json."""
    settings = load_user_settings()
    return {
        "gemini_api_key": settings.get("gemini_api_key", ""),
        "use_ai": bool(settings.get("use_ai", False)),
        "model": settings.get("model", "gemini-flash-lite-latest"),
        "default_db_path": settings.get("default_db_path", ""),
        "default_out_path": settings.get("default_out_path", ""),
    }


@app.post("/api/settings")
async def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Save user settings permanently."""
    save_user_settings(payload)
    return {"status": "success", "message": "Pengaturan berhasil disimpan secara permanen."}


def _run_processing_job(
    job_id: str,
    pdf_paths: list[Path],
    db_path: Path,
    out_dir: Path,
    use_ocr: bool,
    use_ai: bool,
    gemini_key: str | None,
    gemini_model: str,
    run_name: str | None,
    mode: str = "standard",
    local_root_dir: Path | None = None,
) -> None:
    job = JOBS[job_id]
    job["status"] = "processing"
    t0 = time.perf_counter()

    def progress_callback(curr: int, tot: int, desc: str) -> None:
        pct = int((curr / max(1, tot)) * 100)
        job["progress"] = min(99, max(0, pct))
        job["current"] = curr
        job["total"] = tot
        job["message"] = desc
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] ({curr}/{tot}) {desc}"
        job["logs"].append(log_line)
        logger.info(log_line)

    try:
        if mode == "multi_folder" and local_root_dir is not None:
            progress_callback(1, 10, f"Memindai struktur multi-folder: {local_root_dir.name}...")
            mf_res: MultiFolderResult = process_multi_folder_tree(
                root_dir=local_root_dir,
                db_path=db_path,
                out_dir=out_dir,
                use_ai=use_ai,
                gemini_api_key=gemini_key,
                gemini_model=gemini_model,
                use_ocr=use_ocr,
                progress_callback=progress_callback,
            )
            combined_items: list[BulkItem] = []
            combined_lampiran: list[BulkItem] = []
            for sub_res in mf_res.folder_results.values():
                for it in sub_res.items:
                    it.index = len(combined_items) + 1
                    combined_items.append(it)
                for lit in sub_res.lampiran_items:
                    lit.index = len(combined_lampiran) + 1
                    combined_lampiran.append(lit)
            res = BulkResult(
                run_dir=mf_res.run_dir,
                total_files=mf_res.total_files,
                accepted_count=mf_res.accepted_count,
                review_count=mf_res.review_count,
                items=combined_items,
                lampiran_items=combined_lampiran,
            )
        elif mode == "ftp_fast_rekap" and local_root_dir is not None:
            progress_callback(1, 10, f"Rekap Cepat FTP (Tanpa OCR): {local_root_dir.name}...")
            ftp_out_dir = out_dir / f"Hasil_FTP_{clean_field(local_root_dir.name)}_{datetime.now().strftime('%d-%b-%Y_%H.%M')}"
            ftp_out_dir.mkdir(parents=True, exist_ok=True)
            ftp_res = process_preformatted_ftp_folder(
                root_dir=local_root_dir,
                db_path=db_path,
                out_dir=ftp_out_dir,
                progress_callback=progress_callback,
            )
            res = BulkResult(
                run_dir=ftp_out_dir,
                total_files=int(ftp_res.get("total_files", 0)),
                accepted_count=int(ftp_res.get("total_files", 0)),
                review_count=0,
                items=[],
                lampiran_items=[],
            )
        else:
            progress_callback(1, max(1, len(pdf_paths)), "Memulai analisis berkas...")
            res = process_bulk_files(
                pdf_paths=pdf_paths,
                db_path=db_path,
                out_dir=out_dir,
                use_ai=use_ai,
                gemini_api_key=gemini_key,
                gemini_model=gemini_model,
                run_name=run_name,
                progress_callback=progress_callback,
                use_ocr=use_ocr,
            )

        elapsed = round(time.perf_counter() - t0, 2)
        job["elapsed_sec"] = elapsed
        job["result"] = res
        job["run_dir"] = str(res.run_dir.resolve())
        job["status"] = "completed"
        job["progress"] = 100
        job["message"] = f"Pemrosesan selesai dalam {elapsed:.2f} detik!"
        timestamp = datetime.now().strftime("%H:%M:%S")
        job["logs"].append(
            f"[{timestamp}] Selesai ({elapsed:.2f}s)! {res.accepted_count} diterima, {res.review_count} perlu review."
        )
    except Exception as exc:
        logger.exception("Job processing failed")
        job["status"] = "error"
        job["error"] = str(exc)
        timestamp = datetime.now().strftime("%H:%M:%S")
        job["logs"].append(f"[{timestamp}] ERROR: {str(exc)}")


@app.post("/api/process")
async def start_process(
    files: list[UploadFile] = File(...),
    db_file: UploadFile | None = File(None),
    use_sample_db: bool = Form(False),
    use_ocr: bool = Form(True),
    use_ai: bool = Form(False),
    gemini_key: str | None = Form(None),
    gemini_model: str = Form("gemini-flash-lite-latest"),
    run_name: str | None = Form(None),
) -> dict[str, Any]:
    """Upload PDF files and Excel database, initialize background processing job with OCR & AI options."""
    if not files:
        raise HTTPException(status_code=400, detail="Tidak ada berkas PDF yang diunggah.")

    if gemini_key and gemini_key.strip():
        save_user_settings(gemini_api_key=gemini_key.strip(), model=gemini_model, use_ai=use_ai)

    job_id = str(uuid.uuid4())[:8]
    work_dir = BASE_DIR / "out" / "web_uploads" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    saved_pdf_paths: list[Path] = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            continue
        safe_name = Path(f.filename).name
        dest = work_dir / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved_pdf_paths.append(dest)

    if not saved_pdf_paths:
        raise HTTPException(status_code=400, detail="Tidak ada berkas PDF yang valid (.pdf).")

    target_db_path: Path
    if use_sample_db:
        sample_path = BASE_DIR / "templates" / "sample_database.xlsx"
        if not sample_path.exists():
            raise HTTPException(status_code=400, detail="Template sample_database.xlsx tidak ditemukan.")
        target_db_path = sample_path
    elif db_file and db_file.filename:
        db_dest = work_dir / Path(db_file.filename).name
        content = await db_file.read()
        db_dest.write_bytes(content)
        target_db_path = db_dest
    else:
        sample_path = BASE_DIR / "templates" / "sample_database.xlsx"
        if sample_path.exists():
            target_db_path = sample_path
        else:
            raise HTTPException(status_code=400, detail="Database Excel (.xlsx) harus disediakan.")

    out_dir = BASE_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    clean_run_name = run_name.strip() if run_name and run_name.strip() else None

    final_api_key = gemini_key.strip() if gemini_key and gemini_key.strip() else None
    if use_ai and not final_api_key:
        curr_settings = load_user_settings()
        final_api_key = curr_settings.get("gemini_api_key")

    JOBS[job_id] = {
        "id": job_id,
        "mode": "standard",
        "status": "pending",
        "progress": 0,
        "current": 0,
        "total": len(saved_pdf_paths),
        "message": "Menyiapkan berkas...",
        "logs": [
            f"[{datetime.now().strftime('%H:%M:%S')}] Job #{job_id} dibuat dengan {len(saved_pdf_paths)} berkas PDF.",
            f"[{datetime.now().strftime('%H:%M:%S')}] Konfigurasi: OCR={use_ocr} | AI Rescue={use_ai} | Model={gemini_model}",
        ],
        "pdf_paths": saved_pdf_paths,
        "db_path": target_db_path,
        "result": None,
        "run_dir": None,
        "elapsed_sec": 0.0,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_processing_job,
        args=(
            job_id,
            saved_pdf_paths,
            target_db_path,
            out_dir,
            use_ocr,
            use_ai,
            final_api_key,
            gemini_model,
            clean_run_name,
            "standard",
            None,
        ),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "total_files": len(saved_pdf_paths), "status": "pending"}


@app.post("/api/process-local")
async def start_local_folder_process(
    folder_path: str = Form(...),
    db_path: str = Form(""),
    use_sample_db: bool = Form(True),
    mode: str = Form("multi_folder"),  # standard | multi_folder | ftp_fast_rekap
    use_ocr: bool = Form(True),
    use_ai: bool = Form(False),
    gemini_key: str | None = Form(None),
    gemini_model: str = Form("gemini-flash-lite-latest"),
    run_name: str | None = Form(None),
) -> dict[str, Any]:
    """Process a local folder path directly (supports Single Folder, Multi-Folder Tree, and FTP Fast-Rekap)."""
    root = Path(folder_path.strip())
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Folder lokal tidak ditemukan: {root}")

    if use_sample_db or not db_path.strip():
        target_db = BASE_DIR / "templates" / "sample_database.xlsx"
    else:
        target_db = Path(db_path.strip())
    if not target_db.exists():
        raise HTTPException(status_code=400, detail=f"Berkas database tidak ditemukan: {target_db}")

    pdf_files = sorted(p for p in root.rglob("*.pdf") if not any(part.startswith(".") for part in p.parts))
    if not pdf_files:
        raise HTTPException(status_code=400, detail=f"Tidak ditemukan berkas PDF di dalam folder: {root}")

    job_id = str(uuid.uuid4())[:8]
    out_dir = BASE_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    final_api_key = gemini_key.strip() if gemini_key and gemini_key.strip() else None
    if use_ai and not final_api_key:
        final_api_key = load_user_settings().get("gemini_api_key")

    JOBS[job_id] = {
        "id": job_id,
        "mode": mode,
        "status": "pending",
        "progress": 0,
        "current": 0,
        "total": len(pdf_files),
        "message": f"Menyiapkan pemrosesan lokal ({mode})...",
        "logs": [
            f"[{datetime.now().strftime('%H:%M:%S')}] Job Lokal #{job_id} ({mode}) untuk {len(pdf_files)} PDF di {root}.",
        ],
        "pdf_paths": pdf_files,
        "db_path": target_db,
        "result": None,
        "run_dir": None,
        "elapsed_sec": 0.0,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_processing_job,
        args=(
            job_id,
            pdf_files,
            target_db,
            out_dir,
            use_ocr,
            use_ai,
            final_api_key,
            gemini_model,
            run_name.strip() if run_name else None,
            mode,
            root,
        ),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "total_files": len(pdf_files), "mode": mode, "status": "pending"}


@app.get("/api/progress/{job_id}")
async def get_progress_stream(job_id: str) -> StreamingResponse:
    """Stream real-time SSE progress events for a processing job."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    async def event_generator():
        last_log_idx = 0
        while True:
            job = JOBS.get(job_id)
            if not job:
                break

            current_logs = job["logs"][last_log_idx:]
            last_log_idx = len(job["logs"])

            payload = {
                "job_id": job_id,
                "status": job["status"],
                "progress": job["progress"],
                "current": job["current"],
                "total": job["total"],
                "message": job["message"],
                "new_logs": current_logs,
                "elapsed_sec": job.get("elapsed_sec", 0.0),
                "error": job["error"],
            }

            yield f"data: {json.dumps(payload)}\n\n"

            if job["status"] in ("completed", "error"):
                break

            await asyncio.sleep(0.35)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/results")
def get_job_results(job_id: str) -> dict[str, Any]:
    """Retrieve structured results, items, candidates, lampiran, and workbook availability."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    if job["status"] != "completed" or not job.get("result"):
        return {"status": job["status"], "progress": job["progress"], "message": job["message"]}

    res: BulkResult = job["result"]
    run_dir = res.run_dir

    items_data = []
    for item in res.items:
        dest_pdf = run_dir / "pdf" / item.file_name if item.file_name else None
        has_dest_pdf = dest_pdf.exists() if dest_pdf else False

        items_data.append({
            "index": item.index,
            "src_path": str(item.src_path.resolve()),
            "file_name": item.file_name,
            "nid": item.nid,
            "nama": item.nama,
            "jabatan": item.jabatan,
            "unit": item.unit,
            "is_skp": item.is_skp,
            "level": item.level,
            "peran": item.peran,
            "judul": item.judul,
            "resource_code": getattr(item, "resource_code", ""),
            "institute_code": getattr(item, "institute_code", "D002"),
            "number": item.number,
            "issue_date": item.issue_date,
            "expiry_date": item.expiry_date,
            "status": item.status,
            "reason": item.reason,
            "candidates": item.candidates,
            "pages": item.pages,
            "page_count": max(1, len(item.pages)),
            "is_bundle": item.is_bundle,
            "has_dest_pdf": has_dest_pdf,
        })

    lampiran_data = [
        {
            "index": lit.index,
            "file_name": lit.file_name,
            "src_name": lit.src_path.name,
            "judul": lit.judul,
            "reason": lit.reason,
        }
        for lit in getattr(res, "lampiran_items", [])
    ]

    return {
        "job_id": job_id,
        "mode": job.get("mode", "standard"),
        "status": "completed",
        "elapsed_sec": job.get("elapsed_sec", 0.0),
        "run_dir": str(run_dir.resolve()),
        "run_name": run_dir.name,
        "total_files": res.total_files,
        "accepted_count": res.accepted_count,
        "review_count": res.review_count,
        "lampiran_count": len(lampiran_data),
        "items": items_data,
        "lampiran_items": lampiran_data,
        "has_data_xlsx": (run_dir / "data.xlsx").exists(),
        "has_manifest_xlsx": (run_dir / "manifest_slice.xlsx").exists(),
        "has_nps_xlsx": (run_dir / "For NPS 0726.xlsx").exists(),
        "has_lampiran_xlsx": (run_dir / "rekap_lampiran_dokumen.xlsx").exists()
        or (run_dir / "rekap_seluruh_lampiran.xlsx").exists(),
        "has_master_xlsx": (run_dir / "rekap_seluruh_sertifikat.xlsx").exists(),
    }


@app.get("/api/jobs/{job_id}/analytics")
def get_job_analytics(job_id: str) -> dict[str, Any]:
    """Compute executive analytics: SKP Level 1-9 distribution, Resource Code coverage, Unit & Expiry status."""
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job atau hasil tidak ditemukan")

    res: BulkResult = job["result"]
    items = res.items
    total = len(items)
    accepted = sum(1 for it in items if it.status == "accepted")
    review = total - accepted
    skp_count = sum(1 for it in items if it.is_skp)

    level_counts: dict[str, int] = {}
    rc_counts: dict[str, int] = {}
    unit_counts: dict[str, int] = {}
    expiry_stats = {"active": 0, "expiring_soon": 0, "expired": 0, "no_expiry": 0}
    today = date.today()

    rc_resolved = 0
    for it in items:
        lvl_label = it.level.strip() if (it.level and it.level.strip() not in ["-", ""]) else ("SKP" if it.is_skp else "Non-SKP")
        level_counts[lvl_label] = level_counts.get(lvl_label, 0) + 1

        rc = getattr(it, "resource_code", "").strip()
        if rc and rc != "-":
            rc_resolved += 1
            rc_counts[rc] = rc_counts.get(rc, 0) + 1

        u = it.unit.strip() if (it.unit and it.unit.strip() not in ["-", ""]) else "Tanpa Unit"
        unit_counts[u] = unit_counts.get(u, 0) + 1

        exp_str = (it.expiry_date or "").strip()
        if not exp_str or exp_str == "-":
            expiry_stats["no_expiry"] += 1
        else:
            try:
                exp_dt = date.fromisoformat(exp_str[:10])
                days_left = (exp_dt - today).days
                if days_left < 0:
                    expiry_stats["expired"] += 1
                elif days_left <= 90:
                    expiry_stats["expiring_soon"] += 1
                else:
                    expiry_stats["active"] += 1
            except ValueError:
                expiry_stats["no_expiry"] += 1

    elapsed = float(job.get("elapsed_sec") or 0.01)
    return {
        "job_id": job_id,
        "total_certificates": total,
        "accepted_count": accepted,
        "review_count": review,
        "lampiran_count": len(getattr(res, "lampiran_items", [])),
        "acceptance_rate": round((accepted / max(1, total)) * 100, 1),
        "skp_count": skp_count,
        "non_skp_count": total - skp_count,
        "resource_code_resolved": rc_resolved,
        "resource_code_rate": round((rc_resolved / max(1, total)) * 100, 1),
        "level_distribution": dict(sorted(level_counts.items(), key=lambda x: -x[1])),
        "top_resource_codes": dict(sorted(rc_counts.items(), key=lambda x: -x[1])[:12]),
        "unit_distribution": dict(sorted(unit_counts.items(), key=lambda x: -x[1])[:10]),
        "expiry_breakdown": expiry_stats,
        "elapsed_sec": round(elapsed, 2),
        "throughput_items_per_sec": round(total / max(0.01, elapsed), 2),
    }


@app.get("/api/jobs/{job_id}/preview/{item_index}")
def get_item_preview(job_id: str, item_index: int, page: int = 1, zoom: float = 1.2) -> Response:
    """Render a PNG preview of a specific certificate page (1-based within the item's pages)."""
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job atau hasil tidak ditemukan")

    res: BulkResult = job["result"]
    target_item = next((it for it in res.items if it.index == item_index), None)
    if not target_item:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")

    target_pdf = target_item.src_path
    item_pages = target_item.pages or [0]
    total_item_pages = len(item_pages)
    clamped_idx = max(0, min(total_item_pages - 1, page - 1))
    target_doc_page_1based = item_pages[clamped_idx] + 1

    png_bytes = render_page_image(target_pdf, page_num_1based=target_doc_page_1based, zoom=zoom)
    if not png_bytes:
        png_bytes = render_page_image(target_pdf, page_num_1based=1, zoom=zoom)

    if not png_bytes:
        raise HTTPException(status_code=500, detail="Gagal me-render pratinjau halaman PDF.")

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Total-Pages": str(total_item_pages),
            "X-Current-Page": str(clamped_idx + 1),
        },
    )


def _apply_item_approval(
    target_item: BulkItem,
    run_dir: Path,
    nid: str,
    nama: str,
    unit: str = "",
    jabatan: str = "",
    custom_title: str | None = None,
    level: str | None = None,
    peran: str | None = None,
    resource_code: str | None = None,
) -> str:
    """Apply metadata updates to a BulkItem, re-slice its PDF, and remove stale [REVIEW]_ PDF."""
    old_filename = target_item.file_name
    target_item.nid = nid.strip()
    target_item.nama = nama.strip()
    if unit is not None and unit.strip():
        target_item.unit = unit.strip()
    if jabatan is not None and jabatan.strip():
        target_item.jabatan = jabatan.strip()
    if custom_title is not None and custom_title.strip():
        target_item.judul = custom_title.strip()
    if level is not None and level.strip():
        target_item.level = level.strip()
        target_item.is_skp = True
    if peran is not None and peran.strip():
        target_item.peran = peran.strip()

    target_item.status = "accepted"
    target_item.reason = ""

    clean_nid = clean_field(target_item.nid)
    clean_nama = clean_field(target_item.nama) or "Tanpa_Nama"
    if target_item.is_skp:
        resolved_level = resolve_skp_level(target_item.level, full_text=target_item.judul)
        resolved_peran = clean_skp_peran(target_item.peran or target_item.judul)
        clean_level = clean_field(resolved_level)
        clean_peran = clean_field(resolved_peran)
        parts = [p for p in [clean_nid, clean_nama, clean_level, clean_peran] if p]
        new_filename = f"{'_'.join(parts)}.pdf"
        target_item.level = resolved_level
        target_item.peran = resolved_peran
    else:
        clean_judul = clean_field(target_item.judul) or "Sertifikat"
        parts = [p for p in [clean_nid, clean_nama, clean_judul] if p]
        new_filename = f"{'_'.join(parts)}.pdf"

    if resource_code is not None and resource_code.strip():
        target_item.resource_code = resource_code.strip().upper()
    else:
        rc_info = lookup_resource_code(
            title=target_item.judul or target_item.peran,
            level=target_item.level,
            jabatan=target_item.jabatan,
            raw_text=new_filename,
        )
        if rc_info.get("code"):
            target_item.resource_code = rc_info["code"]
            target_item.institute_code = rc_info.get("institute", "D002")

    target_item.file_name = new_filename

    pdf_dir = run_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_out = pdf_dir / new_filename
    try:
        if target_item.src_path.exists():
            doc = pymupdf.open(target_item.src_path)
            new_doc = pymupdf.open()
            for p in target_item.pages:
                if 0 <= p < len(doc):
                    new_doc.insert_pdf(doc, from_page=p, to_page=p)
            new_doc.save(pdf_out)
            new_doc.close()
            doc.close()
        if old_filename and old_filename != new_filename:
            old_path = pdf_dir / old_filename
            if old_path.exists():
                old_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Failed to slice approved item: %s", exc)

    return new_filename


@app.post("/api/jobs/{job_id}/approve")
def approve_candidate(
    job_id: str,
    item_index: int = Form(...),
    nid: str = Form(...),
    nama: str = Form(...),
    unit: str = Form(""),
    jabatan: str = Form(""),
    custom_title: str | None = Form(None),
    level: str | None = Form(None),
    peran: str | None = Form(None),
    resource_code: str | None = Form(None),
) -> dict[str, Any]:
    """Approve or manually correct a candidate match, re-slice PDF, and re-sync all Excel workbooks."""
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    res: BulkResult = job["result"]
    run_dir = res.run_dir

    target_item = next((it for it in res.items if it.index == item_index), None)
    if not target_item:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")

    new_filename = _apply_item_approval(
        target_item=target_item,
        run_dir=run_dir,
        nid=nid,
        nama=nama,
        unit=unit,
        jabatan=jabatan,
        custom_title=custom_title,
        level=level,
        peran=peran,
        resource_code=resource_code,
    )

    res.accepted_count = sum(1 for it in res.items if it.status == "accepted")
    res.review_count = sum(1 for it in res.items if it.status != "accepted")

    # Persist changes to data.xlsx, manifest_slice.xlsx, For NPS 0726.xlsx, and plan.json
    resync_bulk_outputs(run_dir, res.items, db_path=job.get("db_path"))

    return {
        "status": "success",
        "item_index": item_index,
        "file_name": new_filename,
        "resource_code": target_item.resource_code,
        "accepted_count": res.accepted_count,
        "review_count": res.review_count,
    }


@app.post("/api/jobs/{job_id}/approve-batch")
def approve_batch_candidates(
    job_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Batch-approve unmatched items whose top candidate meets min_score (or explicit item_indices)."""
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    res: BulkResult = job["result"]
    run_dir = res.run_dir
    min_score = float(payload.get("min_score", 85.0))
    explicit_indices = set(payload.get("item_indices") or [])

    approved_indices: list[int] = []
    for item in res.items:
        if item.status == "accepted" and not explicit_indices:
            continue
        if explicit_indices and item.index not in explicit_indices:
            continue
        if item.candidates:
            top_cand = item.candidates[0]
            if float(top_cand.get("score", 0.0)) >= min_score or item.index in explicit_indices:
                _apply_item_approval(
                    target_item=item,
                    run_dir=run_dir,
                    nid=str(top_cand.get("nid") or item.nid),
                    nama=str(top_cand.get("nama") or item.nama),
                    unit=str(top_cand.get("unit") or item.unit),
                    jabatan=str(top_cand.get("jabatan") or item.jabatan),
                )
                approved_indices.append(item.index)

    res.accepted_count = sum(1 for it in res.items if it.status == "accepted")
    res.review_count = sum(1 for it in res.items if it.status != "accepted")

    if approved_indices:
        resync_bulk_outputs(run_dir, res.items, db_path=job.get("db_path"))

    return {
        "status": "success",
        "approved_count": len(approved_indices),
        "approved_indices": approved_indices,
        "accepted_count": res.accepted_count,
        "review_count": res.review_count,
    }


@app.get("/api/jobs/{job_id}/download/zip")
def download_zip(job_id: str) -> StreamingResponse:
    """Download all processed PDFs and Excel workbooks as a single zip archive."""
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    res: BulkResult = job["result"]
    run_dir = res.run_dir

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for xlsx_name in [
            "data.xlsx",
            "manifest_slice.xlsx",
            "For NPS 0726.xlsx",
            "rekap_lampiran_dokumen.xlsx",
            "rekap_seluruh_sertifikat.xlsx",
            "rekap_seluruh_lampiran.xlsx",
            "unmatched_report.xlsx",
            "plan.json",
        ]:
            fpath = run_dir / xlsx_name
            if fpath.exists():
                zf.write(fpath, arcname=xlsx_name)

        pdf_dir = run_dir / "pdf"
        if pdf_dir.exists():
            for p in sorted(pdf_dir.glob("*.pdf")):
                zf.write(p, arcname=f"pdf/{p.name}")

    zip_buffer.seek(0)
    zip_filename = f"Hasil_Sertifikat_{run_dir.name}.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@app.get("/api/jobs/{job_id}/download/{filename}")
def download_workbook(job_id: str, filename: str) -> FileResponse:
    """Download individual audit Excel workbooks."""
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    res: BulkResult = job["result"]
    safe_name = Path(filename).name
    target_file = res.run_dir / safe_name
    if not target_file.exists():
        raise HTTPException(status_code=404, detail=f"Berkas {safe_name} tidak ditemukan")

    return FileResponse(target_file, filename=safe_name)


@app.post("/api/open-folder")
def open_folder(path: str = Form(...)) -> dict[str, Any]:
    """Open folder in native system file manager."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Folder tidak ditemukan")

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p.resolve()))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p.resolve())])
        else:
            subprocess.Popen(["xdg-open", str(p.resolve())])
        return {"status": "success", "message": f"Membuka folder {p}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/", response_class=HTMLResponse)
def index_page() -> str:
    """Single Page Application (SPA) Precision Studio Web Dashboard."""
    return WEB_HTML_CONTENT


WEB_HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="id" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Certificate Manager — Precision Studio v3.0</title>
  <style>
    :root {
      --bg: #090d16;
      --bg-canvas: #090d16;
      --bg-surface: #111827;
      --bg-elevated: #1e293b;
      --bg-console: #050811;
      --surface: #111827;
      --text: #f8fafc;
      --text-secondary: #cbd5e1;
      --text-muted: #94a3b8;
      --text-on-accent: #ffffff;
      --border: #1e293b;
      --border-strong: #334155;
      --accent: #0ea5e9;
      --accent-strong: #0369a1;
      --accent-hover: #075985;
      --accent-soft: rgba(14, 165, 233, 0.14);
      --pill-ok-bg: #064e3b;
      --pill-ok-text: #ecfdf5;
      --pill-ok-border: #059669;
      --pill-accent-bg: #0c4a6e;
      --pill-accent-text: #f0f9ff;
      --pill-warn-bg: #78350f;
      --pill-warn-text: #fef3c7;
      --pill-warn-border: #d97706;
      --btn-emerald-bg: #047857;
      --btn-emerald-hover: #065f46;
      --btn-amber-bg: #92400e;
      --btn-amber-hover: #78350f;
      --ok: #10b981;
      --warn: #f59e0b;
      --danger: #f43f5e;
      --radius-inner: 8px;
      --pad-card: 16px;
      --radius-outer: calc(var(--radius-inner) + var(--pad-card));
      --space-1: 4px;
      --space-2: 8px;
      --space-3: 12px;
      --space-4: 16px;
      --space-5: 24px;
      --space-6: 32px;
      --font-sans: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --font-mono: "JetBrains Mono", "Fira Code", Consolas, monospace;
      --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
    }

    [data-theme="light"] {
      --bg: #f8fafc;
      --bg-canvas: #f8fafc;
      --bg-surface: #ffffff;
      --bg-elevated: #f1f5f9;
      --bg-console: #0f172a;
      --surface: #ffffff;
      --text: #0f172a;
      --text-secondary: #334155;
      --text-muted: #475569;
      --text-on-accent: #ffffff;
      --border: #e2e8f0;
      --border-strong: #cbd5e1;
      --accent: #0369a1;
      --accent-strong: #0369a1;
      --accent-hover: #075985;
      --accent-soft: rgba(2, 132, 199, 0.1);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background-color: var(--bg-canvas);
      color: var(--text);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }

    .tabular-nums, table, td, th, .metric-val, .badge-num, .progress-num {
      font-variant-numeric: tabular-nums;
    }

    /* Header & Navigation */
    .studio-header {
      background-color: var(--bg-surface);
      border-bottom: 1px solid var(--border-strong);
      position: sticky;
      top: 0;
      z-index: 40;
    }
    .header-inner {
      max-width: 1400px;
      margin: 0 auto;
      padding: 0 var(--space-5);
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-4);
    }
    .brand-group {
      display: flex;
      align-items: center;
      gap: var(--space-3);
    }
    .brand-mark {
      width: 40px;
      height: 40px;
      border-radius: var(--radius-inner);
      background-color: var(--accent-strong);
      color: var(--text-on-accent);
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 16px;
      letter-spacing: -0.03em;
    }
    .brand-title {
      font-size: 17px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.02em;
    }
    .brand-subtitle {
      font-size: 12px;
      color: var(--text-muted);
    }
    .status-cluster {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid var(--border-strong);
      background-color: var(--bg-elevated);
      color: var(--text-secondary);
    }
    .pill-ok {
      background-color: var(--pill-ok-bg);
      color: var(--pill-ok-text);
      border-color: var(--pill-ok-border);
    }
    .pill-accent {
      background-color: var(--pill-accent-bg);
      color: var(--pill-accent-text);
      border-color: var(--accent-strong);
    }
    .pill-warn {
      background-color: var(--pill-warn-bg);
      color: var(--pill-warn-text);
      border-color: var(--pill-warn-border);
    }
    .dot-live {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: var(--ok);
    }

    /* Tabs Bar */
    .tabs-bar {
      background-color: var(--bg-surface);
      border-bottom: 1px solid var(--border);
    }
    .tabs-inner {
      max-width: 1400px;
      margin: 0 auto;
      padding: 0 var(--space-5);
      display: flex;
      gap: var(--space-5);
      overflow-x: auto;
    }
    .tab-btn {
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      padding: 13px 4px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: opacity 160ms var(--ease-out), transform 160ms var(--ease-out);
    }
    .tab-btn:hover { color: var(--text); }
    .tab-btn.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
    }

    /* Main Workspace Layout */
    .workspace {
      flex: 1;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
      padding: var(--space-5);
    }
    .hidden { display: none !important; }

    .grid-12 {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: var(--space-5);
    }
    .col-7 { grid-column: span 7 / span 7; }
    .col-5 { grid-column: span 5 / span 5; }
    .col-6 { grid-column: span 6 / span 6; }
    .col-12 { grid-column: span 12 / span 12; }

    @media (max-width: 1024px) {
      .col-7, .col-5, .col-6 { grid-column: span 12 / span 12; }
    }

    /* Concentric Surface Panels */
    .surface-outer {
      background-color: var(--bg-surface);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-outer);
      padding: var(--pad-card);
    }
    .surface-inner {
      background-color: var(--bg-elevated);
      border: 1px solid var(--border);
      border-radius: var(--radius-inner);
      padding: var(--space-4);
    }
    .panel {
      background-color: var(--bg-surface);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-outer);
      padding: var(--pad-card);
    }
    .panel + .panel { margin-top: var(--space-4); }

    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--space-3);
      padding-bottom: var(--space-2);
      border-bottom: 1px solid var(--border);
    }
    .section-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* Dropzone */
    .dropzone {
      border: 2px dashed var(--border-strong);
      border-radius: var(--radius-inner);
      padding: var(--space-5);
      text-align: center;
      cursor: pointer;
      background-color: var(--bg-canvas);
      transition: transform 160ms var(--ease-out), opacity 160ms var(--ease-out);
    }
    .dropzone:hover, .dropzone.drag-active {
      border-color: var(--accent);
      background-color: var(--accent-soft);
    }

    /* Controls & Inputs */
    .input-field, .select-field {
      width: 100%;
      padding: 9px 12px;
      border-radius: var(--radius-inner);
      background-color: var(--bg-canvas);
      border: 1px solid var(--border-strong);
      color: var(--text);
      font-family: inherit;
      font-size: 13px;
    }
    .input-field:focus, .select-field:focus {
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    .label-sm {
      display: block;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-secondary);
      margin-bottom: 4px;
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 9px 16px;
      border-radius: var(--radius-inner);
      font-family: inherit;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
      transition: transform 140ms var(--ease-out), opacity 140ms var(--ease-out);
    }
    .btn:active { transform: scale(0.98); }
    .btn:disabled { opacity: 0.55; cursor: not-allowed; }
    .btn-primary {
      background-color: var(--accent-strong);
      color: var(--text-on-accent);
    }
    .btn-primary:hover:not(:disabled) { background-color: var(--accent-hover); }
    .btn-emerald {
      background-color: var(--btn-emerald-bg);
      color: var(--text-on-accent);
    }
    .btn-emerald:hover:not(:disabled) { background-color: var(--btn-emerald-hover); }
    .btn-amber {
      background-color: var(--btn-amber-bg);
      color: var(--text-on-accent);
    }
    .btn-amber:hover:not(:disabled) { background-color: var(--btn-amber-hover); }
    .btn-ghost {
      background-color: var(--bg-elevated);
      color: var(--text);
      border-color: var(--border-strong);
    }
    .btn-ghost:hover:not(:disabled) { border-color: var(--accent); }

    /* Log Console & Progress */
    .progress-track {
      width: 100%;
      height: 10px;
      background-color: var(--bg-canvas);
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--border-strong);
    }
    .progress-fill {
      height: 100%;
      width: 0%;
      background-color: var(--accent);
      transition: transform 220ms var(--ease-out), opacity 220ms var(--ease-out);
    }
    .log-box {
      background-color: var(--bg-console);
      color: var(--text-secondary);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-inner);
      padding: var(--space-3);
      font-family: var(--font-mono);
      font-size: 12px;
      height: 360px;
      overflow-y: auto;
    }

    /* Review Workbench */
    .review-split {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: var(--space-5);
      min-height: 700px;
    }
    @media (max-width: 1100px) {
      .review-split { grid-template-columns: 1fr; }
    }
    .item-row {
      padding: 10px 12px;
      border-radius: var(--radius-inner);
      border: 1px solid var(--border-strong);
      background-color: var(--bg-canvas);
      cursor: pointer;
      margin-bottom: 8px;
      transition: transform 140ms var(--ease-out), opacity 140ms var(--ease-out);
    }
    .item-row:hover { border-color: var(--accent); }
    .item-row.selected {
      border-color: var(--accent);
      background-color: var(--accent-soft);
    }
    .preview-stage {
      background-color: var(--bg-console);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-inner);
      overflow: auto;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 520px;
      max-height: 620px;
      padding: var(--space-3);
    }

    /* Analytics Metric Strips */
    .metrics-strip {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: var(--space-4);
      margin-bottom: var(--space-5);
    }
    .metric-box {
      background-color: var(--bg-surface);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-outer);
      padding: var(--pad-card);
    }
    .metric-label {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .metric-val {
      font-size: 28px;
      font-weight: 800;
      color: var(--text);
      margin-top: 4px;
    }
    .bar-row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
      font-size: 12px;
    }
    .bar-track {
      flex: 1;
      height: 8px;
      background-color: var(--bg-canvas);
      border-radius: 999px;
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      background-color: var(--accent);
    }
  </style>
</head>
<body>

  <!-- Top Header -->
  <header class="studio-header">
    <div class="header-inner">
      <div class="brand-group">
        <div class="brand-mark">CS</div>
        <div>
          <h1 class="brand-title">Certificate Manager Studio</h1>
          <p class="brand-subtitle">Precision Slicer • RapidOCR • SKP &amp; Resource Code Engine</p>
        </div>
      </div>

      <div class="status-cluster">
        <span id="aiActiveIndicator" class="pill pill-accent hidden">AI Rescue Aktif</span>
        <span class="pill pill-ok">
          <span class="dot-live"></span>
          100% Lokal &amp; Offline-First
        </span>
        <button type="button" onclick="toggleTheme()" class="btn btn-ghost" style="padding: 5px 10px; font-size: 12px;" title="Ganti Tema Terang / Gelap">
          Tema UI
        </button>
      </div>
    </div>
  </header>

  <!-- Navigation Tabs -->
  <nav class="tabs-bar" aria-label="Menu Utama">
    <div class="tabs-inner">
      <button id="tabBtnProcess" onclick="switchTab('process')" class="tab-btn active">
        1. Pemrosesan &amp; Ekstraksi Batch
      </button>
      <button id="tabBtnReview" onclick="switchTab('review')" class="tab-btn">
        2. Workbench Review &amp; Koreksi
        <span id="badgeReviewCount" class="pill badge-num hidden" style="padding: 1px 8px; font-size: 11px;">0</span>
      </button>
      <button id="tabBtnAnalytics" onclick="switchTab('analytics')" class="tab-btn">
        3. Analitik Eksekutif &amp; Matriks SKP
      </button>
      <button id="tabBtnSettings" onclick="switchTab('settings')" class="tab-btn">
        4. Pengaturan &amp; Integrasi AI
      </button>
    </div>
  </nav>

  <!-- Main Workspace -->
  <main class="workspace">

    <!-- TAB 1: PEMROSESAN & EKSTRAKSI -->
    <section id="tabContentProcess">
      <div class="grid-12">

        <!-- Left Column: Input Controls -->
        <div class="col-7">

          <!-- Mode Selector -->
          <div class="surface-outer" style="margin-bottom: 16px;">
            <div class="surface-inner">
              <div class="section-head">
                <span class="section-title">Mode Sumber Pemrosesan</span>
                <span class="badge-num" style="font-size: 12px; color: var(--text-muted);">Pilih Unggah File atau Path Folder Lokal</span>
              </div>
              <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                <button type="button" id="modeBtnUpload" onclick="setSourceMode('upload')" class="btn btn-primary">
                  Unggah Berkas PDF (Browser)
                </button>
                <button type="button" id="modeBtnLocal" onclick="setSourceMode('local')" class="btn btn-ghost">
                  Path Folder Lokal / Multi-Folder / FTP Cepat
                </button>
              </div>
            </div>
          </div>

          <!-- Upload Mode Panel -->
          <div id="panelUploadMode" class="panel">
            <div class="section-head">
              <span class="section-title">Berkas Sertifikat (PDF)</span>
              <span id="pdfFileCount" class="badge-num" style="font-size: 12px; color: var(--text-muted);">0 berkas terpilih</span>
            </div>

            <div id="pdfDropzone" class="dropzone">
              <input type="file" id="pdfInput" multiple accept=".pdf" class="hidden" onchange="handlePdfSelect(this.files)">
              <p style="font-weight: 700; font-size: 14px; margin-bottom: 4px;">
                Tarik &amp; lepas berkas PDF ke sini, atau klik untuk memilih berkas
              </p>
              <p style="font-size: 12px; color: var(--text-muted);">
                Mendukung PDF sertifikat tunggal, bundel multi-halaman, surat pengantar (roster), dan lampiran
              </p>
            </div>

            <div id="pdfSelectedList" class="hidden" style="margin-top: 12px; max-height: 150px; overflow-y: auto; font-size: 12px;"></div>
          </div>

          <!-- Local Folder Mode Panel -->
          <div id="panelLocalMode" class="panel hidden">
            <div class="section-head">
              <span class="section-title">Pemrosesan Folder Lokal / Multi-Folder / FTP</span>
              <span style="font-size: 12px; color: var(--accent);">Bebas Batas Ukuran Upload</span>
            </div>
            <div style="display: grid; gap: 12px;">
              <div>
                <label class="label-sm" for="localFolderPathInput">Path Absolut Folder PDF di Komputer / Server</label>
                <input type="text" id="localFolderPathInput" class="input-field" placeholder="Contoh: /home/dapp/Projects/cert_slicer/out atau D:\Data_Sertifikat">
              </div>
              <div>
                <label class="label-sm" for="localProcessMode">Metode Pemrosesan Folder</label>
                <select id="localProcessMode" class="select-field">
                  <option value="multi_folder">Multi-Folder Otomatis (Rekursif Subfolder + Master Rekap)</option>
                  <option value="standard">Folder Tunggal (Potong Bundel + OCR + Penamaan)</option>
                  <option value="ftp_fast_rekap">Mode Cepat FTP (File Sudah Bernama NID_Nama -> Langsung Rekap Excel &amp; NPS 0726)</option>
                </select>
              </div>
            </div>
          </div>

          <!-- Database Master Pegawai -->
          <div class="panel">
            <div class="section-head">
              <span class="section-title">Database Master Pegawai (.xlsx)</span>
              <label style="display: inline-flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer;">
                <input type="checkbox" id="chkUseSampleDb" checked onchange="toggleSampleDb(this.checked)">
                <span>Gunakan Sample Database Bawaan</span>
              </label>
            </div>
            <div id="dbDropzone" class="dropzone" style="padding: 14px; opacity: 0.65;">
              <input type="file" id="dbInput" accept=".xlsx,.xls" class="hidden" onchange="handleDbSelect(this.files)" disabled>
              <span id="dbFilenameLabel" style="font-size: 13px; font-weight: 600;">templates/sample_database.xlsx (Aktif)</span>
            </div>
          </div>

          <!-- Opsi OCR, AI Rescue & Eksekusi -->
          <div class="panel">
            <div class="section-head">
              <span class="section-title">Konfigurasi Mesin Ekstraksi</span>
              <span style="font-size: 12px; color: var(--text-muted);">RapidOCR &amp; Gemini Rescue</span>
            </div>

            <div style="display: grid; gap: 12px; margin-bottom: 16px;">
              <label style="display: flex; align-items: flex-start; gap: 10px; cursor: pointer;">
                <input type="checkbox" id="chkUseOcr" checked style="margin-top: 4px;">
                <div>
                  <div style="font-size: 13px; font-weight: 600;">Gunakan Pembaca Teks Gambar (RapidOCR Multi-Thread)</div>
                  <div style="font-size: 12px; color: var(--text-muted);">Mengekstrak teks dari sertifikat hasil scan/bitmap secara otomatis.</div>
                </div>
              </label>

              <label style="display: flex; align-items: flex-start; gap: 10px; cursor: pointer;">
                <input type="checkbox" id="chkUseAi" onchange="toggleAiOptions(this.checked)" style="margin-top: 4px;">
                <div>
                  <div style="font-size: 13px; font-weight: 600;">Aktifkan Penyelamatan AI (Gemini Flash-Lite) untuk Item Ambigu</div>
                  <div style="font-size: 12px; color: var(--text-muted);">Hanya dipanggil untuk sertifikat yang gagal dicocokkan oleh mesin lokal.</div>
                </div>
              </label>
            </div>

            <div id="aiSettingsPanel" class="surface-inner hidden" style="margin-bottom: 16px;">
              <div style="display: grid; gap: 10px;">
                <div>
                  <label class="label-sm" for="geminiApiKeyInput">Google Gemini API Key</label>
                  <div style="display: flex; gap: 8px;">
                    <input type="password" id="geminiApiKeyInput" class="input-field" placeholder="AIzaSy...">
                    <button type="button" onclick="toggleApiKeyVisibility()" class="btn btn-ghost">Lihat</button>
                    <button type="button" onclick="saveApiKeyDirect()" class="btn btn-primary">Simpan</button>
                  </div>
                  <span id="apiKeyStatusText" style="font-size: 11px; color: var(--text-muted); display: block; margin-top: 4px;">
                    API Key disimpan lokal di ~/.certslice_settings.json
                  </span>
                </div>
                <div>
                  <label class="label-sm" for="geminiModelSelect">Model Gemini</label>
                  <select id="geminiModelSelect" class="select-field">
                    <option value="gemini-flash-lite-latest">gemini-flash-lite-latest (Rekomendasi Cepat &amp; Hemat Kuota)</option>
                  </select>
                </div>
              </div>
            </div>

            <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
              <input type="text" id="runNameInput" class="input-field" style="flex: 1; min-width: 220px;" placeholder="Nama folder output (opsional, misal: Batch_SKP_2026)">
              <button type="button" id="btnStartProcess" onclick="startProcessing()" class="btn btn-primary" style="padding: 11px 22px;">
                Mulai Proses Sertifikat
              </button>
            </div>
          </div>
        </div>

        <!-- Right Column: Telemetry & Real-time Console -->
        <div class="col-5">
          <div class="panel" style="height: 100%; display: flex; flex-direction: column;">
            <div class="section-head">
              <span class="section-title">Telemetri &amp; Log Pemrosesan Real-Time</span>
              <span id="statusBadge" class="pill">Siap</span>
            </div>

            <div style="margin-bottom: 14px;">
              <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 600; margin-bottom: 6px;">
                <span id="progressMessage">Menunggu instruksi pemrosesan...</span>
                <span id="progressPercent" class="progress-num">0%</span>
              </div>
              <div class="progress-track">
                <div id="progressBar" class="progress-fill"></div>
              </div>
            </div>

            <div id="logConsole" class="log-box" style="flex: 1;">
              <div>[Sistem] Certificate Manager Studio v3.0 siap digunakan.</div>
              <div>[Info] Pilih berkas PDF atau masukkan path folder lokal untuk memulai.</div>
            </div>
          </div>
        </div>

      </div>

      <!-- Result Banner -->
      <div id="resultBanner" class="panel hidden" style="margin-top: 20px; border-color: var(--ok);">
        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;">
          <div>
            <h2 style="font-size: 16px; font-weight: 700;">Pemrosesan Selesai</h2>
            <p id="resultSummaryText" class="tabular-nums" style="font-size: 13px; color: var(--text-secondary); margin-top: 2px;">
              Semua berkas telah diproses.
            </p>
          </div>
          <div style="display: flex; flex-wrap: wrap; gap: 8px;">
            <button type="button" onclick="downloadZip()" class="btn btn-primary">Unduh Semua (ZIP)</button>
            <button type="button" onclick="downloadExcel('data.xlsx')" class="btn btn-emerald">Unduh data.xlsx</button>
            <button type="button" id="btnDownloadNps" onclick="downloadExcel('For NPS 0726.xlsx')" class="btn btn-amber hidden">Unduh For NPS 0726.xlsx</button>
            <button type="button" id="btnDownloadLampiran" onclick="downloadExcel('rekap_lampiran_dokumen.xlsx')" class="btn btn-ghost hidden">Unduh Rekap Lampiran</button>
            <button type="button" onclick="openFolderNative()" class="btn btn-ghost">Buka Folder Output</button>
            <button type="button" onclick="switchTab('review')" class="btn btn-primary">Buka Workbench Review</button>
          </div>
        </div>
      </div>
    </section>

    <!-- TAB 2: WORKBENCH REVIEW & KOREKSI INTERAKTIF -->
    <section id="tabContentReview" class="hidden">
      <div class="review-split">

        <!-- Left: Search, Filter, Batch Actions & Item List -->
        <div class="panel" style="display: flex; flex-direction: column; max-height: 760px;">
          <div class="section-head">
            <span class="section-title">Daftar Sertifikat</span>
            <span id="reviewItemBadge" class="pill badge-num">0 Item</span>
          </div>

          <div style="display: grid; gap: 8px; margin-bottom: 12px;">
            <input type="text" id="searchReviewInput" oninput="renderItemsList()" class="input-field" placeholder="Cari nama, NID, judul, unit, atau kode resource...">
            <div style="display: flex; gap: 8px;">
              <select id="filterStatus" onchange="renderItemsList()" class="select-field" style="flex: 1;">
                <option value="all">Semua Status</option>
                <option value="unmatched">Perlu Review Saja</option>
                <option value="accepted">Tervalidasi (Accepted)</option>
              </select>
              <button type="button" id="btnBatchApprove" onclick="batchApproveHighConfidence()" class="btn btn-emerald" title="Setujui otomatis semua item review dengan kandidat skor >= 85%">
                Setujui Massal (&ge;85%)
              </button>
            </div>
          </div>

          <div id="itemsContainer" style="flex: 1; overflow-y: auto; padding-right: 4px;">
            <div style="text-align: center; padding: 48px 16px; color: var(--text-muted); font-size: 13px;">
              Belum ada data pemrosesan. Jalankan proses terlebih dahulu pada Tab 1.
            </div>
          </div>
        </div>

        <!-- Right: Multi-Page Preview & Metadata Inspector -->
        <div class="panel" style="display: flex; flex-direction: column;">
          <div class="section-head">
            <span class="section-title">Inspektur Dokumen &amp; Koreksi Metadata (Sinkronisasi Excel Otomatis)</span>
            <div style="display: flex; align-items: center; gap: 6px;">
              <button type="button" onclick="changePreviewPage(-1)" class="btn btn-ghost" style="padding: 4px 10px;">&larr; Hal</button>
              <span id="pageIndicator" class="pill badge-num">Hal 1 / 1</span>
              <button type="button" onclick="changePreviewPage(1)" class="btn btn-ghost" style="padding: 4px 10px;">Hal &rarr;</button>
              <button type="button" onclick="zoomPreview(1.2)" class="btn btn-ghost" style="padding: 4px 10px;">+</button>
              <button type="button" onclick="zoomPreview(0.8)" class="btn btn-ghost" style="padding: 4px 10px;">&minus;</button>
              <button type="button" onclick="resetZoom()" class="btn btn-ghost" style="padding: 4px 10px;">100%</button>
            </div>
          </div>

          <div class="grid-12" style="flex: 1;">
            <!-- PDF Image Canvas -->
            <div class="col-7">
              <div class="preview-stage">
                <img id="pdfPreviewImg" src="" alt="Pratinjau Halaman Sertifikat" class="hidden" style="max-width: 100%; height: auto; transition: transform 160ms var(--ease-out);">
                <div id="previewPlaceholder" style="text-align: center; color: var(--text-muted); font-size: 13px;">
                  Pilih item sertifikat di sebelah kiri untuk menampilkan pratinjau halaman PDF.
                </div>
              </div>
            </div>

            <!-- Metadata Editor Form -->
            <div class="col-5" style="display: flex; flex-direction: column; justify-content: space-between;">
              <div style="display: grid; gap: 10px;">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                  <div>
                    <label class="label-sm" for="editNid">Nomor Induk (NID)</label>
                    <input type="text" id="editNid" class="input-field tabular-nums">
                  </div>
                  <div>
                    <label class="label-sm" for="editResourceCode">Resource Code (NPS)</label>
                    <input type="text" id="editResourceCode" class="input-field tabular-nums" placeholder="Contoh: A100 / PK21">
                  </div>
                </div>

                <div>
                  <label class="label-sm" for="editNama">Nama Pemilik Sertifikat</label>
                  <input type="text" id="editNama" class="input-field">
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                  <div>
                    <label class="label-sm" for="editUnit">Unit Kerja</label>
                    <input type="text" id="editUnit" class="input-field">
                  </div>
                  <div>
                    <label class="label-sm" for="editJabatan">Jabatan</label>
                    <input type="text" id="editJabatan" class="input-field">
                  </div>
                </div>

                <div>
                  <label class="label-sm" for="editJudul">Judul Sertifikat / Pelatihan</label>
                  <input type="text" id="editJudul" class="input-field">
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                  <div>
                    <label class="label-sm" for="editLevel">Level Kualifikasi (SKP)</label>
                    <input type="text" id="editLevel" class="input-field" placeholder="Contoh: Level 4 / Pelaksana Senior">
                  </div>
                  <div>
                    <label class="label-sm" for="editPeran">Peran Kompetensi (SKP)</label>
                    <input type="text" id="editPeran" class="input-field">
                  </div>
                </div>

                <div style="padding-top: 8px; border-top: 1px solid var(--border);">
                  <span class="label-sm" style="color: var(--accent);">Kandidat Terdekat dari Database Master (Klik untuk Terapkan)</span>
                  <div id="candidatesList" style="max-height: 150px; overflow-y: auto; display: grid; gap: 6px; margin-top: 6px;">
                    <div style="font-size: 12px; color: var(--text-muted);">Tidak ada rekomendasi kandidat.</div>
                  </div>
                </div>
              </div>

              <div style="padding-top: 12px; border-top: 1px solid var(--border); margin-top: 12px;">
                <button type="button" id="btnApproveItem" onclick="saveItemApproval()" class="btn btn-emerald" style="width: 100%; padding: 11px;">
                  Setujui, Potong Ulang PDF &amp; Perbarui Semua Excel (Ctrl+Enter)
                </button>
              </div>
            </div>
          </div>
        </div>

      </div>
    </section>

    <!-- TAB 3: ANALITIK EKSEKUTIF & MATRIKS SKP -->
    <section id="tabContentAnalytics" class="hidden">
      <div class="metrics-strip">
        <div class="metric-box">
          <div class="metric-label">Total Sertifikat</div>
          <div id="kpiTotalCerts" class="metric-val tabular-nums">0</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Tingkat Validasi (Accepted)</div>
          <div id="kpiAcceptanceRate" class="metric-val tabular-nums">0%</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Cakupan Resource Code</div>
          <div id="kpiResourceRate" class="metric-val tabular-nums">0%</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Kecepatan Ekstraksi</div>
          <div id="kpiThroughput" class="metric-val tabular-nums">0.0 dok/dtk</div>
        </div>
      </div>

      <div class="grid-12">
        <div class="col-6">
          <div class="panel">
            <div class="section-head">
              <span class="section-title">Distribusi Level Kualifikasi SKP &amp; Non-SKP</span>
            </div>
            <div id="chartLevels" class="tabular-nums" style="font-size: 13px; color: var(--text-muted);">
              Belum ada data analitik.
            </div>
          </div>

          <div class="panel">
            <div class="section-head">
              <span class="section-title">Status Masa Berlaku (Expiry Compliance)</span>
            </div>
            <div id="chartExpiry" class="tabular-nums" style="font-size: 13px; color: var(--text-muted);">
              Belum ada data masa berlaku.
            </div>
          </div>
        </div>

        <div class="col-6">
          <div class="panel">
            <div class="section-head">
              <span class="section-title">Top Resource Code (Komparasi NPS 0726)</span>
            </div>
            <div id="chartResourceCodes" class="tabular-nums" style="font-size: 13px; color: var(--text-muted);">
              Belum ada data Resource Code.
            </div>
          </div>

          <div class="panel">
            <div class="section-head">
              <span class="section-title">Distribusi Unit Kerja</span>
            </div>
            <div id="chartUnits" class="tabular-nums" style="font-size: 13px; color: var(--text-muted);">
              Belum ada data unit kerja.
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- TAB 4: PENGATURAN & AI -->
    <section id="tabContentSettings" class="hidden">
      <div class="panel" style="max-width: 760px; margin: 0 auto;">
        <div class="section-head">
          <span class="section-title">Pengaturan Permanen &amp; Kunci API Gemini</span>
          <span style="font-size: 12px; color: var(--text-muted);">~/.certslice_settings.json</span>
        </div>

        <div style="display: grid; gap: 16px;">
          <div>
            <label class="label-sm" for="tabSettingsApiKey">Google Gemini API Key</label>
            <div style="display: flex; gap: 8px;">
              <input type="password" id="tabSettingsApiKey" class="input-field" placeholder="AIzaSy...">
              <button type="button" onclick="toggleSettingsApiKeyVisibility()" class="btn btn-ghost">Tampilkan</button>
            </div>
            <p style="font-size: 12px; color: var(--text-muted); margin-top: 6px;">
              Digunakan khusus ketika opsi AI Rescue diaktifkan untuk membaca sertifikat hasil scan buram yang gagal dikenali secara offline.
            </p>
          </div>

          <div>
            <label class="label-sm">Model AI Rescue Default</label>
            <label class="surface-inner" style="display: flex; align-items: center; justify-content: space-between; cursor: pointer;">
              <div>
                <div style="font-weight: 700; font-size: 13px;">Gemini Flash-Lite (gemini-flash-lite-latest)</div>
                <div style="font-size: 12px; color: var(--text-muted);">Kecepatan tinggi, latensi rendah, dan efisiensi kuota terbaik untuk ekstraksi batch.</div>
              </div>
              <input type="radio" name="optModel" value="gemini-flash-lite-latest" checked>
            </label>
          </div>

          <div style="display: flex; align-items: center; justify-content: space-between; padding-top: 12px; border-top: 1px solid var(--border);">
            <span id="settingsSaveToast" class="hidden" style="font-size: 13px; font-weight: 600; color: var(--ok);">
              Pengaturan berhasil disimpan!
            </span>
            <button type="button" onclick="saveFullSettings()" class="btn btn-primary" style="margin-left: auto;">
              Simpan Pengaturan
            </button>
          </div>
        </div>
      </div>
    </section>

  </main>

  <script>
    let currentJobId = null;
    let sourceMode = 'upload';
    let selectedPdfFiles = [];
    let selectedDbFile = null;
    let allItems = [];
    let activeItemIndex = null;
    let currentPreviewPage = 1;
    let totalPreviewPages = 1;
    let currentZoom = 1.0;
    let latestRunDir = "";

    document.addEventListener('DOMContentLoaded', () => {
      fetchSettings();
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          const revSection = document.getElementById('tabContentReview');
          if (!revSection.classList.contains('hidden') && activeItemIndex !== null) {
            e.preventDefault();
            saveItemApproval();
          }
        }
      });
    });

    function toggleTheme() {
      const html = document.documentElement;
      const curr = html.getAttribute('data-theme') || 'dark';
      html.setAttribute('data-theme', curr === 'dark' ? 'light' : 'dark');
    }

    function setSourceMode(mode) {
      sourceMode = mode;
      const upPanel = document.getElementById('panelUploadMode');
      const locPanel = document.getElementById('panelLocalMode');
      const btnUp = document.getElementById('modeBtnUpload');
      const btnLoc = document.getElementById('modeBtnLocal');
      if (mode === 'upload') {
        upPanel.classList.remove('hidden');
        locPanel.classList.add('hidden');
        btnUp.className = 'btn btn-primary';
        btnLoc.className = 'btn btn-ghost';
      } else {
        upPanel.classList.add('hidden');
        locPanel.classList.remove('hidden');
        btnUp.className = 'btn btn-ghost';
        btnLoc.className = 'btn btn-primary';
      }
    }

    async function fetchSettings() {
      try {
        const resp = await fetch('/api/settings');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.gemini_api_key) {
          document.getElementById('geminiApiKeyInput').value = data.gemini_api_key;
          document.getElementById('tabSettingsApiKey').value = data.gemini_api_key;
          document.getElementById('apiKeyStatusText').innerText = "API Key tersimpan di sistem";
        }
        if (data.model) {
          document.getElementById('geminiModelSelect').value = data.model;
        }
        if (data.use_ai) {
          document.getElementById('chkUseAi').checked = true;
          toggleAiOptions(true);
        }
      } catch (err) {
        console.warn("Gagal memuat pengaturan:", err);
      }
    }

    function toggleAiOptions(show) {
      const panel = document.getElementById('aiSettingsPanel');
      const badge = document.getElementById('aiActiveIndicator');
      if (show) {
        panel.classList.remove('hidden');
        badge.classList.remove('hidden');
      } else {
        panel.classList.add('hidden');
        badge.classList.add('hidden');
      }
    }

    function toggleApiKeyVisibility() {
      const inp = document.getElementById('geminiApiKeyInput');
      inp.type = inp.type === 'password' ? 'text' : 'password';
    }

    function toggleSettingsApiKeyVisibility() {
      const inp = document.getElementById('tabSettingsApiKey');
      inp.type = inp.type === 'password' ? 'text' : 'password';
    }

    async function saveApiKeyDirect() {
      const key = document.getElementById('geminiApiKeyInput').value.trim();
      const model = document.getElementById('geminiModelSelect').value;
      const useAi = document.getElementById('chkUseAi').checked;
      if (!key) return alert("Masukkan API Key terlebih dahulu!");
      const resp = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gemini_api_key: key, model: model, use_ai: useAi })
      });
      if (resp.ok) {
        document.getElementById('apiKeyStatusText').innerText = "API Key berhasil disimpan!";
        document.getElementById('tabSettingsApiKey').value = key;
      }
    }

    async function saveFullSettings() {
      const key = document.getElementById('tabSettingsApiKey').value.trim();
      const resp = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gemini_api_key: key, model: 'gemini-flash-lite-latest' })
      });
      if (resp.ok) {
        document.getElementById('geminiApiKeyInput').value = key;
        const toast = document.getElementById('settingsSaveToast');
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 3000);
      }
    }

    function switchTab(tab) {
      ['Process', 'Review', 'Analytics', 'Settings'].forEach(t => {
        const key = t.toLowerCase();
        const content = document.getElementById('tabContent' + t);
        const btn = document.getElementById('tabBtn' + t);
        if (key === tab) {
          content.classList.remove('hidden');
          btn.classList.add('active');
        } else {
          content.classList.add('hidden');
          btn.classList.remove('active');
        }
      });
      if (tab === 'analytics' && currentJobId) {
        loadAnalytics(currentJobId);
      }
    }

    const pdfDropzone = document.getElementById('pdfDropzone');
    const pdfInput = document.getElementById('pdfInput');
    pdfDropzone.addEventListener('click', () => pdfInput.click());
    pdfDropzone.addEventListener('dragover', (e) => { e.preventDefault(); pdfDropzone.classList.add('drag-active'); });
    pdfDropzone.addEventListener('dragleave', () => { pdfDropzone.classList.remove('drag-active'); });
    pdfDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      pdfDropzone.classList.remove('drag-active');
      handlePdfSelect(e.dataTransfer.files);
    });

    function handlePdfSelect(files) {
      selectedPdfFiles = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
      document.getElementById('pdfFileCount').innerText = `${selectedPdfFiles.length} berkas terpilih`;
      const listEl = document.getElementById('pdfSelectedList');
      if (selectedPdfFiles.length > 0) {
        listEl.classList.remove('hidden');
        listEl.innerHTML = selectedPdfFiles.map(f => `
          <div class="tabular-nums" style="display: flex; justify-content: space-between; padding: 6px 10px; border-bottom: 1px solid var(--border);">
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 75%;">${f.name}</span>
            <span style="color: var(--text-muted);">${(f.size / 1024).toFixed(1)} KB</span>
          </div>
        `).join('');
      } else {
        listEl.classList.add('hidden');
      }
    }

    const dbDropzone = document.getElementById('dbDropzone');
    const dbInput = document.getElementById('dbInput');
    dbDropzone.addEventListener('click', () => {
      if (!document.getElementById('chkUseSampleDb').checked) dbInput.click();
    });

    function toggleSampleDb(useSample) {
      dbInput.disabled = useSample;
      dbDropzone.style.opacity = useSample ? '0.65' : '1';
      if (useSample) {
        document.getElementById('dbFilenameLabel').innerText = "templates/sample_database.xlsx (Aktif)";
        selectedDbFile = null;
      } else {
        document.getElementById('dbFilenameLabel').innerText = "Klik untuk memilih berkas Excel database pegawai (.xlsx)...";
      }
    }

    function handleDbSelect(files) {
      if (files.length > 0) {
        selectedDbFile = files[0];
        document.getElementById('dbFilenameLabel').innerText = `${selectedDbFile.name} (${(selectedDbFile.size / 1024).toFixed(1)} KB)`;
      }
    }

    async function startProcessing() {
      const btn = document.getElementById('btnStartProcess');
      const useSample = document.getElementById('chkUseSampleDb').checked;
      const useOcr = document.getElementById('chkUseOcr').checked;
      const useAi = document.getElementById('chkUseAi').checked;
      const geminiKey = document.getElementById('geminiApiKeyInput').value.trim();
      const geminiModel = document.getElementById('geminiModelSelect').value;
      const runName = document.getElementById('runNameInput').value.trim();

      const formData = new FormData();
      let endpoint = '/api/process';

      if (sourceMode === 'local') {
        const folderPath = document.getElementById('localFolderPathInput').value.trim();
        if (!folderPath) return alert("Masukkan path folder lokal terlebih dahulu!");
        endpoint = '/api/process-local';
        formData.append('folder_path', folderPath);
        formData.append('mode', document.getElementById('localProcessMode').value);
      } else {
        if (selectedPdfFiles.length === 0) return alert("Pilih minimal satu berkas PDF terlebih dahulu!");
        selectedPdfFiles.forEach(f => formData.append('files', f));
        if (!useSample && selectedDbFile) {
          formData.append('db_file', selectedDbFile);
        }
      }

      formData.append('use_sample_db', useSample);
      formData.append('use_ocr', useOcr);
      formData.append('use_ai', useAi);
      if (useAi && geminiKey) {
        formData.append('gemini_key', geminiKey);
        formData.append('gemini_model', geminiModel);
      }
      if (runName) formData.append('run_name', runName);

      btn.disabled = true;
      btn.innerText = "Memproses...";

      try {
        const resp = await fetch(endpoint, { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.detail || "Gagal memulai pemrosesan");
        }
        const data = await resp.json();
        currentJobId = data.job_id;
        document.getElementById('statusBadge').innerText = "Berjalan";
        listenToProgress(currentJobId);
      } catch (err) {
        alert("Error: " + err.message);
        btn.disabled = false;
        btn.innerText = "Mulai Proses Sertifikat";
      }
    }

    function listenToProgress(jobId) {
      const evtSource = new EventSource(`/api/progress/${jobId}`);
      const logConsole = document.getElementById('logConsole');

      evtSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        document.getElementById('progressPercent').innerText = `${data.progress}%`;
        document.getElementById('progressBar').style.width = `${data.progress}%`;
        document.getElementById('progressMessage').innerText = data.message;

        if (data.new_logs && data.new_logs.length > 0) {
          data.new_logs.forEach(msg => {
            const div = document.createElement('div');
            div.style.color = msg.includes("ERROR") ? "#f43f5e" : "#e2e8f0";
            div.textContent = msg;
            logConsole.appendChild(div);
          });
          logConsole.scrollTop = logConsole.scrollHeight;
        }

        if (data.status === 'completed') {
          evtSource.close();
          document.getElementById('statusBadge').innerText = "Selesai";
          const btn = document.getElementById('btnStartProcess');
          btn.disabled = false;
          btn.innerText = "Mulai Proses Baru";
          fetchResults(jobId);
        } else if (data.status === 'error') {
          evtSource.close();
          document.getElementById('statusBadge').innerText = "Error";
          const btn = document.getElementById('btnStartProcess');
          btn.disabled = false;
          btn.innerText = "Mulai Ulang";
        }
      };
    }

    async function fetchResults(jobId) {
      const resp = await fetch(`/api/jobs/${jobId}/results`);
      if (!resp.ok) return;
      const res = await resp.json();
      allItems = res.items || [];
      latestRunDir = res.run_dir;

      const banner = document.getElementById('resultBanner');
      banner.classList.remove('hidden');
      document.getElementById('resultSummaryText').innerText =
        `Total ${res.total_files} sertifikat (${res.elapsed_sec || 0}s): ${res.accepted_count} Tervalidasi, ${res.review_count} Perlu Review, ${res.lampiran_count || 0} Lampiran.`;

      document.getElementById('btnDownloadNps').classList.toggle('hidden', !res.has_nps_xlsx);
      document.getElementById('btnDownloadLampiran').classList.toggle('hidden', !res.has_lampiran_xlsx);

      const badge = document.getElementById('badgeReviewCount');
      if (res.review_count > 0) {
        badge.classList.remove('hidden');
        badge.innerText = `${res.review_count} review`;
      } else {
        badge.classList.add('hidden');
      }

      renderItemsList();
      loadAnalytics(jobId);
    }

    function renderItemsList() {
      const filter = document.getElementById('filterStatus').value;
      const q = (document.getElementById('searchReviewInput').value || '').toLowerCase().trim();
      const container = document.getElementById('itemsContainer');

      const filtered = allItems.filter(item => {
        if (filter === 'unmatched' && item.status === 'accepted') return false;
        if (filter === 'accepted' && item.status !== 'accepted') return false;
        if (q) {
          const hay = `${item.nama} ${item.nid} ${item.judul} ${item.unit} ${item.level} ${item.resource_code} ${item.file_name}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      });

      document.getElementById('reviewItemBadge').innerText = `${filtered.length} / ${allItems.length} Item`;

      if (filtered.length === 0) {
        container.innerHTML = `<div style="text-align: center; padding: 36px 12px; color: var(--text-muted); font-size: 12px;">Tidak ada item yang cocok dengan filter.</div>`;
        return;
      }

      container.innerHTML = filtered.map(item => {
        const isAcc = item.status === 'accepted';
        const statusPill = isAcc
          ? `<span class="pill pill-ok" style="padding: 1px 8px; font-size: 11px;">Tervalidasi</span>`
          : `<span class="pill pill-warn" style="padding: 1px 8px; font-size: 11px;">Perlu Review</span>`;

        const isSelected = item.index === activeItemIndex;
        const rcBadge = item.resource_code ? `<span class="pill pill-accent" style="padding: 1px 6px; font-size: 10px;">RC: ${item.resource_code}</span>` : '';

        return `
          <div onclick="selectItem(${item.index})" class="item-row tabular-nums ${isSelected ? 'selected' : ''}">
            <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 4px;">
              <strong style="font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">#${item.index} ${item.nama || item.file_name}</strong>
              ${statusPill}
            </div>
            <div style="font-size: 12px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              ${item.judul || 'Sertifikat'} ${item.level ? '• ' + item.level : ''}
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-top: 4px;">
              <span>NID: ${item.nid || '-'} • ${item.unit || '-'}</span>
              ${rcBadge}
            </div>
          </div>
        `;
      }).join('');

      if (activeItemIndex === null && filtered.length > 0) {
        selectItem(filtered[0].index);
      }
    }

    function selectItem(index) {
      activeItemIndex = index;
      currentPreviewPage = 1;
      renderItemsList();

      const item = allItems.find(it => it.index === index);
      if (!item) return;

      totalPreviewPages = Math.max(1, item.page_count || (item.pages ? item.pages.length : 1));
      document.getElementById('editNama').value = item.nama || "";
      document.getElementById('editNid').value = item.nid || "";
      document.getElementById('editUnit').value = item.unit || "";
      document.getElementById('editJabatan').value = item.jabatan || "";
      document.getElementById('editJudul').value = item.judul || "";
      document.getElementById('editLevel').value = item.level || "";
      document.getElementById('editPeran').value = item.peran || "";
      document.getElementById('editResourceCode').value = item.resource_code || "";

      const candEl = document.getElementById('candidatesList');
      if (item.candidates && item.candidates.length > 0) {
        candEl.innerHTML = item.candidates.map(c => `
          <div onclick="applyCandidate('${(c.nid||'').replace(/'/g, "\\'")}', '${(c.nama||'').replace(/'/g, "\\'")}', '${(c.unit||'').replace(/'/g, "\\'")}')"
               class="item-row tabular-nums" style="margin-bottom: 0; padding: 8px 10px; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="font-weight: 700; font-size: 12px;">${c.nama}</div>
              <div style="font-size: 11px; color: var(--text-muted);">NID: ${c.nid} • ${c.unit || '-'}</div>
            </div>
            <span class="pill pill-accent" style="font-size: 11px;">${c.score}%</span>
          </div>
        `).join('');
      } else {
        candEl.innerHTML = `<div style="font-size: 12px; color: var(--text-muted);">Tidak ada kandidat alternatif terdeteksi.</div>`;
      }

      loadPdfPreview(index, currentPreviewPage);
    }

    function applyCandidate(nid, nama, unit) {
      document.getElementById('editNid').value = nid;
      document.getElementById('editNama').value = nama;
      document.getElementById('editUnit').value = unit;
    }

    function changePreviewPage(delta) {
      if (activeItemIndex === null) return;
      const next = currentPreviewPage + delta;
      if (next < 1 || next > totalPreviewPages) return;
      currentPreviewPage = next;
      loadPdfPreview(activeItemIndex, currentPreviewPage);
    }

    function loadPdfPreview(itemIndex, pageNum = 1) {
      const img = document.getElementById('pdfPreviewImg');
      const placeholder = document.getElementById('previewPlaceholder');
      document.getElementById('pageIndicator').innerText = `Hal ${pageNum} / ${totalPreviewPages}`;

      img.src = `/api/jobs/${currentJobId}/preview/${itemIndex}?page=${pageNum}&zoom=${currentZoom}&t=${Date.now()}`;
      img.onload = () => {
        img.classList.remove('hidden');
        placeholder.classList.add('hidden');
      };
      img.onerror = () => {
        img.classList.add('hidden');
        placeholder.classList.remove('hidden');
        placeholder.innerText = "Gagal memuat pratinjau halaman PDF.";
      };
    }

    function zoomPreview(factor) {
      currentZoom = Math.max(0.5, Math.min(3.0, currentZoom * factor));
      document.getElementById('pdfPreviewImg').style.transform = `scale(${currentZoom})`;
    }

    function resetZoom() {
      currentZoom = 1.0;
      document.getElementById('pdfPreviewImg').style.transform = `scale(1)`;
    }

    async function saveItemApproval() {
      if (activeItemIndex === null || !currentJobId) return;
      const btn = document.getElementById('btnApproveItem');
      btn.disabled = true;
      btn.innerText = "Menyimpan & Memperbarui Excel...";

      const formData = new FormData();
      formData.append('item_index', activeItemIndex);
      formData.append('nid', document.getElementById('editNid').value);
      formData.append('nama', document.getElementById('editNama').value);
      formData.append('unit', document.getElementById('editUnit').value);
      formData.append('jabatan', document.getElementById('editJabatan').value);
      formData.append('custom_title', document.getElementById('editJudul').value);
      formData.append('level', document.getElementById('editLevel').value);
      formData.append('peran', document.getElementById('editPeran').value);
      formData.append('resource_code', document.getElementById('editResourceCode').value);

      try {
        const resp = await fetch(`/api/jobs/${currentJobId}/approve`, { method: 'POST', body: formData });
        if (!resp.ok) throw new Error("Gagal menyimpan persetujuan");
        const res = await resp.json();

        const localItem = allItems.find(it => it.index === activeItemIndex);
        if (localItem) {
          localItem.status = 'accepted';
          localItem.nid = document.getElementById('editNid').value;
          localItem.nama = document.getElementById('editNama').value;
          localItem.unit = document.getElementById('editUnit').value;
          localItem.jabatan = document.getElementById('editJabatan').value;
          localItem.judul = document.getElementById('editJudul').value;
          localItem.level = document.getElementById('editLevel').value;
          localItem.peran = document.getElementById('editPeran').value;
          localItem.resource_code = res.resource_code || document.getElementById('editResourceCode').value;
          localItem.file_name = res.file_name;
          document.getElementById('editResourceCode').value = localItem.resource_code || "";
        }

        const badge = document.getElementById('badgeReviewCount');
        if (res.review_count > 0) {
          badge.classList.remove('hidden');
          badge.innerText = `${res.review_count} review`;
        } else {
          badge.classList.add('hidden');
        }

        renderItemsList();
      } catch (err) {
        alert("Error: " + err.message);
      } finally {
        btn.disabled = false;
        btn.innerText = "Setujui, Potong Ulang PDF & Perbarui Semua Excel (Ctrl+Enter)";
      }
    }

    async function batchApproveHighConfidence() {
      if (!currentJobId) return;
      const btn = document.getElementById('btnBatchApprove');
      btn.disabled = true;
      try {
        const resp = await fetch(`/api/jobs/${currentJobId}/approve-batch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ min_score: 85.0 })
        });
        if (!resp.ok) throw new Error("Gagal menjalankan persetujuan massal");
        await fetchResults(currentJobId);
      } catch (err) {
        alert("Error: " + err.message);
      } finally {
        btn.disabled = false;
      }
    }

    async function loadAnalytics(jobId) {
      try {
        const resp = await fetch(`/api/jobs/${jobId}/analytics`);
        if (!resp.ok) return;
        const a = await resp.json();

        document.getElementById('kpiTotalCerts').innerText = a.total_certificates;
        document.getElementById('kpiAcceptanceRate').innerText = `${a.acceptance_rate}%`;
        document.getElementById('kpiResourceRate').innerText = `${a.resource_code_rate}%`;
        document.getElementById('kpiThroughput').innerText = `${a.throughput_items_per_sec} dok/dtk`;

        const renderBars = (obj, total) => {
          const entries = Object.entries(obj || {});
          if (entries.length === 0) return `<div style="color: var(--text-muted);">Tidak ada data.</div>`;
          return entries.map(([k, v]) => {
            const pct = Math.round((v / Math.max(1, total)) * 100);
            return `
              <div class="bar-row tabular-nums">
                <span style="width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${k}</span>
                <div class="bar-track"><div class="bar-fill" style="width: ${pct}%;"></div></div>
                <strong style="width: 70px; text-align: right;">${v} (${pct}%)</strong>
              </div>
            `;
          }).join('');
        };

        document.getElementById('chartLevels').innerHTML = renderBars(a.level_distribution, a.total_certificates);
        document.getElementById('chartResourceCodes').innerHTML = renderBars(a.top_resource_codes, a.total_certificates);
        document.getElementById('chartUnits').innerHTML = renderBars(a.unit_distribution, a.total_certificates);

        const eb = a.expiry_breakdown || {};
        document.getElementById('chartExpiry').innerHTML = renderBars({
          "Aktif (> 90 Hari)": eb.active || 0,
          "Segera Habis (<= 90 Hari)": eb.expiring_soon || 0,
          "Kedaluwarsa (Expired)": eb.expired || 0,
          "Tanpa Tanggal Expired": eb.no_expiry || 0,
        }, a.total_certificates);
      } catch (err) {
        console.warn("Gagal memuat analitik:", err);
      }
    }

    function downloadZip() {
      if (!currentJobId) return;
      window.location.href = `/api/jobs/${currentJobId}/download/zip`;
    }

    function downloadExcel(filename) {
      if (!currentJobId) return;
      window.location.href = `/api/jobs/${currentJobId}/download/${encodeURIComponent(filename)}`;
    }

    async function openFolderNative() {
      if (!latestRunDir) return;
      const formData = new FormData();
      formData.append('path', latestRunDir);
      await fetch('/api/open-folder', { method: 'POST', body: formData });
    }
  </script>
</body>
</html>
"""


def launch_web(host: str = "0.0.0.0", port: int = 8500, open_browser: bool = True) -> None:
    """Launch the Certificate Manager Precision Studio Web server using Uvicorn."""
    import uvicorn
    import webbrowser

    url = f"http://127.0.0.1:{port}"
    if sys.stdout is not None:
        try:
            print("\n=======================================================")
            print("Certificate Manager — Precision Studio Web Edition v3.0")
            print(f"Server berjalan di: {url}")
            print("Tekan Ctrl+C di terminal ini untuk berhenti.")
            print("=======================================================\n")
        except Exception:
            pass

    if open_browser:
        def _open() -> None:
            time.sleep(1.0)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    is_main_thread = threading.current_thread() is threading.main_thread()
    has_std_streams = sys.stdout is not None and sys.stderr is not None
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info" if has_std_streams else "critical",
        log_config=uvicorn.config.LOGGING_CONFIG if has_std_streams else None,
    )
    server = uvicorn.Server(config)
    if not is_main_thread:
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    server.run()



if __name__ == "__main__":
    launch_web()
