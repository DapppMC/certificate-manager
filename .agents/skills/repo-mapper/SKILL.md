---
name: repo-mapper
description: Builds a compact read-only map of an existing repository (structure, entry points, conventions, commands, test status) for the planner. Use when asked for a repo map before planning.
---

# repo-mapper

Write `docs/repo-map.md` (about 700 words) and print it in the chat. Change nothing else; never read `.env*`, secrets, `node_modules`, build output or binaries.
Cover: what the project is, language, framework and versions from manifests; folder structure (two levels, one line each); entry points and main modules; conventions (naming, errors, env var names only); commands that exist (install, build, test, lint, run) and whether tests pass today (run once; report exit code and final line); risks noticed. End with "Map generated from files only; unverified areas: <list>."
