---
title: "T-000c Siapkan sampel asli dan gold set (human)"
id: T-000c
date: 2026-10-15
tags: [ticket, human, data]
when: Bisa dikerjakan kapan saja setelah T-000a, paralel dengan tiket executor; dibutuhkan oleh T-017 dan T-020.
status: todo
owner: human
kind: setup
phase: 0
requirements: [R-18]
depends_on: [T-000a]
parallel: true
role: human
effort: medium
score: 2
tests_first: false
risk: normal
---

# T-000c Siapkan sampel asli dan gold set (human)

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

## Goal
Folder `samples/` (diabaikan git) berisi bundel PDF asli, `database.xlsx` asli dan `gold.csv` per bundel yang Anda isi dengan tangan, supaya akurasi bisa dibuktikan di T-020.

## Context to read first
- `docs/spec/modules/interface.md` (bagian 8, format gold.csv)
- `templates/gold.template.csv`

## Files to create or modify
- `samples/` (folder baru; JANGAN di-commit, sudah ada di `.gitignore`)
- `samples/<nama-bundel>.pdf`, `samples/database.xlsx`, `samples/<nama-bundel>.gold.csv`

## Files NOT to touch
- Semua file lain. Jangan pernah menyalin data asli ke `tests/` atau ke chat.

## Canonical example
Satu baris gold untuk sertifikat yang mulai di halaman 4: `4,0012345,Teknisi Gardu Distribusi,DJK.0701.2025,17/08/2025,17/08/2030`. Kolom NID dikosongkan bila orangnya memang tidak ada di database (tool seharusnya TIDAK menerima siapa pun).

## Steps
- [ ] 1. Buat folder `samples` di root repo. Cek: `git check-ignore samples/x.pdf` mencetak `samples/x.pdf`.
- [ ] 2. Salin 3 sampai 6 bundel asli dari penerbit yang BERBEDA (LSP, DJK, Kemnaker, CorpU bila ada), minimal 1 bundel yang berisi sertifikat 2 halaman dan minimal 1 hasil scan (tanpa teks yang bisa diblok). Total sekitar 30 sampai 50 sertifikat.
- [ ] 3. Salin `database.xlsx` asli ke `samples/database.xlsx`. Catat nama kolom NID dan nama di baris header (misalnya `NID`, `NAMA`). Jika namanya tidak ada di daftar `[database]` pada `src/certslice/default_config.toml`, tulis nama kolomnya di laporan (nama kolom saja, bukan isinya) supaya planner menambahkannya.
- [ ] 4. Untuk tiap bundel, salin `templates/gold.template.csv` menjadi `samples/<nama-bundel>.gold.csv`, hapus baris contoh, lalu isi satu baris per SERTIFIKAT: halaman pertamanya, NID yang benar (cari manual di Excel), judul, nomor, tanggal terbit dan tanggal expired seperti tercetak (format DD/MM/YYYY; kosongkan bila tidak ada). Simpan sebagai CSV UTF-8.
- [ ] 5. Catat juga, untuk bahan planner (tanpa data pribadi): berapa bundel, berapa halaman, berapa yang scan, berapa sertifikat 2 halaman, dan apakah template antar-penerbit terlihat berulang.
- [ ] 6. Pastikan `git status` TIDAK menampilkan apa pun dari `samples/`.

## Acceptance criteria
- AC1: `samples/` berisi minimal 3 bundel, `database.xlsx` dan satu `.gold.csv` per bundel.
- AC2: `git status --porcelain samples` tidak mencetak apa-apa.

## Verification
```
git check-ignore samples/database.xlsx
git status --porcelain samples
```
Expected: baris pertama mencetak `samples/database.xlsx`; perintah kedua tidak mencetak apa-apa.

## Stop conditions
- Jika suatu langkah gagal dua kali, berhenti dan tanya planner (tempel error persisnya, tanpa data pribadi).
- Jika `git status` menampilkan file dari `samples/`, berhenti dan jangan commit; tanya planner.
- Jika atasan tidak mengizinkan data asli dipakai di laptop ini, berhenti dan beri tahu planner (rencana pindah ke data samaran).

## Report format
Tempel ke "Execution report": jumlah bundel, halaman, scan, sertifikat 2 halaman, nama kolom header database, pengamatan soal template, output Verification. Jangan tempel nama, NID atau isi sertifikat.

## Constraints (repeated)
- Data asli hanya di `samples/`, tidak pernah di-commit, tidak pernah ditempel di chat.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [tickets index](INDEX.md)
