---
title: "T-018 OCR fallback in the page reader"
id: T-018
date: 2026-10-15
tags: [ticket]
when: Adds ocr.py; LocalPageReader OCRs pages without a text layer and merges OCR into sparse pages over a background picture.
status: todo
owner: executor
kind: feature
phase: 1
requirements: [R-02, R-20, R-22]
depends_on: [T-015, T-017]
parallel: false
role: backend
effort: medium
model_tier: standard
score: 3
tests_first: false
risk: normal
---

# T-018 OCR fallback in the page reader

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Scanned pages are read by the bundled RapidOCR (lazily, one engine per run) with source `ocr`, and plan.json records OCR seconds per page.

## Context to read first
- `AGENTS.md`
- `docs/spec/modules/extraction-rules.md` (section 2 only)
- `spikes/ocr_spike.py` (as the usage example)
- `tests/acceptance/test_ocr.py` (read only)
- `src/certslice/reader.py`
- `src/certslice/pipeline.py`

## Files to create or modify
- `src/certslice/ocr.py` (create)
- `src/certslice/reader.py` (modify: OCR fallback)
- `src/certslice/pipeline.py` (modify: pass OCR times to timings)
- `tests/unit/test_ocr.py` (create)

## Files NOT to touch
- `docs/spec/**` (except this ticket's `status:` line and Execution report), `scripts/**`, `tests/acceptance/**`, `tests/fixtures/**`, `src/certslice/default_config.toml`, `pyproject.toml`, `requirements*.txt`, `AGENTS.md`, unit test files of earlier tickets, and every file not listed above.

## Canonical example
```python
class OcrEngine:
    def __init__(self, dpi: int):
        self.dpi = dpi
        self._engine = None
        self.seconds: list[float] = []

    def page_text(self, page) -> str:
        if self._engine is None:
            from rapidocr import RapidOCR
            self._engine = RapidOCR()
        t = time.perf_counter()
        result = self._engine(page.get_pixmap(dpi=self.dpi).tobytes("png"))
        self.seconds.append(time.perf_counter() - t)
        return "\n".join(result.txts or [])
```

## Steps
1. Write `ocr.py` as the example.
2. In `LocalPageReader`: when OCR is on (`ocr` argument and `cfg["reader"]["ocr_enabled"]`) and the text layer is too short, call `OcrEngine.page_text`; source `ocr` when the result reaches `min_text_chars`, else `none`; any exception from OCR gives source `none` and empty text (log a WARNING with the page number). Expose `reader.ocr_seconds` (the engine's list, or `[]`).
3. Add the sparse-page rule of extraction-rules section 2: `image_cover(page)` = sum of the areas of `pymupdf.Rect(info["bbox"]) & page.rect` over `page.get_image_info()`, divided by the page area, capped at 1.0; when the text layer has at least `min_text_chars` but fewer than `ocr_sparse_below_chars` characters and `image_cover >= ocr_sparse_image_cover`, OCR the page; text = OCR lines, then the text-layer lines whose folded form has `rapidfuzz.fuzz.ratio` below 90 against every folded OCR line; source `mixed`; on OCR failure keep the text layer with source `text`.
4. In `pipeline.run` use `getattr(rdr, "ocr_seconds", [])` for `timings`.
5. Write `tests/unit/test_ocr.py`: a reader with `ocr=False` on an image-only page gives `none`; an OcrEngine whose `_engine` is replaced by a fake callable returning an object with `txts=("A", "B")` gives `A\nB`.
6. Run the Verification (OCR tests take about 30 to 60 seconds). Commit: `git add -A` then `git commit -m "T-018: OCR in reader"`.

## Acceptance criteria
- AC1: `bundle_scan.pdf` pages have source `ocr` and NIDs 0044444 and 8812345; `ocr_seconds_per_page` is above 0 (R-02, R-20).
- AC2: `bundle_k3_like.pdf` pages have source `mixed`; title `TEKNISI K3 LISTRIK`, number, issue 2026-10-12 and expiry 2029-10-12 are filled, and the cue line keeps the signatory `Sudarmaji` out (R-02).
- AC3: With `--no-ocr` both pages are unmatched with reason `no text`.
- AC4: An OCR run with sockets blocked succeeds (R-22).

## Verification
Run exactly these (PowerShell, repo root) and paste the last two lines of each:
```
.\.venv\Scripts\python.exe scripts/run_check.py --name t018 --fail-on "no tests ran" -- python scripts/stage_tests.py --upto T-018
python scripts/run_check.py --name secrets -- python scripts/scan_secrets.py
```
Expected: every command ends with `EXIT=0` and `RESULT: PASS`. `--upto` also re-runs the acceptance files of all earlier tickets and every unit test: they must still pass. If the output looks truncated or ambiguous, re-run the raw command (without any output filter) and report its exit code.

## Stop conditions
- If a verification fails twice, stop and report; do not keep guessing.
- If a file outside the list above is needed, stop and report.
- If a named API, package, command or path does not exist, stop and report; do not invent one.
- If an acceptance test seems wrong, stop and report the test name and the failing assertion; never edit or skip it (the planner fixes test defects).
- If the plan is impossible or contradicts the requirements, stop and report; never deviate from the plan.
- If T-017's report says the spike FAILED on real scans, do not start: the planner replans OCR.

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
