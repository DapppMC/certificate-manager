@echo off
title Certificate Manager
echo Membuka Certificate Manager...

:: Unblock all files in this folder automatically (removes Mark of the Web if downloaded from internet)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" 2>nul

if exist "%~dp0CertificateManager.exe" (
    start "" "%~dp0CertificateManager.exe"
) else if exist "%~dp0dist\CertificateManager\CertificateManager.exe" (
    start "" "%~dp0dist\CertificateManager\CertificateManager.exe"
) else if exist "%~dp0dist\CertificateSlicer\CertificateSlicer.exe" (
    start "" "%~dp0dist\CertificateSlicer\CertificateSlicer.exe"
) else if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m certslice
) else (
    start "" pythonw -m certslice
)
exit
