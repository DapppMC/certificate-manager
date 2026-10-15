---
title: Extraction and matching rules
date: 2026-10-15
tags: [spec, module, pipeline, algorithm]
when: When a ticket implements normalisation, matching, grouping, field extraction, expiry or file naming; these are the exact rules the acceptance tests encode.
---

# Extraction and matching rules

Serves: R-02 to R-08, R-10, R-15, R-16. All numbers and word lists come from `src/certslice/default_config.toml` (named here as `[table] key`); never hard-code them. "Fold" = Unicode NFKD, drop combining marks, `str.casefold()`. "Phrase match" = the folded phrase occurs in the folded text with no letter or digit directly before or after it (regex `(?<![a-z0-9])<escaped phrase>(?![a-z0-9])`). These rules were proven by a throwaway planner implementation (2026-10-15) on the synthetic fixtures AND on two real bundles from the user (a text-layer K3 training bundle of 8 pages and a scanned BNSP bundle of 12 pages, 5,088-row master data): 11 of 14 certificates accepted, the 3 others reported (2 shortened names with the right employee as candidate 1, 1 person not in the master data), no wrong NID seen. See [ADR-008](../decisions/ADR-008-rules-tuned-on-real-samples.md).

## 1. Name normalisation (`normalize.py`)
`normalize_name(s)`: fold; delete `.` and `'`; replace every run of characters other than `a-z` and `0-9` with one space; split on spaces; drop tokens that are in `[matching] titles`; join with one space. Examples: `Ir. BUDI SANTOSO, S.T., M.M.` -> `budi santoso`; `ANDRÉAS SIREGAR` -> `andreas siregar`; `Budi-Santoso` -> `budi santoso`. The same function makes the token list of each page line.

## 2. Reading pages (`reader.py`, `ocr.py`)
- Text layer: `page.get_text("text")`. If its stripped length is at least `[reader] min_text_chars`, source is `text`.
- Otherwise, when OCR is on (`[reader] ocr_enabled` and no `--no-ocr`): render `page.get_pixmap(dpi=[reader] ocr_dpi).tobytes("png")`, pass the bytes to one shared `rapidocr.RapidOCR()` engine (create it once per run, lazily), text = `"\n".join(result.txts or [])`; source `ocr` if that text reaches `min_text_chars`, else `none`. Any OCR exception -> source `none`, empty text. Record seconds per OCR'd page.
- Sparse page (labels drawn in a background picture): when OCR is on, the text layer has at least `min_text_chars` but fewer than `[reader] ocr_sparse_below_chars` characters, and the summed area of the page's images (`page.get_image_info()` bboxes clipped to the page, capped at 1.0) is at least `ocr_sparse_image_cover` of the page: OCR the page too. Text = the OCR lines, then every text-layer line whose folded form has `fuzz.ratio` below 90 against every folded OCR line; source `mixed`. If OCR fails, use the text layer (source `text`).
- OCR off and no text layer: source `none`, keep whatever little text there was.
- A page's lines = non-empty lines of its text, in order.

## 3. Candidates on a page (`matcher.py`)
For every employee with normalised name of k tokens (skip k = 0) and every line (token list `t`):
- k below `[matching] min_tokens_fuzzy`: score 100 if the name equals one token of the line, else 0.
- else: score = max of `rapidfuzz.fuzz.ratio(name, " ".join(t[s:s+w]))` over window sizes w in {k-1, k, k+1} (1 <= w <= len(t)) and all start positions s.
- Shortened name (only when the score above is below `accept_score` and k >= 2): the employee is a "partial" candidate with score `[matching] partial_score` (90, never accepted) when some run of line tokens starts with the employee's FIRST token and then, walking both lists in order, each further line token equals the next employee token, or is a one-letter initial of it, or equals a later employee token (the tokens in between were omitted); the run must contain at least 2 full-word matches and fewer full-word matches than k. Examples: `HANA PERTIWI` for `Hana Pertiwi Kusumawardani`; `SRI AYU DEWI K` for `Sri Ayu Dewi Kartika`.
An employee's page score is its best line score; keep the line index of that best score and the partial flag. Keep candidates scoring at least `[matching] list_score`.
Subsumption: drop a candidate X when another non-partial candidate Y on the same line scores at least `accept_score`, Y's normalised name has more tokens, and `" " + X name + " "` occurs in `" " + Y name + " "` (one-word employee `Sutrisna` inside `Ilham Sutrisna`; 610 one-word names exist in the real master data). For speed with large databases group employees by k and use `rapidfuzz.process.cdist(names, windows, scorer=fuzz.ratio, score_cutoff=list_score)`; results must equal the loop above.

