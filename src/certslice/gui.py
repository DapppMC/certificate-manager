"""Certificate Sorter - Desktop GUI Application (Certificate Management & Settings)."""
from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

# Enable Windows High-DPI Awareness (Crisp Full HD / 2K / 4K rendering)
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

import pymupdf
import webbrowser

from certslice.bulk import (
    BulkResult,
    MultiFolderResult,
    find_all_pdf_folders,
    optimize_process_priority,
    parse_formatted_certificate_name,
    process_bulk_files,
    process_multi_folder_tree,
    process_preformatted_ftp_folder,
)
from certslice.gemini import DEFAULT_GEMINI_API_KEY, load_user_settings, save_user_settings
from certslice.naming import clean_field
from certslice import pipeline
from certslice.resource_codes import get_resource_code_catalog
from certslice.reviewer import get_review_items, render_page_image, approve_group

UI_FONT = "Noto Sans" if sys.platform != "win32" else "Segoe UI"
MONO_FONT = "Noto Sans Mono" if sys.platform != "win32" else "Consolas"


def configure_global_fonts(root: Any) -> None:
    """Configure crisp anti-aliased fonts globally for Tkinter using available modern system fonts."""
    global UI_FONT, MONO_FONT
    try:
        import tkinter.font as tkfont
        available = set(tkfont.families(root))
        for candidate in ["Noto Sans", "DejaVu Sans", "Liberation Sans", "Cantarell", "Ubuntu", "Segoe UI", "Arial"]:
            if candidate in available:
                UI_FONT = candidate
                break
        for candidate in ["Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono", "Consolas", "Courier New"]:
            if candidate in available:
                MONO_FONT = candidate
                break

        for name in ["TkDefaultFont", "TkTextFont", "TkMenuFont"]:
            try:
                tkfont.nametofont(name).configure(family=UI_FONT, size=10)
            except Exception:
                pass
        for name in ["TkHeadingFont", "TkCaptionFont"]:
            try:
                tkfont.nametofont(name).configure(family=UI_FONT, size=11, weight="bold")
            except Exception:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(family=MONO_FONT, size=10)
        except Exception:
            pass
    except Exception:
        pass


def get_default_database_path() -> str:
    """Find default database path from settings, application directory, or common folders."""
    settings = load_user_settings()
    saved = settings.get("default_db_path")
    if saved and Path(saved).exists():
        return saved

    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent

    # Check if bundled Data Master SDM or database exists in app directory, cwd, or templates
    for cand_dir in [app_dir, Path.cwd(), app_dir / "templates"]:
        if cand_dir.exists():
            for f in cand_dir.glob("Data Master*.xlsx"):
                return str(f.resolve())
            for f in cand_dir.glob("*database*.xlsx"):
                return str(f.resolve())

    # User download directory
    plan_dir = Path.home() / "Downloads" / "Plan"
    if plan_dir.exists():
        for f in plan_dir.glob("Data Master*.xlsx"):
            return str(f)

    # Check samples directory
    samples_dir = Path("samples")
    if samples_dir.exists():
        for f in samples_dir.glob("*.xlsx"):
            return str(f.resolve())

    return ""


def get_default_komparasi_path() -> str:
    """Find default Komparasi Kode path from settings, application directory, or common folders."""
    settings = load_user_settings()
    saved = settings.get("default_komparasi_path")
    if saved and Path(saved).exists():
        return saved

    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent

    for cand_dir in [app_dir, Path.cwd(), app_dir / "templates"]:
        if cand_dir.exists():
            for f in cand_dir.glob("*Komparasi*.xlsx"):
                return str(f.resolve())
            for f in cand_dir.glob("*Kode Baru*.xlsx"):
                return str(f.resolve())

    # User download directory
    plan_dir = Path.home() / "Downloads" / "Plan"
    if plan_dir.exists():
        for f in plan_dir.glob("*Komparasi*.xlsx"):
            return str(f.resolve())

    return ""


def get_default_mso_path() -> str:
    """Find default MSO765 template path from settings, application directory, or common folders."""
    settings = load_user_settings()
    saved = settings.get("default_mso_path")
    if saved and Path(saved).exists():
        return saved

    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent

    for cand_dir in [app_dir, Path.cwd(), app_dir / "templates"]:
        if cand_dir.exists():
            for f in cand_dir.glob("*MSO765*.xlsx"):
                return str(f.resolve())
            for f in cand_dir.glob("*MSO*.xlsx"):
                return str(f.resolve())

    # User download directory
    plan_dir = Path.home() / "Downloads" / "Plan"
    if plan_dir.exists():
        for f in plan_dir.glob("*MSO765*.xlsx"):
            return str(f.resolve())

    return ""


def get_default_output_dir() -> str:
    """Find default output folder."""
    settings = load_user_settings()
    saved = settings.get("default_out_path")
    if saved and Path(saved).exists():
        return saved

    d = Path.home() / "Downloads" / "Out"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def make_human_friendly_run_name(pdf_path: Path) -> str:
    """Generate human-friendly folder name: Hasil_[Nama_PDF]_[DD-MMM-YYYY_HH.MM]."""
    stem = pdf_path.stem
    clean_stem = "".join(c for c in stem if c.isalnum() or c in (" ", "-", "_")).strip()
    if len(clean_stem) > 35:
        clean_stem = clean_stem[:35].strip()

    months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agt", "Sep", "Okt", "Nov", "Des"]
    now = datetime.now()
    month_name = months[now.month - 1]
    time_str = now.strftime(f"%d-{month_name}-%Y_%H.%M")
    return f"Hasil_{clean_stem}_{time_str}"


class TextHandler(logging.Handler):
    """Logging handler that writes formatted log records to a Tkinter Text widget."""

    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)

        def append() -> None:
            try:
                self.text_widget.configure(state="normal")
                self.text_widget.insert("end", msg + "\n")
                self.text_widget.see("end")
                self.text_widget.configure(state="disabled")
            except Exception:
                pass

        try:
            self.text_widget.after(0, append)
        except Exception:
            pass


