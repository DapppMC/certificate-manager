#!/usr/bin/env python3
"""check_pack.py - deterministic checks for a spec pack (Obsidian, GitHub, Antigravity safe).

Standard library only, Python 3.9+. No network. Read-only unless --fix-index is given.

Usage:
  python check_pack.py PACK_DIR                 # full check (Gate 4 level)
  python check_pack.py PACK_DIR --gate 2        # only what must exist through Gate 2
  python check_pack.py PACK_DIR --status        # where is this pack, what is next
  python check_pack.py PACK_DIR --fix-index     # (re)write INDEX.md files from frontmatter
  python check_pack.py PACK_DIR --json          # machine-readable findings

PACK_DIR is the folder whose root contains INDEX.md (e.g. docs/spec). The last line is
always "RESULT: PASS" or "RESULT: FAIL (errors=N warnings=M)". Exit code 0 = pass, 1 = fail,
2 = bad usage. Conventions are documented in references/conventions.md of the skill.
"""
import argparse
import datetime
import json
import os
import re
import sys
from urllib.parse import unquote

SKIP_DIRS = {".obsidian", ".git", "node_modules", "__pycache__", ".trash"}
EXEMPT_DIRS = {"repo-root"}           # portable files copied to the repo root (no frontmatter)
EXEMPT_PREFIXES = ("design/references",)  # exported material, not authored docs
QUALITY_MODES = ("economy", "balanced", "max")
DESIGN_ROUTES = ("a", "b", "c", "d")
MODEL_TIERS = ("standard", "premium-review", "premium-hard")
PROCESS_MODES = ("full", "lite")
CLAUDE_MODELS = ("none", "5.5", "4.6")      # which Claude generation the user's Antigravity picker shows (INDEX claude_models)
CLAUDE_46_END = datetime.date(2026, 11, 2)  # Antigravity Models page: Claude 4.6 "will be removed on November 2, 2026" (read 2026-10-09)
ASSET_ROUTES = ("none", "agy-builtin", "chatgpt-web", "codex", "od-media")
ASSET_MODES = ("manual", "auto")
ASSET_STEPS = ("prepare", "generate", "ingest")
ASSET_FORMATS = ("png", "jpg", "jpeg", "webp", "svg", "gif", "mp4", "webm")
LITE_MAX_FEATURES, LITE_MAX_FILES, LITE_MAX_BATCH, LITE_MAX_INTEGRATIONS = 8, 3, 4, 2
LESSON_LIMIT = 15

TICKET_SECTIONS = [
    "Goal", "Context to read first", "Files to create or modify", "Files NOT to touch",
    "Canonical example", "Steps", "Acceptance criteria", "Verification",
    "Stop conditions", "Report format", "Constraints (repeated)",
]
VAGUE = re.compile(r"\b(etc\.?|and so on|as needed|as appropriate|appropriately|properly|"
                   r"handle errors|best practices?|where necessary|if applicable|clean ?up)\b", re.I)
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{30,}", "GitHub fine-grained token"),
    (r"sk-[A-Za-z0-9_-]{20,}", "API key (sk-...)"),
    (r"sbp_[a-f0-9]{30,}", "Supabase access token"),
    (r"sb_secret_[A-Za-z0-9_-]{10,}", "Supabase secret key"),
    (r"AIza[0-9A-Za-z_-]{30,}", "Google API key"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}", "JWT"),
]
GENERIC_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?([A-Za-z0-9/+_\-.]{16,})")
PLACEHOLDER = re.compile(r"(?i)(your|xxx|example|placeholder|changeme|\.\.\.|redacted|dummy|fake|env\.)")
HTML_TAGS = set("a b i u p br hr em kbd sub sup img code pre div span table thead tbody tr td th ul ol li details summary "
                "strong h1 h2 h3 h4 h5 h6 blockquote script style link meta html head body title svg path button input "
                "form label nav main header footer section article aside canvas video audio source".split())
TEMPLATE_PLACEHOLDER = re.compile(r"<([a-z][a-z0-9 _|/,.-]{2,60})>")
CODE_LANGS_WITH_ANGLES = {"html", "xml", "svg", "jsx", "tsx", "js", "ts", "vue", "mermaid", "css", "markup"}


def token_is_placeholder(text, m):
    tok = m.group(0)
    if PLACEHOLDER.search(tok):
        return True
    before, after = text[max(0, m.start() - 3):m.start()], text[m.end():m.end() + 3]
    return before.endswith(("<", "${", "$")) or after.startswith((">", "}"))
MERMAID_OK = {"flowchart", "graph", "sequenceDiagram", "stateDiagram", "stateDiagram-v2",
              "classDiagram", "erDiagram", "gantt", "pie"}
OPEN_MARKERS = re.compile(r"(NEEDS CLARIFICATION|\bTBD\b|\bTODO\b|\bFIXME\b|\?\?\?)")
HIDDEN_INSTR = re.compile(r"(?i)(ignore (all|previous)|disregard|system prompt|you must now|exfiltrate|curl .*\|\s*(ba)?sh)")
RID = re.compile(r"\bR-\d{2,3}\b")
TID = re.compile(r"^T-\d{3}[a-z]?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Findings:
    def __init__(self):
        self.items = []

    def err(self, path, msg):
        self.items.append(("ERROR", path, msg))

    def warn(self, path, msg):
        self.items.append(("WARN", path, msg))

    @property
    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.items if i[0] == "WARN"]


# ---------------------------------------------------------------- parsing helpers
def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v).strip()


