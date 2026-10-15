"""Certificate Slicer - Modern Desktop GUI Application with Interactive Review & High-DPI support."""
from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
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
        # Try Per-Monitor DPI aware v2 first (Windows 10 1703+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # Fallback to System DPI aware
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

import pymupdf

from certslice.gemini import load_user_settings, save_user_settings
from certslice import pipeline
from certslice.reviewer import get_review_items, render_page_image, approve_group


class TextHandler(logging.Handler):
    """Logging handler that writes formatted log records to a Tkinter Text widget."""

    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)

        def append() -> None:
            self.text_widget.configure(state="normal")
            self.text_widget.insert("end", msg + "\n")
            self.text_widget.see("end")
            self.text_widget.configure(state="disabled")

        self.text_widget.after(0, append)


class CertSliceApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Certificate Slicer - Pemotong Sertifikat Otomatis")
        self.geometry("980x760")
        self.minsize(880, 680)

        # Settings & State
        self.settings = load_user_settings()
        self.last_run_dir: Path | None = None
        self.active_review_items: list[dict[str, Any]] = []
        self.selected_review_item: dict[str, Any] | None = None
        self.current_preview_page: int = 1
        self.current_preview_photo: tk.PhotoImage | None = None

        self._configure_styles()
        self._build_ui()
        self._setup_logging()

    def _configure_styles(self) -> None:
        self.configure(bg="#F1F5F9")
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Global fonts & colors
        font_family = "Segoe UI"
        self.style.configure(".", font=(font_family, 9), background="#F1F5F9", foreground="#0F172A")
        
        # Notebook (Tabs)
        self.style.configure("TNotebook", background="#F1F5F9", borderwidth=0)
        self.style.configure("TNotebook.Tab", font=(font_family, 10, "bold"), padding=(16, 8), background="#E2E8F0", foreground="#475569")
        self.style.map("TNotebook.Tab",
            background=[("selected", "#FFFFFF"), ("active", "#CBD5E1")],
            foreground=[("selected", "#2563EB"), ("active", "#1E293B")]
        )

        # Cards & Frames
        self.style.configure("Card.TFrame", background="#FFFFFF", relief="flat")
        self.style.configure("Card.TLabelframe", background="#FFFFFF", relief="solid", borderwidth=1, bordercolor="#E2E8F0")
        self.style.configure("Card.TLabelframe.Label", font=(font_family, 10, "bold"), background="#FFFFFF", foreground="#1E293B")
        
        # Labels
        self.style.configure("Header.TLabel", font=(font_family, 15, "bold"), background="#FFFFFF", foreground="#1E3A8A")
        self.style.configure("SubHeader.TLabel", font=(font_family, 9), background="#FFFFFF", foreground="#64748B")
        self.style.configure("Muted.TLabel", font=(font_family, 8), foreground="#64748B")
        self.style.configure("CardLabel.TLabel", background="#FFFFFF", font=(font_family, 9))
        self.style.configure("CardLabelBold.TLabel", background="#FFFFFF", font=(font_family, 9, "bold"))
        self.style.configure("SuccessBadge.TLabel", font=(font_family, 9, "bold"), foreground="#059669", background="#ECFDF5")
        self.style.configure("WarningBadge.TLabel", font=(font_family, 9, "bold"), foreground="#D97706", background="#FEF3C7")

        # Buttons
        self.style.configure("Primary.TButton", font=(font_family, 10, "bold"), background="#2563EB", foreground="#FFFFFF")
        self.style.map("Primary.TButton",
            background=[("active", "#1D4ED8"), ("disabled", "#94A3B8")],
            foreground=[("disabled", "#F8FAFC")]
        )
        self.style.configure("Success.TButton", font=(font_family, 10, "bold"), background="#059669", foreground="#FFFFFF")
        self.style.map("Success.TButton",
            background=[("active", "#047857"), ("disabled", "#94A3B8")]
        )
        self.style.configure("Secondary.TButton", font=(font_family, 9), background="#E2E8F0", foreground="#1E293B")
        self.style.map("Secondary.TButton",
            background=[("active", "#CBD5E1")]
        )

        # Treeview styling for modern review table
        self.style.configure("Treeview", font=(font_family, 9), rowheight=26, background="#FFFFFF", fieldbackground="#FFFFFF")
        self.style.configure("Treeview.Heading", font=(font_family, 9, "bold"), background="#F8FAFC", foreground="#1E293B")
        self.style.map("Treeview", background=[("selected", "#EFF6FF")], foreground=[("selected", "#1D4ED8")])

    def _build_ui(self) -> None:
        # 1. Top Header Banner
        header_bar = tk.Frame(self, bg="#FFFFFF", height=70, bd=0, highlightthickness=1, highlightbackground="#E2E8F0")
        header_bar.pack(fill="x", side="top")
        header_bar.pack_propagate(False)

        h_inner = tk.Frame(header_bar, bg="#FFFFFF")
        h_inner.pack(fill="both", expand=True, padx=20, pady=10)

        title_lbl = tk.Label(h_inner, text="⚡ Certificate Slicer", font=("Segoe UI", 14, "bold"), fg="#1E40AF", bg="#FFFFFF")
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            h_inner,
            text="Pemotong Dokumen PDF Sertifikat & Pencocok Master Pegawai Otomatis (Didukung AI Gemini Flash)",
            font=("Segoe UI", 9),
            fg="#64748B",
            bg="#FFFFFF",
        )
        sub_lbl.pack(anchor="w")

        # 2. Main Tabbed Notebook
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=12)

        # Tab 1: Pemrosesan Utama
        self.tab_process = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_process, text=" ⚡ Potong Sertifikat ")

        # Tab 2: Review & Koreksi Interaktif
        self.tab_review = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_review, text=" 🔍 Review & Koreksi Hasil ")

        # Tab 3: Pengaturan & AI
        self.tab_settings = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_settings, text=" ⚙️ Pengaturan & AI Gemini ")

        self._build_process_tab()
        self._build_review_tab()
        self._build_settings_tab()

    # =========================================================================
    # TAB 1: PEMROSESAN UTAMA
    # =========================================================================
    def _build_process_tab(self) -> None:
        # Split into upper cards and lower activity log
        top_frame = ttk.Frame(self.tab_process)
        top_frame.pack(fill="x", expand=False)

        # Card 1: Input Dokumen
        input_card = ttk.LabelFrame(top_frame, text=" 1. Dokumen Input & Data Master ", padding=12, style="Card.TLabelframe")
        input_card.pack(fill="x", pady=(0, 8))

        # PDF input
        ttk.Label(input_card, text="File PDF Bundel Sertifikat:", style="CardLabelBold.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.pdf_var = tk.StringVar()
        ttk.Entry(input_card, textvariable=self.pdf_var, width=64).grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(input_card, text="Pilih PDF...", style="Secondary.TButton", command=self._browse_pdf).grid(row=0, column=2, pady=4)

        # Excel input
        ttk.Label(input_card, text="File Database Master (Excel):", style="CardLabelBold.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.db_var = tk.StringVar()
        ttk.Entry(input_card, textvariable=self.db_var, width=64).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(input_card, text="Pilih Excel...", style="Secondary.TButton", command=self._browse_db).grid(row=1, column=2, pady=4)

        # Output folder
        ttk.Label(input_card, text="Folder Penyimpanan Hasil:", style="CardLabelBold.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.out_var = tk.StringVar(value=str(Path("out").resolve()))
        ttk.Entry(input_card, textvariable=self.out_var, width=64).grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(input_card, text="Pilih Folder...", style="Secondary.TButton", command=self._browse_out).grid(row=2, column=2, pady=4)

        input_card.columnconfigure(1, weight=1)

        # Card 2: Pengaturan Pemrosesan (Disederhanakan & Jelas)
        opts_card = ttk.LabelFrame(top_frame, text=" 2. Pengaturan Proses ", padding=12, style="Card.TLabelframe")
        opts_card.pack(fill="x", pady=(0, 8))

        # Halaman per sertifikat
        row_opts = ttk.Frame(opts_card, style="Card.TFrame")
        row_opts.pack(fill="x", pady=2)
        ttk.Label(row_opts, text="Halaman per Sertifikat:", style="CardLabelBold.TLabel").pack(side="left")
        self.pages_mode_var = tk.StringVar(value="auto")
        mode_cb = ttk.Combobox(
            row_opts,
            textvariable=self.pages_mode_var,
            values=["auto", "1", "2"],
            state="readonly",
            width=8,
        )
        mode_cb.pack(side="left", padx=8)
        ttk.Label(row_opts, text="(Pilih 'auto' untuk mendeteksi lampiran halaman 2 otomatis, atau '2' jika setiap sertifikat pasti 2 halaman)", style="Muted.TLabel").pack(side="left")

        # OCR Checkbox + Description
        row_ocr = ttk.Frame(opts_card, style="Card.TFrame")
        row_ocr.pack(fill="x", pady=(6, 2))
        self.ocr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_ocr, text="Gunakan Pembaca Gambar/Scan (OCR)", variable=self.ocr_var).pack(side="left")
        ttk.Label(row_ocr, text="— Aktifkan jika sertifikat berasal dari hasil scan foto/gambar (Disarankan selalu aktif)", style="Muted.TLabel").pack(side="left", padx=8)

        # AI Rescue Checkbox + Description
        row_ai = ttk.Frame(opts_card, style="Card.TFrame")
        row_ai.pack(fill="x", pady=2)
        self.ai_var = tk.BooleanVar(value=bool(self.settings.get("use_ai", False)))
        ttk.Checkbutton(row_ai, text="Aktifkan AI Rescue (Gemini Flash)", variable=self.ai_var).pack(side="left")
        ttk.Label(row_ai, text="— Otomatis menyelamatkan nama yang terpotong, typo, atau singkatan melalui AI Google", style="Muted.TLabel").pack(side="left", padx=8)

        # Dry Run Checkbox + Description
        row_dry = ttk.Frame(opts_card, style="Card.TFrame")
        row_dry.pack(fill="x", pady=2)
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row_dry, text="Mode Uji Coba Saja (Simulasi)", variable=self.dry_run_var).pack(side="left")
        ttk.Label(row_dry, text="— Hanya mencocokkan data tanpa menyimpan file PDF fisik ke disk", style="Muted.TLabel").pack(side="left", padx=8)

        # Card 3: Action Buttons
        act_frame = ttk.Frame(top_frame)
        act_frame.pack(fill="x", pady=4)

        self.btn_run = ttk.Button(
            act_frame,
            text="⚡ Mulai Proses Pemotongan",
            style="Primary.TButton",
            command=self._start_processing,
        )
        self.btn_run.pack(side="left", padx=(0, 8), ipady=6, ipadx=12)

        self.btn_open_out = ttk.Button(
            act_frame,
            text="📂 Buka Folder Hasil",
            style="Secondary.TButton",
            state="disabled",
            command=self._open_output_dir,
        )
        self.btn_open_out.pack(side="left", padx=4, ipady=4)

        self.btn_goto_review = ttk.Button(
            act_frame,
            text="🔍 Buka Menu Review & Koreksi",
            style="Secondary.TButton",
            state="disabled",
            command=lambda: self.notebook.select(self.tab_review),
        )
        self.btn_goto_review.pack(side="left", padx=4, ipady=4)

        self.progress_bar = ttk.Progressbar(act_frame, mode="indeterminate", length=220)
        # Hidden until run

        # Card 4: Log Output
        log_card = ttk.LabelFrame(self.tab_process, text=" Log Aktivitas Pemrosesan ", padding=8, style="Card.TLabelframe")
        log_card.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk.Text(
            log_card,
            wrap="word",
            state="disabled",
            bg="#0F172A",
            fg="#F8FAFC",
            font=("Consolas", 9),
            relief="flat",
            padx=8,
            pady=8,
        )
        scroll = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    # =========================================================================
    # TAB 2: REVIEW & KOREKSI INTERAKTIF
    # =========================================================================
    def _build_review_tab(self) -> None:
        # Top banner of review tab
        rev_header = tk.Frame(self.tab_review, bg="#FFFFFF", relief="solid", bd=1)
        rev_header.pack(fill="x", pady=(0, 8), padx=2, ipady=4)

        tk.Label(rev_header, text="Folder Sesi yang Direview:", font=("Segoe UI", 9, "bold"), bg="#FFFFFF").pack(side="left", padx=(10, 4))
        self.review_dir_var = tk.StringVar()
        self.review_dir_cb = ttk.Combobox(rev_header, textvariable=self.review_dir_var, width=45)
        self.review_dir_cb.pack(side="left", padx=4)
        self.review_dir_cb.bind("<<ComboboxSelected>>", lambda e: self._load_review_data())

        ttk.Button(rev_header, text="Pilih Folder...", style="Secondary.TButton", command=self._browse_review_folder).pack(side="left", padx=4)
        ttk.Button(rev_header, text="Muat Ulang", style="Secondary.TButton", command=self._load_review_data).pack(side="left", padx=4)

        self.lbl_review_status = tk.Label(rev_header, text="Belum ada data run yang dimuat.", font=("Segoe UI", 9), bg="#FFFFFF", fg="#64748B")
        self.lbl_review_status.pack(side="right", padx=12)

        # Split pane: Left list, Right detail
        paned = tk.PanedWindow(self.tab_review, orient="horizontal", bg="#E2E8F0", sashwidth=4)
        paned.pack(fill="both", expand=True)

        # Left list panel
        left_panel = tk.Frame(paned, bg="#FFFFFF")
        paned.add(left_panel, minsize=320, width=380)

        list_title = tk.Label(left_panel, text="📋 Daftar Sertifikat Butuh Review", font=("Segoe UI", 10, "bold"), bg="#F8FAFC", fg="#1E293B", pady=6)
        list_title.pack(fill="x")

        # Treeview
        tree_frame = tk.Frame(left_panel, bg="#FFFFFF")
        tree_frame.pack(fill="both", expand=True)

        cols = ("pages", "reason", "candidate")
        self.review_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        self.review_tree.heading("pages", text="Halaman")
        self.review_tree.heading("reason", text="Alasan")
        self.review_tree.heading("candidate", text="Kandidat Teratas")
        self.review_tree.column("pages", width=70, anchor="center")
        self.review_tree.column("reason", width=120)
        self.review_tree.column("candidate", width=170)

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.review_tree.yview)
        self.review_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.review_tree.pack(side="left", fill="both", expand=True)

        self.review_tree.bind("<<TreeviewSelect>>", self._on_select_review_item)

        # Right detail & preview panel
        right_panel = tk.Frame(paned, bg="#FFFFFF")
        paned.add(right_panel, minsize=480)

        # Detail header
        self.det_title = tk.Label(right_panel, text="Pilih salah satu item di sebelah kiri untuk mereview", font=("Segoe UI", 10, "bold"), bg="#F8FAFC", fg="#1E293B", pady=6)
        self.det_title.pack(fill="x")

        # Content frame with 2 columns: Visual PDF preview on left, Candidate form on right
        content_box = tk.Frame(right_panel, bg="#FFFFFF")
        content_box.pack(fill="both", expand=True, padx=8, pady=8)

        # 1. Preview column
        preview_col = tk.Frame(content_box, bg="#FFFFFF", width=360)
        preview_col.pack(side="left", fill="both", expand=True, padx=(0, 6))

        prev_header = tk.Frame(preview_col, bg="#FFFFFF")
        prev_header.pack(fill="x", pady=2)
        tk.Label(prev_header, text="Pratinjau Halaman Asli:", font=("Segoe UI", 9, "bold"), bg="#FFFFFF").pack(side="left")

        self.btn_prev_p = ttk.Button(prev_header, text="◀ Hal Sblm", style="Secondary.TButton", width=10, command=self._preview_prev_page)
        self.btn_prev_p.pack(side="right", padx=2)
        self.btn_next_p = ttk.Button(prev_header, text="Hal Brkt ▶", style="Secondary.TButton", width=10, command=self._preview_next_page)
        self.btn_next_p.pack(side="right", padx=2)
        self.lbl_page_num = tk.Label(prev_header, text="Hal -", font=("Segoe UI", 9), bg="#FFFFFF", fg="#64748B")
        self.lbl_page_num.pack(side="right", padx=4)

        self.preview_canvas = tk.Label(preview_col, text="Pratinjau Halaman PDF", bg="#F1F5F9", relief="solid", bd=1)
        self.preview_canvas.pack(fill="both", expand=True, pady=4)

        # 2. Decision & Approval column
        action_col = tk.Frame(content_box, bg="#FFFFFF")
        action_col.pack(side="right", fill="both", expand=True, padx=(6, 0))

        # Yellow alert box for explanation
        self.alert_box = tk.Label(
            action_col,
            text="Pilih item sertifikat di daftar untuk memeriksa kecocokan nama.",
            font=("Segoe UI", 9),
            bg="#FEF3C7",
            fg="#92400E",
            wraplength=340,
            justify="left",
            relief="solid",
            bd=1,
            padx=8,
            pady=6,
        )
        self.alert_box.pack(fill="x", pady=(0, 8))

        # Suggested candidates box
        tk.Label(action_col, text="Saran Nama dari Database Master:", font=("Segoe UI", 9, "bold"), bg="#FFFFFF").pack(anchor="w")
        self.candidates_frame = tk.Frame(action_col, bg="#FFFFFF")
        self.candidates_frame.pack(fill="x", pady=4)
        self.selected_cand_idx = tk.IntVar(value=-1)

        # Custom / Search employee box
        custom_box = ttk.LabelFrame(action_col, text=" Atau Cari Manual Pegawai Lain ", padding=6, style="Card.TLabelframe")
        custom_box.pack(fill="x", pady=6)

        ttk.Label(custom_box, text="NID / Nomor Induk:", style="CardLabel.TLabel").grid(row=0, column=0, sticky="w")
        self.custom_nid_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.custom_nid_var, width=24).grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(custom_box, text="Nama Lengkap:", style="CardLabel.TLabel").grid(row=1, column=0, sticky="w")
        self.custom_nama_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.custom_nama_var, width=24).grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(custom_box, text="Unit Kerja:", style="CardLabel.TLabel").grid(row=2, column=0, sticky="w")
        self.custom_unit_var = tk.StringVar()
        ttk.Entry(custom_box, textvariable=self.custom_unit_var, width=24).grid(row=2, column=1, sticky="ew", padx=4, pady=2)
        custom_box.columnconfigure(1, weight=1)

        # Field Tweaks
        fields_box = ttk.LabelFrame(action_col, text=" Informasi Tambahan Sertifikat ", padding=6, style="Card.TLabelframe")
        fields_box.pack(fill="x", pady=4)

        ttk.Label(fields_box, text="Judul Sertifikat:", style="CardLabel.TLabel").grid(row=0, column=0, sticky="w")
        self.edit_title_var = tk.StringVar()
        ttk.Entry(fields_box, textvariable=self.edit_title_var, width=24).grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(fields_box, text="Tahun Terbit:", style="CardLabel.TLabel").grid(row=1, column=0, sticky="w")
        self.edit_year_var = tk.StringVar()
        ttk.Entry(fields_box, textvariable=self.edit_year_var, width=24).grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        fields_box.columnconfigure(1, weight=1)

        # Approval buttons
        btn_box = tk.Frame(action_col, bg="#FFFFFF")
        btn_box.pack(fill="x", pady=8)

        self.btn_approve = ttk.Button(
            btn_box,
            text="✓ Setujui & Simpan ke PDF",
            style="Success.TButton",
            command=self._approve_current_item,
        )
        self.btn_approve.pack(fill="x", pady=2, ipady=5)

        self.btn_view_pdf = ttk.Button(
            btn_box,
            text="📄 Buka File PDF Asli",
            style="Secondary.TButton",
            command=self._open_item_pdf,
        )
        self.btn_view_pdf.pack(fill="x", pady=2)

    # =========================================================================
    # TAB 3: PENGATURAN & AI GEMINI
    # =========================================================================
    def _build_settings_tab(self) -> None:
        settings_card = ttk.LabelFrame(self.tab_settings, text=" Integrasi Google Gemini API (AI Rescue) ", padding=14, style="Card.TLabelframe")
        settings_card.pack(fill="x", pady=6)

        desc = (
            "Fitur AI Rescue menggunakan Google Gemini Flash-Lite multimodal untuk memeriksa gambar sertifikat secara cerdas "
            "jika pencocokan teks lokal ragu-ragu (misal: nama terpotong, disingkat, atau karakter OCR buram).\n"
            "Kunci API disimpan secara aman di komputer lokal Anda (~/.certslice_settings.json) dan tidak pernah dibagikan."
        )
        ttk.Label(settings_card, text=desc, wraplength=760, justify="left", style="CardLabel.TLabel").pack(anchor="w", pady=(0, 10))

        # API Key input
        key_frame = ttk.Frame(settings_card, style="Card.TFrame")
        key_frame.pack(fill="x", pady=4)
        ttk.Label(key_frame, text="Google Gemini API Key:", style="CardLabelBold.TLabel", width=22).pack(side="left")
        
        self.api_key_var = tk.StringVar(value=self.settings.get("gemini_api_key", ""))
        self.key_entry = ttk.Entry(key_frame, textvariable=self.api_key_var, show="*", width=42)
        self.key_entry.pack(side="left", padx=6)

        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_frame, text="Lihat Key", variable=self.show_key_var, command=self._toggle_show_key).pack(side="left", padx=4)

        ttk.Button(key_frame, text="Simpan Key", style="Primary.TButton", command=self._save_api_key).pack(side="left", padx=8)

        # Model selection
        model_frame = ttk.Frame(settings_card, style="Card.TFrame")
        model_frame.pack(fill="x", pady=6)
        ttk.Label(model_frame, text="Model Gemini:", style="CardLabelBold.TLabel", width=22).pack(side="left")
        self.model_var = tk.StringVar(value=self.settings.get("gemini_model", "gemini-2.0-flash-lite"))
        model_cb = ttk.Combobox(
            model_frame,
            textvariable=self.model_var,
            values=["gemini-2.0-flash-lite", "gemini-2.5-flash", "gemini-1.5-flash"],
            state="readonly",
            width=26,
        )
        model_cb.pack(side="left", padx=6)
        ttk.Label(model_frame, text="(Gemini 2.0 Flash-Lite sangat cepat dan hemat kuota)", style="Muted.TLabel").pack(side="left", padx=6)

        # Help Card
        help_card = ttk.LabelFrame(self.tab_settings, text=" 💡 Panduan & Tips Pemrosesan ", padding=14, style="Card.TLabelframe")
        help_card.pack(fill="both", expand=True, pady=10)

        tips_text = (
            "• Cara mendapatkan Google API Key gratis:\n"
            "  1. Kunjungi Google AI Studio (https://aistudio.google.com/).\n"
            "  2. Klik 'Get API key' dan buat kunci baru secara gratis.\n"
            "  3. Salin dan tempel ke kolom di atas, lalu klik 'Simpan Key'.\n\n"
            "• Penjelasan Kenapa Ada Nama yang Memerlukan Review:\n"
            "  Sistem offline menerapkan ambang batas keamanan (Accept Score = 92%). Jika nama di sertifikat terpotong "
            "  atau hanya berupa singkatan (contoh: di sertifikat tertulis 'RR. Syailendra' sedangkan di data master tertulis "
            "  'RR SYAILENDRA DYAH NIRMALA WIDYASMARA'), algoritma sengaja memasukannya ke daftar review agar tidak salah kirim. "
            "  Anda cukup masuk ke tab 'Review & Koreksi Hasil' untuk menyetujuinya dengan 1 klik!\n\n"
            "• Resolusi Tinggi (High-DPI):\n"
            "  Aplikasi ini otomatis menyesuaikan ketajaman layar Full HD, 2K, dan 4K Windows agar teks dan tombol tidak buram."
        )
        ttk.Label(help_card, text=tips_text, justify="left", wraplength=760, style="CardLabel.TLabel").pack(anchor="w")

    def _setup_logging(self) -> None:
        self.logger = logging.getLogger("certslice")
        self.logger.setLevel(logging.INFO)
        handler = TextHandler(self.log_text)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        self.logger.addHandler(handler)

    def _toggle_show_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key_var.get() else "*")

    def _save_api_key(self) -> None:
        key = self.api_key_var.get().strip()
        model = self.model_var.get().strip()
        save_user_settings(gemini_api_key=key, gemini_model=model, use_ai=self.ai_var.get())
        messagebox.showinfo("Tersimpan", "Pengaturan Gemini API key berhasil disimpan dengan aman di profil komputer Anda.")

    def _browse_pdf(self) -> None:
        f = filedialog.askopenfilename(
            title="Pilih File PDF Bundel Sertifikat",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if f:
            self.pdf_var.set(f)

    def _browse_db(self) -> None:
        f = filedialog.askopenfilename(
            title="Pilih File Database Master Excel",
            filetypes=[("Excel Files", "*.xlsx *.xls"), ("All Files", "*.*")],
        )
        if f:
            self.db_var.set(f)

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Pilih Folder Penyimpanan Hasil")
        if d:
            self.out_var.set(d)

    def _browse_review_folder(self) -> None:
        d = filedialog.askdirectory(title="Pilih Folder Run Sesi Pemotongan", initialdir=str(self.out_var.get()))
        if d:
            self.review_dir_var.set(d)
            self._load_review_data()

    def _open_output_dir(self) -> None:
        target = self.last_run_dir or Path(self.out_var.get())
        if target.exists():
            if sys.platform == "win32":
                os.startfile(target)
            else:
                subprocess.run(["xdg-open", str(target)])

    # =========================================================================
    # PROCESSING WORKER
    # =========================================================================
    def _start_processing(self) -> None:
        pdf_file = Path(self.pdf_var.get().strip())
        db_file = Path(self.db_var.get().strip())
        out_dir = Path(self.out_var.get().strip())

        if not pdf_file.exists() or not pdf_file.is_file():
            messagebox.showerror("Error", "File PDF bundel sertifikat tidak ditemukan. Silakan pilih file yang valid.")
            return

        if not db_file.exists() or not db_file.is_file():
            messagebox.showerror("Error", "File database Excel tidak ditemukan. Silakan pilih file yang valid.")
            return

        # Save AI setting
        save_user_settings(
            gemini_api_key=self.api_key_var.get().strip(),
            gemini_model=self.model_var.get().strip(),
            use_ai=self.ai_var.get(),
        )

        self.btn_run.configure(state="disabled")
        self.btn_open_out.configure(state="disabled")
        self.btn_goto_review.configure(state="disabled")
        self.progress_bar.pack(side="left", padx=12)
        self.progress_bar.start(10)

        threading.Thread(target=self._run_pipeline_worker, daemon=True).start()

    def _run_pipeline_worker(self) -> None:
        pdf_file = Path(self.pdf_var.get().strip())
        db_file = Path(self.db_var.get().strip())
        out_dir = Path(self.out_var.get().strip())
        pages_mode = self.pages_mode_var.get()
        ocr_enabled = self.ocr_var.get()
        dry_run = self.dry_run_var.get()
        use_ai = self.ai_var.get()
        api_key = self.api_key_var.get().strip() or None
        model = self.model_var.get().strip()

        self.logger.info("=== Memulai Pemrosesan Sertifikat ===")
        self.logger.info("PDF: %s", pdf_file.name)
        self.logger.info("Database: %s", db_file.name)
        self.logger.info("Mode Halaman: %s | OCR: %s | Dry-Run: %s | AI Rescue: %s", pages_mode, ocr_enabled, dry_run, use_ai)

        res = None
        error_msg = None
        try:
            res = pipeline.run(
                pdf_file=pdf_file,
                db_file=db_file,
                out_path=out_dir,
                pages_per_cert=pages_mode,
                ocr_enabled=ocr_enabled,
                dry_run=dry_run,
                use_ai=use_ai,
                gemini_api_key=api_key,
                gemini_model=model,
            )
        except Exception as e:
            error_msg = str(e)
            self.logger.error("Terjadi kesalahan: %s", error_msg, exc_info=True)

        self.after(0, self._on_processing_complete, res, error_msg)

    def _on_processing_complete(self, res: Any, error_msg: str | None) -> None:
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.btn_run.configure(state="normal")

        if error_msg:
            messagebox.showerror("Gagal", f"Pemrosesan gagal:\n{error_msg}")
            return

        if res:
            self.last_run_dir = res.run_dir
            self.btn_open_out.configure(state="normal")
            summary = res.summary
            accepted = summary.get("accepted", 0)
            unmatched = summary.get("unmatched", 0)
            total = summary.get("pages", 0)

            self.logger.info("=== Pemrosesan Selesai ===")
            self.logger.info("Total halaman: %d | Cocok: %d | Butuh Review: %d", total, accepted, unmatched)
            self.logger.info("Folder hasil: %s", res.run_dir)

            # Auto-populate review combobox
            self.review_dir_var.set(str(res.run_dir))
            self._load_review_data()

            if unmatched > 0:
                self.btn_goto_review.configure(state="normal", text=f"🔍 Review & Koreksi ({unmatched} item)")
                ans = messagebox.askyesno(
                    "Pemotongan Berhasil",
                    f"Pemotongan selesai!\n\n"
                    f"✓ Cocok sempurna: {accepted} sertifikat\n"
                    f"⚠️ Butuh review: {unmatched} sertifikat\n\n"
                    f"Apakah Anda ingin membuka menu Review & Koreksi sekarang untuk memeriksa sertifikat tersebut?",
                )
                if ans:
                    self.notebook.select(self.tab_review)
            else:
                self.btn_goto_review.configure(state="disabled")
                messagebox.showinfo(
                    "Sukses Sempurna",
                    f"Pemotongan selesai dengan 100% kecocokan!\n\n"
                    f"✓ Berhasil dicocokkan: {accepted} sertifikat\n"
                    f"Semua file tersimpan rapi di folder:\n{res.run_dir}",
                )

    # =========================================================================
    # REVIEW LOGIC & PREVIEW
    # =========================================================================
    def _load_review_data(self) -> None:
        folder_str = self.review_dir_var.get().strip()
        if not folder_str:
            return

        run_dir = Path(folder_str)
        self.active_review_items = get_review_items(run_dir)

        # Clear tree
        for item in self.review_tree.get_children():
            self.review_tree.delete(item)

        unmatched_count = len(self.active_review_items)
        if unmatched_count == 0:
            self.lbl_review_status.configure(
                text="✓ Tidak ada sertifikat yang butuh review di sesi ini.",
                fg="#059669",
            )
            self._clear_review_detail()
        else:
            self.lbl_review_status.configure(
                text=f"⚠️ Ditemukan {unmatched_count} sertifikat yang perlu direview.",
                fg="#D97706",
            )
            for idx, g in enumerate(self.active_review_items):
                pages_str = "-".join(str(p) for p in g.get("pages", []))
                reason = g.get("reason", "")
                cands = g.get("candidates", [])
                top_cand = f"{cands[0]['nama']} ({cands[0].get('score', 0)}%)" if cands else "-"
                self.review_tree.insert("", "end", iid=str(idx), values=(pages_str, reason, top_cand))

            # Select first item automatically
            self.review_tree.selection_set("0")
            self._on_select_review_item(None)

    def _clear_review_detail(self) -> None:
        self.det_title.configure(text="Semua sertifikat telah cocok atau tidak ada item butuh review.")
        self.preview_canvas.configure(image="", text="Tidak ada pratinjau.")
        self.alert_box.configure(text="Semua sertifikat pada sesi ini sudah tervalidasi dengan baik.")
        for w in self.candidates_frame.winfo_children():
            w.destroy()
        self.custom_nid_var.set("")
        self.custom_nama_var.set("")
        self.custom_unit_var.set("")
        self.edit_title_var.set("")
        self.edit_year_var.set("")

    def _on_select_review_item(self, event: Any) -> None:
        sel = self.review_tree.selection()
        if not sel:
            return

        idx = int(sel[0])
        if idx >= len(self.active_review_items):
            return

        grp = self.active_review_items[idx]
        self.selected_review_item = grp

        pages = grp.get("pages", [1])
        pages_str = "-".join(str(p) for p in pages)
        self.det_title.configure(text=f"Review Sertifikat ID #{grp.get('id')} — Halaman {pages_str}")

        # Update alert box
        reason = grp.get("reason", "")
        if reason == "partial name":
            msg = (
                "⚠️ Alasan Review: Nama Parsial / Singkatan.\n"
                "Nama yang tercetak di sertifikat lebih pendek dari data master pegawai (hanya singkatan / sebagian nama). "
                "Sistem offline meminta persetujuan manusia agar tidak terjadi kekeliruan orang."
            )
        elif reason == "close candidates":
            msg = (
                "⚠️ Alasan Review: Ada dua kandidat nama dengan kemiripan sangat dekat.\n"
                "Silakan pilih pegawai yang tepat dari pilihan di bawah ini."
            )
        elif reason == "homonym":
            msg = "⚠️ Alasan Review: Ditemukan nama yang identik (homonim) di database."
        else:
            msg = f"⚠️ Alasan Review: {reason or 'Perlu pemeriksaan manual'}."
        self.alert_box.configure(text=msg)

        # Clear & populate candidates
        for w in self.candidates_frame.winfo_children():
            w.destroy()

        cands = grp.get("candidates", [])
        self.selected_cand_idx.set(0 if cands else -1)

        if cands:
            for c_idx, c in enumerate(cands):
                cand_text = f"{c.get('nama', '')}  (Skor: {c.get('score', 0)}%)\nNID: {c.get('nid', '-')} | Unit: {c.get('unit', '-')}"
                rb = tk.Radiobutton(
                    self.candidates_frame,
                    text=cand_text,
                    variable=self.selected_cand_idx,
                    value=c_idx,
                    justify="left",
                    anchor="w",
                    bg="#FFFFFF",
                    font=("Segoe UI", 9),
                    command=lambda: self._on_candidate_chosen(),
                )
                rb.pack(fill="x", pady=2)
            self._on_candidate_chosen()
        else:
            tk.Label(self.candidates_frame, text="Tidak ada kandidat otomatis yang ditemukan.", bg="#FFFFFF", fg="#64748B").pack(anchor="w")

        # Fill fields
        fields = grp.get("fields", {})
        self.edit_title_var.set(fields.get("title", {}).get("value") or "Tanpa Judul")
        iss_date = fields.get("issue_date", {}).get("value")
        self.edit_year_var.set(iss_date.split("-")[0] if iss_date else "0000")

        # Load first page preview
        self.current_preview_page = pages[0]
        self._render_preview()

    def _on_candidate_chosen(self) -> None:
        idx = self.selected_cand_idx.get()
        if not self.selected_review_item or idx < 0:
            return
        cands = self.selected_review_item.get("candidates", [])
        if idx < len(cands):
            c = cands[idx]
            self.custom_nid_var.set(c.get("nid", ""))
            self.custom_nama_var.set(c.get("nama", ""))
            self.custom_unit_var.set(c.get("unit", ""))

    def _render_preview(self) -> None:
        folder_str = self.review_dir_var.get().strip()
        if not folder_str or not self.selected_review_item:
            return

        run_dir = Path(folder_str)
        plan_path = run_dir / "plan.json"
        if not plan_path.exists():
            return

        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            pdf_path = Path(plan_data.get("input_pdf", ""))
            pages = self.selected_review_item.get("pages", [self.current_preview_page])

            self.lbl_page_num.configure(text=f"Hal {self.current_preview_page} (dari {pages[0]}-{pages[-1]})")

            png_bytes = render_page_image(pdf_path, self.current_preview_page, max_width=380)
            if png_bytes:
                self.current_preview_photo = tk.PhotoImage(data=png_bytes)
                self.preview_canvas.configure(image=self.current_preview_photo, text="")
            else:
                self.preview_canvas.configure(image="", text="Gagal memuat pratinjau halaman.")
        except Exception as e:
            self.preview_canvas.configure(image="", text=f"Error preview: {e}")

    def _preview_prev_page(self) -> None:
        if not self.selected_review_item:
            return
        pages = self.selected_review_item.get("pages", [])
        if self.current_preview_page > pages[0]:
            self.current_preview_page -= 1
            self._render_preview()

    def _preview_next_page(self) -> None:
        if not self.selected_review_item:
            return
        pages = self.selected_review_item.get("pages", [])
        if self.current_preview_page < pages[-1]:
            self.current_preview_page += 1
            self._render_preview()

    def _open_item_pdf(self) -> None:
        folder_str = self.review_dir_var.get().strip()
        if not folder_str or not self.selected_review_item:
            return
        run_dir = Path(folder_str)
        plan_path = run_dir / "plan.json"
        if not plan_path.exists():
            return
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            pdf_path = Path(plan_data.get("input_pdf", ""))
            if pdf_path.exists():
                if sys.platform == "win32":
                    os.startfile(pdf_path)
                else:
                    subprocess.run(["xdg-open", str(pdf_path)])
        except Exception as e:
            messagebox.showerror("Error", f"Gagal membuka PDF: {e}")

    def _approve_current_item(self) -> None:
        if not self.selected_review_item:
            messagebox.showinfo("Info", "Pilih sertifikat yang ingin disetujui terlebih dahulu.")
            return

        nid = self.custom_nid_var.get().strip()
        nama = self.custom_nama_var.get().strip()
        unit = self.custom_unit_var.get().strip()
        judul = self.edit_title_var.get().strip()
        tahun = self.edit_year_var.get().strip()

        if not nid or not nama:
            messagebox.showwarning("Peringatan", "NID dan Nama Pegawai harus terisi untuk menyetujui sertifikat.")
            return

        folder_str = self.review_dir_var.get().strip()
        run_dir = Path(folder_str)
        group_id = self.selected_review_item["id"]

        try:
            file_name = approve_group(
                run_dir=run_dir,
                group_id=group_id,
                nid=nid,
                nama=nama,
                unit=unit,
                custom_title=judul,
                custom_year=tahun,
            )
            messagebox.showinfo(
                "Berhasil Disetujui",
                f"Sertifikat berhasil disetujui dan dipotong!\n\n"
                f"File PDF: {file_name}\n"
                f"Tersimpan di: {run_dir / 'pdf' / file_name}\n\n"
                f"Rekap manifest_slice.xlsx dan unmatched_report.xlsx telah otomatis diperbarui.",
            )
            # Reload review data
            self._load_review_data()
        except Exception as e:
            messagebox.showerror("Gagal Menyetujui", f"Terjadi kesalahan saat menyetujui: {e}")


def main() -> None:
    app = CertSliceApp()
    app.mainloop()


if __name__ == "__main__":
    main()
