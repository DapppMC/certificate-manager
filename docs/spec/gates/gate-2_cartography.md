# Gate -2: Static Cartography (brownfield, zero LLM)
Goal: know the legacy code's shape before any model reads it. Output: `CARTOGRAPHY.json`.

## One command (preferred)
`python tools/cartograph.py <repo> --out CARTOGRAPHY.json`
Combines the ranked repo map, entrypoint scan (HTTP routes, CLI mains, workers, event subscribers),
SQL calls and tables, env key names (never values), deprecated APIs and orphan detection.
Deterministic: same repo, same bytes (no timestamps). Add `--engine ast-grep` to also run
`tools/sgrecipes/*.yml` (catches multi-line calls; falls back to regex with a warning if the binary is missing).
Add `--repomap-out repomap.txt` to keep the ranked map as text.

## What it does, so you can sanity check it
1. Ranks files and symbols (`repomap.py`, budget `--budget`, default 1500 tokens).
2. Lists entrypoints with file and line. Dynamic loading (`require(var)`, `importlib`, `__import__`) is flagged.
3. Marks orphans: files no entrypoint chain references. In a repo with dynamic loading an orphan is `unknown`, never `dead`.
4. Collects DB tables (SQL strings, `.sql` files) and env key names.

## Output schema (`"schema": "cartography/1"`)
```json
{"schema": "cartography/1", "repo_root": "", "engine": "regex",
 "entrypoints": [{"type": "http_route", "method": "GET", "route": "", "handler": "", "path": "", "line": 0}],
 "top_dependencies": [{"symbol": "", "file": "", "pagerank_score": 0.0, "referenced_by": 0}],
 "orphan_files": [], "orphan_notes": {}, "db_schema": {"tables": []}, "env_contract": [],
 "sql_calls": [], "deprecated_calls": [], "dynamic_dispatch_files": [], "stats": {}, "limits": []}
```
`type` is one of http_route, cli, worker, event. `limits` states what the scan cannot know.

## Exit criteria
- `CARTOGRAPHY.json` parses and has the keys above (`python -m unittest tests/test_cartograph.py` covers the tool).
- Every orphan has a reason line; dynamic-dispatch suspects are listed as `unknown`.
- No claim about behaviour yet. Behaviour belongs to Gate -1.