def parse_frontmatter(text):
    """Return (dict or None, body). Supports scalars, [a, b] lists and '- a' block lists."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", text, re.S)
    if not m:
        return None, text
    fm, key = {}, None
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", raw)
        if item and key is not None:
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(_scalar(item.group(1)))
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", raw)
        if not kv:
            return {"__bad__": raw}, m.group(2)
        key, val = kv.group(1), kv.group(2).strip()
        if val.startswith("[") and "]" in val:
            inner = val[1:val.index("]")]
            fm[key] = [_scalar(x) for x in inner.split(",") if x.strip()]
        elif val == "":
            fm[key] = ""
        else:
            fm[key] = _scalar(val)
    return fm, m.group(2)


def strip_code(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"~~~.*?~~~", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def section(body, title):
    """Return text under '## title' (case-insensitive prefix match) up to the next '## '."""
    pat = re.compile(r"^##\s+" + re.escape(title) + r"[^\n]*\n(.*?)(?=^##\s|\Z)", re.S | re.M | re.I)
    m = pat.search(body)
    return m.group(1) if m else None


def exact_exists(path):
    """Case-sensitive existence check (GitHub/Linux semantics even on Windows/macOS)."""
    path = os.path.abspath(path)
    head, tail = os.path.split(path)
    if head == path:
        return os.path.exists(path)
    if not exact_exists(head):
        return False
    try:
        return tail in os.listdir(head)
    except OSError:
        return False


# ---------------------------------------------------------------- pack model
class Pack:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.docs = {}    # rel path -> dict(text, fm, body)
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
            for fn in sorted(fns):
                if fn.lower().endswith(".md"):
                    full = os.path.join(dp, fn)
                    rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                    text = read(full)
                    fm, body = parse_frontmatter(text)
                    self.docs[rel] = {"text": text, "fm": fm, "body": body}
        idx = self.docs.get("INDEX.md")
        self.root_fm = (idx or {}).get("fm") or {}
        self.tier = str(self.root_fm.get("tier", "")).upper() or "M"
        self.ui = str(self.root_fm.get("ui", "false")).lower() == "true"
        self.integrations = [x for x in as_list(self.root_fm.get("integrations")) if x and x.lower() != "none"]
        self.quality = str(self.root_fm.get("quality", "economy")).lower() or "economy"
        self.design_route = str(self.root_fm.get("design_route", "")).lower()
        self.process = str(self.root_fm.get("process", "full")).lower() or "full"
        self.claude_models = str(self.root_fm.get("claude_models", "")).lower()
        self.asset_route = str(self.root_fm.get("asset_route", "")).lower()
        self.asset_mode = str(self.root_fm.get("asset_mode", "manual")).lower() or "manual"
        try:
            self.premium_budget = int(str(self.root_fm.get("premium_budget", "0")))
        except ValueError:
            self.premium_budget = -1
        try:
            self.gate = int(str(self.root_fm.get("gate", "4")))
        except ValueError:
            self.gate = 4

    def exempt_fm(self, rel):
        top = rel.split("/")[0]
        return top in EXEMPT_DIRS or rel == "AGENTS.md" or any(rel.startswith(p) for p in EXEMPT_PREFIXES)

    def authored(self):
        return [r for r in self.docs if not self.exempt_fm(r)]

    def tickets(self):
        return {r: d for r, d in self.docs.items() if r.startswith("tickets/") and os.path.basename(r).startswith("T-")}

    def requirement_ids(self):
        d = self.docs.get("01-requirements.md")
        if not d:
            return []
        return re.findall(r"^#{2,4}\s+(R-\d{2,3})\b", d["body"], re.M)


# ---------------------------------------------------------------- checks
def required_files(pack, gate):
    """Files that must exist once `gate` is reached (cumulative). Gate 0 = brief + executor profile."""
    req = ["INDEX.md"]
    if gate >= 0:
        req += ["00-brief.md", "executor-profile.md"]
    if gate >= 1:
        req += ["01-requirements.md"]
        if pack.tier in ("M", "L"):      # S keeps its short discovery notes inside the brief
            req += ["decisions/discovery-log.md"]
    if gate >= 2:
        req += ["02-design.md", "decisions/INDEX.md"]
        if pack.tier in ("M", "L"):
            req += ["03-workflows.md"]
        if pack.tier in ("M", "L") or pack.integrations or pack.asset_route not in ("", "none"):
            req += ["05-tooling.md"]      # also when images are planned: it holds the asset route and the T-000h result
        if pack.ui:
            req += ["repo-root/DESIGN.md"]
            if pack.tier in ("M", "L"):  # S uses UI-lite: brief, screens and components live inside DESIGN.md
                req += ["design/INDEX.md", "design/design-brief.md", "design/screens.md", "design/components.md"]
    if gate >= 3:
        req += ["04-verification.md", "AGENTS.md", "repo-root/AGENTS.md", "tickets/INDEX.md"]
    if gate >= 4:
        req += ["READY.md"]
    return req


# first gate at which a file is expected; used to ignore forward links while an earlier gate is being checked
def target_gate(key):
    fixed = {"01-requirements.md": 1, "02-design.md": 2, "03-workflows.md": 2, "05-tooling.md": 2,
             "04-verification.md": 3, "AGENTS.md": 3, "READY.md": 4, "decisions/INDEX.md": 2,
             "assets-manifest.md": 3, "lessons.md": 3}
    if key in fixed:
        return fixed[key]
    if key.startswith("tickets/") or key == "repo-root/AGENTS.md" or key.startswith("repo-root/scripts"):
        return 3
    if key.startswith(("design/", "repo-root/", "decisions/ADR-")):
        return 2
    return 0


def check_structure(pack, f, gate):
    for rel in required_files(pack, gate):
        if rel not in pack.docs:
            f.err(rel, "required file is missing for tier %s at gate %d" % (pack.tier, gate))
    if gate >= 2 and not any(r.startswith("decisions/ADR-") for r in pack.docs):
        f.err("decisions/", "no ADR found (need at least decisions/ADR-001-<slug>.md)")
    if gate >= 3:
        if not any(os.path.basename(r).startswith("T-000") for r in pack.tickets()):
            f.err("tickets/", "no T-000* ticket (environment/safety/integration setup) found")
        else:
            have = {str((d["fm"] or {}).get("id", "")) for d in pack.tickets().values()}
            for need, what in (("T-000a", "safety baseline"), ("T-000b", "environment bootstrap")):
                if need not in have:
                    f.err("tickets/", "missing %s (%s): it is never cut, also in lite mode" % (need, what))
    if gate >= 2 and pack.ui:
        has_tokens = any(fn.startswith("tokens.") for fn in os.listdir(os.path.join(pack.root, "repo-root"))) \
            if os.path.isdir(os.path.join(pack.root, "repo-root")) else False
        if not has_tokens:
            f.err("repo-root/", "UI project: need a tokens file (repo-root/tokens.css or tokens.json)")
    if pack.tier not in ("S", "M", "L"):
        f.err("INDEX.md", "frontmatter 'tier' must be S, M or L")
    if pack.quality not in QUALITY_MODES:
        f.err("INDEX.md", "frontmatter 'quality' must be economy, balanced or max (default economy)")
    if pack.premium_budget < 0:
        f.err("INDEX.md", "frontmatter 'premium_budget' must be a whole number (0 or more)")
    if pack.process not in PROCESS_MODES:
        f.err("INDEX.md", "frontmatter 'process' must be full or lite (default full)")
    elif pack.process == "lite" and pack.tier != "S":
        f.err("INDEX.md", "process lite is only for tier S (small packs): set process=full, or keep tier S")
    if pack.claude_models and pack.claude_models not in CLAUDE_MODELS:
        f.err("INDEX.md", "frontmatter 'claude_models' must be none, 5.5 or 4.6 (the Claude generation shown in the Antigravity model picker)")
    if pack.asset_route and pack.asset_route not in ASSET_ROUTES:
        f.err("INDEX.md", "frontmatter 'asset_route' must be one of %s" % ", ".join(ASSET_ROUTES))
    if pack.asset_mode not in ASSET_MODES:
        f.err("INDEX.md", "frontmatter 'asset_mode' must be manual or auto (default manual)")
    if pack.design_route and pack.design_route not in DESIGN_ROUTES:
        f.err("INDEX.md", "frontmatter 'design_route' must be one of a, b, c, d (see tooling-planner design-routes)")
    if gate >= 2 and pack.ui and not pack.design_route:
        f.warn("INDEX.md", "UI project without 'design_route' (a|b|c|d): record the chosen design route with --set design_route=c")
    if gate >= 3 and pack.design_route == "c" and "open-design" not in [x.lower() for x in pack.integrations]:
        f.err("INDEX.md", "design_route c (Open Design) needs 'open-design' in integrations (INT block, setup and smoke-test tickets)")


def check_frontmatter(pack, f):
    for rel in pack.authored():
        d = pack.docs[rel]
        fm = d["fm"]
        if fm is None:
            f.err(rel, "missing or unterminated YAML frontmatter")
            continue
        if "__bad__" in fm:
            f.err(rel, "frontmatter line is not 'key: value': %r" % fm["__bad__"][:60])
            continue
        if not fm.get("title"):
            f.err(rel, "frontmatter: title is required")
        if not DATE.match(str(fm.get("date", ""))):
            f.err(rel, "frontmatter: date must be YYYY-MM-DD")
        if not isinstance(fm.get("tags"), list) or not fm.get("tags"):
            f.err(rel, "frontmatter: tags must be a non-empty YAML list, e.g. tags: [spec, brief]")
        base = os.path.basename(rel)
        if rel.startswith("tickets/") and base.startswith("T-"):
            for k in ("id", "status", "owner", "kind", "requirements", "effort", "role"):
                if fm.get(k) in (None, "", []):
                    f.err(rel, "ticket frontmatter: %s is required" % k)
            if fm.get("status") not in ("todo", "doing", "done", "blocked"):
                f.err(rel, "ticket status must be todo|doing|done|blocked")
            if fm.get("owner") not in ("executor", "human"):
                f.err(rel, "ticket owner must be executor|human")
            if fm.get("effort") not in ("low", "medium", "high"):
                f.err(rel, "ticket effort must be low|medium|high")
            if fm.get("kind") not in ("setup", "smoke-test", "design", "design-system", "asset", "feature", "test", "fix", "review", "docs"):
                f.err(rel, "ticket kind must be setup|smoke-test|design|design-system|asset|feature|test|fix|review|docs")
            mt = str(fm.get("model_tier", "standard")).lower()
            if mt not in MODEL_TIERS:
                f.err(rel, "ticket model_tier must be standard|premium-review|premium-hard (no model names in tickets)")
            elif mt != "standard" and len(str(fm.get("premium_reason", "")).split()) < 3:
                f.err(rel, "premium ticket needs premium_reason: at least 3 words saying why a premium model is worth the quota")
            tid = str(fm.get("id", ""))
            if not TID.match(tid):
                f.err(rel, "ticket id must look like T-003 or T-000a")
            elif not base.startswith(tid):
                f.err(rel, "file name must start with the ticket id (%s)" % tid)
        if base.startswith("ADR-"):
            if fm.get("status") not in ("proposed", "accepted", "superseded", "rejected"):
                f.err(rel, "ADR status must be proposed|accepted|superseded|rejected")
            if not fm.get("id"):
                f.err(rel, "ADR frontmatter: id is required (e.g. ADR-001)")
        if base == "READY.md" and rel == "READY.md" and fm.get("status") not in ("ready", "not-ready"):
            f.err(rel, "READY.md status must be ready|not-ready")


def check_related(pack, f):
    for rel in pack.authored():
        lines = [ln for ln in pack.docs[rel]["body"].splitlines() if ln.strip()]
        if not lines or not lines[-1].lstrip().startswith("Related:"):
            f.err(rel, "document must end with a 'Related:' line (use 'Related: none' if truly standalone)")


def link_targets(text):
    clean = strip_code(text)
    out = []
    for m in re.finditer(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", clean):
        out.append(m.group(1))
    return out, clean


def check_links(pack, f, gate=4):
    linked = {}
    for rel, d in pack.docs.items():
        if rel.split("/")[0] in EXEMPT_DIRS:
            continue
        targets, clean = link_targets(d["text"])
        for tgt in re.findall(r"\]\(([^)\n]{1,300})\)", clean):
            if re.search(r"\s", tgt) and re.search(r"\.md\b", tgt) and not re.search(r"\s+\"", tgt):
                f.err(rel, "link target contains an unencoded space (use %%20): %s" % tgt[:60])
        if re.search(r"\[\[[^\]]+\]\]", clean):
            f.warn(rel, "wikilink found; use relative markdown links (works on GitHub and in Antigravity)")
        for t in targets:
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t) or t.startswith("#"):
                continue
            path = unquote(t.split("#")[0].split("?")[0])
            if not path:
                continue
            full = os.path.normpath(os.path.join(os.path.dirname(os.path.join(pack.root, rel)), path))
            inside = full == pack.root or full.startswith(pack.root + os.sep)
            if not inside:
                f.warn(rel, "link leaves the pack root (Obsidian cannot follow it): %s" % t)
                continue
            if not exact_exists(full):
                key0 = os.path.relpath(full, pack.root).replace(os.sep, "/")
                if gate < 4 and target_gate(key0) > gate:
                    continue  # forward link to a file of a later gate: fine for now, checked at that gate
                alt = os.path.exists(full)
                f.err(rel, "broken link%s: %s" % (" (case differs from file on disk)" if alt else "", t))
            else:
                key = os.path.relpath(full, pack.root).replace(os.sep, "/")
                linked.setdefault(key, set()).add(rel)
    return linked


def check_index_and_orphans(pack, f, linked):
    dirs = {}
    for rel in pack.authored():
        dirs.setdefault(os.path.dirname(rel), []).append(rel)
    for d, files in sorted(dirs.items()):
        idx = (d + "/INDEX.md") if d else "INDEX.md"
        if idx not in pack.docs:
            f.err(d or ".", "folder has documents but no INDEX.md (run --fix-index)")
            continue
        targets = linked_from(pack, idx)
        for rel in files:
            if rel == idx:
                continue
            if rel not in targets:
                f.err(idx, "INDEX does not link to %s" % rel)
        for s_ in child_dirs(d, pack.authored()):
            sub_idx = ((d + "/") if d else "") + s_ + "/INDEX.md"
            if sub_idx in pack.docs and sub_idx not in targets:
                f.err(idx, "INDEX does not link to sub-index %s" % sub_idx)
        n = len(pack.docs[idx]["text"].splitlines())
        if n > 220:
            f.warn(idx, "index is %d lines; keep under about 200" % n)
    for rel in pack.authored():
        if rel == "INDEX.md" or rel.endswith("/INDEX.md"):
            continue
        if rel not in linked:
            f.err(rel, "orphan: no other document links to it")


def child_dirs(d, rels):
    prefix = (d + "/") if d else ""
    out = set()
    for r in rels:
        if r.startswith(prefix):
            rest = r[len(prefix):]
            if "/" in rest:
                out.add(rest.split("/")[0])
    return sorted(out)


def linked_from(pack, rel):
    targets, _ = link_targets(pack.docs[rel]["text"])
    out = set()
    for t in targets:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t) or t.startswith("#"):
            continue
        p = unquote(t.split("#")[0])
        full = os.path.normpath(os.path.join(os.path.dirname(os.path.join(pack.root, rel)), p))
        out.add(os.path.relpath(full, pack.root).replace(os.sep, "/"))
    return out


def check_requirements(pack, f, gate):
    rd = pack.docs.get("01-requirements.md")
    if not rd:
        return
    ids = pack.requirement_ids()
    if not ids:
        f.err("01-requirements.md", "no requirements found; use headings like '### R-01: Title'")
        return
    dup = {i for i in ids if ids.count(i) > 1}
    for i in sorted(dup):
        f.err("01-requirements.md", "duplicate requirement id %s" % i)
    blocks = re.split(r"^#{2,4}\s+(?=R-\d{2,3}\b)", rd["body"], flags=re.M)[1:]
    prio = {}
    for b in blocks:
        rid = RID.search(b).group(0)
        pm = re.search(r"(?im)^\**Priority\**\s*:\s*\**\s*(must|should|could|won't)", b)
        if not pm:
            f.err("01-requirements.md", "%s: missing 'Priority: must|should|could'" % rid)
        else:
            prio[rid] = pm.group(1).lower()
        if not re.search(r"(?im)^\**Acceptance\**\s*:", b):
            f.err("01-requirements.md", "%s: missing 'Acceptance:' line" % rid)
    # dangling references anywhere
    known = set(ids)
    for rel in pack.authored():
        for r in set(RID.findall(strip_code(pack.docs[rel]["body"]))):
            if r not in known and rel != "01-requirements.md":
                f.err(rel, "references %s, which is not defined in 01-requirements.md" % r)
    if gate >= 3:
        vd = pack.docs.get("04-verification.md")
        if vd:
            for r in ids:
                rows = [ln for ln in vd["body"].splitlines() if ln.lstrip().startswith("|") and re.search(r"\b%s\b" % r, ln)]
                if not rows:
                    f.err("04-verification.md", "%s has no traceability row" % r)
                elif not any(("`" in ln) or re.search(r"(?i)\bmanual\b", ln) for ln in rows):
                    f.err("04-verification.md", "%s row has neither a `command` nor 'manual' check" % r)
        tix = pack.tickets()
        covered = set()
        for rel, d in tix.items():
            covered.update(as_list((d["fm"] or {}).get("requirements")))
        for r in ids:
            if r not in covered and prio.get(r) in ("must", "should"):
                f.err("tickets/", "%s (%s) is not referenced by any ticket" % (r, prio.get(r)))
            elif r not in covered and prio.get(r) == "could":
                f.warn("tickets/", "%s (could) is not covered by any ticket" % r)


def check_tickets(pack, f):
    tix = pack.tickets()
    ids, deps = {}, {}
    for rel, d in sorted(tix.items()):
        fm = d["fm"] or {}
        tid = str(fm.get("id", ""))
        if tid in ids:
            f.err(rel, "duplicate ticket id %s (also in %s)" % (tid, ids[tid]))
        ids[tid] = rel
        deps[tid] = as_list(fm.get("depends_on"))
        body = d["body"]
        reqs = as_list(fm.get("requirements"))
        if not any(RID.fullmatch(x) for x in reqs):
            f.err(rel, "ticket must list at least one R-xx in 'requirements' (use R-00 for environment/safety)")
        is_human = fm.get("owner") == "human"
        if is_human:
            # human tickets may be written in the user's language: only the checklist and the stop rule are enforced
            if not re.search(r"- \[[ x]\]", body):
                f.err(rel, "human ticket must contain a checklist (- [ ] ...)")
            if not re.search(r"(?i)twice|two (failed )?attempts|dua kali|2 kali", body):
                f.err(rel, "human ticket needs the 'if a step fails twice, stop and ask' rule")
            continue
        for s in TICKET_SECTIONS:
            if section(body, s) is None:
                f.err(rel, "missing section '## %s'" % s)
        ver = section(body, "Verification")
        if ver is not None and not re.search(r"```[^\n]*\n.*?\S.*?```", ver, re.S):
            f.err(rel, "Verification section needs a fenced code block with the exact command(s)")
        if ver is not None and not re.search(r"(?i)(expected|exit code|PASS)", ver):
            f.err(rel, "Verification section must state the expected result (exit code / PASS line)")
        if ver is not None and fm.get("owner") == "executor" and fm.get("kind") not in ("docs", "review") \
                and fm.get("status") != "blocked" and "run_check.py" not in ver:
            f.err(rel, "Verification must run its command(s) through `python scripts/run_check.py --name <n> -- <command>`: "
                       "without it there is no EXIT=/RESULT: line, no log for the evidence bundle and no loop guard")
        if "win over any generic" not in body:
            f.err(rel, "missing the precedence line ('The ticket and requirements win over any generic ...')")
        sc = section(body, "Stop conditions")
        if sc is not None and not re.search(r"(?i)twice|two (failed )?attempts", sc):
            f.err(rel, "Stop conditions must include the 'fails twice -> stop and report' rule")
        fl = section(body, "Files to create or modify")
        if fl is not None and not re.search(r"`[^`]+`", fl) and fm.get("owner") == "executor":
            f.err(rel, "Files to create or modify must list exact paths in backticks")
        ctx = section(body, "Context to read first")
        if ctx is not None:
            n = len([ln for ln in ctx.splitlines() if ln.strip().startswith(("-", "*"))])
            if n > 6:
                f.warn(rel, "context list has %d items; keep a ticket to a handful of files" % n)
        steps = section(body, "Steps")
        if steps is not None:
            for m in VAGUE.finditer(steps):
                f.warn(rel, "vague wording in Steps: %r (executor cannot guess)" % m.group(0))
        try:
            sc_val = int(str(fm.get("score", "0")))
        except ValueError:
            sc_val = 0
            f.warn(rel, "frontmatter 'score' should be a number 1-10")
        if sc_val >= 5 and not re.search(r"(?i)split|exception|escalat", body):
            f.warn(rel, "size score %d (>=5): split the ticket or explain the exception in the body" % sc_val)
        if len(d["text"].splitlines()) > 160:
            f.warn(rel, "ticket is %d lines; probably too big for one session" % len(d["text"].splitlines()))
    for tid, ds in deps.items():
        for x in ds:
            if x not in ids:
                f.err(ids[tid], "depends_on %s which does not exist" % x)
    state = {}

    def visit(t, chain):
        if state.get(t) == 1:
            f.err(ids.get(t, "tickets/"), "dependency cycle: %s" % " -> ".join(chain + [t]))
            return
        if state.get(t) == 2 or t not in deps:
            return
        state[t] = 1
        for x in deps[t]:
            visit(x, chain + [t])
        state[t] = 2

    for t in list(deps):
        visit(t, [])
    if pack.ui:
        dsys = [t for t, rel in ids.items() if (tix[rel]["fm"] or {}).get("kind") == "design-system"]
        if not dsys:
            f.err("tickets/", "UI project: need a ticket with kind: design-system before feature tickets")
        else:
            def ancestors(t, seen=None):
                seen = seen if seen is not None else set()
                for x in deps.get(t, []):
                    if x not in seen:
                        seen.add(x)
                        ancestors(x, seen)
                return seen
            for t, rel in ids.items():
                if (tix[rel]["fm"] or {}).get("kind") == "feature" and not (set(dsys) & ancestors(t)):
                    f.err(rel, "UI feature ticket must depend (directly or transitively) on the design-system ticket")
            if not any("ui_check.py" in tix[ids[t]]["text"] for t in dsys):
                f.err(ids[dsys[0]], "design-system ticket must run scripts/ui_check.py in its Verification")
            if pack.design_route == "c":
                td_text = (pack.docs.get("05-tooling.md") or {}).get("text", "")
                auto = bool(re.search(r"(?im)^\W*mode\W*:\W*auto\b", td_text))
                dz = [t for t, rel in ids.items() if (tix[rel]["fm"] or {}).get("kind") == "design"]
                need = 2 if auto else 3
                if len(dz) < need:
                    f.err("tickets/", "design_route c (%s mode) needs %d tickets with kind: design (prepare brief%s, pull and lint); found %d"
                          % ("auto" if auto else "manual", need, "" if auto else ", human generates in Open Design", len(dz)))
                pulls = [t for t in dz if (tix[ids[t]]["fm"] or {}).get("owner") == "executor" and re.search(r"\blint\b", tix[ids[t]]["text"])]
                if not pulls:
                    f.err("tickets/", "design_route c: no pull ticket (kind: design, owner: executor, runs od lint in its Verification)")
                else:
                    if not any(set(pulls) & ancestors(t) for t in dsys):
                        f.err(ids[dsys[0]], "design_route c: the design-system ticket must depend (directly or transitively) on the pull-and-lint ticket %s" % ", ".join(pulls))
                    if not auto:
                        humans = [t for t in dz if (tix[ids[t]]["fm"] or {}).get("owner") == "human"]
                        if not humans or not any(set(humans) & ancestors(pt) for pt in pulls):
                            f.err(ids[pulls[0]], "design_route c (manual): the pull ticket must depend on the human generate ticket (kind: design, owner: human)")


def check_tooling(pack, f):
    td = pack.docs.get("05-tooling.md")
    tix = pack.tickets()
    blocks = []
    if td:
        parts = re.split(r"^###\s+(?=INT-\d+)", td["body"], flags=re.M)[1:]
        for b in parts:
            iid = re.match(r"(INT-\d+)", b).group(1)
            blocks.append((iid, b))
    ids = [i for i, _ in blocks]
    for i in sorted({x for x in ids if ids.count(x) > 1}):
        f.err("05-tooling.md", "duplicate integration id %s" % i)
    if pack.integrations and not blocks:
        f.err("05-tooling.md", "INDEX declares integrations %s but 05-tooling.md has no '### INT-xx' blocks" % pack.integrations)
    for iid, b in blocks:
        for label in ("Owner", "Auth", "Security", "Smoke test"):
            if not re.search(r"(?im)^[-*]?\s*\**%s\**\s*:" % label, b):
                f.err("05-tooling.md", "%s: missing '%s:' line" % (iid, label))
        if re.search(r"(?im)^[-*]?\s*\**Milestone\**\s*:\s*\**\s*later", b):
            continue  # documented for a later milestone: tickets are written when that milestone is planned
        kinds = {}
        for rel, d in tix.items():
            fm = d["fm"] or {}
            if fm.get("integration") == iid:
                kinds.setdefault(fm.get("kind"), []).append(rel)
        if "setup" not in kinds:
            f.err("tickets/", "%s has no setup ticket (kind: setup, integration: %s)" % (iid, iid))
        if "smoke-test" not in kinds:
            f.err("tickets/", "%s has no smoke-test ticket (kind: smoke-test, integration: %s)" % (iid, iid))
    if td and blocks and len(blocks) > 5:
        f.warn("05-tooling.md", "%d integrations; every active MCP server costs context tokens, keep the set minimal" % len(blocks))


def check_quality(pack, f, today=None):
    """Quality mode and premium budget (warnings: they guide spending, they do not break an executor)."""
    today = today or datetime.date.today()
    prem = []
    for rel, d in sorted(pack.tickets().items()):
        fm = d["fm"] or {}
        mt = str(fm.get("model_tier", "standard")).lower()
        if mt in ("premium-review", "premium-hard"):
            prem.append((rel, mt, str(fm.get("premium_reason", "")).lower()))
    if pack.quality == "economy" and pack.premium_budget:
        f.warn("INDEX.md", "quality is economy but premium_budget is %d: economy means 0; set quality balanced or max, or premium_budget 0" % pack.premium_budget)
    for rel, d in sorted(pack.tickets().items()):
        fm = d["fm"] or {}
        if str(fm.get("model_tier", "standard")).lower() != "standard" and fm.get("owner") == "human":
            f.warn(rel, "model_tier on a human ticket has no effect; remove it")
        if re.search(r"(?i)\b(opus|sonnet|gpt-?oss)\b|gemini[ -]?[0-9]", strip_code(d["text"])):
            f.warn(rel, "a ticket names a model: tickets carry model_tier only (the mapping is in AGENTS.md). Ignore this warning only if the project itself calls that model")
    if prem and pack.quality == "economy":
        f.warn("INDEX.md", "%d premium ticket(s) but quality is economy: set quality=balanced or max (and premium_budget), or make them standard" % len(prem))
    if prem and pack.premium_budget and len(prem) > pack.premium_budget:
        f.warn("INDEX.md", "%d premium tickets exceed premium_budget %d: premium quota is small, cut or raise the budget on purpose"
               % (len(prem), pack.premium_budget))
    if prem and not pack.premium_budget and pack.quality != "economy":
        f.warn("INDEX.md", "quality %s with premium tickets but premium_budget is 0: write the budget the user agreed to" % pack.quality)
    if not prem and pack.quality != "economy" and pack.premium_budget:
        f.warn("INDEX.md", "quality %s with premium_budget %d but no premium ticket is planned: add the review tickets the policy calls for, or set quality economy"
               % (pack.quality, pack.premium_budget))
    cm = pack.claude_models
    if prem and not cm:
        f.err("INDEX.md", "premium tickets exist but INDEX has no claude_models (none | 5.5 | 4.6): ask the model-picker question (0F) and record it with --set claude_models=...")
    if cm == "none" and (pack.quality != "economy" or prem):
        f.warn("INDEX.md", "claude_models is none (no Claude in the model picker): premium sessions cannot run; set quality=economy and premium_budget=0, or correct claude_models")
    if cm == "4.6":
        if pack.quality == "max":
            f.err("INDEX.md", "quality max with claude_models 4.6: max is only for Claude 5.5. 4.6 reads and rescues, it does not build (independent boards rank Gemini 3.8 Flash above it); use balanced")
        if today >= CLAUDE_46_END:
            f.warn("INDEX.md", "claude_models is 4.6 but Antigravity lists Claude 4.6 as removed on %s: look at the model picker, then --set claude_models=5.5 or none and re-run the ladder question (0F)" % CLAUDE_46_END.isoformat())
        for rel, mt, why in prem:
            kind = (pack.tickets()[rel]["fm"] or {}).get("kind")
            if kind in ("feature", "design-system", "test") and not why.startswith("escalation"):
                f.warn(rel, "claude_models 4.6: premium is for review and rescue tickets; building a %s ticket with it is not supported by evidence" % kind)
            if mt == "premium-hard" and not why.startswith("escalation"):
                f.warn(rel, "claude_models 4.6: premium-hard (Opus 4.6) is the last escalation only (premium_reason: escalation ...)")
    for rel, mt, why in prem:
        if (pack.tickets()[rel]["fm"] or {}).get("kind") in ("setup", "smoke-test", "docs", "design", "asset"):
            f.warn(rel, "premium model on a %s ticket: setup, smoke, docs, design pulls and assets never need one" % (pack.tickets()[rel]["fm"] or {}).get("kind"))
        if mt == "premium-hard" and pack.quality == "balanced" and not why.startswith("escalation"):
            f.warn(rel, "premium-hard in a balanced pack should be an escalation (premium_reason: escalation ...); use premium-review otherwise")


def ticket_ancestors(tix):
    """Return (ids, deps, ancestors(fn)) for the ticket set."""
    ids = {str((d["fm"] or {}).get("id", "")): rel for rel, d in tix.items()}
    deps = {t: as_list((tix[rel]["fm"] or {}).get("depends_on")) for t, rel in ids.items()}

    def anc(t, seen=None):
        seen = seen if seen is not None else set()
        for x in deps.get(t, []):
            if x not in seen:
                seen.add(x)
                anc(x, seen)
        return seen
    return ids, deps, anc


def listed_files(body):
    sec = section(body, "Files to create or modify") or ""
    return [ln for ln in sec.splitlines() if ln.strip().startswith(("-", "*")) and re.search(r"`[^`]+`", ln)]


def check_process(pack, f):
    """process: lite limits (warnings). One-way rule: when a limit trips, the planner runs --set process=full."""
    if pack.process != "lite":
        return
    tix = pack.tickets()
    feats = {rel: d for rel, d in tix.items() if (d["fm"] or {}).get("kind") == "feature"}
    if len(feats) > LITE_MAX_FEATURES:
        f.warn("INDEX.md", "process lite with %d feature tickets (limit about %d): switch with --set process=full" % (len(feats), LITE_MAX_FEATURES))
    if len(pack.integrations) > LITE_MAX_INTEGRATIONS:
        f.warn("INDEX.md", "process lite with %d integrations (limit %d): switch with --set process=full" % (len(pack.integrations), LITE_MAX_INTEGRATIONS))
    batches, nobatch = {}, []
    for rel, d in sorted(feats.items()):
        fm = d["fm"] or {}
        n = len(listed_files(d["body"]))
        if n > LITE_MAX_FILES:
            f.warn(rel, "process lite: ticket lists %d files (limit %d); split it, or run the project as process=full" % (n, LITE_MAX_FILES))
        b = str(fm.get("batch", "")).strip()
        if b:
            batches.setdefault(b, []).append(rel)
        else:
            nobatch.append(rel)
    if nobatch:
        f.warn(nobatch[0], "process lite: %d feature ticket(s) have no 'batch:' (B1, B2 ...); lite reviews per batch of 2 to %d tickets" % (len(nobatch), LITE_MAX_BATCH))
    for b, rels in sorted(batches.items()):
        if len(rels) > LITE_MAX_BATCH:
            f.warn(rels[0], "process lite: batch %s has %d tickets (limit %d); one review of too much diff finds fewer defects" % (b, len(rels), LITE_MAX_BATCH))


AST_BLOCK = re.compile(r"^###\s+(AST-\d{2,3})\b[^\n]*\n", re.M)


def parse_assets(pack):
    """Parse docs/spec/assets-manifest.md '### AST-01 ...' blocks into {id: {Key: value}}."""
    d = pack.docs.get("assets-manifest.md")
    out, dups = {}, []
    if not d:
        return out, dups
    body = d["body"]
    ms = list(AST_BLOCK.finditer(body))
    for i, m in enumerate(ms):
        chunk = body[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(body)]
        if m.group(1) in out:
            dups.append(m.group(1))
        vals = {}
        for ln in chunk.splitlines():
            kv = re.match(r"^[-*]?\s*\**([A-Za-z]+)\**\s*:\s*(.+?)\s*$", ln)
            if kv:
                vals[kv.group(1).capitalize()] = kv.group(2).strip().strip("`")
        out[m.group(1)] = vals
    return out, dups


def check_assets(pack, f):
    """Image and video assets: kind asset tickets, assets-manifest.md, route and mode (human in the middle by default)."""
    tix = pack.tickets()
    atix = {rel: d for rel, d in tix.items() if (d["fm"] or {}).get("kind") == "asset"}
    plan, dups = parse_assets(pack)
    has_doc = "assets-manifest.md" in pack.docs
    if not atix and not has_doc and pack.asset_route in ("", "none"):
        return
    ids, deps, anc = ticket_ancestors(tix)
    route, auto = pack.asset_route, pack.asset_mode == "auto"
    if route in ("", "none") and (atix or has_doc):
        f.err("INDEX.md", "assets are planned but INDEX has no asset_route (agy-builtin | chatgpt-web | codex | od-media): record the answer with --set asset_route=...")
    if route not in ("", "none") and not os.path.isfile(os.path.join(pack.root, "repo-root", "assets", "MANIFEST.md")):
        f.err("repo-root/assets/MANIFEST.md", "missing: copy the result-table template (design-docs assets/MANIFEST.md); the asset checks and the human approval need it")
    if atix and not has_doc:
        f.err("assets-manifest.md", "kind: asset tickets exist but docs/spec/assets-manifest.md is missing (one '### AST-xx' block per asset)")
    if has_doc and not plan:
        f.err("assets-manifest.md", "no '### AST-01 Title' blocks found")
    for i in dups:
        f.err("assets-manifest.md", "duplicate asset id %s" % i)
    reqs = set(pack.requirement_ids())
    for aid, v in sorted(plan.items()):
        for key in ("Purpose", "Slug", "Prompt", "Aspect", "Format", "Alt"):
            if not v.get(key):
                f.err("assets-manifest.md", "%s: missing '%s:' line" % (aid, key))
        if v.get("Purpose") and not (set(RID.findall(v["Purpose"])) & reqs):
            f.err("assets-manifest.md", "%s: Purpose must name an existing R-xx" % aid)
        if v.get("Slug") and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", v["Slug"]):
            f.err("assets-manifest.md", "%s: Slug must be lowercase letters, digits and hyphens" % aid)
        if v.get("Aspect") and not re.fullmatch(r"\d{1,2}:\d{1,2}", v["Aspect"]):
            f.err("assets-manifest.md", "%s: Aspect must look like 16:9" % aid)
        if v.get("Format") and v["Format"].lower() not in ASSET_FORMATS:
            f.err("assets-manifest.md", "%s: Format must be one of %s" % (aid, ", ".join(ASSET_FORMATS)))
        if re.search(r"https?://", v.get("Prompt", "")):
            f.warn("assets-manifest.md", "%s: a prompt with a URL invites copying; describe the image in words" % aid)
    if plan and not atix:
        f.warn("tickets/", "assets-manifest.md has %d asset(s) but no kind: asset ticket" % len(plan))
    steps = {}
    for rel, d in sorted(atix.items()):
        fm = d["fm"] or {}
        step = str(fm.get("asset_step", ""))
        listed = as_list(fm.get("assets"))
        tid = str(fm.get("id", ""))
        if step not in ASSET_STEPS:
            f.err(rel, "asset ticket needs asset_step: prepare | generate | ingest")
            continue
        if not listed:
            f.err(rel, "asset ticket needs assets: [AST-01, ...]")
        for a in listed:
            if a not in plan:
                f.err(rel, "assets: %s is not defined in assets-manifest.md" % a)
            steps.setdefault((a, step), []).append(tid)
        vids = [a for a in listed if (plan.get(a, {}).get("Format", "")).lower() in ("mp4", "webm")]
        generated_by_agent = step == "generate" and fm.get("owner") == "executor"
        if generated_by_agent:
            if not (auto and route == "agy-builtin"):
                f.err(rel, "asset generation by the executor needs asset_mode=auto and asset_route=agy-builtin; otherwise the human generates (ChatGPT web must never be automated)")
            if vids:
                f.err(rel, "video assets (%s) are always generated by the human: there is no verified automatic route" % ", ".join(vids))
            smokes = [t for t, r in ids.items() if t.startswith("T-000h") and (tix[r]["fm"] or {}).get("kind") == "smoke-test"]
            if not smokes or not (set(smokes) & anc(tid)):
                f.err(rel, "automatic generation must depend on the image smoke test T-000h (kind: smoke-test)")
        if step == "generate" and not (set(x for x, r in ids.items() if (tix[r]["fm"] or {}).get("asset_step") == "prepare") & anc(tid)):
            f.warn(rel, "generate step should depend on a prepare step")
        if step == "prepare" and "check_asset.py" not in (section(d["body"], "Verification") or ""):
            f.err(rel, "prepare ticket must run `check_asset.py prompts` in its Verification (one prompt file per asset id, equal to the plan)")
        if step == "ingest":
            if not (set(x for x, r in ids.items() if (tix[r]["fm"] or {}).get("asset_step") == "generate") & anc(tid)):
                f.err(rel, "ingest step must depend on a generate step")
            if "check_asset.py" not in (section(d["body"], "Verification") or ""):
                f.err(rel, "ingest ticket must run scripts/check_asset.py in its Verification")
    for aid in sorted(plan):
        for step in ASSET_STEPS:
            if (aid, step) not in steps:
                f.err("tickets/", "%s has no '%s' step ticket (kind: asset, asset_step: %s, assets: [%s])" % (aid, step, step, aid))
    if auto and route != "agy-builtin":
        f.err("INDEX.md", "asset_mode auto is only supported for asset_route agy-builtin")
    if route == "agy-builtin" and not any(t.startswith("T-000h") for t in ids):
        f.warn("tickets/", "asset_route agy-builtin without the smoke test T-000h: nobody has proven the built-in image tool works for this setup")
    if route == "od-media" and "open-design" not in [x.lower() for x in pack.integrations]:
        f.warn("INDEX.md", "asset_route od-media uses the Open Design CLI: add 'open-design' to integrations")
    for rel, d in sorted(tix.items()):
        fm = d["fm"] or {}
        if fm.get("kind") == "asset":
            continue
        for a in as_list(fm.get("assets")):
            if a not in plan:
                f.err(rel, "assets: %s is not defined in assets-manifest.md" % a)
                continue
            ing = set(t for (aa, st), ts in steps.items() if aa == a and st == "ingest" for t in ts)
            if not (ing & anc(str(fm.get("id", "")))):
                f.err(rel, "uses %s but does not depend (directly or transitively) on its ingest ticket" % a)


LESSON_LINE = re.compile(r"^[ \t]*[-*]?[ \t]*(L-\d{2,3})[ \t]*\|(.*)$", re.M)


def check_lessons(pack, f):
    """lessons.md (planner-only, short) and the 'lessons:' references in tickets."""
    ld = pack.docs.get("lessons.md")
    known = {}
    if ld:
        for m in LESSON_LINE.finditer(ld["body"]):
            known[m.group(1)] = m.group(2)
            if m.group(2).count("|") < 2 or not all(x.strip() for x in m.group(2).split("|")[:3]):
                f.warn("lessons.md", "%s: write 'L-xx | trigger | rule | evidence' (four parts)" % m.group(1))
        if len(known) > LESSON_LIMIT:
            f.warn("lessons.md", "%d lessons (limit about %d): merge or drop the oldest; lessons load into every ticket that cites them" % (len(known), LESSON_LIMIT))
    for rel, d in sorted(pack.tickets().items()):
        for lid in as_list((d["fm"] or {}).get("lessons")):
            if lid not in known:
                f.err(rel, "lessons: %s is not defined in lessons.md" % lid)


MUTANTS_BLOCK = re.compile(r"```mutants[ \t]*\r?\n(.*?)```", re.S)


def parse_mutants(text):
    """Return (list of dicts, list of error strings) from the ticket's ```mutants block (one JSON object per line)."""
    m = MUTANTS_BLOCK.search(text)
    if not m:
        return None, []
    out, errs = [], []
    for n, ln in enumerate(m.group(1).splitlines(), 1):
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
            if not isinstance(o, dict) or not all(isinstance(o.get(k), str) and o.get(k) for k in ("file", "find", "replace")):
                raise ValueError("needs string fields file, find, replace")
            if o["find"] == o["replace"]:
                raise ValueError("find and replace are identical")
            out.append(o)
        except (ValueError, RecursionError) as e:
            errs.append("line %d: %s" % (n, str(e)[:80]))
    return out, errs


