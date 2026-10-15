---
title: "T-017 Uji OCR pada 3 halaman scan asli (human)"
id: T-017
date: 2026-10-15
tags: [ticket, human, spike]
when: Setelah T-016 dan T-000c. Anda menilai apakah RapidOCR cukup baik pada scan asli sebelum OCR dipasang ke mesin (T-018).
status: todo
owner: human
kind: test
phase: 1
requirements: [R-02]
depends_on: [T-016, T-000c]
parallel: false
role: human
effort: low
score: 1
tests_first: false
risk: normal
---

# T-017 Uji OCR pada 3 halaman scan asli (human)

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Ada keputusan tertulis: OCR lokal (RapidOCR) LULUS atau GAGAL pada 3 halaman scan asli. Di sandbox planner (2026-10-15), RapidOCR membaca bundel BNSP asli Anda dengan sangat baik (sekitar 3,5 detik per halaman). Tiket ini memastikan hal yang sama di laptop Windows Anda dan pada bundel scan lain.

## Context to read first
- `docs/spec/tickets/T-016-ocr-spike-script.md` (cara pakai skrip)

## Files to create or modify
- Tidak ada file yang di-commit. Output masuk ke `spikes/output/` (diabaikan git).

## Files NOT to touch
- Semua file di repo. Jangan salin teks hasil OCR ke chat (berisi data pribadi).

## Canonical example
Output yang baik terlihat seperti: `PAGE 1 seconds=3.2`, lalu baris-baris seperti `0.987  Diberikan kepada:` dan `0.954  <NAMA PEGAWAI>`, diakhiri `RESULT: PASS`.

## Steps
- [ ] 1. Pilih 3 halaman scan asli dari `samples/` yang paling umum (bukan yang paling bagus): misalnya 2 halaman dari satu bundel scan dan 1 dari bundel lain.
- [ ] 2. Jalankan di terminal Anda sendiri (bukan lewat agent), satu per baris, ganti nama file dan halaman:
  `.\.venv\Scripts\python.exe spikes/ocr_spike.py --pdf samples\<bundel-scan>.pdf --pages 1,2`
  `.\.venv\Scripts\python.exe spikes/ocr_spike.py --pdf samples\<bundel-lain>.pdf --pages 1`
- [ ] 3. Buka file di `spikes/output/` dan bandingkan dengan halaman aslinya. Untuk tiap halaman, centang mana yang terbaca benar: nama pemegang, nomor sertifikat, tanggal terbit, tanggal berlaku.
- [ ] 4. Jika nama terbaca tetapi buruk, ulangi sekali dengan `--dpi 300` dan catat bedanya.
- [ ] 5. Keputusan: LULUS jika nama pemegang DAN minimal satu tanggal terbaca benar pada minimal 2 dari 3 halaman; selain itu GAGAL.

## Acceptance criteria
- AC1: Laporan berisi detik per halaman, hasil centang per halaman, dpi yang dipakai, dan keputusan LULUS/GAGAL.

## Verification
```
.\.venv\Scripts\python.exe spikes/ocr_spike.py --pdf tests/fixtures/bundle_scan.pdf --pages 1
```
Expected: berakhir `RESULT: PASS` (memastikan skrip jalan di laptop Anda sebelum mencoba scan asli).

## Stop conditions
- Jika suatu langkah gagal dua kali, berhenti dan tanya planner (tempel error persisnya, tanpa data pribadi).
- Jika skrip error, tempel baris error terakhir ke planner (tanpa isi sertifikat).
- Jika GAGAL, jangan mulai T-018: planner akan menulis ADR untuk alternatif (Tesseract atau Windows OCR) atau menaikkan prioritas M3.

## Report format
Tempel ke "Execution report": detik per halaman, centang per field per halaman, dpi, keputusan. Jangan tempel nama atau NID.

## Constraints (repeated)
- Data asli tidak keluar dari `samples/` dan `spikes/output/`; keduanya tidak di-commit.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [tickets index](INDEX.md)
