"""PyInstaller build script to generate CertificateManager Windows standalone application."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def main() -> None:
    root = Path(__file__).resolve().parent

    entry_point = root / "src" / "certslice" / "gui.py"
    config_file = root / "src" / "certslice" / "default_config.toml"
    sample_db = root / "templates" / "sample_database.xlsx"
    data_master_db = root / "Data Master SDM April (Ellipse 30 04 2026).xlsx"
    komparasi_file = root / "Komparasi Kode Baru Sertifikasi NPS 0726.xlsx"
    mso_file = root / "MSO765_NPS.xlsx"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name",
        "CertificateManager",
        "--add-data",
        f"{config_file};certslice",
        "--add-data",
        f"{sample_db};templates",
        "--hidden-import",
        "certslice.levels",
        "--hidden-import",
        "certslice.resource_codes",
        "--hidden-import",
        "certslice.mso",
        "--hidden-import",
        "certslice.bulk",
        "--hidden-import",
        "certslice.reviewer",
        "--hidden-import",
        "certslice.gui",
        "--hidden-import",
        "certslice.web",
        "--collect-all",
        "rapidocr",
        "--collect-all",
        "pymupdf",
        str(entry_point),
    ]

    print("=======================================================")
    print("Membangun CertificateManager Standalone (.EXE)...")
    print("Command:", " ".join(cmd))
    print("=======================================================\n")

    res = subprocess.run(cmd, cwd=root)
    if res.returncode != 0:
        print("\nBUILD FAILED with exit code", res.returncode)
        sys.exit(res.returncode)

    dist_dir = root / "dist" / "CertificateManager"
    exe_file = dist_dir / "CertificateManager.exe"

    # Copy companion files to dist/CertificateManager
    if sample_db.exists():
        shutil.copy2(sample_db, dist_dir / "sample_database.xlsx")
        print(f"Disalin: sample_database.xlsx -> {dist_dir}")

    # Copy the 3 essential Excel files requested by the user
    for xlsx_file in [data_master_db, komparasi_file, mso_file]:
        if xlsx_file.exists():
            shutil.copy2(xlsx_file, dist_dir / xlsx_file.name)
            print(f"Disalin: {xlsx_file.name} -> {dist_dir}")

    bat_file = root / "Buka_Certificate_Manager.bat"
    if bat_file.exists():
        shutil.copy2(bat_file, dist_dir / "Buka_Certificate_Manager.bat")
        print(f"Disalin: Buka_Certificate_Manager.bat -> {dist_dir}")

    web_bat = root / "Buka_Web_Studio_v3.bat"
    if web_bat.exists():
        shutil.copy2(web_bat, dist_dir / "Buka_Web_Studio_v3.bat")
        print(f"Disalin: Buka_Web_Studio_v3.bat -> {dist_dir}")

    readme_file = root / "PETUNJUK_PENGGUNAAN.txt"
    if readme_file.exists():
        shutil.copy2(readme_file, dist_dir / "PETUNJUK_PENGGUNAAN.txt")
        print(f"Disalin: PETUNJUK_PENGGUNAAN.txt -> {dist_dir}")

    settings_file = root / "certslice_settings.json"
    if settings_file.exists():
        shutil.copy2(settings_file, dist_dir / "certslice_settings.json")
        print(f"Disalin: certslice_settings.json -> {dist_dir}")

    # Create portable release ZIP
    zip_path_v3 = root / "CertificateManager_v3.0_Windows.zip"
    zip_path_v2 = root / "CertificateManager_v2.0_Windows.zip"
    zip_path_dist_v3 = root / "dist" / "CertificateManager_v3.0_Windows.zip"
    zip_path_dist = root / "dist" / "CertificateManager_v2.0_Windows.zip"
    zip_path_dist_v1 = root / "dist" / "CertificateManager_v1.0_Windows.zip"
    print(f"\nMembuat archive portable release: {zip_path_v3.name}...")
    with zipfile.ZipFile(zip_path_v3, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dist_dir.rglob("*"):
            if f.is_file():
                arcname = f.relative_to(root / "dist")
                zf.write(f, arcname)

    shutil.copy2(zip_path_v3, zip_path_v2)
    shutil.copy2(zip_path_v3, root / "CertificateManager_v1.0_Windows.zip")
    shutil.copy2(zip_path_v3, zip_path_dist_v3)
    shutil.copy2(zip_path_v3, zip_path_dist)
    shutil.copy2(zip_path_v3, zip_path_dist_v1)

    print("\n=======================================================")
    print("BUILD SUCCEEDED & FINALIZED!")
    print(f"Folder EXE Portable  : {dist_dir}")
    print(f"File EXE Utama       : {exe_file}")
    print(f"File Release ZIP     : {zip_path_v3} ({round(zip_path_v3.stat().st_size / (1024 * 1024), 1)} MB)")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