def check_mutants(pack, f):
    """Planner-written mutants for risky tickets; scripts/check_mutants.py runs them (human or executor, after the ticket passes)."""
    for rel, d in sorted(pack.tickets().items()):
        fm = d["fm"] or {}
        if fm.get("owner") == "human":
            continue
        muts, errs = parse_mutants(d["body"])
        for e in errs:
            f.err(rel, "mutants block %s" % e)
        risky = fm.get("kind") == "feature" and (str(fm.get("risk", "normal")).lower() == "high" or str(fm.get("tests_first", "false")).lower() == "true")
        if muts is None:
            if risky:
                f.warn(rel, "risky ticket (risk high or tests_first) has no ```mutants block: add 2 to 4 one-line mutations so weak tests are caught (check_mutants.py)")
            continue
        if not 2 <= len(muts) <= 4 and not errs:
            f.warn(rel, "mutants block has %d entries; write 2 to 4 non-equivalent mutations" % len(muts))
        body = strip_code(d["body"])
        listed = " ".join(listed_files(d["body"]))
        for mu in muts:
            if mu["file"] not in listed and mu["file"] not in body:
                f.warn(rel, "mutant file %s is not one of the ticket's listed files" % mu["file"])


def check_ready(pack, f):
    rd = pack.docs.get("READY.md")
    if not rd:
        return
    unchecked = [ln for ln in rd["text"].splitlines() if re.match(r"^\s*[-*]\s+\[ \]", ln)]
    for ln in unchecked:
        f.err("READY.md", "unchecked item: %s" % ln.strip()[:80])
    checked = [ln for ln in rd["text"].splitlines() if re.match(r"^\s*[-*]\s+\[[xX]\]", ln)]
    if len(checked) < 10:
        f.err("READY.md", "Definition of Ready checklist looks incomplete (%d checked items, expect >= 10)" % len(checked))
    if not re.search(r"(?i)unmeasured", rd["text"]):
        f.err("READY.md", "cost/size estimate must be labelled 'unmeasured' (no A/B run yet)")
    if (rd["fm"] or {}).get("status") != "ready":
        f.err("READY.md", "status is not 'ready': tick each item you verified (- [x]) and set status: ready, or the pack is not deliverable")


