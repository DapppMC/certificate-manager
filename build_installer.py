"""PyInstaller build script to generate CertificateSlicer Windows application."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    root = Path(__file__).resolve().parent
    pyinstaller = root / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller.exists():
        pyinstaller = Path(sys.executable).parent / "pyinstaller.exe"

    entry_point = root / "src" / "certslice" / "gui.py"
    config_file = root / "src" / "certslice" / "default_config.toml"

    cmd = [
        str(pyinstaller),
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name",
        "CertificateManager",
        "--add-data",
        f"{config_file};certslice",
        "--collect-all",
        "rapidocr",
        "--collect-all",
        "pymupdf",
        str(entry_point),
    ]

    print("Running PyInstaller...")
    print("Command:", " ".join(cmd))
    res = subprocess.run(cmd, cwd=root)
    if res.returncode == 0:
        dist_dir = root / "dist" / "CertificateManager"
        exe_file = dist_dir / "CertificateManager.exe"
        print("\n=======================================================")
        print("BUILD SUCCEEDED!")
        print(f"Aplikasi EXE siap digunakan di:")
        print(f"{exe_file}")
        print("=======================================================\n")
    else:
        print("\nBUILD FAILED with exit code", res.returncode)
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()
