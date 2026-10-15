---
title: "T-020 Pilot dengan data asli dan cek kill criteria (human)"
id: T-020
date: 2026-10-15
tags: [ticket, human, pilot]
when: Tiket terakhir M1. Anda menjalankan mesin pada bundel asli, mengukur akurasi dengan gold set, dan memutuskan lanjut ke M2, memperbaiki, atau pivot.
status: todo
owner: human
kind: test
phase: 1
requirements: [R-18, R-20]
depends_on: [T-018, T-019, T-000c]
parallel: false
role: human
effort: medium
score: 2
tests_first: false
risk: high
---

# T-020 Pilot dengan data asli dan cek kill criteria (human)

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Angka akurasi nyata per bundel (teks dan scan dipisah), waktu proses, dan keputusan berdasarkan kill criteria di brief.

## Context to read first
- `docs/spec/00-brief.md` (bagian Success metrics dan Kill criteria)
- `docs/spec/03-workflows.md` (Flow 1 dan Flow 3)

## Files to create or modify
- Tidak ada file yang di-commit. Hasil masuk ke `out/` (diabaikan git).

## Files NOT to touch
- Semua file di repo. Jangan menempel nama, NID atau isi sertifikat ke chat.

## Canonical example
Baris yang ditempel ke planner untuk satu bundel:
```
BUNDLE B1 pages=34 seconds=41 PAGES=34 ACCEPTED=29 UNMATCHED=4
SET text certificates=30 correct=28 pct=93.3
SET scan certificates=0 correct=0 pct=n/a
FIELD title text=26/30 scan=0/0
FIELD number text=29/30 scan=0/0
FIELD issue_date text=28/30 scan=0/0
FIELD expiry_date text=25/30 scan=0/0
WRONG_NID=0
```

## Steps
- [ ] 1. Untuk tiap bundel di `samples/`, di terminal Anda sendiri (satu per baris), ukur waktunya:
  `Measure-Command { .\.venv\Scripts\python.exe -m certslice run --pdf samples\<bundel>.pdf --db samples\database.xlsx --out out | Out-Default }`
  Catat detik total dan baris `PAGES=...`, serta `RUN_DIR=...`.
- [ ] 2. Jalankan evaluasi: `.\.venv\Scripts\python.exe -m certslice evaluate --run <RUN_DIR> --gold samples\<bundel>.gold.csv`.
- [ ] 3. Jika database asli gagal dibaca (exit 2, kolom tidak ditemukan), catat nama kolom header saja dan berhenti: planner menambahkan alias kolom di config.
- [ ] 4. Buka `unmatched_report.xlsx` dan sheet `needs_review`; tuliskan 3 alasan terbanyak (misalnya "homonym", "no confident holder") dan pola yang Anda lihat (tanpa data pribadi).
- [ ] 5. Buka beberapa PDF hasil dan cocokkan nama file dengan isinya (spot check 10 file).
- [ ] 6. Bandingkan dengan kill criteria: (a) ada `WRONG_NID` > 0 di bundel mana pun = JANGAN rilis; (b) SET text di bawah 80% = putaran perbaikan (maksimal 2), lalu aktifkan M3; (c) satu bundel 30 halaman bertext layer lebih dari 5 menit = laporkan.

## Acceptance criteria
- AC1: Untuk setiap bundel ada blok seperti contoh di atas di laporan.
- AC2: Keputusan tertulis: lanjut M2, putaran perbaikan, atau aktifkan M3.

## Verification
```
.\.venv\Scripts\python.exe scripts/run_check.py --name all --fail-on "no tests ran" -- python scripts/stage_tests.py --all
```
Expected: `EXIT=0` dan `RESULT: PASS` (mesin yang dipakai pilot lulus semua tes penerimaan).

## Stop conditions
- Jika `WRONG_NID` lebih dari 0, berhenti dan laporkan nomor halaman serta alasan (tanpa nama); jangan pakai hasilnya.
- Jika suatu langkah gagal dua kali, tempel baris error terakhir ke planner.

## Report format
Tempel ke "Execution report": blok per bundel, 3 alasan terbanyak, hasil spot check, keputusan. Tanpa data pribadi.

## Constraints (repeated)
- Data asli tetap di `samples/` dan `out/`; tidak di-commit, tidak ditempel ke chat.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [brief](../00-brief.md), [tickets index](INDEX.md)
