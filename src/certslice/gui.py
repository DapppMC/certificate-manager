"""Certificate Slicer - Modern Desktop GUI Application."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from certslice.gemini import load_user_settings, save_user_settings
from certslice import pipeline


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
        self.geometry("820x720")
        self.minsize(760, 640)

        # Settings
        self.settings = load_user_settings()
        self.last_run_dir: Path | None = None

        self._configure_styles()
        self._build_ui()
        self._setup_logging()

    def _configure_styles(self) -> None:
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self.style.configure(".", font=("Segoe UI", 9))
        self.style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#005A9E")
        self.style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#555555")
        self.style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#005A9E")
        self.style.configure("Action.TButton", font=("Segoe UI", 11, "bold"), foreground="#FFFFFF", background="#0078D7")
        self.style.map("Action.TButton", background=[("active", "#005A9E"), ("disabled", "#CCCCCC")])

    def _build_ui(self) -> None:
        # Header
        header_frame = ttk.Frame(self, padding=(16, 12, 16, 6))
        header_frame.pack(fill="x")
        ttk.Label(
            header_frame,
            text="⚡ Certificate Slicer",
            style="Header.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header_frame,
            text="Pemotong bundel sertifikat PDF & pencocok master data pegawai otomatis (Offline & AI)",
            style="SubHeader.TLabel",
        ).pack(anchor="w")

        # Main scrollable canvas or notebook
        content_frame = ttk.Frame(self, padding=(16, 6, 16, 12))
        content_frame.pack(fill="both", expand=True)

        # 1. File Inputs Section
        input_frame = ttk.LabelFrame(content_frame, text=" 1. Input Dokumen & Data Master ", padding=10, style="Section.TLabelframe")
        input_frame.pack(fill="x", pady=5)

        # PDF input
        ttk.Label(input_frame, text="File PDF Bundel Sertifikat:").grid(row=0, column=0, sticky="w", pady=4)
        self.pdf_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.pdf_var, width=60).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(input_frame, text="Pilih PDF...", command=self._browse_pdf).grid(row=0, column=2, pady=4)

        # Database Excel input
        ttk.Label(input_frame, text="File Database Master (Excel):").grid(row=1, column=0, sticky="w", pady=4)
        self.db_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.db_var, width=60).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(input_frame, text="Pilih Excel...", command=self._browse_db).grid(row=1, column=2, pady=4)

        # Output Folder input
        ttk.Label(input_frame, text="Folder Hasil (Output):").grid(row=2, column=0, sticky="w", pady=4)
        default_out = str(Path.cwd() / "out")
        self.out_var = tk.StringVar(value=default_out)
        ttk.Entry(input_frame, textvariable=self.out_var, width=60).grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(input_frame, text="Pilih Folder...", command=self._browse_out).grid(row=2, column=2, pady=4)
        input_frame.columnconfigure(1, weight=1)

        # 2. Options Section
        opt_frame = ttk.LabelFrame(content_frame, text=" 2. Pengaturan Proses ", padding=10, style="Section.TLabelframe")
        opt_frame.pack(fill="x", pady=5)

        # Pages per cert
        ttk.Label(opt_frame, text="Halaman per Sertifikat:").grid(row=0, column=0, sticky="w", pady=4)
        self.pages_mode_var = tk.StringVar(value="auto")
        mode_cb = ttk.Combobox(
            opt_frame,
            textvariable=self.pages_mode_var,
            values=["auto", "1", "2"],
            state="readonly",
            width=15,
        )
        mode_cb.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(opt_frame, text="(auto: otomatis mendeteksi 1 atau 2 halaman)").grid(row=0, column=2, sticky="w", pady=4)

        # OCR Checkbox
        self.ocr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt_frame,
            text="Gunakan OCR Lokal (RapidOCR) untuk halaman pindaian/scan tanpa text layer",
            variable=self.ocr_var,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=3)

        # Dry Run Checkbox
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_frame,
            text="Simulasi saja (Dry Run - hitung pencocokan tanpa memotong file fisik)",
            variable=self.dry_run_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=3)

        # 3. AI Rescue Layer Section (Gemini Flash-Lite)
        ai_frame = ttk.LabelFrame(content_frame, text=" 3. Penyelamat AI (Google Gemini Flash-Lite) ", padding=10, style="Section.TLabelframe")
        ai_frame.pack(fill="x", pady=5)

        self.use_ai_var = tk.BooleanVar(value=bool(self.settings.get("use_ai", False)))
        ttk.Checkbutton(
            ai_frame,
            text="Aktifkan Penyelamat AI untuk nama yang ragu / disingkat / unmatched",
            variable=self.use_ai_var,
            command=self._toggle_ai_fields,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=3)

        ttk.Label(ai_frame, text="Google Gemini API Key:").grid(row=1, column=0, sticky="w", pady=4)
        self.api_key_var = tk.StringVar(value=self.settings.get("gemini_api_key", ""))
        self.api_key_entry = ttk.Entry(ai_frame, textvariable=self.api_key_var, show="*", width=42)
        self.api_key_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=4)

        key_btn_frame = ttk.Frame(ai_frame)
        key_btn_frame.grid(row=1, column=2, sticky="w", pady=4)
        self.show_key_btn = ttk.Button(key_btn_frame, text="👁", width=3, command=self._toggle_key_visibility)
        self.show_key_btn.pack(side="left", padx=2)
        ttk.Button(key_btn_frame, text="Simpan Key", command=self._save_api_key).pack(side="left", padx=2)

        ttk.Label(ai_frame, text="Pilihan Model:").grid(row=2, column=0, sticky="w", pady=4)
        self.model_var = tk.StringVar(value=self.settings.get("model", "gemini-2.5-flash"))
        model_cb = ttk.Combobox(
            ai_frame,
            textvariable=self.model_var,
            values=["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"],
            state="readonly",
            width=20,
        )
        model_cb.grid(row=2, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(
            ai_frame,
            text="💡 Gratis dari Google AI Studio. Hanya halaman yang 'unmatched' yang dikirim ke AI.",
            font=("Segoe UI", 8, "italic"),
            foreground="#666666",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=2)
        ai_frame.columnconfigure(1, weight=1)

        # 4. Action Buttons & Progress
        action_frame = ttk.Frame(content_frame, padding=(0, 6, 0, 6))
        action_frame.pack(fill="x", pady=4)

        self.start_btn = ttk.Button(
            action_frame,
            text="▶  Mulai Proses Pemotongan",
            style="Action.TButton",
            command=self._start_processing,
        )
        self.start_btn.pack(side="left", padx=4)

        self.open_out_btn = ttk.Button(
            action_frame,
            text="📂  Buka Folder Hasil",
            state="disabled",
            command=self._open_output_folder,
        )
        self.open_out_btn.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(action_frame, mode="indeterminate", length=220)
        self.progress.pack(side="right", padx=6)

        # Status Label
        self.status_var = tk.StringVar(value="Siap.")
        ttk.Label(content_frame, textvariable=self.status_var, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=2)

        # 5. Log Console
        log_frame = ttk.LabelFrame(content_frame, text=" Log Aktivitas & Hasil ", padding=6, style="Section.TLabelframe")
        log_frame.pack(fill="both", expand=True, pady=4)

        self.log_text = tk.Text(log_frame, wrap="word", height=10, state="disabled", font=("Consolas", 9), background="#FAFAFA")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _setup_logging(self) -> None:
        handler = TextHandler(self.log_text)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    def _browse_pdf(self) -> None:
        fn = filedialog.askopenfilename(
            title="Pilih File PDF Bundel Sertifikat",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if fn:
            self.pdf_var.set(fn)

    def _browse_db(self) -> None:
        fn = filedialog.askopenfilename(
            title="Pilih File Database Master Karyawan (Excel)",
            filetypes=[("Excel Files", "*.xlsx *.xls"), ("All Files", "*.*")],
        )
        if fn:
            self.db_var.set(fn)

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Pilih Folder Hasil Output")
        if d:
            self.out_var.set(d)

    def _toggle_ai_fields(self) -> None:
        save_user_settings({"use_ai": self.use_ai_var.get()})

    def _toggle_key_visibility(self) -> None:
        if self.api_key_entry.cget("show") == "":
            self.api_key_entry.configure(show="*")
            self.show_key_btn.configure(text="👁")
        else:
            self.api_key_entry.configure(show="")
            self.show_key_btn.configure(text="🔒")

    def _save_api_key(self) -> None:
        key = self.api_key_var.get().strip()
        model = self.model_var.get().strip()
        save_user_settings({
            "gemini_api_key": key,
            "use_ai": self.use_ai_var.get(),
            "model": model,
        })
        messagebox.showinfo("Tersimpan", "Pengaturan API Key Gemini berhasil disimpan!")

    def _open_output_folder(self) -> None:
        folder = self.last_run_dir or Path(self.out_var.get())
        if folder.exists():
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        else:
            messagebox.showwarning("Peringatan", f"Folder belum ditemukan: {folder}")

    def _start_processing(self) -> None:
        pdf_path = self.pdf_var.get().strip()
        db_path = self.db_var.get().strip()
        out_dir = self.out_var.get().strip()

        if not pdf_path or not Path(pdf_path).exists():
            messagebox.showerror("Error", "File PDF bundel belum dipilih atau tidak ditemukan.")
            return

        if not db_path or not Path(db_path).exists():
            messagebox.showerror("Error", "File Database Excel belum dipilih atau tidak ditemukan.")
            return

        if not out_dir:
            out_dir = str(Path.cwd() / "out")
            self.out_var.set(out_dir)

        # Disable button & start progress bar
        self.start_btn.configure(state="disabled")
        self.open_out_btn.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Sedang memproses dokumen...")

        use_ai = self.use_ai_var.get()
        api_key = self.api_key_var.get().strip()
        model = self.model_var.get().strip()
        pages_mode = self.pages_mode_var.get()
        ocr_enabled = self.ocr_var.get()
        dry_run = self.dry_run_var.get()

        # Save current settings
        save_user_settings({
            "gemini_api_key": api_key,
            "use_ai": use_ai,
            "model": model,
        })

        thread = threading.Thread(
            target=self._run_pipeline_worker,
            args=(pdf_path, db_path, out_dir, pages_mode, ocr_enabled, dry_run, use_ai, api_key, model),
            daemon=True,
        )
        thread.start()

    def _run_pipeline_worker(
        self,
        pdf_path: str,
        db_path: str,
        out_dir: str,
        pages_mode: str,
        ocr_enabled: bool,
        dry_run: bool,
        use_ai: bool,
        api_key: str,
        model: str,
    ) -> None:
        try:
            res = pipeline.run(
                pdf_path=pdf_path,
                db_path=db_path,
                out_dir=out_dir,
                dry_run=dry_run,
                pages_per_cert=pages_mode,
                ocr=ocr_enabled,
                use_ai=use_ai,
                gemini_api_key=api_key,
                gemini_model=model,
            )
            self.last_run_dir = res.run_dir
            self.after(0, self._on_processing_success, res)
        except Exception as exc:
            self.after(0, self._on_processing_error, str(exc))

    def _on_processing_success(self, res: pipeline.RunResult) -> None:
        self.progress.stop()
        self.start_btn.configure(state="normal")
        self.open_out_btn.configure(state="normal")
        self.status_var.set("Pemrosesan Selesai Sukses!")

        summary = res.summary
        run_folder = str(res.run_dir) if res.run_dir else self.out_var.get()

        report_msg = (
            f"=== HASIL PEMOTONGAN SERTIFIKAT ===\n"
            f"Total Halaman Diproses: {summary['pages']}\n"
            f"Sertifikat Berhasil Dicocokkan (Accepted): {summary['accepted']}\n"
            f"Sertifikat Perlu Review (Unmatched): {summary['unmatched']}\n"
            f"Folder Output: {run_folder}\n"
            f"====================================\n"
        )
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "\n" + report_msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

        messagebox.showinfo(
            "Selesai",
            f"Proses berhasil selesai!\n\n"
            f"Diterima: {summary['accepted']} sertifikat\n"
            f"Perlu Review: {summary['unmatched']} sertifikat\n\n"
            f"Hasil tersimpan di:\n{run_folder}",
        )

    def _on_processing_error(self, err_msg: str) -> None:
        self.progress.stop()
        self.start_btn.configure(state="normal")
        self.status_var.set("Gagal: terjadi kesalahan.")
        messagebox.showerror("Terjadi Kesalahan", f"Proses pemotongan gagal:\n\n{err_msg}")


def launch_gui() -> None:
    app = CertSliceApp()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()
