@echo off
title Certificate Manager
echo Membuka Certificate Manager...
if exist "%~dp0dist\CertificateManager\CertificateManager.exe" (
    start "" "%~dp0dist\CertificateManager\CertificateManager.exe"
) else if exist "%~dp0dist\CertificateSlicer\CertificateSlicer.exe" (
    start "" "%~dp0dist\CertificateSlicer\CertificateSlicer.exe"
) else (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m certslice
)
exit