def check_placeholders(pack, f):
    """A pack that still contains template placeholders is not finished (default templates must never pass)."""
    for rel, d in pack.docs.items():
        text = d["text"]
        if re.search(r"YYYY-MM-DD|\bT-xxx\b|\bT-0xx\b|<slug>", strip_code(text)):
            f.err(rel, "template placeholder left (YYYY-MM-DD, T-xxx, <slug> ...): fill it in")
        spans = []
        for m in re.finditer(r"```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)```", text, re.S):
            spans.append((m.group(1).lower(), m.group(2)))
        prose = strip_code(text)
        scan = [("", prose)] + [(lang, body) for lang, body in spans if lang not in CODE_LANGS_WITH_ANGLES]
        for idx, (_, chunk) in enumerate(scan):
            for m in TEMPLATE_PLACEHOLDER.finditer(chunk):
                first = re.match(r"[a-z0-9]+", m.group(1)).group(0)
                if first in HTML_TAGS:
                    continue
                msg = "template placeholder left: <%s> (fill it in or remove it)" % m.group(1)[:40]
                if idx == 0:
                    f.err(rel, msg)      # in prose: never acceptable
                else:
                    f.warn(rel, msg + " [in a code block; fine only for a CLI usage synopsis]")
                break


def check_open_questions(pack, f):
    brief = pack.docs.get("00-brief.md")
    if brief:
        oq = section(brief["body"], "Open questions")
        if oq is None:
            f.err("00-brief.md", "missing '## Open questions' section (write 'None.' when closed)")
        elif not oq.strip().lower().startswith("none"):
            f.err("00-brief.md", "open questions remain; resolve them or convert to assumptions, then write 'None.'")
    for rel in pack.authored():
        if rel.startswith("tickets/") and re.search(r"(?i)^##\s+Execution report", pack.docs[rel]["body"], re.M):
            body = pack.docs[rel]["body"].split("## Execution report")[0]
        else:
            body = pack.docs[rel]["body"]
        body = strip_code(body)
        for m in OPEN_MARKERS.finditer(body):
            f.err(rel, "open-item marker %r left in the document" % m.group(0))
            break


