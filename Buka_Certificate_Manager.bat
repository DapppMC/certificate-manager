@echo off
title Certificate Manager v3.1 - Desktop GUI Native
cd /d "%~dp0"
echo ==============================================================
echo  Membuka Certificate Manager v3.1 (Desktop GUI Native)
echo ==============================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" 2>nul

set PYTHONPATH=%~dp0src;%PYTHONPATH%

if exist "%~dp0dist\CertificateManager\CertificateManager.exe" (
    start "" "%~dp0dist\CertificateManager\CertificateManager.exe"
) else if exist "%~dp0CertificateManager.exe" (
    start "" "%~dp0CertificateManager.exe"
) else if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m certslice gui
) else if exist "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe" (
    start "" "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe" -m certslice gui
) else if exist "C:\Python313\pythonw.exe" (
    start "" "C:\Python313\pythonw.exe" -m certslice gui
) else (
    start "" pythonw -m certslice gui
)
exit
