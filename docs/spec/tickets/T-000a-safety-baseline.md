---
title: "T-000a Safety baseline (human)"
id: T-000a
date: 2026-10-15
tags: [ticket, setup, safety]
when: Selalu pertama. Membuat folder proyek, repo privat, mode izin aman, dan menyalin file repo-root sebelum sesi agent mana pun.
status: todo
owner: human
kind: setup
phase: 0
requirements: [R-00, R-22]
depends_on: []
parallel: false
role: human
effort: low
score: 2
tests_first: false
risk: high
---

# T-000a Safety baseline (human)

The ticket and requirements win over any generic "write less code" or brevity heuristic; apply such heuristics only to implementation choices inside this ticket's scope.

Kenapa: agent pernah menghapus drive, menghapus database produksi dan membocorkan `.env`. Instruksi ke model tidak cukup; yang melindungi adalah mode izin, deny list dan kredensial terpisah. Menu Antigravity berubah tiap bulan: cocokkan nama menu dengan aplikasi (dicek terakhir 2026-10-05).

## Goal
Proyek ada di folder sendiri dengan remote git privat yang sudah di-push, executor berjalan di mode izin terbatas, dan file `repo-root/` sudah ada di root repo.

## Context to read first
- `docs/spec/AGENTS.md`
- `docs/spec/repo-root/AGENTS.md`
- `docs/spec/05-tooling.md` (bagian Human-only steps)

## Files to create or modify
- Root repo: salinan isi `docs/spec/repo-root/` (AGENTS.md, pyproject.toml, requirements*.txt, scripts/, src/certslice/default_config.toml, tests/acceptance/, templates/)
- `.agents/skills/`: empat skill executor dari kit (`ticket-executor`, `ticket-reviewer`, `repo-mapper`, `diagnose-failure`)
- `.gitignore` (tambahkan isi `gitignore.additions`)

## Files NOT to touch
- Apa pun di luar folder proyek. Jangan ubah setelan git global atau sistem.

## Canonical example
Deny rule berbentuk `action(target)`, misalnya `command(git push --force)`. Deny menang atas Ask, Ask menang atas Allow.