## 4. Template and signatory names (bundle level)
Count, per employee, the pages where it is a candidate (score at least `list_score`, after subsumption). If that count is at least `max(template_min_pages, ceil(template_min_share * page_count))`, the employee is a template name: removed from the candidates of EVERY page of the bundle and listed (by `nama`, sorted) in `plan.json` `template_names`. Known limit: a bundle where one person holds every certificate goes to the report (safe).

## 5. Holder cue filter (per page)
Cue lines = lines with a phrase match of any `[matching] holder_cues`. If there is a cue line and at least one remaining candidate scoring at least `accept_score` whose best line index is between a cue line index and that index + `cue_window_lines` (inclusive), drop every candidate outside such windows. Otherwise keep all. Then sort candidates by score (high first), ties by database row order.

## 6. Decision (per page)
With sorted candidates c1, c2:
1. No candidate -> unmatched, reason `no confident holder`. c1 below `accept_score` -> unmatched, reason `partial name` when c1 is a partial candidate, else `no confident holder`.
2. c2 exists and has the same normalised name as c1 -> unmatched, reason `homonym`.
3. c2 exists and c1 - c2 is below `margin` -> unmatched, reason `close candidates`.
4. Otherwise accepted: NID, nama, email, unit come from c1's database row.
A page is a "start page" when c1 exists and scores at least `accept_score` (whatever the decision).

## 7. Grouping (`grouping.py`)
Certificate number of a page = section 9 rule on that page's lines. Continuation sign = any `[grouping] continuation_patterns` regex matches the folded page text.
Auto mode, pages in order; `prev` = last group, `first` = prev's first page:
- start page: append to prev only if prev has exactly 1 page, prev's size is below `max_pages_per_cert`, both this page and `first` are accepted with the same NID, and (continuation sign, or same number as `first`, or this page has no number). Else start a new group.
- not a start page: append to prev if prev's size is below `max_pages_per_cert` and (continuation sign or same number as `first`, a number must exist to be "same"). Else start a new group.
Group decision = decision of its first page; if unmatched and the first page source is `none`, reason becomes `no text`.
Fixed mode N (`--pages-per-cert 1|2`): consecutive chunks of N pages (last chunk may be shorter). Accepted only if the chunk has at least one start page, every start page in it is accepted, and they all have the same NID; else unmatched with reason `fixed chunk without one clear holder` (`no text` when every page source is `none`). Candidates come from the first start page (or the first page).
Then check: the groups cover pages 1..N exactly once, else `AccountingError`.

## 8. Template lines (bundle level, for fields)
Fold and collapse spaces in every line. If the bundle has at least 3 pages, a line is a template line when it appears on at least `[fields] template_line_share` of the pages.

## 9. Fields per group (`fields.py`)
Lines of the group = lines of all its pages in order; first-page lines = lines of its first page.
- **Number**: for each line, for each match of a whole-word `number_keywords` entry (longest first, case-insensitive): after it skip spaces and `.:#` and any further number keywords or the words `sertifikat`, `certificate`; the next token of `[A-Za-z0-9][A-Za-z0-9./-]{3,}`, stripped of trailing `.-/`, is the number if it contains a digit. First hit on the first page wins (`Nomor Registrasi: DJK.0701.2025` -> `DJK.0701.2025`). Confidence high when found, else value null and low.
- **Dates**: patterns on folded text, in this order, a later pattern never re-uses text an earlier one matched: day range `d1 - d2 month yyyy` and `month d1 - d2, yyyy` (dash `-`, en dash or em dash; the date is the LAST day d2), `d month yyyy`, `month d, yyyy`, `yyyy-mm-dd`, `dd/mm/yyyy` (also `-` or `.`). Day numbers may carry an English ordinal suffix `st`, `nd`, `rd`, `th` (`October 05th - 12nd, 2026` -> 2026-10-12; a superscript `ⁿᵈ` folds to `nd`). The contract test is `tests/acceptance/test_dates_contract.py`. Month names: Indonesian and English, full and short (`januari jan january`, `februari feb`, `maret mar march`, `april apr`, `mei may`, `juni jun june`, `juli jul july`, `agustus agu agt ags aug august`, `september sep sept`, `oktober okt oct october`, `november nov nopember`, `desember des dec december`). Invalid dates and years outside 1990..2100 are ignored.
  Scan group lines in order. Skip a line with a phrase match of `ignore_date_markers`. A line containing (folded substring) an `expiry_markers` entry is an expiry line: if it has no date, use the dates of the next line (and skip it). On an expiry line with 2 or more dates: the first is the issue date (only if none yet, confidence medium) and the last is the expiry date (high); with 1 date it is the expiry date (high). Else a line containing an `issue_markers` entry with a date sets the issue date (high). Other dates are "unmarked". If no issue date was set: the first unmarked date, confidence medium if it was the only unmarked date, else low. If expiry is not after issue, expiry becomes null, low. Missing issue date: null, low.
