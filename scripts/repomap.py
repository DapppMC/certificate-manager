#!/usr/bin/env python3
"""repomap.py: Ranked symbol map within a token budget. Standard library only.

Aider-style: definitions are found with regexes, cross-file references become weighted
edges, personalized PageRank ranks files, and the top symbols are printed under a budget.
"""
import argparse, collections, json, math, os, re, subprocess, sys, time

DEF = {
    ".py": [r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", r"^\s*class\s+(\w+)"],
    ".ts": [r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)",
            r"^\s*export\s+(?:abstract\s+)?class\s+(\w+)",
            r"^\s*export\s+(?:const|let)\s+(\w+)\s*[:=]",
            r"^\s*export\s+(?:interface|type|enum)\s+(\w+)",
            r"^(?:async\s+)?function\*?\s+(\w+)\s*[(<]", r"^class\s+(\w+)"],
    ".go": [r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(", r"^type\s+(\w+)\s+(?:struct|interface)"],
    ".rs": [r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"],
}
for e in (".tsx", ".js", ".jsx", ".mjs", ".cjs"):
    DEF[e] = DEF[".ts"]
SKIP_DIR = {"node_modules", ".git", "dist", "build", "out", ".next", "vendor", "__pycache__", "target", ".venv", "coverage"}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
est = lambda s: (len(s) + 3) // 4


def list_files(root):
    try:
        out = subprocess.check_output(["git", "-C", root, "ls-files"], text=True, stderr=subprocess.DEVNULL)
        fs = [f for f in out.splitlines() if os.path.splitext(f)[1] in DEF]
        if fs:
            return [f for f in fs if not (set(f.split("/")) & SKIP_DIR)]
    except Exception:
        pass
    fs = []
    for r, ds, names in os.walk(root):
        ds[:] = [d for d in ds if d not in SKIP_DIR]
        fs += [os.path.relpath(os.path.join(r, n), root).replace("\\", "/") for n in names if os.path.splitext(n)[1] in DEF]
    return fs


def defs_of(text, ext):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for rx in DEF[ext]:
            m = re.match(rx, line)
            if m:
                out.append((m.group(1), i, line.strip()[:110]))
                break
    return out


def pagerank(edges, nodes, pers, d=0.85, it=40):
    out_w = collections.defaultdict(float)
    for (a, b), w in edges.items():
        out_w[a] += w
    pr = {n: 1.0 / len(nodes) for n in nodes}
    tot = sum(pers.get(n, 1.0) for n in nodes)
    tele = {n: pers.get(n, 1.0) / tot for n in nodes}
    for _ in range(it):
        nxt = {n: (1 - d) * tele[n] for n in nodes}
        dang = sum(pr[n] for n in nodes if out_w[n] == 0)
        for (a, b), w in edges.items():
            nxt[b] += d * pr[a] * w / out_w[a]
        for n in nodes:
            nxt[n] += d * dang * tele[n]
        pr = nxt
    return pr


def build_repomap(root, budget=1500, focus=None, max_defs=6):
    focus = focus or []
    files = list_files(root)
    defs, refs = {}, {}
    defined_in = collections.defaultdict(set)
    for f in files:
        try:
            with open(os.path.join(root, f), encoding="utf8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if len(text) > 400_000:
            continue
        ext = os.path.splitext(f)[1]
        defs[f] = defs_of(text, ext)
        refs[f] = collections.Counter(IDENT.findall(text))
        for name, _, _ in defs[f]:
            defined_in[name].add(f)
    edges = collections.defaultdict(float)
    for f, cnt in refs.items():
        for name, c in cnt.items():
            ds = defined_in.get(name)
            if not ds or len(ds) > 3:
                continue
            w = math.sqrt(c) * (10 if len(name) >= 8 else 1) * (0.1 if name.startswith("_") else 1)
            for d in ds:
                if d != f:
                    edges[(f, d)] += w
    pers = {f: 50.0 for f in files if any(x in f for x in focus)}
    for x in focus:
        for d in defined_in.get(x, ()):
            pers[d] = 50.0
    nodes = list(defs) or ["-"]
    pr = pagerank(edges, nodes, pers)
    build_repomap.last_pr = pr
    build_repomap.last_state = {"files": files, "defs": defs, "refs": refs, "defined_in": defined_in, "pr": pr}
    lines, used = [], 0
    for f in sorted(nodes, key=lambda n: -pr[n]):
        if not defs.get(f):
            continue
        ext_refs = lambda name: sum(refs[o].get(name, 0) for o in refs if o != f)
        ranked = sorted(defs[f], key=lambda d: -ext_refs(d[0]) if len(defined_in[d[0]]) <= 3 else 0)[:max_defs]
        ranked = [d for d in ranked if ext_refs(d[0]) > 0 and len(defined_in[d[0]]) <= 3]
        if not ranked:
            continue
        block = [f + ":"] + [f"  {ln:>4}| {sig}" for _, ln, sig in sorted(ranked, key=lambda d: d[1])]
        cost = est("\n".join(block))
        if used + cost > budget:
            continue
        lines += block
        used += cost
    return "\n".join(lines), used, len(files)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ranked repo map within a token budget")
    ap.add_argument("repo", default=".", nargs="?")
    ap.add_argument("--budget", type=int, default=1500)
    ap.add_argument("--focus", nargs="*", default=[])
    ap.add_argument("--max-defs", type=int, default=6)
    ap.add_argument("--json", action="store_true", help="also print file PageRank scores as JSON (for Gate -2)")
    a = ap.parse_args(argv)
    t0 = time.time()
    res, used, nfiles = build_repomap(a.repo, a.budget, a.focus, a.max_defs)
    print(res)
    if a.json:
        pr = getattr(build_repomap, "last_pr", {})
        top = sorted(pr.items(), key=lambda kv: -kv[1])[:25]
        print(json.dumps([{"file": f, "pagerank_score": round(s, 4)} for f, s in top if f != "-"]))
    print(f"\n# {nfiles} files scanned, {used} tokens (budget {a.budget}), {time.time()-t0:.2f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
