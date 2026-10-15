# Certificate Slicer
Splits bundled certificate PDFs, matches holders to database.xlsx and names the files (local, offline).
Setup: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt ; .\.venv\Scripts\python.exe -m pip install -e .
Run: .\.venv\Scripts\python.exe -m certslice run --pdf <bundle.pdf> --db <database.xlsx> --out <folder>
Checks: .\.venv\Scripts\python.exe scripts/stage_tests.py --all
Spec: docs/spec/INDEX.md