def check_security(pack, f):
    for rel, d in pack.docs.items():
        text = d["text"]
        for pat, label in SECRET_PATTERNS:
            for m in re.finditer(pat, text):
                if not token_is_placeholder(text, m):
                    f.err(rel, "possible secret (%s): %s..." % (label, m.group(0)[:6]))
        for m in GENERIC_SECRET.finditer(text):
            val = m.group(2)
            if not PLACEHOLDER.search(val) and not val.startswith(("$", "<")) and not re.fullmatch(r"[a-z_.]+", val):
                f.err(rel, "possible hard-coded secret near %r" % m.group(1))
        for m in re.finditer(r"%%(.{0,4000}?)%%", text, re.S):
            f.warn(rel, "Obsidian %%comment%% is hidden in reading view but visible to agents; remove it")
            if HIDDEN_INSTR.search(m.group(1)):
                f.err(rel, "hidden %%comment%% contains instruction-like text")
        for m in re.finditer(r"<!--(.{0,4000}?)-->", text, re.S):
            if HIDDEN_INSTR.search(m.group(1)):
                f.err(rel, "HTML comment contains instruction-like text")
        if "--dangerously-skip-permissions" in text and not re.search(r"(?i)(never|do not|don't|forbid|forbidden)", text):
            f.err(rel, "mentions --dangerously-skip-permissions without forbidding it")
        for m in re.finditer(r"```mermaid\s*\n\s*([^\s]+)", text):
            kind = m.group(1)
            if kind not in MERMAID_OK:
                f.warn(rel, "mermaid type %r may not render everywhere (use flowchart, sequence, state, class, ER, gantt, pie)" % kind)


