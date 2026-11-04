#!/usr/bin/env python3
"""design_lint.py: deterministic anti-slop linter for a page's HTML/CSS. Standard library only.

    python tools/design_lint.py page.html [--css extra.css ...] [--strict] [--json]

Checks the Planner design constitution (design/DESIGN_CONSTITUTION.md):
  R01 ai-color          G-01  indigo/purple/violet cliches (banned hex, Tailwind classes, hue 245-290 saturated)
  R02 concentric-radius L3.1  outer radius = inner radius + padding, for every rounded parent/child pair
  R03 tabular-nums      L3.3  numbers in tables, stats and counters use tabular figures (or a mono font)
  R04 backdrop-blur     G-03  backdrop blur only on header/nav/modal/overlay
  R05 motion            L4    only transform and opacity may transition or animate (no `transition: all`)
  R06 cardception       G-02  no card inside a card
  R07 filler-copy       G-04  no empty marketing jargon
  R08 contrast          G-05  text/background contrast >= 4.5:1 (3:1 for large text)
  R09 tokens            L2    colors resolve to CSS variables (few raw hex values, tokens defined)
Exit 0 when nothing fails. `na` (rule not applicable, e.g. no nested rounded boxes) passes unless --strict.
Static analysis only: content built by JavaScript is covered through the CSS rules and script strings.
"""
import argparse, colorsys, json, os, re, sys
from html.parser import HTMLParser

BANNED_HEX = {"#6366f1", "#4f46e5", "#4338ca", "#818cf8", "#a5b4fc", "#8b5cf6", "#7c3aed", "#6d28d9", "#a78bfa",
              "#a855f7", "#9333ea", "#7e22ce", "#c084fc", "#d946ef", "#c026d3"}
FILLER = ["supercharge", "unlock", "next-gen", "next generation", "seamless", "revolutioniz", "empower", "elevate your",
          "streamline your", "game-changer", "game changer", "cutting-edge", "leverage the power", "lorem ipsum", "all-in-one",
          "take your .* to the next level"]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TEXT = {"script", "style", "head", "title"}
INTERACTIVE = {"hover", "focus", "active", "visited", "disabled", "checked", "before", "after", "placeholder", "selection",
               "focus-visible", "focus-within", "first-letter", "first-line", "backdrop", "marker"}
BLUR_OK = re.compile(r"header|nav|modal|dialog|overlay|backdrop|scrim|toolbar|sticky|drawer|popover|menu", re.I)
MONO = re.compile(r"mono|courier|menlo|consolas", re.I)
CARDISH = re.compile(r"(^|[-_])(card|tile|panel)$", re.I)


