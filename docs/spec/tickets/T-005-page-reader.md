---
title: "T-005 Page reader interface and PDF input checks"
id: T-005
date: 2026-10-15
tags: [ticket]
when: Adds reader.py: open_pdf with clear errors and LocalPageReader (text layer); pipeline accepts any PageReader.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-01, R-17, R-19]
depends_on: [T-004]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: false
risk: normal
---

# T-005 Page reader interface and PDF input checks

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
`pipeline.run(..., reader=...)` reads every page through a `PageReader`; the default `LocalPageReader` uses the text layer; corrupt, encrypted or empty PDFs stop with exit 2 before anything is written.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 2 only)
- `docs/spec/modules/interface.md` (section 2 only)
- `tests/acceptance/test_pdf_input.py` and `tests/acceptance/test_reader_hook.py` (read only)
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/reader.py` (create)
- `src/certslice/pipeline.py` (modify: open_pdf, reader, lines per page)
- `tests/unit/test_reader.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
def open_pdf(path: Path) -> "pymupdf.Document":
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise InputError(f"cannot open PDF {path}: {exc}") from exc
    if doc.needs_pass or doc.is_encrypted:
        raise InputError(f"PDF {path} is encrypted (password protected)")
    if doc.page_count == 0:
        raise InputError(f"PDF {path} has no pages")
    return doc

class LocalPageReader:
    def __init__(self, cfg: dict, ocr: bool = True): ...
    def read(self, page) -> PageText: ...   # text layer now; T-018 adds OCR
```

## Steps
1. Write `reader.py` with `open_pdf` exactly as in the example and `LocalPageReader.read`: `text = page.get_text("text")`; if `len(text.strip()) >= cfg["reader"]["min_text_chars"]` return `PageText(page.number + 1, text, "text")`, else `PageText(page.number + 1, text, "none")`. Store `ocr` on the instance (unused until T-018).
2. In `pipeline.run`: after loading employees call `doc = open_pdf(Path(pdf_path))`; `rdr = reader or LocalPageReader(cfg, ocr)`; for each page keep `PageText` and `lines = [l for l in pt.text.splitlines() if l.strip()]`; plan.json `pages[].source` is `pt.source`. Matching uses `pt.text`. All input errors still happen before the run folder exists. Close the document at the end.
3. Write `tests/unit/test_reader.py`: a text page (build one with `pymupdf.open()`, `new_page()`, `insert_text`) gives source `text`; an empty page gives `none`; `open_pdf` raises `InputError` for `tests/fixtures/corrupt.pdf` and `tests/fixtures/encrypted.pdf`.
4. Run the Verification. Commit: `git add -A` then `git commit -m "T-005: page reader"`.

## Acceptance criteria
- AC1: Corrupt, encrypted and missing PDFs exit 2 with nothing written (R-19).
- AC2: A fake reader passed to `pipeline.run` drives the run and its `source` appears in plan.json (R-17).
- AC3: All earlier acceptance tests still pass (R-01).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t005 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-005
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

## Stop conditions
- If a verification fails twice, stop and report; do not keep guessing.
- If a file outside the list above is needed, stop and report.
- If a named API, package, command or path does not exist, stop and report; do not invent one.
- If an acceptance test seems wrong, stop and report the test name and the failing assertion; never edit or skip it (the planner fixes test defects).
- If the plan is impossible or contradicts the requirements, stop and report; never deviate from the plan.

## Report format
Append to the "Execution report" section below (this format is mandatory and is not subject to any terse or brevity style):
- Ticket, status (done / blocked / failed)
- Files changed (path, created or modified)
- Commands run, each with exit code and the final PASS/FAIL line
- Each acceptance criterion: pass or fail with the evidence
- Deviations from the plan (none, or list)
- Open issues and questions for the planner

## Constraints (repeated)
- Touch only the files listed above. Never edit `tests/acceptance/**`, `tests/fixtures/**`, `scripts/**`, `src/certslice/default_config.toml` or `docs/spec/**` beyond this ticket's report.
- No new dependencies, no network calls, no real data, no secrets. Read thresholds and word lists from the config dict, never hard-code them.
- Never say done until the verification commands ran in this session and their exit codes and final lines are pasted.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [verification](../04-verification.md), [tickets index](INDEX.md)
