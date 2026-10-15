---
title: Tooling and integrations
date: 2026-10-15
tags: [spec, tooling]
when: To set up the toolchain, see which steps only a human can do, and confirm that no MCP server or external service is needed.
---

# Tooling and integrations

Principles: minimal set; per-project config; nothing secret in M1; the project builds and tests offline once packages are installed. Facts marked "verified on" can go stale; re-check before use.

## Toolchain (pinned)
| Tool | Version | Install check command |
|------|---------|-----------------------|
| Python | 3.13.x (user has 3.13.13) | `python --version` |
| pip (inside `.venv`) | bundled with Python 3.13 | `.venv\Scripts\python.exe -m pip --version` |
| Git | any recent | `git --version` |
| PyMuPDF | 1.28.2 | in `requirements.txt` |
| rapidfuzz | 3.14.6 | in `requirements.txt` |
| openpyxl | 3.1.5 | in `requirements.txt` |
| rapidocr | 3.10.0 (ships its ONNX models; no download at run time) | in `requirements.txt` |
| onnxruntime | 1.31.0 | in `requirements.txt` |
| numpy | 2.5.4 | in `requirements.txt` |
| pytest | 9.1.1 | in `requirements-dev.txt` |

Verified on 2026-10-15: Windows `win_amd64` wheels for Python 3.13 exist for every package above and for their dependencies (`pip download --platform win_amd64 --python-version 3.13 --only-binary=:all:`), and all of them installed and ran on Linux Python 3.13.16 in the planner sandbox. PyInstaller 6.22.3 (M2 only) also has a Windows wheel. Re-check before M2.

Commands (Windows PowerShell 5.1, one per line; every check runs through `python scripts/run_check.py --name <n> -- <cmd>`):
- create env: `python -m venv .venv`
- install: `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt` then `.venv\Scripts\python.exe -m pip install -e .`
- fixtures: `.venv\Scripts\python.exe scripts/make_fixtures.py`
- all tests: `.venv\Scripts\python.exe -m pytest tests -q`
- one acceptance file: `.venv\Scripts\python.exe -m pytest tests/acceptance/test_matching.py -q`
- run: `.venv\Scripts\python.exe -m certslice run --pdf <file> --db <file> --out <folder>`

## Environment variables (names only, values go in .env, which is gitignored)
- None in M1. M3 will add `GEMINI_API_KEY` in a config file beside the app (ADR-005); it is not needed now.

## Integrations
None. The executor needs no MCP server, no browser tool and no external API to build or verify M1: everything is local Python, files and pytest. The optional Gemini layer is a product feature of M3 (ADR-005), not a development integration, and gets its own tooling section when M3 is planned.

## Design route (UI projects)
Not applicable in M1 (`ui: false`). M2 adds a simple GUI; its route is chosen when M2's Gate 2 opens.

## Images and video
None.

## Token-saving tools (executor profile)
Profile: see [executor-profile.md](executor-profile.md). None enabled; the project never depends on one.

## Human-only steps
| Step | Why only a human | Ticket |
|------|------------------|--------|
| Safety baseline: private remote, permission preset, deny list, copy repo-root files | settings and accounts | T-000a |
| Put real samples in `samples/` and fill the gold set | needs real company data and judgement | T-000c |
| Run the OCR spike on 3 real scanned pages and judge the text | real scans, visual judgement | T-017 |
| Real-data pilot and kill-criteria check | real data, decision | T-020 |

## Config placement
- No `.agents/mcp_config.json` is needed. Executor skills live in `.agents/skills/` (copied by T-000a); never gitignore `.agents/` or `AGENTS.md`.

## Security notes
- No secrets in M1. `samples/`, `out/`, `.venv/`, `.env` and run folders are gitignored; real data must never be committed (GitHub keeps history).
- Tests block network access to prove the engine is offline (R-22).
- Pinned versions plus `requirements.lock.txt` (made by T-000b with `pip freeze`); no new package without a ticket that names it.

Related: [design](02-design.md), [requirements](01-requirements.md), [executor profile](executor-profile.md)