# ---------------- CSS ----------------
def split_top(s, sep):
    out, depth, quote, cur = [], 0, None, ""
    for ch in s:
        if quote:
            cur += ch
            quote = None if ch == quote else quote
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def parse_decls(body):
    decls = []
    for part in split_top(body, ";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k = k.strip().lower()
            if k:
                decls.append((k, re.sub(r"\s*!important\s*$", "", v.strip(), flags=re.I)))
    return decls


def parse_block(css, media=False):
    rules, keyframes, pos = [], [], 0
    while True:
        b = css.find("{", pos)
        if b < 0:
            break
        prelude = css[pos:b].strip()
        if ";" in prelude:
            prelude = prelude.rsplit(";", 1)[1].strip()
        depth, j = 1, b + 1
        while j < len(css) and depth:
            depth += (css[j] == "{") - (css[j] == "}")
            j += 1
        body, pos = css[b + 1:j - 1], j
        if prelude.startswith("@"):
            name = prelude.split()[0].lower()
            if name in ("@media", "@supports", "@layer", "@container"):
                r, k = parse_block(body, True)
                rules += r
                keyframes += k
            elif name.endswith("keyframes"):
                props = set()
                for inner in re.finditer(r"\{([^{}]*)\}", body):
                    props |= {k for k, _ in parse_decls(inner.group(1))}
                keyframes.append((prelude, props))
        else:
            rules.append({"selectors": [s.strip() for s in prelude.split(",") if s.strip()], "decls": parse_decls(body), "media": media})
    return rules, keyframes


def collect_vars(rules):
    v = {}
    for r in rules:
        if r["media"]:
            continue
        if any(s in (":root", "html", "body", "*") for s in r["selectors"]):
            for k, val in r["decls"]:
                if k.startswith("--"):
                    v[k] = val
    return v


def resolve(val, vars_, depth=0):
    if depth > 8 or "var(" not in val:
        return val

    def sub(m):
        name, fb = m.group(1), m.group(2)
        if name in vars_:
            return resolve(vars_[name], vars_, depth + 1)
        return fb.strip() if fb else m.group(0)
    return resolve(re.sub(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)", sub, val), vars_, depth + 1)


def to_px(val, vars_):
    v = resolve(val.strip(), vars_)
    if v in ("0", "0px"):
        return 0.0
    expr = v
    if v.startswith("calc("):
        expr = v[5:-1]
    expr = re.sub(r"(-?\d*\.?\d+)rem\b", lambda m: "(%s*16)" % m.group(1), expr)
    expr = re.sub(r"(-?\d*\.?\d+)px\b", r"\1", expr)
    if not re.fullmatch(r"[\d\s.+\-*/()]+", expr) or "**" in expr or len(expr) > 80:
        return None
    try:
        return float(eval(expr, {"__builtins__": {}}, {}))  # charset-restricted arithmetic only
    except Exception:
        return None


def box_values(val, vars_):
    parts = [to_px(p, vars_) for p in resolve(val, vars_).split()]
    return [p for p in parts if p is not None] if parts and all(p is not None for p in parts) else None


# ---------------- colors ----------------
def parse_color(s):
    s = s.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3,8})", s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) in (6, 8):
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            return (r, g, b, int(h[6:8], 16) / 255 if len(h) == 8 else 1.0)
        return None
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)(?:[\s,/]+([\d.]+%?))?\s*\)", s)
    if m:
        def ch(x):
            return float(x[:-1]) * 2.55 if x.endswith("%") else float(x)
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return (ch(m.group(1)), ch(m.group(2)), ch(m.group(3)), alpha)
    m = re.fullmatch(r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%(?:[\s,/]+([\d.]+%?))?\s*\)", s)
    if m:
        r, g, b = colorsys.hls_to_rgb(float(m.group(1)) / 360 % 1, float(m.group(3)) / 100, float(m.group(2)) / 100)
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return (r * 255, g * 255, b * 255, alpha)
    return {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0), "transparent": (0, 0, 0, 0.0)}.get(s)


def first_color(val):
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|\b(?:white|black|transparent)\b", val):
        c = parse_color(m.group(0))
        if c:
            return c
    return None


def rel_lum(c):
    f = lambda x: (x / 255) / 12.92 if x / 255 <= 0.03928 else (((x / 255) + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def contrast(a, b):
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, bg):
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def hsl_of(c):
    h, l, s = colorsys.rgb_to_hls(c[0] / 255, c[1] / 255, c[2] / 255)
    return h * 360, s, l


# ---------------- HTML tree ----------------
class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.classes = (self.attrs.get("class") or "").split()
        self.id = self.attrs.get("id")
        self.children, self.text, self.style_cache = [], "", None


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.cur = self.root
        self.styles, self.scripts, self.texts = [], [], []
        self.in_style = self.in_script = False

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag == "style":
            self.in_style = True
        elif tag == "script":
            self.in_script = True
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        elif tag == "script":
            self.in_script = False
        p = self.cur
        while p is not None and p.tag != tag:
            p = p.parent
        if p is not None and p.parent is not None:
            self.cur = p.parent

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)
        elif self.in_script:
            self.scripts.append(data)
        else:
            self.cur.text += data
            if data.strip():
                self.texts.append(data.strip())


def walk(n):
    yield n
    for c in n.children:
        yield from walk(c)


def compound_re():
    return re.compile(r"^(\*|[a-zA-Z][\w-]*)?((?:[.#][\w-]+|\[[^\]]*\]|:{1,2}[\w-]+(?:\([^)]*\))?)*)$")


COMPOUND = compound_re()


def parse_selector(sel):
    if re.search(r"[+~]", re.sub(r"\[[^\]]*\]|\([^)]*\)", "", sel)):
        return None
    toks = re.findall(r"[^\s>]+|>", sel)
    parts, comb = [], " "
    for t in toks:
        if t == ">":
            comb = ">"
            continue
        m = COMPOUND.match(t)
        if not m:
            return None
        tag, rest = m.group(1), m.group(2)
        pseudos = re.findall(r":{1,2}([\w-]+)", re.sub(r"\([^)]*\)", "", rest))
        if any(p in INTERACTIVE or p == "not" for p in pseudos):
            return None
        parts.append((comb, {"tag": None if tag in (None, "*") else tag.lower(), "classes": re.findall(r"\.([\w-]+)", rest),
                             "id": (re.findall(r"#([\w-]+)", rest) or [None])[0], "root": "root" in pseudos}))
        comb = " "
    return parts or None


