#!/usr/bin/env python3
"""cartograph.py: Gate -2 in ONE command. Zero LLM, standard library only.

    python tools/cartograph.py <repo> --out CARTOGRAPHY.json [--budget 1500] [--engine regex|ast-grep]

Combines repomap.py (ranked symbols, PageRank), entrypoint scanning (HTTP routes, CLI, workers, events),
SQL table extraction, env-key discovery, deprecated API calls and orphan detection into one deterministic
CARTOGRAPHY.json (same repo bytes => same output bytes; no timestamps).

Engines: `regex` (default, line based, identical on every machine) and `ast-grep` (adds multi-line calls by
running tools/sgrecipes/*.yml; falls back to regex with a warning when the binary is missing).
Everything found is a hypothesis about structure, not behaviour: dynamic dispatch is reported as `unknown`.
"""
import argparse, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repomap  # noqa: E402

SRC_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs"}
SCAN_EXT = SRC_EXT | {".sql"}
ENV_FILES = (".env.example", ".env.sample", ".env.template", ".env")
MAX_BYTES = 400_000
ROOT_NAMES = {"main", "index", "server", "app", "cli", "__init__", "__main__", "manage", "setup", "conftest", "wsgi", "asgi"}

HTTP = r"(get|post|put|delete|patch|head|options|all)"
EXPRESS = re.compile(r"""\b(?:app|router|server|api|routes?)\.""" + HTTP + r"""\s*\(\s*(['"`])([^'"`]*)\2\s*(?:,(.*))?""")
FLASK = re.compile(r"""^\s*@\w+\.route\(\s*['"]([^'"]+)['"](.*)$""")
FASTAPI = re.compile(r"""^\s*@(?:app|router|api)\.""" + HTTP + r"""\(\s*['"]([^'"]+)['"]""")
DEF_PY = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
CLI_PATTERNS = [
    (re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]"""), "__main__"),
    (re.compile(r"""\bprogram\.command\(\s*['"]([^'"]+)['"]"""), None),
    (re.compile(r"^func\s+main\s*\(\s*\)"), "main"),
    (re.compile(r"^fn\s+main\s*\(\s*\)"), "main"),
]
WORKER_PATTERNS = [
    re.compile(r"\bcron\.schedule\("), re.compile(r"\bnew\s+CronJob\("), re.compile(r"\bnew\s+Worker\("),
    re.compile(r"\bschedule\.every\("), re.compile(r"^\s*@(?:shared_task|celery\.task|app\.task)\b"),
]
EVENT = re.compile(r"""\b(?:emitter|bus|events?|eventBus|socket|io|channel)\.on\(\s*['"]([^'"]+)['"]\s*,\s*([\w$.]+)?""")
EVENT_PY = re.compile(r"""^\s*@receiver\(\s*([\w.]+)""")
SUBSCRIBE = re.compile(r"""\.subscribe\(\s*['"]([^'"]+)['"]\s*,\s*([\w$.]+)?""")

STRING_RE = re.compile(r'''("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|`[^`]*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')''')
SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|WITH)\b", re.I)
TABLE_RE = re.compile(r'''\b(?:FROM|JOIN|INTO|UPDATE|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+["`\[]?([A-Za-z_][\w.]*)''', re.I)
SQL_WORDS = {"select", "set", "values", "where", "table", "if", "not", "exists", "only", "lateral", "unnest",
             "the", "a", "an", "this", "that", "these", "those", "your", "my", "our", "their", "its", "here", "there", "one", "each", "any", "all"}
ENV_PATTERNS = [
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]*)"""), re.compile(r"""process\.env\[\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]*)['"]"""), re.compile(r"""os\.environ\.get\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""), re.compile(r"""Deno\.env\.get\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.Getenv\(\s*"([A-Z][A-Z0-9_]*)\""""), re.compile(r"""env::var\(\s*"([A-Z][A-Z0-9_]*)\""""),
]
DEPRECATED = [
    (re.compile(r"\bnew\s+Buffer\s*\("), "new Buffer()"), (re.compile(r"\bfs\.exists\s*\("), "fs.exists()"),
    (re.compile(r"\burl\.parse\s*\("), "url.parse()"), (re.compile(r"\butil\.isArray\s*\("), "util.isArray()"),
    (re.compile(r"""require\(\s*['"]request['"]\s*\)"""), "require('request')"),
    (re.compile(r"\bdatetime\.utcnow\s*\("), "datetime.utcnow()"),
]
DYNAMIC = [re.compile(r"""\brequire\(\s*[^'"\s)]"""), re.compile(r"""\bimport\(\s*[^'"\s)]"""),
           re.compile(r"\bimportlib\.import_module\("), re.compile(r"\b__import__\("), re.compile(r"\beval\(")]
COMMENT_PREFIX = ("//", "#", "*", "/*")
# Entry points inside these folders are test scaffolding, not the application's own entry points.
NONPROD_DIRS = {"tests", "test", "__tests__", "fixtures", "__fixtures__", "docs", "doc", "samples", "sample",
                "examples", "example", "__mocks__", "e2e"}
# Names so common that a "most referenced symbol" ranking built on them says nothing about the business logic.
GENERIC_SYMBOLS = {"get", "set", "add", "sub", "put", "pop", "del", "has", "run", "map", "key", "keys", "value", "values",
                   "data", "name", "type", "id", "all", "any", "new", "init", "main", "open", "close", "read", "write",
                   "save", "load", "list", "find", "make", "send", "call", "test", "items", "update", "delete", "remove",
                   "reset", "clear", "start", "stop", "help", "log", "print", "format", "check", "handle", "process", "apply",
                   "append", "extend", "insert", "join", "split", "strip", "lower", "upper", "replace", "copy", "count", "index"}
MIN_SYMBOL_LEN = 4


def norm(p):
    return p.replace("\\", "/")


def walk(root):
    out = []
    for r, ds, fs in os.walk(root):
        ds[:] = sorted(d for d in ds if d not in repomap.SKIP_DIR)
        for f in sorted(fs):
            full = os.path.join(r, f)
            if os.path.splitext(f)[1] in SCAN_EXT or f in ENV_FILES:
                try:
                    if os.path.getsize(full) <= MAX_BYTES:
                        out.append(norm(os.path.relpath(full, root)))
                except OSError:
                    pass
    return sorted(out)


def read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def is_nonprod(rel, extra=()):
    return bool(set(rel.split("/")[:-1]) & (NONPROD_DIRS | set(extra)))


def code_mask(text, ext):
    """One bool per character: True where the character is code, False inside a string literal or a comment.
    Lets a pattern such as `fs.exists(` be ignored when it only appears as text, e.g. in a scanner's own source."""
    n = len(text)
    mask = [True] * n
    py = ext == ".py"
    i = 0
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if (py and c == "#") or (not py and two == "//"):
            j = text.find("\n", i)
            j = n if j < 0 else j
            mask[i:j] = [False] * (j - i)
            i = j
        elif not py and two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            mask[i:j] = [False] * (j - i)
            i = j
        elif c in "'\"`":
            tri = text[i:i + 3]
            q = tri if py and tri in ('"""', "'''") else c
            j = i + len(q)
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text.startswith(q, j):
                    j += len(q)
                    break
                if text[j] == "\n" and len(q) == 1 and q != "`":
                    break
                j += 1
            j = min(j, n)
            mask[i:j] = [False] * (j - i)
            i = j
        else:
            i += 1
    return mask


def is_comment(line):
    return line.lstrip().startswith(COMMENT_PREFIX)


def last_handler(rest):
    """Last identifier-like argument of a call's remaining text, or <inline>/'' when unknown."""
    if rest is None:
        return ""
    body = rest.strip()
    body = re.sub(r"\)\s*;?\s*$", "", body)
    if "=>" in body or re.search(r"\bfunction\b", body):
        return "<inline>"
    parts = [p.strip() for p in body.split(",") if p.strip()]
    for p in reversed(parts):
        if re.fullmatch(r"[A-Za-z_$][\w$.]*", p):
            return p
    return ""


def parse_express(text):
    m = EXPRESS.search(text)
    if not m:
        return None
    return {"type": "http_route", "method": m.group(1).upper(), "route": m.group(3), "handler": last_handler(m.group(4))}


# ---------- per-file scanners ----------
def scan_entrypoints(rel, lines):
    out = []
    if os.path.basename(rel) == "__main__.py":
        out.append({"type": "cli", "handler": "__main__", "path": rel, "line": 1})
    for i, line in enumerate(lines, 1):
        if is_comment(line):
            continue
        m = FASTAPI.match(line)
        fm = FLASK.match(line)
        if m or fm:
            if m:
                method, route = m.group(1).upper(), m.group(2)
            else:
                route = fm.group(1)
                ms = re.search(r"methods\s*=\s*\[([^\]]*)\]", fm.group(2))
                method = re.sub(r"[\s'\"]", "", ms.group(1)).upper().replace(",", "|") if ms else "GET"
            handler = ""
            for nxt in lines[i:i + 6]:
                dm = DEF_PY.match(nxt)
                if dm:
                    handler = dm.group(1)
                    break
            out.append({"type": "http_route", "method": method, "route": route, "handler": handler, "path": rel, "line": i})
            continue
        e = parse_express(line)
        if e:
            e.update(path=rel, line=i)
            out.append(e)
            continue
        for rx, name in CLI_PATTERNS:
            cm = rx.search(line)
            if cm:
                out.append({"type": "cli", "handler": name or cm.group(1), "path": rel, "line": i})
                break
        for rx in WORKER_PATTERNS:
            if rx.search(line):
                handler = rx.pattern.strip("^\\s*").replace("\\", "")[:30]
                for nxt in lines[i:i + 4]:
                    dm = DEF_PY.match(nxt)
                    if dm:
                        handler = dm.group(1)
                        break
                out.append({"type": "worker", "handler": handler, "path": rel, "line": i})
                break
        em = EVENT.search(line) or SUBSCRIBE.search(line)
        if em:
            out.append({"type": "event", "event": em.group(1), "handler": em.group(2) or "", "path": rel, "line": i})
        else:
            rm = EVENT_PY.match(line)
            if rm:
                handler = rm.group(1)
                for nxt in lines[i:i + 4]:
                    dm = DEF_PY.match(nxt)
                    if dm:
                        handler = dm.group(1)
                        break
                out.append({"type": "event", "event": rm.group(1), "handler": handler, "path": rel, "line": i})
    return out


def looks_like_sql(s):
    """Cheap guard so prose such as 'Update your profile' is not read as SQL."""
    u = s.upper()
    word = lambda w: re.search(r"\b" + w + r"\b", u) is not None
    if re.match(r"\s*(CREATE|ALTER|DROP)\s+TABLE\b", u):
        return True
    if re.match(r"\s*SELECT\b", u) or re.match(r"\s*WITH\b", u):
        return word("FROM")
    if re.match(r"\s*INSERT\b", u):
        return word("INTO")
    if re.match(r"\s*UPDATE\b", u):
        return word("SET")
    if re.match(r"\s*DELETE\b", u):
        return word("FROM")
    return False


def sql_in(text, rel, rel_is_sql=False):
    """Return (calls, tables) found in SQL-looking string literals (or whole .sql files)."""
    calls, tables = [], set()
    if rel_is_sql:
        chunks = [(1, text)]
    else:
        chunks = []
        for m in STRING_RE.finditer(text):
            s = m.group(0)
            q = 3 if s[:3] in ('"""', "'''") else 1
            chunks.append((text.count("\n", 0, m.start()) + 1, s[q:-q]))
    for line, s in chunks:
        if not rel_is_sql and not (SQL_START.match(s) and looks_like_sql(s)):
            continue
        found = [t.lower() for t in TABLE_RE.findall(s) if t.lower() not in SQL_WORDS]
        if not found and not rel_is_sql:
            continue
        tables.update(found)
        calls.append({"path": rel, "line": line, "sql": re.sub(r"\s+", " ", s).strip()[:120]})
    return calls, tables


def scan_env(lines):
    keys = set()
    for line in lines:
        for rx in ENV_PATTERNS:
            keys.update(rx.findall(line))
    return keys


def env_file_keys(text):
    return {m.group(1) for m in re.finditer(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", text, re.M)}


# ---------- ast-grep (optional) ----------
def find_astgrep():
    for c in (shutil.which("ast-grep"), shutil.which("sg"), os.path.expanduser("~/.local/bin/ast-grep")):
        if not c or not os.path.exists(c):
            continue
        try:
            out = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=10).stdout.lower()
        except (OSError, subprocess.SubprocessError):
            continue
        if "ast-grep" in out or re.match(r"^(ast-grep|sg)\s+\d", out):
            return c
    return None


def run_recipe(binary, recipe, root):
    r = subprocess.run([binary, "scan", "-r", os.path.join(HERE, "sgrecipes", recipe), root, "--json=compact"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def merge_entry(table, e):
    key = (e["path"], e["line"], e["type"], e.get("method", ""), e.get("route", ""))
    old = table.get(key)
    if old is None or (not old.get("handler") and e.get("handler")):
        table[key] = e


# ---------- main analysis ----------
def cartograph(repo, budget=1500, engine="regex", top=15, include_nonprod=False, ignore=()):
    root = repo
    warnings = []
    files = walk(root)
    entries, sql_calls, tables, env, deprecated, dynamic = {}, [], set(), set(), [], set()
    src_files, nonprod_skipped = [], 0
    for rel in files:
        base = os.path.basename(rel)
        text = read(root, rel)
        if base in ENV_FILES:
            env |= env_file_keys(text)
            continue
        if rel.endswith(".sql"):
            c, t = sql_in(text, rel, True)
            sql_calls += c
            tables |= set(t)
            continue
        src_files.append(rel)
        lines = text.splitlines()
        if include_nonprod or not is_nonprod(rel, ignore):
            for e in scan_entrypoints(rel, lines):
                merge_entry(entries, e)
        else:
            nonprod_skipped += 1
        c, t = sql_in(text, rel)
        sql_calls += c
        tables |= t
        env |= scan_env(lines)
        mask, offset = code_mask(text, os.path.splitext(rel)[1]), 0
        for i, line in enumerate(text.split("\n"), 1):
            line_start, offset = offset, offset + len(line) + 1
            if is_comment(line):
                continue
            for rx, label in DEPRECATED:
                if any(mask[line_start + m.start()] for m in rx.finditer(line.rstrip("\r"))):
                    deprecated.append({"path": rel, "line": i, "pattern": label})
            if any(rx.search(line) for rx in DYNAMIC):
                dynamic.add(rel)
    used_engine, sg_matches, sg_version = "regex", 0, None
    if engine == "ast-grep":
        binary = find_astgrep()
        if not binary:
            warnings.append("ast-grep binary not found; fell back to regex engine")
        else:
            used_engine = "ast-grep"
            sg_version = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.strip()
            for m in run_recipe(binary, "find_api_routes.yml", root):
                sg_matches += 1
                rel = norm(os.path.relpath(m["file"], root)) if os.path.isabs(m["file"]) or m["file"].startswith(root) else norm(m["file"])
                e = parse_express(re.sub(r"\s+", " ", m["text"]))
                if e and (include_nonprod or not is_nonprod(rel, ignore)):
                    e.update(path=rel, line=m["range"]["start"]["line"] + 1)
                    merge_entry(entries, e)
            for m in run_recipe(binary, "extract_sql_queries.yml", root):
                sg_matches += 1
                rel = norm(os.path.relpath(m["file"], root)) if os.path.isabs(m["file"]) or m["file"].startswith(root) else norm(m["file"])
                c, t = sql_in(m["text"], rel)
                line = m["range"]["start"]["line"] + 1
                for call in c:
                    call["line"] = line
                    if not any(x["path"] == rel and x["line"] == line for x in sql_calls):
                        sql_calls.append(call)
                tables |= t
            for m in run_recipe(binary, "find_deprecated_calls.yml", root):
                sg_matches += 1
                rel = norm(os.path.relpath(m["file"], root)) if os.path.isabs(m["file"]) or m["file"].startswith(root) else norm(m["file"])
                line = m["range"]["start"]["line"] + 1
                if not any(d["path"] == rel and d["line"] == line for d in deprecated):
                    label = next((lb for rx, lb in DEPRECATED if rx.search(m["text"])), m["text"][:30])
                    deprecated.append({"path": rel, "line": line, "pattern": label})
    # repomap-based ranking and orphans
    map_text, map_tokens, nfiles = repomap.build_repomap(root, budget)
    st = getattr(repomap.build_repomap, "last_state", None) or {"files": [], "defs": {}, "refs": {}, "defined_in": {}, "pr": {}}
    defs, refs, defined_in, pr = st["defs"], st["refs"], st["defined_in"], st["pr"]
    prod = lambda f: include_nonprod or not is_nonprod(f, ignore)
    # only production files count as referrers / ranked files: tests, docs copies and samples say nothing about the app's core
    ext_refs = lambda f, name: sum(1 for o in refs if o != f and prod(o) and refs[o].get(name, 0) > 0)
    top_deps = []
    for f in sorted((x for x in defs if prod(x)), key=lambda n: (-pr.get(n, 0), n)):
        syms = [(ext_refs(f, n), n) for n, _, _ in defs[f]
                if len(defined_in[n]) <= 3 and n.lower() not in GENERIC_SYMBOLS and len(n.lstrip("_")) >= MIN_SYMBOL_LEN
                and not (n.startswith("__") and n.endswith("__"))]
        syms = sorted([s for s in syms if s[0] > 0], key=lambda s: (-s[0], s[1]))[:3]
        for cnt, name in syms:
            top_deps.append({"symbol": name, "file": f, "pagerank_score": round(pr[f], 4), "referenced_by": cnt})
    top_deps = top_deps[:top]
    entry_files = {e["path"] for e in entries.values()}
    orphans, notes = [], {}
    for f in sorted(defs):
        if not defs[f] or f in entry_files:
            continue
        stem = os.path.splitext(os.path.basename(f))[0]
        if stem in ROOT_NAMES or stem.startswith("test_") or re.search(r"\.(test|spec)\.", f) or "/tests/" in "/" + f or "/__tests__/" in "/" + f:
            continue
        if not any(ext_refs(f, n) > 0 for n, _, _ in defs[f]):
            orphans.append(f)
            notes[f] = "no other file references any symbol defined here"
    if orphans and dynamic:
        for f in orphans:
            notes[f] += "; repo uses dynamic loading (" + ", ".join(sorted(dynamic)[:3]) + "), so treat as unknown until verified"
    order = {"http_route": 0, "cli": 1, "worker": 2, "event": 3}
    entrypoints = sorted(entries.values(), key=lambda e: (e["path"], e["line"], order[e["type"]], e.get("route", "")))
    result = {
        "schema": "cartography/1",
        "repo_root": norm(os.path.normpath(repo)),
        "engine": used_engine,
        "entrypoints": entrypoints,
        "top_dependencies": top_deps,
        "orphan_files": orphans,
        "orphan_notes": {k: notes[k] for k in sorted(notes)},
        "db_schema": {"tables": sorted(tables)},
        "env_contract": sorted(env),
        "sql_calls": sorted(sql_calls, key=lambda c: (c["path"], c["line"])),
        "deprecated_calls": sorted(deprecated, key=lambda d: (d["path"], d["line"], d["pattern"])),
        "dynamic_dispatch_files": sorted(dynamic),
        "stats": {"files_scanned": len(files), "source_files": len(src_files), "repomap_files": nfiles, "repomap_tokens": map_tokens,
                  "entrypoint_scan_skipped_files": nonprod_skipped},
        "limits": ["Structure only: behaviour is not inferred. Verify with golden tests (Gate -1).",
                   "Regex engine is line based; multi-line calls need --engine ast-grep.",
                   ("Entry points were also scanned in non-production folders (--include-nonprod)." if include_nonprod else
                    "Entry points and top_dependencies ignore these folders (--include-nonprod to scan them): "
                    + ", ".join(sorted(NONPROD_DIRS | set(ignore))) + "."),
                   "top_dependencies skips generic names (get, set, add, save, ...), dunder methods and names under 4 characters."],
    }
    if sg_version:
        result["ast_grep"] = {"version": sg_version, "matches": sg_matches}
    if warnings:
        result["warnings"] = warnings
    return result, map_text


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gate -2 static cartography in one command")
    ap.add_argument("repo")
    ap.add_argument("--out", default="CARTOGRAPHY.json")
    ap.add_argument("--budget", type=int, default=1500)
    ap.add_argument("--engine", choices=["regex", "ast-grep"], default="regex")
    ap.add_argument("--top", type=int, default=15, help="max top_dependencies entries")
    ap.add_argument("--include-nonprod", action="store_true", help="also scan tests/fixtures/docs/samples folders for entry points")
    ap.add_argument("--ignore", action="append", default=[], metavar="DIR", help="extra folder name to treat as non-production (repeatable), e.g. scripts")
    ap.add_argument("--repomap-out", help="also write the ranked symbol map text here")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.repo):
        print(f"FAIL: not a directory: {a.repo}", file=sys.stderr)
        return 2
    result, map_text = cartograph(a.repo, a.budget, a.engine, a.top, a.include_nonprod, a.ignore)
    with open(a.out, "w", encoding="utf8", newline="\n") as fh:
        fh.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if a.repomap_out:
        with open(a.repomap_out, "w", encoding="utf8", newline="\n") as fh:
            fh.write(map_text + "\n")
    for w in result.get("warnings", []):
        print(f"WARNING: {w}", file=sys.stderr)
    s = result["stats"]
    kinds = {}
    for e in result["entrypoints"]:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    print(f"Wrote {a.out}: {len(result['entrypoints'])} entrypoints {kinds or ''}, {len(result['orphan_files'])} orphans, "
          f"{len(result['db_schema']['tables'])} tables, {len(result['env_contract'])} env keys, "
          f"{len(result['deprecated_calls'])} deprecated calls ({s['files_scanned']} files, engine={result['engine']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