# =========================================================================
# REVIEW & KOREKSI DIALOG (MODAL WINDOW)
# =========================================================================
class ReviewDialog(tk.Toplevel):
    def __init__(self, parent: CertSliceApp, run_dir: Path) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.run_dir = run_dir
        self.title("Review & Koreksi Sertifikat - Certificate Manager")

        # Responsive sizing: adaptive to screen resolution and DPI
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        init_w = min(1280, max(900, screen_w - 60))
        init_h = min(840, max(600, screen_h - 80))
        self.geometry(f"{init_w}x{init_h}")
        self.minsize(800, 520)
        self.resizable(True, True)
        self.configure(bg="#F1F5F9")
        # Do not use self.transient(parent) so that Windows titlebar keeps full Minimize & Maximize buttons
        self.lift()
        self.focus_force()
        configure_global_fonts(self)

        # Maximize by default on Windows so user gets clean full screen without clipping
        try:
            self.state("zoomed")
        except Exception:
            pass

        self.review_items: list[dict[str, Any]] = []
        self.selected_item: dict[str, Any] | None = None
        self.current_preview_page: int = 1
        self.current_photo: tk.PhotoImage | None = None
        self.zoom_level: float = 1.0
        self.fit_width_mode: bool = True
        self.canvas_img_id: Any = None

        self._build_ui()
        self.bind("<Configure>", self._on_window_configure)
        self.bind("<Control-Return>", lambda e: self._approve_item())
        self.bind("<Control-equal>", lambda e: self._zoom_in())
        self.bind("<Control-plus>", lambda e: self._zoom_in())
        self.bind("<Control-minus>", lambda e: self._zoom_out())
        self._load_data()

    def _build_ui(self) -> None:
        # Header banner
        header = tk.Frame(self, bg="#FFFFFF", relief="solid", bd=1)
        header.pack(fill="x", padx=10, pady=(10, 6), ipady=4)

        # Window controls on the right (Minimize, Maximize / Restore, Close)
        win_ctrls = tk.Frame(header, bg="#FFFFFF")
        win_ctrls.pack(side="right", padx=12, pady=2)

        self.btn_minimize = ttk.Button(
            win_ctrls,
            text="🗕 Minimalkan",
            style="Secondary.TButton",
            command=self._minimize_window,
        )
        self.btn_minimize.pack(side="left", padx=2)

        init_max_text = "❐ Pulihkan Ukuran" if (getattr(self, "state", lambda: "")() == "zoomed") else "🗖 Maksimalkan"
        self.btn_toggle_max = ttk.Button(
            win_ctrls,
            text=init_max_text,
            style="Secondary.TButton",
            command=self._toggle_maximize,
        )
        self.btn_toggle_max.pack(side="left", padx=2)

        btn_close = ttk.Button(
            win_ctrls,
            text="✕ Tutup",
            style="Secondary.TButton",
            command=self.destroy,
        )
        btn_close.pack(side="left", padx=2)

        # Left titles
        title_frame = tk.Frame(header, bg="#FFFFFF")
        title_frame.pack(side="left", fill="both", expand=True, padx=12)

        title_lbl = tk.Label(title_frame, text="🔍 Review & Koreksi Hasil Pemotongan", font=(UI_FONT, 12, "bold"), fg="#1E40AF", bg="#FFFFFF")
        title_lbl.pack(anchor="w")

        self.lbl_summary = tk.Label(
            title_frame,
            text=f"Folder Sesi: {self.run_dir.name}",
            font=(UI_FONT, 9),
            fg="#64748B",
            bg="#FFFFFF",
        )
        self.lbl_summary.pack(anchor="w")

        # Paned view: Left list, Right detail & preview
        paned = tk.PanedWindow(self, orient="horizontal", bg="#CBD5E1", sashwidth=5, sashrelief="ridge")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 1. Left List
        left_frame = tk.Frame(paned, bg="#FFFFFF", relief="solid", bd=1)
        paned.add(left_frame, minsize=260, width=320)

        tk.Label(left_frame, text="📋 Daftar yang Perlu Dicek", font=(UI_FONT, 10, "bold"), bg="#F8FAFC", fg="#1E293B", pady=8).pack(fill="x")

        # Left Batch Toolbar
        left_toolbar = tk.Frame(left_frame, bg="#F8FAFC", padx=6, pady=4)
        left_toolbar.pack(fill="x")

        self.btn_approve_all = ttk.Button(
            left_toolbar,
            text="⚡ Setujui Semua (Prediksi Teratas)",
            command=self._approve_all_items,
        )
        self.btn_approve_all.pack(fill="x", pady=2)

        self.btn_approve_selected = ttk.Button(
            left_toolbar,
            text="✓ Setujui Item yang Dipilih",
            command=self._approve_selected_items,
        )
        self.btn_approve_selected.pack(fill="x", pady=(2, 4))

        search_row = tk.Frame(left_toolbar, bg="#F8FAFC")
        search_row.pack(fill="x", pady=(2, 2))
        tk.Label(search_row, text="🔎", bg="#F8FAFC", fg="#64748B", font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._populate_review_tree())
        ttk.Entry(search_row, textvariable=self.search_var).pack(side="left", fill="x", expand=True)

        cols = ("hal", "alasan", "prediksi")
        self.tree = ttk.Treeview(left_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("hal", text="Halaman")
        self.tree.heading("alasan", text="Alasan Ragu")
        self.tree.heading("prediksi", text="Prediksi Teratas")
        self.tree.column("hal", width=65, minwidth=50, stretch=False)
        self.tree.column("alasan", width=110, minwidth=80, stretch=True)
        self.tree.column("prediksi", width=140, minwidth=100, stretch=True)

        tree_scroll = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_select_item)

        # 2. Right Detail & Viewer
        right_frame = tk.Frame(paned, bg="#FFFFFF", relief="solid", bd=1)
        paned.add(right_frame, minsize=500, stretch="always")

        # Top bar of right frame
        self.det_title = tk.Label(right_frame, text="Pilih salah satu item di sebelah kiri", font=(UI_FONT, 10, "bold"), bg="#F8FAFC", fg="#1E293B", pady=8)
        self.det_title.pack(fill="x")

        # Nested PanedWindow inside right frame: Preview on left, Scrollable Form on right
        content_paned = tk.PanedWindow(right_frame, orient="horizontal", bg="#E2E8F0", sashwidth=5, sashrelief="ridge")
        content_paned.pack(fill="both", expand=True, padx=6, pady=6)

        # Preview col
        prev_col = tk.Frame(content_paned, bg="#FFFFFF")
        content_paned.add(prev_col, minsize=300, stretch="always")

        prev_nav = tk.Frame(prev_col, bg="#FFFFFF")
        prev_nav.pack(fill="x", pady=2)
        tk.Label(prev_nav, text="Pratinjau Halaman PDF:", font=(UI_FONT, 9, "bold"), bg="#FFFFFF").pack(side="left")

        self.btn_prev_p = ttk.Button(prev_nav, text="◀ Hal Sblm", width=10, command=self._prev_page)
        self.btn_prev_p.pack(side="right", padx=2)
        self.btn_next_p = ttk.Button(prev_nav, text="Hal Brkt ▶", width=10, command=self._next_page)
        self.btn_next_p.pack(side="right", padx=2)
        self.lbl_page_num = tk.Label(prev_nav, text="Hal -", font=(UI_FONT, 9), bg="#FFFFFF", fg="#64748B")
        self.lbl_page_num.pack(side="right", padx=4)

        # Zoom Toolbar
        zoom_bar = tk.Frame(prev_col, bg="#F1F5F9", relief="flat", bd=1, padx=4, pady=2)
        zoom_bar.pack(fill="x", pady=(2, 4))

        ttk.Button(zoom_bar, text="🔍 -", width=5, command=self._zoom_out).pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="100%", width=6, command=self._zoom_reset).pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="🔍 +", width=5, command=self._zoom_in).pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="⛶ Fit Lebar", width=10, command=self._zoom_fit_width).pack(side="left", padx=4)
        self.lbl_zoom = tk.Label(zoom_bar, text="Fit Lebar", font=(UI_FONT, 9, "bold"), bg="#F1F5F9", fg="#1E40AF")
        self.lbl_zoom.pack(side="left", padx=4)
        tk.Label(zoom_bar, text="(Scroll / Ctrl+Scroll)", font=(UI_FONT, 8, "italic"), bg="#F1F5F9", fg="#64748B").pack(side="right", padx=4)

        # Scrollable Canvas
        canvas_container = tk.Frame(prev_col, bg="#F8FAFC", relief="solid", bd=1)
        canvas_container.pack(fill="both", expand=True)

        self.preview_canvas = tk.Canvas(canvas_container, bg="#CBD5E1", highlightthickness=0)
        self.v_scrollbar = ttk.Scrollbar(canvas_container, orient="vertical", command=self.preview_canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(canvas_container, orient="horizontal", command=self.preview_canvas.xview)
        self.preview_canvas.configure(xscrollcommand=self.h_scrollbar.set, yscrollcommand=self.v_scrollbar.set)

        self.v_scrollbar.pack(side="right", fill="y")
        self.h_scrollbar.pack(side="bottom", fill="x")
        self.preview_canvas.pack(side="left", fill="both", expand=True)

        self.preview_canvas.bind("<MouseWheel>", self._on_canvas_wheel)
        self.preview_canvas.bind("<Control-MouseWheel>", self._on_canvas_ctrl_wheel)

        # Scrollable Decision / Form column
        form_outer = tk.Frame(content_paned, bg="#FFFFFF")
        content_paned.add(form_outer, minsize=350, width=420, stretch="never")

        form_canvas = tk.Canvas(form_outer, bg="#FFFFFF", highlightthickness=0)
        form_scroll = ttk.Scrollbar(form_outer, orient="vertical", command=form_canvas.yview)
        form_col = tk.Frame(form_canvas, bg="#FFFFFF")

        form_col.bind(
            "<Configure>",
            lambda e: form_canvas.configure(scrollregion=form_canvas.bbox("all")),
        )
        form_win_id = form_canvas.create_window((0, 0), window=form_col, anchor="nw")
        form_canvas.bind(
            "<Configure>",
            lambda e: form_canvas.itemconfig(form_win_id, width=e.width),
        )
        form_canvas.configure(yscrollcommand=form_scroll.set)
        form_scroll.pack(side="right", fill="y")
        form_canvas.pack(side="left", fill="both", expand=True)

        # Alert Box (Explains why the system hesitated)
        self.alert_box = tk.Label(
            form_col,
            text="Pilih item di sebelah kiri.",
            font=(UI_FONT, 9),
            bg="#FEF3C7",
            fg="#92400E",
            wraplength=360,
            justify="left",
            relief="solid",
            bd=1,
            padx=8,
            pady=8,
        )
        self.alert_box.pack(fill="x", pady=(0, 8), padx=4)

        # Prediction / Candidate recommendation
        tk.Label(form_col, text="Prediksi Rekomendasi dari Database Master:", font=(UI_FONT, 9, "bold"), bg="#FFFFFF").pack(anchor="w", padx=4)
        self.candidates_frame = tk.Frame(form_col, bg="#FFFFFF")
        self.candidates_frame.pack(fill="x", pady=4, padx=4)
        self.cand_idx_var = tk.IntVar(value=-1)

        # Manual edit/tweak fields
        custom_box = ttk.LabelFrame(form_col, text=" Konfirmasi Data Pegawai & Nama File ", padding=8)
        custom_box.pack(fill="x", pady=6, padx=4)

        ttk.Label(custom_box, text="NID:").grid(row=0, column=0, sticky="w", pady=2)
        self.nid_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.nid_var, width=28).grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(custom_box, text="Nama Pegawai:").grid(row=1, column=0, sticky="w", pady=2)
        self.nama_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.nama_var, width=28).grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(custom_box, text="Unit Kerja:").grid(row=2, column=0, sticky="w", pady=2)
        self.unit_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.unit_var, width=28).grid(row=2, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(custom_box, text="Judul Sertifikat:").grid(row=3, column=0, sticky="w", pady=2)
        self.title_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.title_var, width=28).grid(row=3, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(custom_box, text="Tahun:").grid(row=4, column=0, sticky="w", pady=2)
        self.year_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.year_var, width=28).grid(row=4, column=1, sticky="ew", padx=4, pady=2)
        custom_box.columnconfigure(1, weight=1)

        # Action Buttons
        btn_box = tk.Frame(form_col, bg="#FFFFFF")
        btn_box.pack(fill="x", pady=8, padx=4)

        self.btn_approve = ttk.Button(
            btn_box,
            text="✓ Setujui & Lanjut ke Berikutnya",
            style="Success.TButton",
            command=self._approve_item,
        )
        self.btn_approve.pack(fill="x", pady=2, ipady=6)

        self.lbl_status_msg = tk.Label(
            btn_box,
            text="",
            font=(UI_FONT, 9, "bold"),
            bg="#FFFFFF",
            fg="#059669",
            wraplength=380,
            justify="center",
        )
        self.lbl_status_msg.pack(fill="x", pady=2)

        self.btn_open_pdf = ttk.Button(
            btn_box,
            text="📄 Buka File PDF Asli di Reader",
            command=self._open_pdf,
        )
        self.btn_open_pdf.pack(fill="x", pady=2)

    def _toggle_maximize(self) -> None:
        """Toggle window between zoomed (maximized) and normal size."""
        try:
            if self.state() == "zoomed":
                self.state("normal")
                if hasattr(self, "btn_toggle_max"):
                    self.btn_toggle_max.configure(text="🗖 Maksimalkan")
            else:
                self.state("zoomed")
                if hasattr(self, "btn_toggle_max"):
                    self.btn_toggle_max.configure(text="❐ Pulihkan Ukuran")
        except Exception:
            pass

    def _minimize_window(self) -> None:
        """Minimize the review window to taskbar."""
        try:
            self.iconify()
        except Exception:
            pass

    def _on_window_configure(self, event: Any = None) -> None:
        """Keep the toggle maximize button label in sync with current window state."""
        if event and getattr(event, "widget", None) == self and hasattr(self, "btn_toggle_max"):
            try:
                is_zoomed = (self.state() == "zoomed")
                desired = "❐ Pulihkan Ukuran" if is_zoomed else "🗖 Maksimalkan"
                if self.btn_toggle_max.cget("text") != desired:
                    self.btn_toggle_max.configure(text=desired)
            except Exception:
                pass

    def _load_data(self, select_idx: int = 0) -> None:
        self.review_items = get_review_items(self.run_dir)
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.review_items:
            self.lbl_summary.configure(text=f"Folder Sesi: {self.run_dir.name} — ✓ Semua sertifikat telah tervalidasi!", fg="#059669")
            self.det_title.configure(text="Tidak ada sertifikat yang memerlukan review.")
            self.alert_box.configure(text="Semua sertifikat pada sesi pemotongan ini sudah berhasil dicocokkan 100%.")
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(250, 150, text="✓ Tidak ada item yang memerlukan review.", fill="#059669", font=(UI_FONT, 11, "bold"))
            self.btn_approve.configure(state="disabled")
            if hasattr(self, "btn_approve_all"):
                self.btn_approve_all.configure(state="disabled")
            if hasattr(self, "btn_approve_selected"):
                self.btn_approve_selected.configure(state="disabled")
            return

        self.btn_approve.configure(state="normal")
        if hasattr(self, "btn_approve_all"):
            self.btn_approve_all.configure(state="normal")
        if hasattr(self, "btn_approve_selected"):
            self.btn_approve_selected.configure(state="normal")

        self.lbl_summary.configure(text=f"Folder Sesi: {self.run_dir.name} — ⚠️ Ada {len(self.review_items)} sertifikat butuh konfirmasi.", fg="#D97706")
        self._populate_review_tree(select_idx=select_idx)

    def _populate_review_tree(self, select_idx: int = 0) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        q = (getattr(self, "search_var", None).get() if hasattr(self, "search_var") else "").strip().lower()
        inserted_iids: list[str] = []
        for idx, g in enumerate(self.review_items):
            pages_str = "-".join(str(p) for p in g.get("pages", []))
            reason = g.get("reason", "")
            cands = g.get("candidates", [])
            top_cand = f"{cands[0]['nama']} ({cands[0].get('score', 0)}%)" if cands else "Tidak ada di database"
            sub_folder_name = ""
            if g.get("_plan_dir"):
                try:
                    sub_folder_name = str(Path(g["_plan_dir"]).relative_to(self.run_dir)).replace("\\", "/")
                except Exception:
                    sub_folder_name = Path(g["_plan_dir"]).name

            if q and q not in f"hal {pages_str} {reason} {top_cand} {g.get('nama', '')} {sub_folder_name}".lower():
                continue
            iid = str(idx)
            self.tree.insert("", "end", iid=iid, values=(f"Hal {pages_str}", reason, top_cand))
            inserted_iids.append(iid)

        if inserted_iids:
            target_iid = str(select_idx) if str(select_idx) in inserted_iids else inserted_iids[0]
            self.tree.selection_set(target_iid)
            self.tree.see(target_iid)
            self._on_select_item(None)

    def _on_select_item(self, event: Any) -> None:
        sel = self.tree.selection()
        if not sel:
            return

        idx = int(sel[0])
        if idx >= len(self.review_items):
            return

        grp = self.review_items[idx]
        self.selected_item = grp
        pages = grp.get("pages", [1])
        pages_str = "-".join(str(p) for p in pages)
        folder_info = ""
        if grp.get("_plan_dir"):
            try:
                rel = str(Path(grp["_plan_dir"]).relative_to(self.run_dir)).replace("\\", "/")
                folder_info = f" [{rel}]"
            except Exception:
                folder_info = f" [{Path(grp['_plan_dir']).name}]"
        self.det_title.configure(text=f"Review Sertifikat #{grp.get('id')}{folder_info} (Halaman {pages_str})")

        # Set human friendly explanation
        reason = grp.get("reason", "")
        cands = grp.get("candidates", [])
        raw_name = grp.get("raw_name") or ""

        if not cands or "tidak ditemukan di database" in reason.lower() or reason == "no confident holder":
            target_str = f"'{raw_name}'" if raw_name else "penerima sertifikat"
            msg = (
                f"⚠️ Alasan Ragu: Nama Tidak Ditemukan di Database Master.\n"
                f"Nama yang tertera pada sertifikat ({target_str}) sudah dicari di seluruh file database Excel tetapi TIDAK DITEMUKAN sama sekali.\n\n"
                f"👉 Kolom NID dan Unit Kerja sengaja dikosongkan.\n"
                f"👉 Nama, Judul Sertifikat, dan Tahun telah diambil langsung dari PDF.\n"
                f"Silakan periksa pratinjau gambar sertifikat di sebelah kiri. Jika sudah benar, Anda dapat langsung klik 'Setujui & Simpan ke PDF'."
            )
        elif reason == "partial name":
            cand_name = cands[0]["nama"] if cands else "pegawai terkait"
            msg = (
                f"⚠️ Alasan Ragu: Nama Parsial / Singkatan.\n"
                f"Nama di sertifikat lebih panjang atau hanya singkatan dari data master. "
                f"Sistem memprediksi nama yang paling mendekati adalah '{cand_name}'. "
                f"Silakan periksa pratinjau gambar sertifikat di sebelah kiri untuk memastikan."
            )
        elif reason == "close candidates":
            msg = "⚠️ Alasan Ragu: Ditemukan lebih dari satu kandidat pegawai dengan kemiripan nama yang hampir sama."
        elif reason == "homonym":
            msg = "⚠️ Alasan Ragu: Ditemukan nama yang identik (homonim) di database."
        else:
            msg = f"⚠️ Alasan Ragu: {reason or 'Perlu pemeriksaan manual'}. Silakan cek gambar sertifikat."

        self.alert_box.configure(text=msg)

        # Clear & fill candidates
        for w in self.candidates_frame.winfo_children():
            w.destroy()

        self.cand_idx_var.set(0 if cands else -1)
        if cands:
            for c_idx, c in enumerate(cands):
                cand_text = f"● {c.get('nama', '')}  (Skor: {c.get('score', 0)}%)\n   NID: {c.get('nid', '-')} | Unit: {c.get('unit', '-')}"
                rb = tk.Radiobutton(
                    self.candidates_frame,
                    text=cand_text,
                    variable=self.cand_idx_var,
                    value=c_idx,
                    justify="left",
                    anchor="w",
                    bg="#FFFFFF",
                    font=(UI_FONT, 9),
                    wraplength=340,
                    command=self._on_cand_chosen,
                )
                rb.pack(fill="x", pady=2)
            self._on_cand_chosen()
        else:
            tk.Label(
                self.candidates_frame,
                text="⚠️ Tidak ditemukan kecocokan di database master.\nData NID & Unit dikosongkan. Nama & judul diambil dari sertifikat.",
                bg="#FFFFFF",
                fg="#DC2626",
                justify="left",
                font=(UI_FONT, 9, "bold"),
            ).pack(anchor="w", pady=4)
            self.nama_var.set(raw_name)
            self.nid_var.set("")
            self.unit_var.set("")

        fields = grp.get("fields", {})
        self.title_var.set(fields.get("title", {}).get("value") or "Tanpa Judul")
        iss_date = fields.get("issue_date", {}).get("value")
        self.year_var.set(iss_date.split("-")[0] if iss_date else "0000")

        self.current_preview_page = pages[0]
        self._render_page()

    def _on_cand_chosen(self) -> None:
        idx = self.cand_idx_var.get()
        if not self.selected_item or idx < 0:
            return
        cands = self.selected_item.get("candidates", [])
        if idx < len(cands):
            c = cands[idx]
            self.nid_var.set(c.get("nid", ""))
            self.nama_var.set(c.get("nama", ""))
            self.unit_var.set(c.get("unit", ""))

    def _zoom_in(self) -> None:
        self.fit_width_mode = False
        self.zoom_level = min(3.0, round(self.zoom_level + 0.25, 2))
        self.lbl_zoom.configure(text=f"{int(self.zoom_level * 100)}%")
        self._render_page()

    def _zoom_out(self) -> None:
        self.fit_width_mode = False
        self.zoom_level = max(0.3, round(self.zoom_level - 0.25, 2))
        self.lbl_zoom.configure(text=f"{int(self.zoom_level * 100)}%")
        self._render_page()

    def _zoom_reset(self) -> None:
        self.fit_width_mode = False
        self.zoom_level = 1.0
        self.lbl_zoom.configure(text="100%")
        self._render_page()

    def _zoom_fit_width(self) -> None:
        self.fit_width_mode = True
        self.zoom_level = 1.0
        self.lbl_zoom.configure(text="Fit Lebar")
        self._render_page()

    def _on_canvas_wheel(self, event: Any) -> None:
        if event.state & 0x0004:
            self._on_canvas_ctrl_wheel(event)
        else:
            self.preview_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_canvas_ctrl_wheel(self, event: Any) -> None:
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _render_page(self) -> None:
        if not self.selected_item:
            return

        # 1. Resolve plan directory & plan path (supports single & multi-folder sessions)
        plan_path_str = self.selected_item.get("_plan_path")
        if plan_path_str and Path(plan_path_str).exists():
            plan_path = Path(plan_path_str)
            plan_dir = plan_path.parent
        elif (self.run_dir / "plan.json").exists():
            plan_path = self.run_dir / "plan.json"
            plan_dir = self.run_dir
        else:
            found_plans = list(self.run_dir.rglob("plan.json"))
            if found_plans:
                plan_path = found_plans[0]
                plan_dir = plan_path.parent
            else:
                plan_path = None
                plan_dir = self.run_dir

        try:
            # 2. Determine target PDF to render
            pdf_path = None
            file_name = self.selected_item.get("review_file_name") or self.selected_item.get("file_name") or ""
            if file_name and (plan_dir / "pdf" / file_name).exists():
                pdf_path = plan_dir / "pdf" / file_name
            elif file_name and (plan_dir / file_name).exists():
                pdf_path = plan_dir / file_name

            if not pdf_path or not pdf_path.exists():
                src_str = self.selected_item.get("src_pdf")
                if src_str and Path(src_str).exists():
                    pdf_path = Path(src_str)

            if not pdf_path or not pdf_path.exists():
                if plan_path and plan_path.exists():
                    try:
                        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                        cand_input = Path(plan_data.get("input_pdf", ""))
                        if cand_input.exists():
                            pdf_path = cand_input
                    except Exception:
                        pass

            if not pdf_path or not pdf_path.exists():
                self.preview_canvas.delete("all")
                self.preview_canvas.create_text(250, 150, text="Berkas PDF tidak ditemukan pada disk.", fill="#DC2626", font=(UI_FONT, 10))
                return

            pages = self.selected_item.get("pages", [self.current_preview_page])
            if not pages:
                pages = [self.current_preview_page]

            doc_test = pymupdf.open(pdf_path)
            total_doc_pages = len(doc_test)
            doc_test.close()

            render_page_idx = self.current_preview_page
            if pdf_path.parent.name == "pdf":
                if self.current_preview_page in pages:
                    rel_p = pages.index(self.current_preview_page) + 1
                    render_page_idx = min(max(1, rel_p), total_doc_pages)
                else:
                    render_page_idx = min(max(1, self.current_preview_page), total_doc_pages)
            else:
                render_page_idx = min(max(1, self.current_preview_page), total_doc_pages)

            self.lbl_page_num.configure(text=f"Hal {self.current_preview_page} (dari {pages[0]}-{pages[-1]})")

            self.preview_canvas.update_idletasks()
            canvas_w = self.preview_canvas.winfo_width()
            target_w = max(450, canvas_w - 30 if canvas_w > 100 else 750)
            zoom_val = 1.0 if self.fit_width_mode else self.zoom_level
            png_bytes = render_page_image(pdf_path, render_page_idx, max_width=target_w, zoom=zoom_val)

            if png_bytes:
                self.current_photo = tk.PhotoImage(data=png_bytes)
                self.preview_canvas.delete("all")
                img_w = self.current_photo.width()
                img_h = self.current_photo.height()
                x_pos = max(img_w // 2, canvas_w // 2) if canvas_w > img_w else img_w // 2 + 10
                y_pos = img_h // 2 + 10
                self.canvas_img_id = self.preview_canvas.create_image(x_pos, y_pos, image=self.current_photo)
                self.preview_canvas.configure(scrollregion=(0, 0, max(canvas_w, img_w + 30), img_h + 30))
            else:
                self.preview_canvas.delete("all")
                self.preview_canvas.create_text(250, 150, text="Gagal memuat pratinjau halaman.", fill="#64748B", font=(UI_FONT, 10))
        except Exception as e:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(250, 150, text=f"Error preview: {e}", fill="#DC2626", font=(UI_FONT, 10))

    def _prev_page(self) -> None:
        if not self.selected_item:
            return
        pages = self.selected_item.get("pages", [])
        if self.current_preview_page > pages[0]:
            self.current_preview_page -= 1
            self._render_page()

    def _next_page(self) -> None:
        if not self.selected_item:
            return
        pages = self.selected_item.get("pages", [])
        if self.current_preview_page < pages[-1]:
            self.current_preview_page += 1
            self._render_page()

    def _open_pdf(self) -> None:
        if not self.selected_item:
            return
        plan_path_str = self.selected_item.get("_plan_path")
        plan_dir = Path(plan_path_str).parent if plan_path_str else self.run_dir
        file_name = self.selected_item.get("review_file_name") or self.selected_item.get("file_name") or ""
        pdf_path = plan_dir / "pdf" / file_name
        if not pdf_path.exists():
            if (plan_dir / file_name).exists():
                pdf_path = plan_dir / file_name
        if not pdf_path.exists():
            src_str = self.selected_item.get("src_pdf")
            if src_str and Path(src_str).exists():
                pdf_path = Path(src_str)
        if pdf_path.exists():
            try:
                if sys.platform == "win32":
                    os.startfile(pdf_path)
                else:
                    subprocess.run(["xdg-open", str(pdf_path)])
            except Exception as e:
                messagebox.showerror("Error", f"Gagal membuka PDF: {e}")

    def _approve_item(self) -> None:
        if not self.selected_item:
            return

        nid = self.nid_var.get().strip()
        nama = self.nama_var.get().strip()
        unit = self.unit_var.get().strip()
        judul = self.title_var.get().strip()
        tahun = self.year_var.get().strip()

        if not nama:
            messagebox.showwarning("Peringatan", "Nama Pegawai harus diisi untuk menyetujui.")
            return

        sel = self.tree.selection()
        curr_idx = int(sel[0]) if sel else 0

        target_run_dir = Path(self.selected_item["_plan_dir"]) if self.selected_item.get("_plan_dir") else self.run_dir
        plan_path = Path(self.selected_item["_plan_path"]) if self.selected_item.get("_plan_path") else None

        try:
            file_name = approve_group(
                run_dir=target_run_dir,
                group_id=self.selected_item["id"],
                nid=nid,
                nama=nama,
                unit=unit,
                custom_title=judul,
                custom_year=tahun,
                plan_path=plan_path,
            )
            self._load_data(select_idx=curr_idx)
            self.parent_app._update_review_badge()
            remaining = len(self.review_items)
            rem_str = f"Tersisa {remaining} item." if remaining > 0 else "Semua sertifikat selesai disetujui!"
            self.lbl_status_msg.configure(
                text=f"✓ Disimpan: {file_name} ({rem_str})",
                fg="#059669",
            )
        except Exception as e:
            messagebox.showerror("Gagal Menyetujui", f"Error: {e}")

    def _approve_all_items(self) -> None:
        if not self.review_items:
            return
        n_items = len(self.review_items)
        if not messagebox.askyesno(
            "Konfirmasi Setujui Semua",
            f"Apakah Anda yakin ingin menyetujui sekaligus seluruh {n_items} item review ini menggunakan data prediksi teratas masing-masing?",
        ):
            return

        approved_count = 0
        errors = []
        for grp in list(self.review_items):
            cands = grp.get("candidates", [])
            if cands:
                top_c = cands[0]
                nid = top_c.get("nid", "")
                nama = top_c.get("nama", "")
                unit = top_c.get("unit", "")
            else:
                raw_name = grp.get("raw_name") or grp.get("nama", "")
                nid = ""
                nama = raw_name
                unit = ""

            fields = grp.get("fields", {})
            judul = fields.get("title", {}).get("value") or "Tanpa Judul"
            iss_date = fields.get("issue_date", {}).get("value")
            tahun = iss_date.split("-")[0] if iss_date else "0000"

            target_run_dir = Path(grp["_plan_dir"]) if grp.get("_plan_dir") else self.run_dir
            plan_path = Path(grp["_plan_path"]) if grp.get("_plan_path") else None

            try:
                approve_group(
                    run_dir=target_run_dir,
                    group_id=grp["id"],
                    nid=nid,
                    nama=nama,
                    unit=unit,
                    custom_title=judul,
                    custom_year=tahun,
                    plan_path=plan_path,
                )
                approved_count += 1
            except Exception as e:
                errors.append(f"Item #{grp.get('id')}: {e}")

        self._load_data()
        self.parent_app._update_review_badge()

        if errors:
            messagebox.showwarning(
                "Selesai dengan Catatan",
                f"{approved_count} dari {n_items} item berhasil disetujui.\nError pada {len(errors)} item:\n" + "\n".join(errors[:5]),
            )
        else:
            self.lbl_status_msg.configure(
                text=f"✓ Seluruh {approved_count} sertifikat berhasil disetujui!",
                fg="#059669",
            )
            messagebox.showinfo(
                "Selesai!",
                f"✓ Seluruh {approved_count} sertifikat berhasil disetujui dan disimpan ke folder pdf/ serta file Excel!",
            )

    def _approve_selected_items(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Pilih satu atau beberapa item di daftar sebelah kiri terlebih dahulu.")
            return

        selected_indices = [int(s) for s in sel]
        items_to_approve = [self.review_items[i] for i in selected_indices if i < len(self.review_items)]
        if not items_to_approve:
            return

        n_items = len(items_to_approve)
        if n_items == 1:
            self._approve_item()
            return

        if not messagebox.askyesno(
            "Konfirmasi Setujui Pilihan",
            f"Setujui {n_items} item terpilih menggunakan data prediksi teratas masing-masing?",
        ):
            return

        approved_count = 0
        for grp in items_to_approve:
            cands = grp.get("candidates", [])
            if cands:
                top_c = cands[0]
                nid = top_c.get("nid", "")
                nama = top_c.get("nama", "")
                unit = top_c.get("unit", "")
            else:
                raw_name = grp.get("raw_name") or grp.get("nama", "")
                nid = ""
                nama = raw_name
                unit = ""

            fields = grp.get("fields", {})
            judul = fields.get("title", {}).get("value") or "Tanpa Judul"
            iss_date = fields.get("issue_date", {}).get("value")
            tahun = iss_date.split("-")[0] if iss_date else "0000"

            target_run_dir = Path(grp["_plan_dir"]) if grp.get("_plan_dir") else self.run_dir
            plan_path = Path(grp["_plan_path"]) if grp.get("_plan_path") else None

            try:
                approve_group(
                    run_dir=target_run_dir,
                    group_id=grp["id"],
                    nid=nid,
                    nama=nama,
                    unit=unit,
                    custom_title=judul,
                    custom_year=tahun,
                    plan_path=plan_path,
                )
                approved_count += 1
            except Exception:
                pass

        curr_first = min(selected_indices) if selected_indices else 0
        self._load_data(select_idx=curr_first)
        self.parent_app._update_review_badge()
        self.lbl_status_msg.configure(
            text=f"✓ {approved_count} item terpilih berhasil disetujui!",
            fg="#059669",
        )


# =========================================================================
# MAIN APPLICATION (2 TABS: CERTIFICATE MANAGEMENT & PENGATURAN)
# =========================================================================
class CertSliceApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        optimize_process_priority()
        self.title("Certificate Manager - Solusi Pemotong, Penamaan & Manajemen Sertifikat Otomatis")
        self.geometry("1020x750")
        self.minsize(880, 680)

        # Settings
        self.settings = load_user_settings()
        self.last_run_dir: Path | None = None
        self.selected_root_folder: Path | None = None
        self.selected_pdf_folders: list[tuple[Path, list[Path]]] = []
        self.selected_pdf_files: list[Path] = []
        self.input_display_var = tk.StringVar(value="Belum ada file/folder PDF dipilih")
        configure_global_fonts(self)

        self._configure_styles()
        self._build_ui()
        self._setup_logging()

    def _configure_styles(self) -> None:
        self.configure(bg="#F8FAFC")
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        font_family = UI_FONT
        self.style.configure(".", font=(font_family, 9), background="#F8FAFC", foreground="#0F172A")

        # Tabs: 2 Tabs only
        self.style.configure("TNotebook", background="#F8FAFC", borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            font=(font_family, 10, "bold"),
            padding=(22, 10),
            background="#E2E8F0",
            foreground="#475569",
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", "#FFFFFF"), ("active", "#CBD5E1")],
            foreground=[("selected", "#2563EB"), ("active", "#1E293B")],
        )

        # Cards
        self.style.configure("Card.TFrame", background="#FFFFFF")
        self.style.configure("Card.TLabelframe", background="#FFFFFF", relief="solid", borderwidth=1, bordercolor="#E2E8F0")
        self.style.configure("Card.TLabelframe.Label", font=(font_family, 10, "bold"), background="#FFFFFF", foreground="#1E293B")

        # Buttons
        self.style.configure("Primary.TButton", font=(font_family, 9, "bold"), background="#2563EB", foreground="#FFFFFF")
        self.style.map(
            "Primary.TButton",
            background=[("active", "#1D4ED8"), ("disabled", "#94A3B8")],
            foreground=[("disabled", "#F8FAFC")],
        )
        self.style.configure("Success.TButton", font=(font_family, 9, "bold"), background="#059669", foreground="#FFFFFF")
        self.style.map(
            "Success.TButton",
            background=[("active", "#047857"), ("disabled", "#94A3B8")],
            foreground=[("disabled", "#F8FAFC")],
        )
        self.style.configure("Warning.TButton", font=(font_family, 9, "bold"), background="#D97706", foreground="#FFFFFF")
        self.style.map(
            "Warning.TButton",
            background=[("active", "#B45309"), ("disabled", "#94A3B8")],
            foreground=[("disabled", "#F8FAFC")],
        )
        self.style.configure("Secondary.TButton", font=(font_family, 9), background="#F1F5F9", foreground="#1E293B")
        self.style.map(
            "Secondary.TButton",
            background=[("active", "#E2E8F0"), ("disabled", "#CBD5E1")],
        )

    def _build_ui(self) -> None:
        # 1. Top Header Banner
        header = tk.Frame(self, bg="#FFFFFF", relief="solid", bd=1)
        header.pack(fill="x", padx=16, pady=(12, 6), ipady=8)

        header_right = tk.Frame(header, bg="#FFFFFF")
        header_right.pack(side="right", padx=16)
        ttk.Button(
            header_right,
            text="🌐 Buka Web Studio (:8500)",
            style="Secondary.TButton",
            command=self._launch_web_studio,
        ).pack(side="right", pady=4)

        title_lbl = tk.Label(header, text="Certificate Manager", font=(UI_FONT, 15, "bold"), fg="#1E40AF", bg="#FFFFFF")
        title_lbl.pack(anchor="w", padx=16, pady=(2, 0))

        sub_lbl = tk.Label(
            header,
            text="Sistem Pemrosesan, Penamaan Otomatis & Rekapitulasi Sertifikat Terintegrasi Database Pegawai",
            font=(UI_FONT, 9),
            fg="#64748B",
            bg="#FFFFFF",
        )
        sub_lbl.pack(anchor="w", padx=16, pady=(0, 2))

        # 2. Main Tabbed Notebook: EXACTLY TWO TABS
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(6, 12))

        # Tab 1: Certificate Management
        self.tab_mgmt = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_mgmt, text=" 📁 Certificate Management ")

        # Tab 2: Pengaturan
        self.tab_settings = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_settings, text=" ⚙️ Pengaturan ")

        self._build_management_tab()
        self._build_settings_tab()

    def _launch_web_studio(self) -> None:
        """Start the local Web Studio server on port 8500 in a background daemon thread and open browser."""
        import socket

        port = 8500
        url = f"http://127.0.0.1:{port}"
        already_up = False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                already_up = True
        except OSError:
            pass

        if not already_up and not getattr(self, "_web_studio_started", False):
            self._web_studio_started = True
            from certslice.web import launch_web

            threading.Thread(
                target=lambda: launch_web(host="127.0.0.1", port=port, open_browser=False),
                daemon=True,
            ).start()

        def _open_when_ready() -> None:
            for _ in range(15):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.15)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open_when_ready, daemon=True).start()


    # =========================================================================
    # TAB 1: CERTIFICATE MANAGEMENT
    # =========================================================================
    def _build_management_tab(self) -> None:
        top_container = ttk.Frame(self.tab_mgmt)
        top_container.pack(fill="x", expand=False)

        # Card 1: Dokumen Input & Database Referensi Excel
        card_input = ttk.LabelFrame(top_container, text=" 1. Dokumen Input & Database Referensi Excel ", padding=12, style="Card.TLabelframe")
        card_input.pack(fill="x", pady=(0, 8))

        # Row 0: Input PDF / Folder
        ttk.Label(card_input, text="File / Folder PDF Sertifikat:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(card_input, textvariable=self.input_display_var, width=48, state="readonly").grid(row=0, column=1, sticky="ew", padx=8, pady=3)

        btn_box = ttk.Frame(card_input, style="Card.TFrame")
        btn_box.grid(row=0, column=2, sticky="e", pady=3)
        ttk.Button(btn_box, text="📄 Pilih File PDF...", style="Secondary.TButton", command=self._browse_files).pack(side="left", padx=(0, 4))
        ttk.Button(btn_box, text="📁 Pilih Folder...", style="Secondary.TButton", command=self._browse_dir).pack(side="left")

        # Row 1: Database Excel Pegawai
        ttk.Label(card_input, text="1. Database Master SDM (Pegawai):", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=1, column=0, sticky="w", pady=3)
        default_db = get_default_database_path()
        self.db_var = tk.StringVar(value=default_db)
        ttk.Entry(card_input, textvariable=self.db_var, width=48).grid(row=1, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(card_input, text="Pilih Excel...", style="Secondary.TButton", command=self._browse_db).grid(row=1, column=2, sticky="ew", pady=3)

        # Row 2: Komparasi Kode Sertifikasi NPS
        ttk.Label(card_input, text="2. Komparasi Kode Sertifikasi NPS:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=2, column=0, sticky="w", pady=3)
        default_komparasi = get_default_komparasi_path()
        self.komparasi_var = tk.StringVar(value=default_komparasi)
        ttk.Entry(card_input, textvariable=self.komparasi_var, width=48).grid(row=2, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(card_input, text="Pilih Excel...", style="Secondary.TButton", command=self._browse_komparasi).grid(row=2, column=2, sticky="ew", pady=3)

        # Row 3: Template MSO765 (For NPS 0726)
        ttk.Label(card_input, text="3. Template MSO765 (For NPS 0726):", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=3, column=0, sticky="w", pady=3)
        default_mso = get_default_mso_path()
        self.mso_var = tk.StringVar(value=default_mso)
        ttk.Entry(card_input, textvariable=self.mso_var, width=48).grid(row=3, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(card_input, text="Pilih Excel...", style="Secondary.TButton", command=self._browse_mso).grid(row=3, column=2, sticky="ew", pady=3)

        # Row 4: Output folder (Pre-filled by default!)
        default_out = get_default_output_dir()
        ttk.Label(card_input, text="Folder Penyimpanan Hasil:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=4, column=0, sticky="w", pady=3)
        self.out_var = tk.StringVar(value=default_out)
        ttk.Entry(card_input, textvariable=self.out_var, width=48).grid(row=4, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(card_input, text="Pilih Folder...", style="Secondary.TButton", command=self._browse_out).grid(row=4, column=2, sticky="ew", pady=3)

        # Row 5: Detection / Hint label
        self.lbl_folder_hint = ttk.Label(card_input, text="✓ 3 File Excel Referensi Terhubung Serentak (Pegawai, Komparasi Kode, & MSO765)", font=(UI_FONT, 8, "italic"), foreground="#0369A1", background="#FFFFFF")
        self.lbl_folder_hint.grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=(2, 0))

        card_input.columnconfigure(1, weight=1)

        # Card 2: Pengaturan Pemrosesan Otomatis
        card_opts = ttk.LabelFrame(top_container, text=" 2. Opsi Pemrosesan Otomatis ", padding=12, style="Card.TLabelframe")
        card_opts.pack(fill="x", pady=(0, 8))

        # Format notice
        row_fmt = ttk.Frame(card_opts, style="Card.TFrame")
        row_fmt.pack(fill="x", pady=(0, 6))
        ttk.Label(
            row_fmt,
            text="💡 Format Otomatis: SKP ➔ {NID}_{Nama}_{Jabatan Level}_{Peran}.pdf  |  Sertifikat Standar ➔ {NID}_{Nama}_{Judul}.pdf",
            font=(UI_FONT, 9, "bold"),
            foreground="#1D4ED8",
            background="#EFF6FF",
            padding=(8, 4),
        ).pack(fill="x")

        # AI Mode Checkbox
        row_ai = ttk.Frame(card_opts, style="Card.TFrame")
        row_ai.pack(fill="x", pady=2)
        self.ai_var = tk.BooleanVar(value=bool(self.settings.get("use_ai", True)))
        ttk.Checkbutton(row_ai, text="Aktifkan Mode AI (Gemini Flash-Lite Cerdas & Hemat Kuota)", variable=self.ai_var).pack(side="left")
        ttk.Label(row_ai, text="— Menyelamatkan nama singkatan, terpotong, atau typo dengan AI multimodal", font=(UI_FONT, 8), foreground="#64748B", background="#FFFFFF").pack(side="left", padx=8)

        # OCR Checkbox
        self.row_ocr = ttk.Frame(card_opts, style="Card.TFrame")
        self.row_ocr.pack(fill="x", pady=(2, 2))
        self.ocr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.row_ocr, text="Gunakan Pembaca Teks Gambar (OCR Otomatis)", variable=self.ocr_var).pack(side="left")
        ttk.Label(self.row_ocr, text="— Membaca sertifikat scan foto/gambar secara akurat (Disarankan selalu aktif)", font=(UI_FONT, 8), foreground="#64748B", background="#FFFFFF").pack(side="left", padx=8)

        # Action Buttons Bar
        act_bar = ttk.Frame(top_container)
        act_bar.pack(fill="x", pady=4)

        self.btn_run = ttk.Button(
            act_bar,
            text="⚡ Mulai Proses Otomatis (Potong & Rename)",
            style="Primary.TButton",
            command=self._start_processing,
        )
        self.btn_run.pack(side="left", padx=(0, 6), ipady=6, ipadx=6)

        self.btn_ftp_rekap = ttk.Button(
            act_bar,
            text="📊 Rekap Excel PDF Terformat (Mode FTP / Dashboard)",
            style="Success.TButton",
            command=self._start_ftp_rekap,
        )
        self.btn_ftp_rekap.pack(side="left", padx=(0, 6), ipady=6, ipadx=6)

        self.btn_open_out = ttk.Button(
            act_bar,
            text="📂 Buka Folder Hasil",
            style="Secondary.TButton",
            state="disabled",
            command=self._open_output_dir,
        )
        self.btn_open_out.pack(side="left", padx=4, ipady=4)

        self.btn_review = ttk.Button(
            act_bar,
            text="🔍 Review & Koreksi",
            style="Warning.TButton",
            state="disabled",
            command=self._open_review_dialog,
        )
        self.btn_review.pack(side="left", padx=4, ipady=4)

        self.progress_bar = ttk.Progressbar(act_bar, mode="indeterminate", length=180)

        # Card 3: Activity Log Viewer
        card_log = ttk.LabelFrame(self.tab_mgmt, text=" Log Aktivitas Pemrosesan ", padding=8, style="Card.TLabelframe")
        card_log.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk.Text(
            card_log,
            wrap="word",
            state="disabled",
            bg="#0F172A",
            fg="#F8FAFC",
            font=(MONO_FONT, 9),
            relief="flat",
            padx=8,
            pady=8,
        )
        scroll = ttk.Scrollbar(card_log, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    # =========================================================================
    # TAB 2: PENGATURAN
    # =========================================================================
    def _build_settings_tab(self) -> None:
        container = ttk.Frame(self.tab_settings)
        container.pack(fill="both", expand=True)

        # Gemini API Key Card
        card_api = ttk.LabelFrame(container, text=" Integrasi Google Gemini API ", padding=14, style="Card.TLabelframe")
        card_api.pack(fill="x", pady=6)

        key_row = ttk.Frame(card_api, style="Card.TFrame")
        key_row.pack(fill="x", pady=4)
        ttk.Label(key_row, text="Google Gemini API Key:", font=(UI_FONT, 9, "bold"), background="#FFFFFF", width=22).pack(side="left")

        current_key = self.settings.get("gemini_api_key") or DEFAULT_GEMINI_API_KEY
        self.api_key_var = tk.StringVar(value=current_key)
        self.key_entry = ttk.Entry(key_row, textvariable=self.api_key_var, show="*", width=42)
        self.key_entry.pack(side="left", padx=6)

        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_row, text="Lihat Key", variable=self.show_key_var, command=self._toggle_show_key).pack(side="left", padx=4)
        ttk.Button(key_row, text="Simpan Key", style="Primary.TButton", command=self._save_settings).pack(side="left", padx=8)

        # Button: Get your own API key
        btn_get_key = ttk.Button(
            card_api,
            text="🔑 Dapatkan API Key Anda (Google AI Studio)",
            style="Secondary.TButton",
            command=lambda: webbrowser.open("https://aistudio.google.com/api-keys"),
        )
        btn_get_key.pack(anchor="w", padx=4, pady=(6, 8))

        # Model row (Gemini Flash-Lite Latest only)
        model_row = ttk.Frame(card_api, style="Card.TFrame")
        model_row.pack(fill="x", pady=4)
        ttk.Label(model_row, text="Pilihan Model Gemini:", font=(UI_FONT, 9, "bold"), background="#FFFFFF", width=22).pack(side="left")
        self.model_var = tk.StringVar(value="gemini-flash-lite-latest")
        ttk.Label(
            model_row,
            text="gemini-flash-lite-latest (Gemini Flash-Lite Terbaru - Cepat & Hemat Kuota)",
            font=(UI_FONT, 9, "bold"),
            foreground="#047857",
            background="#FFFFFF",
        ).pack(side="left", padx=6)

        # Default paths configuration
        card_paths = ttk.LabelFrame(container, text=" Jalur Berkas Referensi & Output Default ", padding=14, style="Card.TLabelframe")
        card_paths.pack(fill="x", pady=6)

        ttk.Label(card_paths, text="1. Database SDM Pegawai:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(card_paths, textvariable=self.db_var, width=55).grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(card_paths, text="Pilih...", style="Secondary.TButton", command=self._browse_db).grid(row=0, column=2, pady=4)

        ttk.Label(card_paths, text="2. Komparasi Kode Resource:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(card_paths, textvariable=self.komparasi_var, width=55).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(card_paths, text="Pilih...", style="Secondary.TButton", command=self._browse_komparasi).grid(row=1, column=2, pady=4)

        ttk.Label(card_paths, text="3. Template MSO765 NPS:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(card_paths, textvariable=self.mso_var, width=55).grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(card_paths, text="Pilih...", style="Secondary.TButton", command=self._browse_mso).grid(row=2, column=2, pady=4)

        ttk.Label(card_paths, text="4. Folder Output Default:", font=(UI_FONT, 9, "bold"), background="#FFFFFF").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(card_paths, textvariable=self.out_var, width=55).grid(row=3, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(card_paths, text="Pilih...", style="Secondary.TButton", command=self._browse_out).grid(row=3, column=2, pady=4)
        card_paths.columnconfigure(1, weight=1)

        ttk.Button(card_paths, text="Simpan Pengaturan Jalur Berkas", style="Primary.TButton", command=self._save_settings).grid(row=4, column=1, sticky="e", pady=6)

        # About / System Info Card
        card_about = ttk.LabelFrame(container, text=" Informasi Aplikasi & Sistem ", padding=14, style="Card.TLabelframe")
        card_about.pack(fill="x", pady=6)

        info_app_text = (
            "Certificate Manager v2.0 Enterprise • PLN Nusantara Power Services\n\n"
            "• Pemrosesan Multi-Folder: Mampu memproses struktur subfolder bersarang dan menyusun hasil terstruktur.\n"
            "• Mode FTP / Dashboard Sync: Merekap instan ribuan berkas PDF terformat ke data.xlsx per subfolder dan Master Excel.\n"
            "• Optimalisasi Laptop Low-End: Prioritas proses rendah & pembatasan thread mencegah laptop hang/freeze."
        )
        ttk.Label(card_about, text=info_app_text, justify="left", wraplength=760, background="#FFFFFF", foreground="#334155").pack(anchor="w")

    def _setup_logging(self) -> None:
        handler = TextHandler(self.log_text)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

        cert_logger = logging.getLogger("certslice")
        cert_logger.setLevel(logging.INFO)
        cert_logger.addHandler(handler)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

    def _toggle_show_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key_var.get() else "*")

    def _save_settings(self) -> None:
        key = self.api_key_var.get().strip() or DEFAULT_GEMINI_API_KEY
        db_path = self.db_var.get().strip()
        komparasi_path = self.komparasi_var.get().strip()
        mso_path = self.mso_var.get().strip()
        out_path = self.out_var.get().strip()
        save_user_settings(
            gemini_api_key=key,
            gemini_model="gemini-flash-lite-latest",
            use_ai=self.ai_var.get(),
            default_db_path=db_path,
            default_komparasi_path=komparasi_path,
            default_mso_path=mso_path,
            default_out_path=out_path,
        )
        messagebox.showinfo("Tersimpan", "Pengaturan berhasil disimpan dengan aman di profil komputer Anda.")

    def _browse_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="Pilih File PDF Sertifikat atau Bundel",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if files:
            self.selected_root_folder = None
            self.selected_pdf_folders = []
            self.selected_pdf_files = [Path(f) for f in files]
            if len(self.selected_pdf_files) == 1:
                p = self.selected_pdf_files[0]
                self.input_display_var.set(f"📄 1 File: {p.name}")
            else:
                self.input_display_var.set(f"📁 {len(self.selected_pdf_files)} file PDF dipilih")
            self.lbl_folder_hint.configure(text=f"✓ Siap memproses {len(self.selected_pdf_files)} file PDF terpilih.")

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(title="Pilih Folder Berisi File PDF Sertifikat")
        if d:
            folder = Path(d)
            pdf_folders = find_all_pdf_folders(folder)
            all_pdfs = [p for _, pdfs in pdf_folders for p in pdfs]
            if not all_pdfs:
                messagebox.showwarning("Peringatan", f"Tidak ditemukan file PDF di folder maupun subfolder:\n{d}")
                return

            self.selected_root_folder = folder
            self.selected_pdf_folders = pdf_folders
            self.selected_pdf_files = all_pdfs

            n_folders = len(pdf_folders)
            if n_folders > 1:
                self.input_display_var.set(f"📁 Folder Multi-Kategori: {folder.name} ({n_folders} subfolder, {len(all_pdfs)} file PDF)")
                self.lbl_folder_hint.configure(text=f"✓ Ditemukan {n_folders} subfolder berkas PDF. Sistem akan memproses dan menyusun folder hasil secara terstruktur.")
            else:
                self.input_display_var.set(f"📁 Folder: {folder.name} ({len(all_pdfs)} file PDF)")
                self.lbl_folder_hint.configure(text=f"✓ Ditemukan {len(all_pdfs)} file PDF di dalam folder.")

            formatted_count = sum(1 for p in all_pdfs if re.match(r"^\d{6,12}_", p.stem) or p.stem.startswith(("SKP-", "LISENSI-")))
            if formatted_count >= max(2, int(len(all_pdfs) * 0.25)):
                self.lbl_folder_hint.configure(
                    text=f"💡 Terdeteksi PDF sudah berformat PLN ({formatted_count}/{len(all_pdfs)}). Anda dapat menggunakan tombol 'Rekap Excel PDF Terformat' untuk rekap data kilat!"
                )

    def _browse_db(self) -> None:
        f = filedialog.askopenfilename(
            title="Pilih File Database Master Excel (Pegawai)",
            filetypes=[("Excel Files", "*.xlsx *.xls"), ("All Files", "*.*")],
        )
        if f:
            self.db_var.set(f)

    def _browse_komparasi(self) -> None:
        f = filedialog.askopenfilename(
            title="Pilih File Komparasi Kode Baru Sertifikasi NPS (.xlsx)",
            filetypes=[("Excel Files", "*.xlsx *.xls"), ("All Files", "*.*")],
        )
        if f:
            self.komparasi_var.set(f)
            try:
                get_resource_code_catalog(f)
            except Exception as e:
                logger = logging.getLogger("certslice")
                logger.warning("Gagal memuat katalog komparasi: %s", e)

    def _browse_mso(self) -> None:
        f = filedialog.askopenfilename(
            title="Pilih File Template MSO765 / For NPS 0726 (.xlsx)",
            filetypes=[("Excel Files", "*.xlsx *.xls"), ("All Files", "*.*")],
        )
        if f:
            self.mso_var.set(f)

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Pilih Folder Penyimpanan Hasil")
        if d:
            self.out_var.set(d)

    def _open_output_dir(self) -> None:
        target = self.last_run_dir or Path(self.out_var.get())
        if target.exists():
            if sys.platform == "win32":
                os.startfile(target)
            else:
                subprocess.run(["xdg-open", str(target)])

    def _open_review_dialog(self) -> None:
        if not self.last_run_dir or not self.last_run_dir.exists():
            messagebox.showinfo("Info", "Belum ada sesi pemrosesan yang dijalankan.")
            return
        ReviewDialog(self, self.last_run_dir)

    def _update_review_badge(self) -> None:
        if not self.last_run_dir:
            self.btn_review.configure(state="disabled")
            return
        items = get_review_items(self.last_run_dir)
        count = len(items)
        if count > 0:
            self.btn_review.configure(state="normal", text=f"🔍 Review & Koreksi ({count} item)")
        else:
            self.btn_review.configure(state="normal", text="✓ Review Selesai (Semua Cocok)")

    def _set_running_state(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.btn_run.configure(state=state)
        self.btn_ftp_rekap.configure(state=state)
        if running:
            self.btn_open_out.configure(state="disabled")
            self.btn_review.configure(state="disabled")
            self.progress_bar.pack(side="left", padx=10)
            self.progress_bar.start(10)
        else:
            self.progress_bar.stop()
            self.progress_bar.pack_forget()
            if self.last_run_dir and self.last_run_dir.exists():
                self.btn_open_out.configure(state="normal")

    # =========================================================================
    # BACKGROUND WORKERS (UNIFIED BUNDLE SLICER, MULTI-FOLDER & FTP SYNC)
    # =========================================================================
    def _start_processing(self) -> None:
        try:
            db_str = self.db_var.get().strip()
            komp_str = self.komparasi_var.get().strip()
            mso_str = self.mso_var.get().strip()
            out_str = self.out_var.get().strip()

            if not self.selected_pdf_files:
                messagebox.showwarning("Peringatan", "Silakan pilih file PDF (satu bundel / banyak file) atau 1 folder terlebih dahulu.")
                return

            if not db_str:
                messagebox.showwarning("Peringatan", "Silakan pilih file database master Excel (Data Master SDM) terlebih dahulu.")
                return

            db_file = Path(db_str)
            komparasi_file = Path(komp_str) if komp_str and Path(komp_str).exists() else None
            mso_file = Path(mso_str) if mso_str and Path(mso_str).exists() else None
            out_dir = Path(out_str)

            if not db_file.exists() or not db_file.is_file():
                messagebox.showerror("Error", f"File database Excel tidak ditemukan:\n{db_str}")
                return

            # Ensure catalog is loaded with chosen komparasi file
            if komparasi_file:
                get_resource_code_catalog(komparasi_file)

            # Save settings
            save_user_settings(
                gemini_api_key=self.api_key_var.get().strip() or DEFAULT_GEMINI_API_KEY,
                gemini_model="gemini-flash-lite-latest",
                use_ai=self.ai_var.get(),
                default_db_path=str(db_file),
                default_komparasi_path=str(komparasi_file) if komparasi_file else "",
                default_mso_path=str(mso_file) if mso_file else "",
                default_out_path=str(out_dir),
            )

            self._set_running_state(True)

            # Check if multi-folder tree
            if self.selected_root_folder and len(self.selected_pdf_folders) > 1:
                threading.Thread(
                    target=self._run_multi_folder_thread,
                    args=(self.selected_root_folder, db_file, out_dir, komparasi_file, mso_file),
                    daemon=True,
                ).start()
            else:
                if len(self.selected_pdf_files) == 1:
                    run_name = make_human_friendly_run_name(self.selected_pdf_files[0])
                else:
                    folder_name = self.selected_root_folder.name if self.selected_root_folder else "Banyak_Item"
                    run_name = f"Hasil_{clean_field(folder_name)}_{datetime.now().strftime('%d-%b-%Y_%H.%M')}"

                threading.Thread(
                    target=self._run_unified_worker,
                    args=(self.selected_pdf_files, db_file, out_dir, run_name, komparasi_file, mso_file),
                    daemon=True,
                ).start()
        except Exception as e:
            self._set_running_state(False)
            messagebox.showerror("Error", f"Gagal memulai proses:\n{e}")

    def _run_unified_worker(
        self,
        pdf_files: list[Path],
        db_file: Path,
        out_dir: Path,
        run_name: str,
        komparasi_file: Path | None = None,
        mso_file: Path | None = None,
    ) -> None:
        use_ai = self.ai_var.get()
        use_ocr = self.ocr_var.get()
        api_key = self.api_key_var.get().strip() or DEFAULT_GEMINI_API_KEY
        model = "gemini-flash-lite-latest"

        logger = logging.getLogger("certslice")
        logger.info("==================================================")
        logger.info("Memulai Proses Otomatis: %d berkas PDF", len(pdf_files))
        logger.info("1. Database Master SDM : %s", db_file.name)
        logger.info("2. Komparasi Kode NPS  : %s", komparasi_file.name if komparasi_file else "(Katalog Standar)")
        logger.info("3. Template MSO765 NPS : %s", mso_file.name if mso_file else "(Struktur Standar)")
        logger.info("Folder Output: %s", run_name)
        logger.info("Mode AI: %s | Model: %s", use_ai, model)
        logger.info("==================================================")

        def cb(curr: int, tot: int, name: str):
            logger.info("Progress (%d/%d): %s", curr, tot, name)

        res = None
        error_msg = None
        try:
            res = process_bulk_files(
                pdf_paths=pdf_files,
                db_path=db_file,
                out_dir=out_dir,
                use_ai=use_ai,
                gemini_api_key=api_key,
                gemini_model=model,
                use_ocr=use_ocr,
                run_name=run_name,
                progress_callback=cb,
                komparasi_path=komparasi_file,
                mso_path=mso_file,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("Terjadi kesalahan: %s", error_msg, exc_info=True)

        self.after(0, self._on_processing_complete, res, error_msg)

    def _run_multi_folder_thread(
        self,
        root_dir: Path,
        db_file: Path,
        out_dir: Path,
        komparasi_file: Path | None = None,
        mso_file: Path | None = None,
    ) -> None:
        use_ai = self.ai_var.get()
        use_ocr = self.ocr_var.get()
        api_key = self.api_key_var.get().strip() or DEFAULT_GEMINI_API_KEY
        model = "gemini-flash-lite-latest"

        logger = logging.getLogger("certslice")
        logger.info("==================================================")
        logger.info("MEMULAI PEMROSESAN BATCH MULTI-FOLDER")
        logger.info("Folder Induk: %s", root_dir)
        logger.info("1. Database Master SDM : %s", db_file.name)
        logger.info("2. Komparasi Kode NPS  : %s", komparasi_file.name if komparasi_file else "(Katalog Standar)")
        logger.info("3. Template MSO765 NPS : %s", mso_file.name if mso_file else "(Struktur Standar)")
        logger.info("Jumlah Subfolder: %d", len(self.selected_pdf_folders))
        logger.info("Total Berkas PDF: %d", len(self.selected_pdf_files))
        logger.info("Mode AI: %s | Model: %s", use_ai, model)
        logger.info("==================================================")

        def cb(curr: int, tot: int, name: str):
            logger.info("Progress (%d/%d): %s", curr, tot, name)

        res = None
        error_msg = None
        try:
            res = process_multi_folder_tree(
                root_dir=root_dir,
                db_path=db_file,
                out_dir=out_dir,
                use_ai=use_ai,
                gemini_api_key=api_key,
                gemini_model=model,
                use_ocr=use_ocr,
                progress_callback=cb,
                komparasi_path=komparasi_file,
                mso_path=mso_file,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("Terjadi kesalahan multi-folder: %s", error_msg, exc_info=True)

        self.after(0, self._on_multi_folder_complete, res, error_msg)

    def _start_ftp_rekap(self) -> None:
        if not self.selected_pdf_files:
            messagebox.showwarning("Peringatan", "Silakan pilih folder berisi berkas PDF terlebih dahulu.")
            return

        target_dir = self.selected_root_folder or self.selected_pdf_files[0].parent
        out_str = self.out_var.get().strip()
        out_dir = Path(out_str) if out_str else None
        db_str = self.db_var.get().strip()
        db_file = Path(db_str) if db_str and Path(db_str).exists() else None
        komp_str = self.komparasi_var.get().strip()
        komparasi_file = Path(komp_str) if komp_str and Path(komp_str).exists() else None
        mso_str = self.mso_var.get().strip()
        mso_file = Path(mso_str) if mso_str and Path(mso_str).exists() else None

        if komparasi_file:
            get_resource_code_catalog(komparasi_file)

        self._set_running_state(True)
        threading.Thread(
            target=self._run_ftp_rekap_thread,
            args=(target_dir, db_file, out_dir, komparasi_file, mso_file),
            daemon=True,
        ).start()

    def _run_ftp_rekap_thread(
        self,
        root_dir: Path,
        db_file: Path | None,
        out_dir: Path | None,
        komparasi_file: Path | None = None,
        mso_file: Path | None = None,
    ) -> None:
        logger = logging.getLogger("certslice")
        logger.info("==================================================")
        logger.info("MEMULAI REKAP EXCEL PDF TERFORMAT (MODE FTP/DASHBOARD)")
        logger.info("Folder Target: %s", root_dir)
        logger.info("1. Database Rujukan SDM: %s", db_file.name if db_file else "Tanpa Database (Ekstraksi Cepat)")
        logger.info("2. Komparasi Kode NPS  : %s", komparasi_file.name if komparasi_file else "(Katalog Standar)")
        logger.info("3. Template MSO765 NPS : %s", mso_file.name if mso_file else "(Struktur Standar)")
        logger.info("==================================================")

        def cb(curr: int, tot: int, name: str):
            logger.info("Progress (%d/%d): %s", curr, tot, name)

        res = None
        error_msg = None
        try:
            res = process_preformatted_ftp_folder(
                root_dir=root_dir,
                db_path=db_file,
                out_dir=out_dir if out_dir != root_dir else None,
                progress_callback=cb,
                komparasi_path=komparasi_file,
                mso_path=mso_file,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("Terjadi kesalahan pada rekap FTP: %s", error_msg, exc_info=True)

        self.after(0, self._on_ftp_rekap_complete, res, error_msg)

    def _on_ftp_rekap_complete(self, res: dict[str, Any] | None, error_msg: str | None) -> None:
        self._set_running_state(False)
        if error_msg:
            messagebox.showerror("Gagal", f"Proses rekap gagal:\n{error_msg}")
            return
        if res:
            self.last_run_dir = Path(res["master_excel"]).parent
            self.btn_open_out.configure(state="normal")
            logger = logging.getLogger("certslice")
            logger.info("==================================================")
            logger.info("REKAP SELESAI! Berhasil merekap %d berkas PDF dari %d subfolder.", res["total_files"], res["total_folders"])
            logger.info("Master Excel: %s", res["master_excel"])
            logger.info("==================================================")
            messagebox.showinfo(
                "Rekap Excel Selesai",
                f"✓ Berhasil membuat file Excel rekapitulasi untuk {res['total_files']} sertifikat dari {res['total_folders']} folder!\n\n"
                f"• data.xlsx telah dibuat di setiap subfolder.\n"
                f"• Master Excel 'rekap_seluruh_sertifikat.xlsx' tersimpan di:\n{res['master_excel']}",
            )

    def _on_multi_folder_complete(self, res: Any, error_msg: str | None) -> None:
        self._set_running_state(False)
        if error_msg:
            messagebox.showerror("Gagal", f"Pemrosesan multi-folder gagal:\n{error_msg}")
            return

        if res:
            self.last_run_dir = res.run_dir
            self.btn_open_out.configure(state="normal")

            logger = logging.getLogger("certslice")
            logger.info("==================================================")
            logger.info("PROSES BATCH MULTI-FOLDER SELESAI!")
            logger.info("Total Berkas: %d | Cocok: %d | Butuh Review: %d", res.total_files, res.accepted_count, res.review_count)
            logger.info("Folder Hasil: %s", res.run_dir)
            logger.info("Master Excel: %s", res.master_excel_path.name)
            logger.info("==================================================")

            self._update_review_badge()

            if res.review_count > 0:
                ans = messagebox.askyesno(
                    "Proses Multi-Folder Selesai (Perlu Review)",
                    f"✓ Seluruh {res.total_folders} subfolder ({res.total_files} file PDF) berhasil diproses!\n\n"
                    f"• Cocok Pasti: {res.accepted_count} sertifikat\n"
                    f"⚠️ Perlu Review: {res.review_count} sertifikat\n\n"
                    f"Sertifikat yang butuh review tersimpan rapi dengan awalan [REVIEW].\n\n"
                    f"Apakah Anda ingin membuka menu Review & Koreksi sekarang untuk memeriksa dan menyetujuinya?",
                )
                if ans:
                    self._open_review_dialog()
            else:
                msg = (
                    f"✓ Seluruh {res.total_folders} subfolder ({res.total_files} file PDF) berhasil diproses!\n\n"
                    f"• Cocok Pasti: {res.accepted_count} sertifikat\n"
                    f"• Perlu Review: 0 sertifikat (100% Cocok)\n\n"
                    f"Master Excel 'rekap_seluruh_sertifikat.xlsx' dan struktur folder hasil tersimpan di:\n{res.run_dir}"
                )
                messagebox.showinfo("Proses Multi-Folder Selesai", msg)

    def _on_processing_complete(self, res: Any, error_msg: str | None) -> None:
        self._set_running_state(False)

        if error_msg:
            messagebox.showerror("Gagal", f"Pemrosesan gagal:\n{error_msg}")
            return

        if res:
            self.last_run_dir = res.run_dir
            self.btn_open_out.configure(state="normal")

            total = res.total_files
            accepted = res.accepted_count
            unmatched = res.review_count

            logger = logging.getLogger("certslice")
            logger.info("==================================================")
            logger.info("PROSES SELESAI!")
            logger.info("Total Sertifikat: %d | Cocok Pasti: %d | Butuh Review: %d", total, accepted, unmatched)
            logger.info("Folder Hasil: %s", res.run_dir)
            logger.info("==================================================")

            self._update_review_badge()

            if unmatched > 0:
                ans = messagebox.askyesno(
                    "Proses Selesai (Perlu Review)",
                    f"Proses sertifikat selesai!\n\n"
                    f"✓ Cocok Pasti: {accepted} sertifikat\n"
                    f"⚠️ Butuh Review/Konfirmasi: {unmatched} sertifikat\n\n"
                    f"Catatan: Sertifikat yang butuh review tersimpan rapi dengan awalan [REVIEW].\n\n"
                    f"Apakah Anda ingin membuka menu Review & Koreksi sekarang untuk memeriksa dan menyetujuinya?",
                )
                if ans:
                    self._open_review_dialog()
            else:
                messagebox.showinfo(
                    "Sukses Sempurna",
                    f"Proses selesai dengan 100% kecocokan!\n\n"
                    f"✓ Seluruh {accepted} sertifikat berhasil dicocokkan dan diproses.\n"
                    f"File hasil dan file rekap 'data.xlsx' tersimpan rapi di:\n{res.run_dir}",
                )


def main() -> None:
    app = CertSliceApp()
    if "--web" in sys.argv:
        app.after(300, app._launch_web_studio)
    app.mainloop()



def launch_gui() -> None:
    main()


if __name__ == "__main__":
    main()