def match_compound(node, c):
    if c["root"]:
        return node.tag == "html"
    if c["tag"] and node.tag != c["tag"]:
        return False
    if c["id"] and node.id != c["id"]:
        return False
    return all(k in node.classes for k in c["classes"])


def match_chain(node, parts, k):
    comb, comp = parts[k]
    if not match_compound(node, comp):
        return False
    if k == 0:
        return True
    if comb == ">":
        return node.parent is not None and match_chain(node.parent, parts, k - 1)
    p = node.parent
    while p is not None:
        if match_chain(p, parts, k - 1):
            return True
        p = p.parent
    return False


def specificity(parts):
    return (sum(1 for _, c in parts if c["id"]), sum(len(c["classes"]) for _, c in parts), sum(1 for _, c in parts if c["tag"]))


INHERITED = {"color", "font-variant-numeric", "font-feature-settings", "font-family", "font-size"}


class Styler:
    def __init__(self, rules, vars_):
        self.vars = vars_
        self.compiled = []
        order = 0
        for r in rules:
            if r["media"]:
                continue
            for s in r["selectors"]:
                parts = parse_selector(s)
                if parts:
                    self.compiled.append((specificity(parts), order, parts, r["decls"]))
                    order += 1
        self.compiled.sort(key=lambda x: (x[0], x[1]))

    def own(self, node):
        if node.style_cache is None:
            d = {}
            for _, _, parts, decls in self.compiled:
                if match_chain(node, parts, len(parts) - 1):
                    for k, v in decls:
                        d[k] = v
                        if k == "padding" or k == "border-radius":
                            d[k + ":short"] = v
            if node.attrs.get("style"):
                for k, v in parse_decls(node.attrs["style"]):
                    d[k] = v
            node.style_cache = d
        return node.style_cache

    def get(self, node, prop):
        n = node
        while n is not None and n.tag != "#root":
            v = self.own(n).get(prop)
            if v is not None:
                return resolve(v, self.vars)
            if prop not in INHERITED:
                return None
            n = n.parent
        return None

    def background(self, node):
        layers, n = [], node
        while n is not None and n.tag != "#root":
            st = self.own(n)
            val = st.get("background-color") or st.get("background")
            if val:
                val = resolve(val, self.vars)
                if "gradient" not in val and "url(" not in val:
                    c = first_color(val)
                    if c:
                        layers.append(c)
                        if c[3] >= 1:
                            break
            n = n.parent
        base = (255, 255, 255, 1.0)
        for c in reversed(layers):
            base = blend(c, base)
        return base


# ---------------- the linter ----------------
def line_of(src, pos):
    return src.count("\n", 0, pos) + 1


