@echo off
title Certificate Manager v3.1 - Precision Studio Web Edition
cd /d "%~dp0"
echo ==============================================================
echo  Membuka Certificate Manager v3.1 (Precision Studio Web)
echo  Port: 8500 (http://127.0.0.1:8500)
echo ==============================================================

:: Unblock seluruh file jika diunduh dari internet
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" 2>nul

set PYTHONPATH=%~dp0src;%PYTHONPATH%

if exist "%~dp0dist\CertificateManager\CertificateManager.exe" (
    start "" "%~dp0dist\CertificateManager\CertificateManager.exe" --web
) else if exist "%~dp0CertificateManager.exe" (
    start "" "%~dp0CertificateManager.exe" --web
) else if exist "%~dp0.venv\Scripts\python.exe" (
    start "" "%~dp0.venv\Scripts\python.exe" -m certslice web --port 8500
) else if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    start "" "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" -m certslice web --port 8500
) else if exist "C:\Python313\python.exe" (
    start "" "C:\Python313\python.exe" -m certslice web --port 8500
) else (
    start "" python -m certslice web --port 8500
)
exit
