@echo off
title Certificate Manager - Precision Studio Web v3.0
cd /d "%~dp0"
echo Membuka Certificate Manager Web Studio v3.0 (http://127.0.0.1:8500)...

:: Otomatis unblock seluruh file di folder ini jika diunduh dari internet
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" 2>nul

if exist "%~dp0CertificateManager.exe" (
    start "" "%~dp0CertificateManager.exe" --web
) else if exist "%~dp0dist\CertificateManager\CertificateManager.exe" (
    start "" "%~dp0dist\CertificateManager\CertificateManager.exe" --web
) else if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m certslice web --port 8500
) else (
    start "" pythonw -m certslice web --port 8500
)
exit