def lint(html_src, extra_css="", strict=False, max_raw_colors=3, allow_hue=False):
    tree = Tree()
    tree.feed(html_src)
    css = re.sub(r"/\*.*?\*/", "", "\n".join(tree.styles) + "\n" + extra_css, flags=re.S)
    rules, keyframes = parse_block(css)
    vars_ = collect_vars(rules)
    st = Styler(rules, vars_)
    nodes = [n for n in walk(tree.root) if n.tag != "#root"]
    script = "\n".join(tree.scripts)
    full = html_src + "\n" + extra_css
    results = []

    def add(rid, name, g, status, evidence, why=""):
        results.append({"id": rid, "name": name, "constitution": g, "status": status, "evidence": evidence[:5], "why": why})

    # R01 ai colors
    ev = []
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)", full):
        c = parse_color(m.group(0))
        if not c:
            continue
        hex_ = m.group(0).lower()
        h, s, l = hsl_of(c)
        if (hex_ in BANNED_HEX or (245 <= h <= 290 and s >= 0.45 and 0.15 <= l <= 0.85)) and not allow_hue:
            ev.append(f"line {line_of(full, m.start())}: {m.group(0)} (hue {h:.0f})")
    for m in re.finditer(r"\b(?:bg|text|from|to|via|border|ring|fill|stroke|accent)-(?:indigo|purple|violet|fuchsia)-\d{2,3}\b", full):
        ev.append(f"line {line_of(full, m.start())}: tailwind class {m.group(0)}")
    add("R01", "ai-color", "G-01", "fail" if ev else "pass", ev, "indigo/purple/violet is the default AI look")

    # R02 concentric radius
    pairs, bad = 0, []
    for p in nodes:
        r_o = to_px(st.own(p).get("border-radius", "x"), vars_) if "border-radius" in st.own(p) else None
        if r_o is None and "border-radius" in st.own(p):
            vals = box_values(st.own(p)["border-radius"], vars_)
            r_o = vals[0] if vals else None
        pad = None
        if "padding" in st.own(p):
            vals = box_values(st.own(p)["padding"], vars_)
            pad = min(vals) if vals else None
        if not r_o or not pad:
            continue
        for c in p.children:
            if "border-radius" not in st.own(c):
                continue
            vals = box_values(st.own(c)["border-radius"], vars_)
            r_i = vals[0] if vals else None
            if not r_i:
                continue
            pairs += 1
            if abs(r_o - (r_i + pad)) > 1.0:
                bad.append(f"<{p.tag} class=\"{' '.join(p.classes)}\">: outer {r_o:g}px != inner {r_i:g}px + padding {pad:g}px = {r_i + pad:g}px")
    formula = "--radius-outer" in vars_ and "calc(" in vars_["--radius-outer"] and "--radius-inner" in vars_["--radius-outer"]
    if bad:
        add("R02", "concentric-radius", "L3.1", "fail", bad, "R_outer = R_inner + padding")
    elif pairs or formula:
        add("R02", "concentric-radius", "L3.1", "pass", [f"{pairs} nested rounded pair(s) verified" + (", radius tokens use the formula" if formula else "")])
    else:
        add("R02", "concentric-radius", "L3.1", "fail" if strict else "na", ["no nested rounded parent/child pair and no --radius-outer formula token found"])

    # R03 tabular numerals
    def tab_ok(n):
        v = (st.get(n, "font-variant-numeric") or "") + " " + (st.get(n, "font-feature-settings") or "")
        return "tabular-nums" in v or "tnum" in v or bool(MONO.search(st.get(n, "font-family") or ""))
    numeric = []
    for n in nodes:
        if n.tag in SKIP_TEXT:
            continue
        t = re.sub(r"\s+", " ", n.text).strip()
        digits = sum(ch.isdigit() for ch in t)
        if t and digits >= 2 and digits / len(t) >= 0.25 and len(t) <= 40:
            numeric.append((n, t))
    tab_rules = [r for r in rules if any(k == "font-variant-numeric" and "tabular-nums" in v for k, v in r["decls"])
                 or any(k == "font-feature-settings" and "tnum" in v for k, v in r["decls"])
                 or any(k == "font-family" and MONO.search(resolve(v, vars_)) for k, v in r["decls"])]
    broad = {"td", "th", "table", "tbody", "tr", "body", "html", ":root", "*"}
    tab_sels = [s.strip() for r in tab_rules for s in r["selectors"]]
    if len(numeric) >= 3:
        miss = [f"<{n.tag} class=\"{' '.join(n.classes)}\">{t}" for n, t in numeric if not tab_ok(n)]
        add("R03", "tabular-nums", "L3.3", "fail" if miss else "pass", miss or [f"{len(numeric)} numeric elements all tabular"], "numbers must not jiggle")
    else:
        covered = bool(tab_rules)
        if re.search(r"<t[dh]\b|createElement\(\s*['\"]t[dh]", script):
            cls = set()
            for m in re.finditer(r"<t[dh][^>]*class=\\?[\"']([^\"'\\]+)", script):
                cls |= set(m.group(1).split())
            last = [re.findall(r"[^\s>]+", s)[-1] if s else "" for s in tab_sels]
            covered = any(x.lstrip(".").split(".")[0].split(":")[0] in broad or x in broad or
                          (x.startswith(".") and x[1:].split(".")[0] in cls) or x.split(".")[0] in broad for x in last)
        add("R03", "tabular-nums", "L3.3", "pass" if covered else "fail",
            ["tabular-nums rule covers table cells / numbers"] if covered else ["numbers/table cells are not styled with font-variant-numeric: tabular-nums"],
            "numbers must not jiggle")

    # R04 backdrop blur
    ev = []
    for r in rules:
        for k, v in r["decls"]:
            if k in ("backdrop-filter", "-webkit-backdrop-filter") and v.strip() != "none":
                pos = dict(r["decls"]).get("position", "")
                if not (any(BLUR_OK.search(s) for s in r["selectors"]) or pos in ("sticky", "fixed")):
                    ev.append(f"{', '.join(r['selectors'])}: {k}: {v}")
    for n in nodes:
        for c in n.classes:
            if c.startswith("backdrop-blur") and not (n.tag in ("header", "nav") or any(BLUR_OK.search(x) for x in n.classes)):
                ev.append(f"<{n.tag}> class {c}")
    add("R04", "backdrop-blur", "G-03", "fail" if ev else "pass", ev, "blur only on sticky headers and modal backdrops")

    # R05 motion
    ev, allowed = [], {"transform", "opacity", "none", "translate", "scale", "rotate"}
    for r in rules:
        for k, v in r["decls"]:
            if k == "transition" or k == "transition-property":
                for item in split_top(v, ","):
                    words = [w for w in re.findall(r"[a-z-]+", item.lower()) if w not in
                             {"ease", "in", "out", "linear", "step", "start", "end", "cubic", "bezier", "steps", "normal", "s", "ms", "infinite", "initial", "inherit"}]
                    prop = words[0] if words else ""
                    if prop and prop not in allowed:
                        ev.append(f"{', '.join(r['selectors'])}: {k}: {v.strip()[:50]}")
                        break
    for name, props in keyframes:
        bad = sorted(p for p in props if p not in allowed and p != "animation-timing-function" and not p.startswith("--"))
        if bad:
            ev.append(f"{name}: animates {', '.join(bad)}")
    for m in re.finditer(r"\btransition-(?:all|colors|shadow|\[)", full):
        ev.append(f"tailwind class {m.group(0)}")
    add("R05", "motion", "L4", "fail" if ev else "pass", ev, "animate only transform and opacity")

    # R06 cardception
    ev = []
    for n in nodes:
        if any(CARDISH.search(c) for c in n.classes):
            for d in walk(n):
                if d is not n and any(CARDISH.search(c) for c in d.classes):
                    ev.append(f"<{d.tag} class=\"{' '.join(d.classes)}\"> inside <{n.tag} class=\"{' '.join(n.classes)}\">")
                    break
    add("R06", "cardception", "G-02", "fail" if ev else "pass", ev, "group with hairlines or a 2-4% shade shift instead")

    # R07 filler
    visible = " ".join(tree.texts)
    ev = [f"'{m.group(0)}'" for p in FILLER for m in [re.search(p, visible, re.I)] if m]
    add("R07", "filler-copy", "G-04", "fail" if ev else "pass", ev, "microcopy must be precise and functional")

    # R08 contrast
    ev, seen = [], set()
    for n in nodes:
        if n.tag in SKIP_TEXT or not n.text.strip():
            continue
        fg = first_color(st.get(n, "color") or "#000000") or (0, 0, 0, 1.0)
        bg = st.background(n)
        fgb = blend(fg, bg) if fg[3] < 1 else fg
        key = (tuple(round(x) for x in fgb[:3]), tuple(round(x) for x in bg[:3]))
        if key in seen:
            continue
        seen.add(key)
        size = to_px(st.get(n, "font-size") or "16px", vars_) or 16
        need = 3.0 if size >= 24 else 4.5
        ratio = contrast(fgb, bg)
        if ratio < need:
            ev.append(f"<{n.tag}> '{n.text.strip()[:24]}' contrast {ratio:.2f}:1 < {need}")
    add("R08", "contrast", "G-05", "fail" if ev else "pass", ev or [f"{len(seen)} color pair(s) checked"], "WCAG AA text contrast")

    # R09 tokens
    raw = []
    for r in rules:
        for k, v in r["decls"]:
            if k.startswith("--"):
                continue
            for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", v):
                if m.group(0).lower() not in ("#fff", "#ffffff", "#000", "#000000"):
                    raw.append(f"{', '.join(r['selectors'])}: {k}: {m.group(0)}")
    defined = [k for k in vars_ if not k.startswith("--tw-")]
    ev = []
    if len(defined) < 6:
        ev.append(f"only {len(defined)} CSS custom properties defined on :root (need >= 6)")
    if len(raw) > max_raw_colors:
        ev.append(f"{len(raw)} raw hex colors outside variable definitions (max {max_raw_colors}): " + "; ".join(raw[:2]))
    add("R09", "tokens", "L2", "fail" if ev else "pass", ev or [f"{len(defined)} tokens, {len(raw)} raw hex"], "colors and sizes resolve to tokens")

    failed = [r for r in results if r["status"] == "fail"]
    return {"ok": not failed, "failed": [r["id"] for r in failed], "results": results}