BUDGET = {"S": 700, "M": 1500, "L": 2500}


def check_size(pack, f):
    total = sum(len(d["text"].splitlines()) for r, d in pack.docs.items() if not r.startswith("design/references"))
    budget = BUDGET.get(pack.tier, 1500) + (600 if pack.design_route == "c" else 0)  # route C adds five tickets
    if total > budget:
        f.warn("INDEX.md", "pack is %d lines of markdown; budget for tier %s%s is about %d. Cut ceremony: fewer ADRs, shorter tickets"
               % (total, pack.tier, " with design route c" if pack.design_route == "c" else "", budget))
    for rel in pack.authored():
        n = len(pack.docs[rel]["text"].splitlines())
        if n > 500 and not rel.startswith("tickets/"):
            f.warn(rel, "%d lines; split it so reviews stay cheap" % n)


# ---------------------------------------------------------------- status and index writer
def status(pack):
    print("pack root : %s" % pack.root)
    print("tier      : %s   ui: %s   declared gate: %s   integrations: %s"
          % (pack.tier, pack.ui, pack.root_fm.get("gate", "?"), pack.integrations or "none"))
    nprem = sum(1 for d in pack.tickets().values()
                if str((d["fm"] or {}).get("model_tier", "standard")).lower() != "standard")
    print("quality   : %s   premium tickets: %d of budget %s   design route: %s   claude models: %s"
          % (pack.quality, nprem, pack.premium_budget, pack.design_route or "-", pack.claude_models or "?"))
    print("process   : %s   asset route: %s (%s)" % (pack.process, pack.asset_route or "-", pack.asset_mode))
    reached = -1
    for g in range(0, 5):
        miss = [r for r in required_files(pack, g) if r not in pack.docs]
        print("gate %d files: %s" % (g, "complete" if not miss else "missing " + ", ".join(miss[:6]) + (" ..." if len(miss) > 6 else "")))
        if not miss and reached == g - 1:
            reached = g
    tix = pack.tickets()
    counts = {}
    for d in tix.values():
        s = (d["fm"] or {}).get("status", "?")
        counts[s] = counts.get(s, 0) + 1
    print("tickets   : %d %s" % (len(tix), counts or ""))
    nxt = {-1: "finish Gate 0 (brief + executor profile)",
           0: "Gate 0 playback approval, then requirements (Gate 1)",
           1: "Gate 1 approval, then design (Gate 2)",
           2: "Gate 2 approval, then verification + tickets (Gate 3)",
           3: "run Gate 4 readiness review",
           4: "pack complete; use the review loop"}[reached]
    print("files reach gate %d -> next: %s" % (reached, nxt))


