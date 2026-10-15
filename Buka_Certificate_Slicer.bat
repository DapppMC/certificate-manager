@echo off
title Certificate Slicer
echo Membuka Certificate Slicer...
if exist "%~dp0dist\CertificateSlicer\CertificateSlicer.exe" (
    start "" "%~dp0dist\CertificateSlicer\CertificateSlicer.exe"
) else (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m certslice
)
exit