def _run_cdp(a):
    """Optional runtime check (Planner 4.0): drive a real Chromium through core/ui. Returns (exit_code, payload)."""
    import importlib.util
    bridge = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "core", "ui", "cdp_linter.py")
    bridge = os.path.normpath(bridge)
    if not os.path.isfile(bridge):
        return 2, {"verdict": "TOOLING_ERROR", "error": "core/ui/cdp_linter.py not found (the CDP linter ships with Planner 4.0)"}
    spec = importlib.util.spec_from_file_location("planner_cdp_linter", bridge)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses with postponed annotations look the module up here
    spec.loader.exec_module(mod)
    res = mod.run_cdp_linter(a.cdp, a.selector, target_lc=a.target_lc, zeta=a.zeta, interrupt=a.interrupt,
                             check_optical=not a.no_optical)
    return res.exit_code, {"verdict": res.verdict, "failures": res.failures(), "report": res.report}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Anti-slop design linter (static HTML rules; optional --cdp runtime checks)")
    ap.add_argument("html", nargs="?", help="HTML file for the static rules (optional when only --cdp is used)")
    ap.add_argument("--css", nargs="*", default=[])
    ap.add_argument("--strict", action="store_true", help="rules that are not applicable (na) fail")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-raw-colors", type=int, default=3)
    ap.add_argument("--allow-hue", action="store_true", help="allow indigo/purple (when the brief explicitly requires it)")
    ap.add_argument("--cdp", metavar="URL", help="also run the runtime CDP kinematic/optical linter against this URL or file (needs node + playwright)")
    ap.add_argument("--selector", help="CSS selector of the element to audit (required with --cdp)")
    ap.add_argument("--target-lc", type=float, help="minimum |APCA Lc| (default: heuristic by size/weight)")
    ap.add_argument("--zeta", type=float, help="expected spring damping ratio (enables the spring check)")
    ap.add_argument("--interrupt", action="store_true", help="test velocity continuity when an animation is interrupted")
    ap.add_argument("--no-optical", action="store_true", help="skip the optical-centring check")
    a = ap.parse_args(argv)
    if not a.html and not a.cdp:
        ap.error("give an HTML file, --cdp URL, or both")
    if a.cdp and not a.selector:
        ap.error("--cdp needs --selector")
    static_rc = 0
    rep = None
    if a.html:
        try:
            with open(a.html, encoding="utf8", errors="ignore") as fh:
                src = fh.read()
            extra = ""
            for c in a.css:
                with open(c, encoding="utf8", errors="ignore") as fh:
                    extra += fh.read() + "\n"
        except OSError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 2
        rep = lint(src, extra, a.strict, a.max_raw_colors, a.allow_hue)
        static_rc = 0 if rep["ok"] else 1
    cdp_rc, cdp = (0, None)
    if a.cdp:
        cdp_rc, cdp = _run_cdp(a)
    if a.json:
        out = dict(rep) if rep is not None else {"ok": True, "failed": [], "results": []}
        if cdp is not None:
            out["cdp"] = cdp
            out["ok"] = bool(out["ok"]) and cdp_rc == 0
        print(json.dumps(out, indent=2))
    else:
        if rep is not None:
            for r in rep["results"]:
                print(f"{r['status'].upper():4} {r['id']} {r['name']:<17} ({r['constitution']})")
                if r["status"] != "pass":
                    for e in r["evidence"]:
                        print(f"       - {e}")
            print("\n" + ("design lint: OK" if rep["ok"] else "design lint: FAILED " + ", ".join(rep["failed"])))
        if cdp is not None:
            print(f"cdp lint: {cdp['verdict']}")
            for line in cdp.get("failures", []):
                print(f"       - {line}")
            if cdp.get("error"):
                print(f"       - {cdp['error']}")
    if static_rc == 2 or cdp_rc == 2:
        return 2
    return 1 if (static_rc or cdp_rc) else 0


if __name__ == "__main__":
    sys.exit(main())