- **Printed duration**: when no printed expiry date was found, an issue date exists and `[fields] duration_pattern` matches the folded group text (`berlaku untuk 3 (tiga) tahun`, `valid for 3 (three) years`): expiry = issue date plus that many years, confidence high, `expiry_source` `duration:<n>y`.
- **Expiry rule** (`expiry.py`): when no printed expiry and no printed duration, take the first `[[validity_rules]]` entry with a phrase match in the folded group text. `years = 0` -> expiry null, confidence medium, `expiry_source` `rule:none`. `years > 0` and an issue date -> issue date plus that many years (29 February becomes 28 February), confidence medium, source `rule:<n>y`. No rule or no issue date -> null, low, source null. Printed expiry -> source `printed`.
- **Title**: title cues sorted longest first; on the first-page lines find the first line whose folded text contains a cue; the rest of that line after the cue, stripped of spaces and `:"'-`, is the title if it has 4 or more characters, is not a `title_generic` entry, and the unstripped rest does not end with `:` (a label such as `Pada Bidang Pekerjaan :`); otherwise the title is the first following first-page line that, stripped of quotes and dashes, has 4 or more characters, does not end with `:`, contains no title cue (skips an English translation line such as `Is Competent for :`) and is not generic. Confidence high when the title is a template line, the bundle has fewer than 3 pages, or the group is the whole bundle; else medium. Without a cue: the first first-page line with a phrase match of a `title_keywords` entry that is not a generic line (medium if it is a template line, else low). Else a generic line (low). Else null, low.
- **Issuer**: for each `issuer_keywords` entry in config order, the first group line whose folded text plus a trailing space contains it (so `pt ` also matches at the end of a line) and that contains no `issuer_exclude` phrase (the employer printed on certificates); when that line, folded, equals an `issuer_join_next` entry, append a space and the next group line (`Lembaga Sertifikasi Profesi` + `Manajemen Sumber Daya Manusia NUSANTARA`); confidence high if that line is a template line, else medium; none found -> null, low.
- **Document type**: `Pelatihan` (medium) if a `training_words` entry phrase-matches the title or the group text; else `Sertifikasi` (medium) if a `certification_words` entry matches; else `Sertifikasi` (low).
- **Renewal**: first match of `[fields] renewal_pattern` in the folded group text -> integer, else null.

## 10. File names (`naming.py`)
- Fields: `nid` and `nama` from the database row; `judul` = alias of the title (section 3 of the interface) or the title, or `[naming] unknown_title` when null; `tahun` = issue year or `[naming] unknown_year`.
- Clean each field: replace `< > : " / \ | ?` `*` and control characters with `-`, replace `_` with `-`, collapse whitespace, strip spaces and dots at both ends.
- Name = `pattern` filled, plus `renewal_suffix` when renewal is set, plus `.pdf`.
- Room = `max_path_chars` - length of the absolute `--out` path - 28 (reserved for `/run-YYYYMMDD-HHMMSS-NN/pdf/`) - 6 (reserved for `_dup99`). While the name is longer than the room and the title is longer than `min_title_chars`, cut the title from the end (then strip spaces and dots). If the name still does not fit, raise `InputError("output folder path is too long ...")` before anything is written.
- Duplicates within the run (case-insensitive): add `duplicate_suffix` with n = 2, 3, ... before `.pdf`; the shortened title is the same for all duplicates.

Related: [interface](interface.md), [design](../02-design.md), [requirements](../01-requirements.md)
