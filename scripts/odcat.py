#!/usr/bin/env python3
"""odcat.py: Ingest an Open Design catalog into tiered, token-budgeted local files. Stdlib only.

Tiers: L0 catalog/FACETS.md (index), L1 catalog/cards/<id>.md (card), L2 raw DESIGN.md (do not load).
A curated subset (catalog/source/, Apache-2.0, see catalog/source/NOTICE) ships with Planner, so `build`
works with no argument and no external clone. `curate` refreshes that subset from an Open Design checkout.
"""
import argparse, collections, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(os.path.dirname(HERE), "catalog")
BUNDLED_SRC = os.path.join(DEFAULT_OUT, "source")  # curated Open Design subset shipped with Planner
CURATION = "curation.json"

FAMILY = {
    "editorial": ["Editorial"],
    "minimal": ["Modern & Minimal", "Layout & Structure", "Starter"],
    "expressive": ["Bold & Expressive", "Creative & Artistic", "Design & Creative", "Themed & Unique", "Retro & Nostalgic", "Morphism & Effects"],
    "product-ui": ["Professional & Corporate", "Backend & Data", "Developer Tools", "Productivity & SaaS", "AI & LLM"],
    "brand-consumer": ["Fintech & Crypto", "E-Commerce & Retail", "Automotive", "Media & Consumer", "Social & Messaging"],
}


def family(cat):
    for fam, keys in FAMILY.items():
        if any(cat.startswith(k) or k in cat for k in keys):
            return fam
    return "other"


def est_tokens(s):
    return (len(s) + 3) // 4


def css_vars(path):
    v = {}
    if os.path.exists(path):
        with open(path, encoding="utf8") as fh:
            for m in re.finditer(r"^\s*(--[\w-]+)\s*:\s*([^;]+);", fh.read(), re.M):
                v.setdefault(m.group(1), m.group(2).strip())
    return v


def lum(hexs):
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hexs or "")
    if not m:
        return None
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def first_font(v):
    m = re.match(r"""["']?([^,"']+)""", v or "")
    return m.group(1).strip() if m else ""


def summary(md):
    q = [l[1:].strip() for l in md.splitlines() if l.startswith(">") and "Category:" not in l]
    return re.sub(r"\s+", " ", " ".join(q[:3]))


def mode_of(L):
    if L is None:
        return "unknown"
    return "dark" if L < 0.2 else "light"


def load_curation(od):
    """curation.json sits next to design-systems/ in the bundled source; absent for a raw checkout."""
    p = os.path.join(od, CURATION)
    if os.path.exists(p):
        with open(p, encoding="utf8") as fh:
            return json.load(fh)
    return {}


def build_facets(records, out_dir, source, curation=None):
    systems = [r for r in records if r["kind"] == "system"]
    fam_cnt = collections.Counter(r["family"] for r in systems)
    mode_cnt = collections.Counter(r["mode"] for r in systems)
    arch_cnt = collections.Counter(r["archetype"] for r in systems if r.get("archetype"))
    facets = [
        "# DESIGN CATALOG FACETS (L0 Index)",
        "> Do not read full DESIGN.md files. Search: `python tools/odcat.py search [filters] -n 8`.",
        f"> Source: {source}",
        "",
        f"**Systems Total:** {len(systems)}",
        "**Families:** " + ", ".join(f"{k} ({v})" for k, v in fam_cnt.most_common()),
        "**Modes:** " + ", ".join(f"{k} ({v})" for k, v in mode_cnt.most_common()),
    ]
    if arch_cnt:
        facets.append("**Archetypes:** " + ", ".join(f"{k} ({v})" for k, v in sorted(arch_cnt.items())))
    facets += [
        "",
        "**Usage:**",
        "- `python tools/odcat.py search --archetype precision-dark -n 8`",
        "- `python tools/odcat.py search --family minimal --mode dark -n 8`",
        "- `python tools/odcat.py card <id>`",
        "",
    ]
    path = os.path.join(out_dir, "FACETS.md")
    with open(path, "w", encoding="utf8") as f:
        f.write("\n".join(facets))
    return path