def title_of(doc, rel):
    fm = doc["fm"] or {}
    if fm.get("title"):
        return str(fm["title"])
    m = re.search(r"^#\s+(.+)$", doc["body"], re.M)
    return m.group(1).strip() if m else rel


def fix_index(pack):
    today = datetime.date.today().isoformat()
    dirs = {}
    for rel in pack.authored():
        dirs.setdefault(os.path.dirname(rel), []).append(rel)
    allrel = set(pack.authored())
    for d in dirs:
        parts = d.split("/") if d else []
        for i in range(1, len(parts)):
            dirs.setdefault("/".join(parts[:i]), [])
    written = []
    for d in sorted(dirs):
        idx = (d + "/INDEX.md") if d else "INDEX.md"
        subs = child_dirs(d, allrel)
        lines = []
        for rel in sorted(dirs[d]):
            if rel == idx:
                continue
            doc = pack.docs[rel]
            when = (doc["fm"] or {}).get("when") or "see the document"
            lines.append("- [%s](%s): %s" % (title_of(doc, rel), os.path.basename(rel).replace(" ", "%20"), when))
        for s in subs:
            lines.append("- [%s/](%s/INDEX.md): sub-index" % (s, s))
        existing = pack.docs.get(idx)
        if existing and existing["fm"] and "__bad__" not in existing["fm"]:
            m = re.match(r"^(---\r?\n.*?\r?\n---[ \t]*\r?\n)", existing["text"], re.S)
            head = m.group(1)
            ttl = title_of(existing, idx)
        else:
            ttl = "Index" if d else "Spec pack index"
            head = "---\ntitle: %s\ndate: %s\ntags: [index]\n%s---\n" % (
                ttl, today, ("tier: M\nui: false\ngate: 0\nintegrations: []\n" if not d else ""))
        body = "\n# %s\n\n%s\n\nRelated: %s\n" % (
            ttl, "\n".join(lines) if lines else "- (empty)",
            "[pack index](%sINDEX.md)" % ("../" * len(d.split("/"))) if d else "none")
        path = os.path.join(pack.root, idx.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(head + body)
        written.append(idx)
    print("wrote %d index file(s): %s" % (len(written), ", ".join(written)))


def set_frontmatter(pack, pairs):
    """Edit keys in the root INDEX.md frontmatter (tier, ui, gate, integrations, quality, premium_budget, design_route)."""
    allowed = {"tier", "ui", "gate", "integrations", "quality", "premium_budget", "design_route", "process", "claude_models", "asset_route", "asset_mode"}
    path = os.path.join(pack.root, "INDEX.md")
    if "INDEX.md" not in pack.docs:
        print("error: INDEX.md not found", file=sys.stderr)
        return 2
    text = read(path)
    m = re.match(r"^(---\r?\n)(.*?)(\r?\n---[ \t]*\r?\n)", text, re.S)
    if not m:
        print("error: INDEX.md has no frontmatter", file=sys.stderr)
        return 2
    head = m.group(2)
    for pair in pairs:
        if "=" not in pair:
            print("error: expected KEY=VALUE, got %r" % pair, file=sys.stderr)
            return 2
        k, v = pair.split("=", 1)
        if k not in allowed:
            print("error: key must be one of %s" % ", ".join(sorted(allowed)), file=sys.stderr)
            return 2
        if k == "integrations":
            items = [x.strip() for x in v.split(",") if x.strip()]
            if not all(re.fullmatch(r"[a-z0-9][a-z0-9-]*", x) for x in items):
                print("error: integrations must be lowercase names with letters, digits and dashes", file=sys.stderr)
                return 2
            v = "[" + ", ".join(items) + "]"
        elif k == "tier" and v not in ("S", "M", "L"):
            print("error: tier must be S, M or L", file=sys.stderr)
            return 2
        elif k == "quality" and v not in QUALITY_MODES:
            print("error: quality must be economy, balanced or max", file=sys.stderr)
            return 2
        elif k == "premium_budget" and not re.fullmatch(r"[0-9]+", v):
            print("error: premium_budget must be a whole number", file=sys.stderr)
            return 2
        elif k == "design_route" and v not in DESIGN_ROUTES:
            print("error: design_route must be a, b, c or d", file=sys.stderr)
            return 2
        elif k == "process" and v not in PROCESS_MODES:
            print("error: process must be full or lite", file=sys.stderr)
            return 2
        elif k == "claude_models" and v not in CLAUDE_MODELS:
            print("error: claude_models must be none, 5.5 or 4.6", file=sys.stderr)
            return 2
        elif k == "asset_route" and v not in ASSET_ROUTES:
            print("error: asset_route must be one of %s" % ", ".join(ASSET_ROUTES), file=sys.stderr)
            return 2
        elif k == "asset_mode" and v not in ASSET_MODES:
            print("error: asset_mode must be manual or auto", file=sys.stderr)
            return 2
        elif k == "ui" and v not in ("true", "false"):
            print("error: ui must be true or false", file=sys.stderr)
            return 2
        elif k == "gate" and v not in ("0", "1", "2", "3", "4"):
            print("error: gate must be 0, 1, 2, 3 or 4", file=sys.stderr)
            return 2
        line = "%s: %s" % (k, v)
        if re.search(r"(?m)^%s\s*:.*$" % k, head):
            head = re.sub(r"(?m)^%s\s*:.*$" % k, lambda _m, line=line: line, head)
        else:
            head += "\n" + line
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(m.group(1) + head + m.group(3) + text[m.end():])
    print("updated INDEX.md frontmatter: %s" % ", ".join(pairs))
    return 0


# ---------------------------------------------------------------- main
def run(pack, gate, today=None):
    f = Findings()
    check_structure(pack, f, gate)
    check_frontmatter(pack, f)
    check_related(pack, f)
    linked = check_links(pack, f, gate)
    check_index_and_orphans(pack, f, linked)
    check_requirements(pack, f, gate)
    if gate >= 3:
        check_tickets(pack, f)
        check_tooling(pack, f)
        check_quality(pack, f, today)
        check_process(pack, f)
        check_assets(pack, f)
        check_lessons(pack, f)
        check_mutants(pack, f)
    if gate >= 4:
        check_ready(pack, f)
        check_open_questions(pack, f)
        check_placeholders(pack, f)
    check_security(pack, f)
    check_size(pack, f)
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a spec pack. Prints findings and a final RESULT line.")
    ap.add_argument("pack_dir", help="folder containing INDEX.md (e.g. docs/spec)")
    ap.add_argument("--gate", type=int, choices=[0, 1, 2, 3, 4], help="check only what is required through this gate (default 4)")
    ap.add_argument("--status", action="store_true", help="show tier, gate and next step, then exit")
    ap.add_argument("--fix-index", action="store_true", help="(re)write INDEX.md files from frontmatter title/when")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    ap.add_argument("--set", nargs="+", metavar="KEY=VALUE",
                    help="edit root INDEX.md frontmatter, e.g. --set gate=2 tier=S ui=false integrations=blender,figma quality=balanced premium_budget=3 design_route=c process=lite claude_models=4.6 asset_route=agy-builtin asset_mode=manual")
    ap.add_argument("--quiet", action="store_true", help="print only the summary (errors first, max 25 lines)")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = ap.parse_args(argv)
    if not os.path.isdir(args.pack_dir):
        print("error: %s is not a directory" % args.pack_dir, file=sys.stderr)
        return 2
    pack = Pack(args.pack_dir)
    if args.status:
        status(pack)
        return 0
    if args.set:
        return set_frontmatter(pack, args.set)
    if args.fix_index:
        fix_index(pack)
        if args.gate is None:
            return 0  # index repair only; run the check separately
        pack = Pack(args.pack_dir)
    gate = 4 if args.gate is None else args.gate
    f = run(pack, gate)
    if args.json:
        print(json.dumps([{"level": a, "path": b, "message": c} for a, b, c in f.items], indent=2))
    else:
        shown = sorted(f.items, key=lambda x: (x[0] != "ERROR", x[1]))
        if args.quiet:
            shown = shown[:25]
        for lvl, path, msg in shown:
            print("%s %s: %s" % (lvl, path, msg))
    ok = not f.errors
    print("RESULT: %s%s" % ("PASS" if ok else "FAIL", "" if ok else " (errors=%d warnings=%d)" % (len(f.errors), len(f.warnings))))
    if ok and f.warnings:
        print("(warnings=%d)" % len(f.warnings))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