## Steps
- [ ] 0. Cek prasyarat di terminal BARU: `git --version` dan `python --version` (harus Python 3.13.x; Anda punya 3.13.13). Kalau `python` membuka Microsoft Store: Settings > Apps > Advanced app settings > App execution aliases, matikan `python.exe` dan `python3.exe`, buka terminal baru lagi.
- [ ] 1. Buat folder di path pendek, bukan di OneDrive/Dropbox, contoh `C:\dev\cert-slicer`. Ekstrak ZIP pack di sana sehingga ada `C:\dev\cert-slicer\docs\spec\INDEX.md`. (Atau pakai prompt bootstrap kit, Bagian A lalu Bagian B; kalau begitu langkah 1, 3b dan `git init` sudah dikerjakan, cukup cek filenya ada.)
- [ ] 2. Buat repo PRIVAT di GitHub (jangan publik: nanti ada data pegawai di mesin ini). Lalu, satu perintah per baris: `git init`, `git add -A`, `git commit -m "chore: initial spec pack"`, `git branch -M main`, `git remote add origin <URL>`, `git push -u origin main`.
- [ ] 3. Salin file repo-root (PowerShell, satu per baris): `Copy-Item -Recurse -Force docs\spec\repo-root\* .` lalu `Get-Content gitignore.additions | Add-Content .gitignore` (lewati jika `.gitignore` sudah berisi `# --- planner kit ---`), lalu `Remove-Item gitignore.additions`.
- [ ] 3b. Salin skill executor: `Copy-Item -Recurse -Force <kit>\antigravity\.agents\skills\ticket-executor .agents\skills\` dan sama untuk `ticket-reviewer`, `repo-mapper`, `diagnose-failure` (buat folder `.agents\skills` dulu dengan `New-Item -ItemType Directory -Force .agents\skills`). Jangan masukkan `.agents/` ke `.gitignore`. Di Antigravity, `/skills` harus menampilkan keempatnya.
- [ ] 4. Di Antigravity pilih preset izin **Request Review** (Windows). JANGAN Turbo / Always Proceed, jangan pernah flag yang melewati izin.
- [ ] 5. Tambahkan deny list di pengaturan izin Antigravity (UI atau `settings.json` bagian `permissions.deny`):
  - `command(regex:(?i)\b(Remove-Item|ri|rm)\b.*-r)`, `command(regex:(?i)rd\s+/s)`, `command(regex:(?i)del\s+/[sfq])`, `command(Format-Volume)`, `command(rmdir)`, `command(format)`
  - `command(regex:(?i)\bgit\s+(clean|reset\s+--hard|push\s+(-f|--force)))`, `command(git push --force)`
  - `command(regex:(?i)collect_evidence)`, `command(regex:(?i)--no-guard)`, `command(regex:(?i)\bgit\s+(switch|checkout|stash|update-ref|branch\s+-[dDmM]|tag\s+-[dfa]))`
  - `command(regex:(?i)(curl|iwr|Invoke-WebRequest).*\|\s*(sh|bash|iex))`
  - `command(regex:(?i)\b(cat|type|gc|Get-Content)\b.*\.env)`, `read_file(.env*)`, `read_file(~/.ssh)`
  - `read_file(samples/)` (data asli: agent tidak perlu membacanya; Anda yang menjalankan pilot)
  Pola ini belum diuji pada semua bentuk perintah Windows: anggap jaring pengaman, bukan jaminan.
- [ ] 6. Tidak ada kredensial di M1. Jangan simpan kunci API apa pun di folder ini.
- [ ] 7. Latihan pemulihan: `git clone <URL> ..\_restore_test`, cek isinya, lalu hapus folder `..\_restore_test` lewat File Explorer.
- [ ] 8. Di repo ini saja: `git config core.longpaths true`. Cek `python scripts/collect_evidence.py --help` mencetak cara pakai.
- [ ] 9. Commit: `git add -A` lalu `git commit -m "chore: baseline"` lalu `git push`.
- [ ] 10. Hafalkan rutinitas per tiket (cara membatalkan sesi yang buruk): (a) `python scripts/collect_evidence.py --start T-xxx` SEBELUM sesi (perlu working tree bersih); (b) jalankan tiket di sesi Antigravity BARU; (c) `python scripts/collect_evidence.py --bundle T-xxx`, tempel blok itu ke chat planner; (d) jalankan sendiri perintah Verification tiket dan tempel dua baris terakhirnya; (e) `--accept T-xxx` jika lulus atau `--abandon T-xxx` jika gagal. Executor tidak pernah menjalankan skrip ini.

## Acceptance criteria
- AC1: `git ls-remote origin` mencetak hash commit.
- AC2: `git check-ignore samples/x.pdf` mencetak `samples/x.pdf`.
- AC3: `AGENTS.md`, `scripts/run_check.py`, `tests/acceptance/conftest.py` dan `src/certslice/default_config.toml` ada di root repo.
- AC4: Preset izin bukan Turbo atau Always Proceed.
- AC5: `python --version` mencetak Python 3.13.x di terminal baru.
- AC6: `python scripts/collect_evidence.py --help` mencetak cara pakai dan exit 0.

## Verification
Jalankan dan bandingkan:
```
git ls-remote origin
git check-ignore samples/x.pdf
python --version
python scripts/run_check.py --name smoke -- python --version
python scripts/collect_evidence.py --help
```
Expected: hash; `samples/x.pdf`; Python 3.13.x; `EXIT=0` dan `RESULT: PASS`; teks usage.

## Stop conditions
- Jika satu langkah gagal dua kali, berhenti dan tempel error persisnya ke planner.
- Jika tidak bisa memastikan preset izin atau deny list, jangan mulai executor.
- Jika ada file di luar folder proyek yang akan berubah, berhenti. Jangan menebak nama menu.

## Report format
Tempel ke "Execution report": kotak yang dicentang, output perintah Verification, nama preset izin, hal yang berbeda dari tiket.

## Constraints (repeated)
- Repo privat saja; tidak ada data asli atau rahasia di file yang di-commit.
- Executor: Request Review. Jangan pernah Turbo atau flag lewati-izin.
- Jangan mulai executor sebelum semua kotak di atas dicentang.

## Execution report
Status: not started.

Related: [requirements](../01-requirements.md), [tickets index](INDEX.md)