def build(od, out):
    os.makedirs(os.path.join(out, "cards"), exist_ok=True)
    records = []
    cur = load_curation(od)
    picks = cur.get("systems", {})
    ds = os.path.join(od, "design-systems")
    if os.path.isdir(ds):
        for d in sorted(os.listdir(ds)):
            p = os.path.join(ds, d)
            if d.startswith("_") or not os.path.isdir(p) or not os.path.exists(os.path.join(p, "DESIGN.md")):
                continue
            if picks and d not in picks:
                continue
            man = {}
            mp = os.path.join(p, "manifest.json")
            if os.path.exists(mp):
                with open(mp, encoding="utf8") as fh:
                    man = json.load(fh)
            with open(os.path.join(p, "DESIGN.md"), encoding="utf8") as fh:
                md = fh.read()
            cat = man.get("category") or (re.search(r"Category:\s*(.+)", md) or [0, "?"])[1].strip()
            v = css_vars(os.path.join(p, "tokens.css"))
            L = lum(v.get("--bg", ""))
            rec = dict(
                kind="system", id=d, name=man.get("name") or d, category=cat, family=family(cat),
                mode=mode_of(L), accent=v.get("--accent", ""),
                font_display=first_font(v.get("--font-display")), font_body=first_font(v.get("--font-body")),
                summary=summary(md)[:260],
            )
            if d in picks:
                rec["archetype"] = picks[d].get("archetype", "")
                rec["use_when"] = picks[d].get("use_when", "")
            records.append(rec)
            card = [
                f"# Card: {d}",
                f"**Name:** {rec['name']} | **Family:** {rec['family']} | **Mode:** {rec['mode']}",
                f"**Display Font:** {rec['font_display']} | **Accent:** {rec['accent'] or 'none'}",
                f"\n> {rec['summary']}\n",
            ]
            if rec.get("archetype"):
                card.insert(2, f"**Archetype:** {rec['archetype']} | **Use when:** {rec['use_when']}")
            card.append(f"Path: {'catalog/source/' if cur else ''}design-systems/{d}/tokens.css")
            with open(os.path.join(out, "cards", f"{d}.md"), "w", encoding="utf8") as f:
                f.write("\n".join(card))
    keep = {r["id"] + ".md" for r in records}
    for fn in os.listdir(os.path.join(out, "cards")):  # a rebuild must not leave cards of systems that are gone
        if fn.endswith(".md") and fn not in keep:
            os.remove(os.path.join(out, "cards", fn))
    build_facets(records, out, (cur.get("source", {}).get("repo", "") + " @" + cur.get("source", {}).get("commit", "")[:7]) if cur else os.path.basename(os.path.normpath(od)))
    with open(os.path.join(out, "catalog.jsonl"), "w", encoding="utf8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"Ingested {len(records)} design systems into {out}")
    return len(records)


def curate(od, into=BUNDLED_SRC):
    """Copy the picked systems (manifest, DESIGN.md, tokens.css only) from an Open Design checkout."""
    cur = load_curation(into)
    if not cur:
        raise SystemExit(f"Error: {os.path.join(into, CURATION)} not found")
    copied = []
    for sid in sorted(cur["systems"]):
        src = os.path.join(od, "design-systems", sid)
        if not os.path.isdir(src):
            raise SystemExit(f"Error: {sid} missing from {od}")
        dst = os.path.join(into, "design-systems", sid)
        os.makedirs(dst, exist_ok=True)
        for fn in cur["source"]["files_copied"]:
            shutil.copyfile(os.path.join(src, fn), os.path.join(dst, fn))
        copied.append(sid)
    lic = os.path.join(od, "LICENSE")
    if os.path.exists(lic):
        shutil.copyfile(lic, os.path.join(into, "LICENSE"))
    print(f"Curated {len(copied)} systems into {into}")
    return copied


def search(out, query=None, fam=None, mode=None, limit=8, archetype=None):
    cat_file = os.path.join(out, "catalog.jsonl")
    if not os.path.exists(cat_file):
        print(f"Error: {cat_file} not found. Run `odcat.py build` first.", file=sys.stderr)
        return None
    results = []
    with open(cat_file, encoding="utf8") as fh:
        for line in fh:
            r = json.loads(line)
            if fam and r.get("family") != fam:
                continue
            if mode and r.get("mode") != mode:
                continue
            if archetype and r.get("archetype") != archetype:
                continue
            if query and query.lower() not in (r.get("name", "") + " " + r.get("summary", "") + " " + r.get("use_when", "")).lower():
                continue
            results.append(r)
    for r in results[:limit]:
        tag = f"{r['archetype']}, " if r.get("archetype") else ""
        print(f"[{r['id']}] {r['name']} ({tag}{r['family']}, {r['mode']}) -> {(r.get('use_when') or r['summary'])[:90]}")
    return results[:limit]


def card(out, item_id):
    p = os.path.join(out, "cards", f"{item_id}.md")
    if os.path.exists(p):
        with open(p, encoding="utf8") as fh:
            print(fh.read())
        return True
    print(f"Card {item_id} not found.", file=sys.stderr)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Open Design catalog tier indexer")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="ingest design-systems/ into catalog/")
    b.add_argument("--od", default=os.environ.get("OPEN_DESIGN_DIR") or BUNDLED_SRC,
                   help="source with design-systems/ (default: the bundled curated subset; or $OPEN_DESIGN_DIR)")
    b.add_argument("--out", default=DEFAULT_OUT)
    s = sub.add_parser("search", help="filter the catalog")
    s.add_argument("--out", default=DEFAULT_OUT)
    s.add_argument("-q", "--query")
    s.add_argument("--family")
    s.add_argument("--archetype", help="editorial | minimal | workspace-light | precision-dark | industrial-warm")
    s.add_argument("--mode", choices=["dark", "light", "unknown"])
    s.add_argument("-n", type=int, default=8)
    k = sub.add_parser("curate", help="refresh the bundled subset from an Open Design checkout (maintainers)")
    k.add_argument("--od", required=True)
    k.add_argument("--into", default=BUNDLED_SRC)
    c = sub.add_parser("card", help="print one L1 card")
    c.add_argument("--out", default=DEFAULT_OUT)
    c.add_argument("id")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        if not os.path.isdir(os.path.join(args.od, "design-systems")):
            print(f"Error: no design-systems/ under {args.od}", file=sys.stderr)
            return 2
        build(args.od, args.out)
        return 0
    if args.cmd == "curate":
        curate(args.od, args.into)
        return 0
    if args.cmd == "search":
        return 0 if search(args.out, args.query, args.family, args.mode, args.n, args.archetype) is not None else 1
    if args.cmd == "card":
        return 0 if card(args.out, args.id) else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
