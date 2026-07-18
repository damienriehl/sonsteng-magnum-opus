#!/usr/bin/env python3
"""
build_site.py — Sonsteng Practicum platform site generator.

Renders the machine-readable data spine (data/) into a static, self-contained
student-facing curriculum site under site/platform/, per:
  - docs/plans/2026-07-17-001-feat-curriculum-buildout-plan.md  (Workstream 5)
  - docs/research/design-direction.md                            ("The Practicum Press")
  - docs/research/firm-dashboard-viz-spec.md                     (firm dashboard)

Python 3, standard library only. Idempotent. One command:
    python3 tools/build_site.py            # build everything + run link check
    python3 tools/build_site.py --check    # build + assert every internal href resolves

Owns:  tools/build_site.py + everything under site/platform/ EXCEPT site/platform/assets/
Reads (never writes): data/, app/chat/, docs/, site/platform/assets/

INSTRUCTOR-SIDE, NEVER RENDERED into any student-facing page:
    facts.md, exercise/instructor-notes.md, persona confidential/disclosure fact TEXT.
"""

import os
import re
import sys
import json
import glob
import html
import shutil
import csv
import io
from collections import defaultdict, OrderedDict

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
APP_CHAT = os.path.join(ROOT, "app", "chat")
SITE = os.path.join(ROOT, "site")
OUT = os.path.join(SITE, "platform")          # generation root
MATTERS_DIR = os.path.join(DATA, "matters")
CURRICULUM_DIR = os.path.join(DATA, "curriculum")   # handbook prose + deliverable templates

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)

def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)

def load_text(path):
    """Read a UTF-8 text file (curriculum markdown); '' if absent."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

def write_file(relpath, content):
    """relpath is relative to OUT (site/platform)."""
    dest = os.path.join(OUT, relpath)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    with open(dest, "wb") as fh:
        fh.write(content)
    return dest

def up_prefix(relpath):
    """Number of directory levels of relpath (under OUT) -> '../' climb string."""
    depth = relpath.count("/")
    return "../" * depth

def money(n, cents=False):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if cents:
        return "${:,.2f}".format(n)
    return "${:,.0f}".format(n)

def money_compact(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    a = abs(n)
    if a >= 1_000_000:
        return "${:.2f}M".format(n / 1_000_000)
    if a >= 1_000:
        return "${:.0f}K".format(n / 1_000)
    return "${:,.0f}".format(n)

def pct(n, digits=1):
    try:
        return "{:.{d}f}%".format(float(n) * 100 if abs(float(n)) <= 1.0001 else float(n), d=digits)
    except (TypeError, ValueError):
        return "—"

# --------------------------------------------------------------------------- #
# Minimal, safe Markdown -> HTML (stdlib only)
# Supports: headings, hr, pipe tables, blockquotes, ol/ul, bold/italic/code,
# paragraphs. All text is HTML-escaped first; only our own tags are emitted.
# --------------------------------------------------------------------------- #
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)")

def _inline(text):
    text = esc(text)
    text = _INLINE_CODE.sub(lambda m: "<code>" + m.group(1) + "</code>", text)
    text = _BOLD.sub(lambda m: "<strong>" + m.group(1) + "</strong>", text)
    text = _ITALIC.sub(lambda m: "<em>" + m.group(1) + "</em>", text)
    return text

def markdown(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)

    def flush_para(buf):
        if buf:
            out.append("<p>" + _inline(" ".join(buf).strip()) + "</p>")
            buf.clear()

    para = []
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank
        if not stripped:
            flush_para(para)
            i += 1
            continue

        # horizontal rule
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_para(para)
            out.append('<hr class="md-hr">')
            i += 1
            continue

        # heading
        h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h:
            flush_para(para)
            level = min(len(h.group(1)) + 1, 6)  # demote so page h1 stays unique
            out.append("<h{l}>{t}</h{l}>".format(l=level, t=_inline(h.group(2).strip())))
            i += 1
            continue

        # table (header row followed by separator row of dashes/pipes)
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\|?[\s:|-]+\|?$", lines[i + 1].strip()) and "-" in lines[i + 1]:
            flush_para(para)
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            body = []
            while i < n and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ['<div class="tablewrap"><table class="ledger md-table">']
            t.append("<thead><tr>" + "".join("<th>" + _inline(c) + "</th>" for c in header) + "</tr></thead>")
            t.append("<tbody>")
            for row in body:
                cells = []
                for c in row:
                    cls = ' class="num"' if re.match(r"^[\(\-]?\$?[\d,\.\)%\s/]+$", c.replace("**", "").strip()) and c.strip() else ""
                    cells.append("<td{cl}>{v}</td>".format(cl=cls, v=_inline(c)))
                t.append("<tr>" + "".join(cells) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue

        # blockquote
        if stripped.startswith(">"):
            flush_para(para)
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append('<blockquote class="md-quote">' + _inline(" ".join(buf)) + "</blockquote>")
            continue

        # unordered list
        if re.match(r"^[-*+]\s+", stripped):
            flush_para(para)
            items = []
            while i < n and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append("<li>" + _inline(re.sub(r"^[-*+]\s+", "", lines[i].strip())) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        # ordered list
        if re.match(r"^\d+\.\s+", stripped):
            flush_para(para)
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append("<li>" + _inline(re.sub(r"^\d+\.\s+", "", lines[i].strip())) + "</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        # paragraph text
        para.append(stripped)
        i += 1

    flush_para(para)
    return "\n".join(out)

# --------------------------------------------------------------------------- #
# Page shell
# --------------------------------------------------------------------------- #
SITE_TITLE = "Sonsteng Practicum"

def page_shell(relpath, title, docket, crumbs, body, body_class=""):
    """
    relpath: output path under OUT (for asset depth).
    crumbs:  list of (label, href_or_None). href relative to this page.
    body:    inner HTML placed inside <main>.
    """
    up = up_prefix(relpath)
    crumb_html = []
    for idx, (label, href) in enumerate(crumbs):
        if idx:
            crumb_html.append('<span class="sep">/</span>')
        if href:
            crumb_html.append('<a href="{h}">{l}</a>'.format(h=esc(href), l=esc(label)))
        else:
            crumb_html.append("<span>{l}</span>".format(l=esc(label)))
    crumb_bar = ('<nav class="breadcrumb" aria-label="Breadcrumb">' + "".join(crumb_html) + "</nav>") if crumbs else ""

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title} — {site}</title>
<meta name="description" content="Sonsteng Practicum — a living casebook of 20 deep synthetic legal matters, a skills taxonomy, and a firm dashboard.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%23f4efe4'/%3E%3Crect y='1' width='16' height='2' fill='%23a9822f'/%3E%3Crect y='12' width='16' height='1' fill='%23a9822f'/%3E%3Crect x='2' y='6' width='3' height='4' fill='%237c1e2b'/%3E%3C/svg%3E">
<link rel="stylesheet" href="{up}assets/fonts.css">
<link rel="stylesheet" href="{up}assets/theme.css">
<link rel="stylesheet" href="{up}platform.css">
<script>try{{if(localStorage.getItem('sonsteng-type-lg')==='1')document.documentElement.classList.add('type-lg');}}catch(e){{}}</script>
</head>
<body class="{bodyclass}">
<a class="skip-link" href="#main">Skip to content</a>
<header class="masthead">
  <div class="masthead__inner">
    <a class="masthead__brand" href="{up}index.html">SONSTENG PRACTICUM</a>
    <span class="masthead__docket mono">{docket}</span>
    <button type="button" class="type-toggle mono" id="type-toggle" aria-pressed="false" title="Toggle large type">A+ LARGE TYPE</button>
  </div>
</header>
{crumb}
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="site-footer__inner">
    <div>
      <strong>Sonsteng Practicum</strong> — a living casebook.
      <span class="mono">MIT-LICENSED</span> · no platform fees; bring your own key.
    </div>
    <div class="site-footer__links mono">
      <a href="{up}data/index.json">DATA CATALOG</a>
      <a href="{up}index.html">HOME</a>
      <a href="{up}about/third-party.html">THIRD-PARTY</a>
    </div>
  </div>
</footer>
<script src="{up}platform.js" defer></script>
</body>
</html>""".format(
        title=esc(title), site=esc(SITE_TITLE), up=up, docket=esc(docket),
        bodyclass=esc(body_class), crumb=crumb_bar, body=body,
        root=up + "../",  # THIRD-PARTY.md lives at repo/site's parent; link is best-effort
    )

# --------------------------------------------------------------------------- #
# Chips
# --------------------------------------------------------------------------- #
def tier_chip(tier, jurisdiction_code):
    if tier == "meridian":
        return '<span class="chip chip--meridian">MERIDIAN</span>'
    code = (jurisdiction_code or "").upper()
    return '<span class="chip chip--state">{c}</span>'.format(c=esc(code))

def folio_chip(folio, no_equiv):
    if no_equiv:
        return '<span class="chip chip--folio chip--noeq" title="No FOLIO equivalent">NO-FOLIO</span>'
    if not folio:
        return ""
    iri = folio.get("iri", "")
    conf = (folio.get("mapping_confidence") or "").upper()
    return ('<span class="chip chip--folio chip--conf-{cl}" title="FOLIO {iri} · {conf}">'
            'FOLIO · {conf} · {iri}</span>').format(cl=esc(conf.lower()), iri=esc(iri), conf=esc(conf))

# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
SHAPE_LABELS = {
    "employment_arbitration": "Employment arbitration",
    "attorney_discipline": "Attorney discipline",
    "auto_negligence": "Auto-negligence jury trial",
    "real_estate_negotiation": "Real-estate purchase negotiation",
    "criminal_dwi": "Criminal DWI",
    "noncompete_trade_secret": "Non-compete / trade secrets",
    "ucc_sale_of_goods": "UCC sale-of-goods dispute",
    "juvenile_delinquency": "Juvenile delinquency",
    "marriage_dissolution": "Marriage dissolution",
    "wills_probate": "Wills & probate contest",
}
MODULE_META = OrderedDict([
    ("M1", {"code": "M1", "title": "Foundational", "thesis": "The groundwork — how to think, read, and carry yourself as a lawyer before the substance arrives.", "accent": "brass"}),
    ("M2", {"code": "M2", "title": "Substantive + Skills", "thesis": "The trades of lawyering, learned on real-shaped matters across ten practice areas.", "accent": "claret"}),
    ("M3", {"code": "M3", "title": "Transition to Practice", "thesis": "The business of law and the professional judgment that turns a graduate into a practitioner.", "accent": "green"}),
])

def load_jurisdictions():
    juris = {}
    mer = load_json(os.path.join(DATA, "jurisdictions", "meridian.json"))
    juris["meridian"] = mer
    for f in glob.glob(os.path.join(DATA, "jurisdictions", "real", "*.json")):
        d = load_json(f)
        juris[(d.get("code") or "").upper()] = d
    return juris

def load_personas_for(matter_dir):
    """Return dict id -> persona for a matter, skipping non-persona files."""
    result = {}
    for pf in sorted(glob.glob(os.path.join(matter_dir, "personas", "*.json"))):
        base = os.path.basename(pf)
        if base == "topic-labels.json":
            continue
        try:
            p = load_json(pf)
        except Exception:
            continue
        if not isinstance(p, dict) or "id" not in p or "identity" not in p:
            continue
        result[p["id"]] = p
    return result

def load_corpus():
    manifest = load_json(os.path.join(MATTERS_DIR, "manifest.json"))
    juris = load_jurisdictions()
    matters = []
    by_id = {}
    for mdir in sorted(glob.glob(os.path.join(MATTERS_DIR, "m*-*"))):
        if not os.path.isdir(mdir):
            continue
        mpath = os.path.join(mdir, "matter.json")
        if not os.path.exists(mpath):
            continue
        m = load_json(mpath)
        m["_dir"] = mdir
        m["_slug"] = m.get("slug") or os.path.basename(mdir)
        m["_personas"] = load_personas_for(mdir)
        # exercise
        ex_path = os.path.join(mdir, "exercise", "exercise.json")
        m["_exercise"] = load_json(ex_path) if os.path.exists(ex_path) else None
        # rubric
        ru_path = os.path.join(mdir, "rubric.json")
        m["_rubric"] = load_json(ru_path) if os.path.exists(ru_path) else None
        # business
        bz_path = os.path.join(mdir, "business", "business.json")
        m["_business"] = load_json(bz_path) if os.path.exists(bz_path) else None
        matters.append(m)
        by_id[m["id"]] = m
    matters.sort(key=lambda x: x["id"])
    skills = load_json(os.path.join(DATA, "taxonomy", "skills.json"))
    tasks = load_json(os.path.join(DATA, "taxonomy", "tasks.json"))
    firm = load_json(os.path.join(DATA, "firm", "firm.json"))
    curriculum = load_curriculum()
    return {
        "manifest": manifest, "matters": matters, "by_id": by_id,
        "juris": juris, "skills": skills, "tasks": tasks, "firm": firm,
        "curriculum": curriculum,
    }

# --------------------------------------------------------------------------- #
# Curriculum handbook layer — volume prose + deliverable templates
# --------------------------------------------------------------------------- #
# The six course deliverable templates, in teaching order. Each is authored as
# clean markdown under data/curriculum/templates/ and rendered by the generator
# onto a single print-friendly platform/templates/ page (see build_templates).
CURRICULUM_TEMPLATES = [
    ("time-sheet",                 "Weekly Time Sheet",            "TIME & BILLING"),
    ("engagement-letter-checklist","Engagement-Letter Checklist",  "CLIENT INTAKE"),
    ("client-interview-plan",      "Client-Interview Plan",        "FACT DEVELOPMENT"),
    ("ssnp",                       "Strategic Settlement & Negotiation Plan", "NEGOTIATION"),
    ("learning-portfolio",         "Learning Portfolio",           "REFLECTION"),
    ("reflective-report",          "Reflective Report",            "REFLECTION"),
]

def load_curriculum():
    """Load the three volume-prose files (m1/m2/m3.md) and the six deliverable
    templates as raw markdown. Missing files degrade gracefully to ''."""
    volumes = {}
    for code in ("M1", "M2", "M3"):
        volumes[code] = load_text(os.path.join(CURRICULUM_DIR, code.lower() + ".md"))
    templates = []
    for stem, title, kicker in CURRICULUM_TEMPLATES:
        md = load_text(os.path.join(CURRICULUM_DIR, "templates", stem + ".md"))
        templates.append({"stem": stem, "title": title, "kicker": kicker, "md": md})
    return {"volumes": volumes, "templates": templates}

# --------------------------------------------------------------------------- #
# Business math helpers
# --------------------------------------------------------------------------- #
def matter_fees(m):
    """Derived per-matter fee figure for the book-of-business chart."""
    biz = m.get("_business")
    if not biz:
        return 0
    invs = biz.get("invoices") or []
    if invs:
        return sum(float(iv.get("fees", 0)) for iv in invs)
    te = biz.get("time_entries") or []
    return sum(float(t.get("hours", 0)) * float(t.get("rate", 0)) for t in te)

# --------------------------------------------------------------------------- #
# SVG chart primitives
# --------------------------------------------------------------------------- #
def _svg(width, height, inner, label):
    return ('<svg class="chart-svg" viewBox="0 0 {w} {h}" width="100%" '
            'preserveAspectRatio="xMinYMin meet" role="img" aria-label="{lab}">{inner}</svg>').format(
        w=width, h=height, inner=inner, lab=esc(label))

def _text(x, y, s, cls="chart-lbl", anchor="start", extra=""):
    return '<text x="{x}" y="{y}" class="{c}" text-anchor="{a}" {e}>{s}</text>'.format(
        x=round(x, 1), y=round(y, 1), c=cls, a=anchor, s=esc(s), e=extra)

def _rect(x, y, w, h, fill, rx=0, extra=""):
    return '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{f}" {e}/>'.format(
        x=round(x, 1), y=round(y, 1), w=round(max(w, 0), 1), h=round(h, 1), rx=rx, f=fill, e=extra)

def viz_mark(parts, hits, x, y, w, h, fill, value, label, color,
             rx=0, pat=None, seg=False, hit=None):
    """Emit one interactive chart mark.

    parts : list — receives the visible <rect> and, when `pat` is given, a
            pattern <rect> overlay (pointer-events:none; shown only when the
            PATTERNS toggle is on or under forced-colors).
    hits  : list — receives a transparent, keyboard-focusable hit-rect that
            owns pointer/focus and carries the tooltip payload: value LEADS
            (data-v, rendered strong), label FOLLOWS (data-l), and data-c keys
            the swatch. aria-label mirrors the readout so the mark reads the
            same to assistive tech (role="img"). Hit-rects render last (on top)
            so thin marks still get a >=24px hit target.
    seg   : True for a segment inside a stacked bar — hit-rect keeps the
            segment width (only height grows to the 24px floor), so adjacent
            segments never overlap.
    hit   : (hx,hy,hw,hh) explicit override — used for vertical columns.
    """
    x = float(x); y = float(y); w = float(max(w, 0)); h = float(h)
    parts.append('<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{f}"/>'.format(
        x=round(x, 1), y=round(y, 1), w=round(w, 1), h=round(h, 1), rx=rx, f=fill))
    if pat:
        parts.append('<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                     'fill="url(#{p})" class="viz-pat" pointer-events="none"/>'.format(
            x=round(x, 1), y=round(y, 1), w=round(w, 1), h=round(h, 1), rx=rx, p=pat))
    if hit is not None:
        hx, hy, hw, hh = hit
    elif seg:
        hh = max(h, 24.0); hy = y - (hh - h) / 2; hx = x; hw = w
    else:
        hh = max(h, 24.0); hy = y - (hh - h) / 2; hx = x; hw = max(w, 24.0)
    aria = value + ("  " + label if label else "")
    hits.append('<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="transparent" '
                'class="viz-mark" tabindex="0" role="img" data-v="{v}" data-l="{l}" '
                'data-c="{c}" aria-label="{a}"/>'.format(
        x=round(hx, 1), y=round(hy, 1), w=round(max(hw, 0), 1), h=round(hh, 1),
        v=esc(value), l=esc(label), c=color, a=esc(aria)))

# 8 diagonal line-textures (45deg / 135deg, four densities each). Assigned per
# chart: categorical charts use distinct angle+density per slot; the ordinal
# funnel and AR severity ramps order density by magnitude. Strokes keep an
# explicit color, but forced-color-adjust stays "auto" so under forced-colors
# the OS remaps them to a system color (guaranteed visible without JS).
def _pattern_defs():
    specs = [("p45_6", 45, 6), ("p45_5", 45, 5), ("p45_4", 45, 4), ("p45_3", 45, 3),
             ("p135_6", 135, 6), ("p135_5", 135, 5), ("p135_4", 135, 4), ("p135_3", 135, 3)]
    pats = "".join(
        '<pattern id="{i}" patternUnits="userSpaceOnUse" width="{s}" height="{s}" '
        'patternTransform="rotate({a})"><line x1="0" y1="0" x2="0" y2="{s}" '
        'stroke="#0b0b0b" stroke-opacity=".5" stroke-width="1.1"/></pattern>'.format(i=i, s=s, a=a)
        for i, a, s in specs)
    return ('<svg class="viz-defs" width="0" height="0" aria-hidden="true" focusable="false" '
            'style="position:absolute;width:0;height:0"><defs>' + pats + '</defs></svg>')

def chart_card(cid, title, caption, svg, table_html, story=""):
    return """
<section class="viz-card" aria-labelledby="{cid}-t">
  <div class="viz-card__head">
    <h3 id="{cid}-t" class="viz-card__title">{title}</h3>
    <button type="button" class="viz-toggle mono" data-target="{cid}-tbl" aria-expanded="false">TABLE</button>
  </div>
  <p class="viz-card__caption">{caption}</p>
  {story}
  <div class="viz-chart">{svg}</div>
  <div class="viz-table" id="{cid}-tbl" hidden>{table}</div>
</section>""".format(cid=esc(cid), title=esc(title), caption=esc(caption),
                     story=story, svg=svg, table=table_html)

def _table(caption, headers, rows, num_cols=None):
    num_cols = num_cols or set()
    th = "".join("<th{cl}>{h}</th>".format(cl=' class="num"' if i in num_cols else "", h=esc(h))
                 for i, h in enumerate(headers))
    body = []
    for row in rows:
        tds = []
        for i, c in enumerate(row):
            cl = ' class="num"' if i in num_cols else ""
            tds.append("<td{cl}>{v}</td>".format(cl=cl, v=esc(c)))
        body.append("<tr>" + "".join(tds) + "</tr>")
    return ('<div class="tablewrap"><table class="ledger"><caption>{cap}</caption>'
            '<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>').format(
        cap=esc(caption), th=th, body="".join(body))

# --------------------------------------------------------------------------- #
# Copy chat app
# --------------------------------------------------------------------------- #
def copy_chat_app():
    """Copy the chat app verbatim (rewrite nothing) — every file except test.html."""
    copied = 0
    for src in sorted(glob.glob(os.path.join(APP_CHAT, "*"))):
        name = os.path.basename(src)
        if name == "test.html" or not os.path.isfile(src):
            continue
        with open(src, "rb") as fh:
            data = fh.read()
        write_file(os.path.join("chat", name), data)
        copied += 1
    return copied

# --------------------------------------------------------------------------- #
# Shared owned assets: platform.css + platform.js
# --------------------------------------------------------------------------- #
def write_platform_assets():
    write_file("platform.css", PLATFORM_CSS)
    write_file("platform.js", PLATFORM_JS)

# --------------------------------------------------------------------------- #
# Shared owned stylesheet (layout only; all tokens come from theme.css)
# --------------------------------------------------------------------------- #
PLATFORM_CSS = r"""/* platform.css — layout helpers for generated pages.
   Composes from theme.css primitives; introduces NO new palette/font/radius. */

.skip-link{position:absolute;left:-9999px;top:0;background:var(--paper);color:var(--ink);
  padding:.6em 1em;border:var(--rule-bold) solid var(--claret);z-index:100}
.skip-link:focus{left:.5rem;top:.5rem}

.masthead__inner{gap:var(--sp-3)}
.type-toggle{margin-left:var(--sp-6);appearance:none;cursor:pointer;background:transparent;
  border:var(--rule) solid var(--line);border-radius:var(--radius);
  font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;
  letter-spacing:.08em;color:var(--ink-soft);padding:.4em .7em;min-height:2.2em}
.type-toggle[aria-pressed="true"]{box-shadow:inset 0 0 0 var(--rule) var(--brass);color:var(--ink)}

/* ---- generic layout ---- */
.lede{font-size:var(--fs-md);color:var(--ink-soft);max-width:var(--maxw-read)}
.section-head{margin:var(--sp-12) 0 var(--sp-6)}
.grid{display:grid;gap:var(--sp-6)}
.grid--3{grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))}
.grid--2{grid-template-columns:repeat(auto-fit,minmax(18rem,1fr))}
.eyebrow{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;
  letter-spacing:.14em;color:var(--brass);margin:0 0 var(--sp-2)}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center}
.card h3{margin-bottom:var(--sp-2)}
.card__meta{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;
  letter-spacing:.08em;color:var(--ink-faint)}
.arrow-link{font-family:var(--font-mono);font-size:var(--fs-sm);letter-spacing:.04em;color:var(--claret)}
.arrow-link::after{content:" →"}

/* ---- home hero ---- */
.hero{padding:var(--sp-8) 0 var(--sp-6)}
.hero h1{font-size:var(--fs-display);max-width:16ch}
.volumes{display:grid;gap:var(--sp-6);grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))}
.volume{position:relative;overflow:hidden}
.volume__num{font-family:var(--font-display);font-weight:900;font-size:var(--fs-2xl);line-height:1;
  color:var(--brass);letter-spacing:-.02em}
.volume--claret .volume__num{color:var(--claret)}
.volume--green .volume__num{color:var(--green)}
.entry-cards{display:grid;gap:var(--sp-6);grid-template-columns:repeat(auto-fit,minmax(16rem,1fr))}

/* ---- module pages ---- */
.module-numeral{font-family:var(--font-display);font-weight:900;
  font-size:clamp(5rem,10vw,9rem);line-height:.85;letter-spacing:-.04em;color:var(--brass);opacity:.9}
.module--claret .module-numeral{color:var(--claret)}
.module--green .module-numeral{color:var(--green)}

/* ---- curriculum prose (module volumes + templates) ---- */
.prose{max-width:var(--maxw-read)}
.prose>p,.prose>ul,.prose>ol,.prose>blockquote,.prose .tablewrap{margin:0 0 var(--sp-6)}
.prose>h3,.prose>h4,.prose>h5{font-family:var(--font-display);line-height:1.15;
  margin:var(--sp-8) 0 var(--sp-3)}
.prose>h3{font-size:var(--fs-lg)}
.prose>h4{font-size:var(--fs-md)}
.prose>ul,.prose>ol{padding-left:var(--sp-6)}
.prose>ul>li,.prose>ol>li{margin:.3rem 0}
.volume-prose{margin:var(--sp-8) 0}
.templates-cta{margin:var(--sp-8) 0}
/* first heading of a volume shouldn't push a big top gap under the eyebrow */
.volume-prose .prose>h3:first-child{margin-top:var(--sp-3)}
.template-doc{border-top:var(--rule) solid var(--line);padding-top:var(--sp-6)}
.template-doc .part__head p.eyebrow{margin-bottom:.15rem}

.index-rows{border-top:var(--rule) solid var(--line)}
.index-row{display:flex;gap:var(--sp-6);align-items:baseline;justify-content:space-between;
  padding:var(--sp-3) 0;border-bottom:var(--rule) solid var(--line-soft)}
.index-row__main{flex:1}
.index-row__code{font-family:var(--font-mono);font-size:var(--fs-mono-xs);color:var(--ink-faint);
  letter-spacing:.08em;text-transform:uppercase}

/* ---- skills browser ---- */
.skill-card{margin:var(--sp-3) 0;scroll-margin-top:5rem}
.skill-card summary{cursor:pointer;list-style:none;display:flex;gap:var(--sp-3);align-items:baseline;
  flex-wrap:wrap}
.skill-card summary::-webkit-details-marker{display:none}
.skill-card summary::before{content:"▸";color:var(--brass);font-family:var(--font-mono);
  transition:transform var(--dur) var(--ease);display:inline-block}
.skill-card[open] summary::before{transform:rotate(90deg)}
.skill-card__name{font-family:var(--font-display);font-size:var(--fs-md);font-weight:600;flex:1;min-width:12rem}
.skill-id{font-family:var(--font-mono);font-size:var(--fs-mono-xs);color:var(--ink-faint);letter-spacing:.08em}
.task-block{margin:var(--sp-3) 0 var(--sp-3) var(--sp-6);padding-left:var(--sp-3);
  border-left:var(--rule) solid var(--line)}
.task-name{font-weight:600}
.subtask{margin-left:var(--sp-6);color:var(--ink-soft);font-size:var(--fs-sm)}
.ext-header{border-left:var(--rule-bold) solid var(--claret);padding-left:var(--sp-3);
  margin:var(--sp-12) 0 var(--sp-6);background:var(--claret-wash)}
.chip--conf-exact{border-left-color:var(--green);color:var(--green)}
.chip--conf-near{border-left-color:var(--brass);color:var(--ink)}
.chip--conf-parent{border-left-color:var(--ink-faint);color:var(--ink-faint)}
.chip--noeq{border-left-color:var(--claret);color:var(--claret)}
.chip--skill{border-left-color:var(--brass);color:var(--ink);cursor:pointer}
.chip--matter{border-left-color:var(--claret);color:var(--claret)}
.bloom{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;
  letter-spacing:.08em;color:var(--ink-faint)}

/* ---- matter library ---- */
.lib-toolbar{display:flex;flex-wrap:wrap;gap:var(--sp-6);align-items:center;
  justify-content:space-between;margin:var(--sp-6) 0}
.shape-row{display:grid;grid-template-columns:1fr;gap:var(--sp-6);margin:var(--sp-6) 0;
  padding-top:var(--sp-6);border-top:var(--rule) solid var(--line)}
.shape-row__label{font-family:var(--font-display);font-weight:600;font-size:var(--fs-lg)}
.shape-row__cards{display:grid;gap:var(--sp-6);grid-template-columns:repeat(auto-fit,minmax(17rem,1fr))}
.matter-card__caption{font-family:var(--font-display);font-weight:600;font-size:var(--fs-md);margin:var(--sp-2) 0}
.matter-card__premise{font-size:var(--fs-sm);color:var(--ink-soft)}
.tier-hidden{display:none !important}
.split-rule{height:var(--rule);background:var(--line);margin:var(--sp-3) 0}

/* ---- packet ---- */
.packet-layout{display:grid;grid-template-columns:1fr;gap:var(--sp-8)}
@media(min-width:60rem){.packet-layout{grid-template-columns:12rem minmax(0,1fr)}}
.packet-body{max-width:var(--maxw-read);min-width:0}
.part{margin:var(--sp-12) 0;scroll-margin-top:5rem}
.part__num{font-family:var(--font-display);font-weight:900;font-size:var(--fs-2xl);color:var(--brass);
  line-height:1;letter-spacing:-.02em}
.part__head{display:flex;gap:var(--sp-3);align-items:baseline;margin-bottom:var(--sp-3)}
.doc-card{margin:var(--sp-6) 0}
.doc-card h3,.doc-card h4,.doc-card h5{font-family:var(--font-display)}
.instructor-note{color:var(--ink-faint);font-style:italic;border-left:var(--rule) solid var(--line);
  padding-left:var(--sp-3);margin:var(--sp-6) 0}
.cta-row{display:flex;flex-wrap:wrap;gap:var(--sp-3);margin:var(--sp-3) 0}
.btn{display:inline-flex;align-items:center;gap:.4em;font-family:var(--font-mono);
  font-size:var(--fs-sm);letter-spacing:.06em;text-transform:uppercase;
  padding:.7em 1.1em;min-height:48px;border-radius:var(--radius);border:var(--rule) solid var(--brass);
  background:var(--brass-wash);color:var(--ink);cursor:pointer}
.btn--claret{border-color:var(--claret);background:var(--claret-wash);color:var(--claret)}
.btn--ghost{border-color:var(--line);background:transparent;color:var(--ink-soft)}
.persona-line{display:flex;flex-wrap:wrap;gap:var(--sp-3);align-items:center;
  padding:var(--sp-3) 0;border-bottom:var(--rule) solid var(--line-soft)}
.persona-line__name{font-weight:600;min-width:12rem}
.tablewrap{overflow-x:auto}
.md-table td,.md-table th{white-space:nowrap}
.md-quote{border-left:var(--rule-bold) solid var(--brass);padding-left:var(--sp-6);
  color:var(--ink-soft);font-style:italic;margin:var(--sp-6) 0}
details.side-conf{margin:var(--sp-3) 0;border:var(--rule) solid var(--line);border-radius:var(--radius);
  padding:var(--sp-3) var(--sp-6);background:var(--paper-3)}
details.side-conf summary{cursor:pointer;font-family:var(--font-mono);font-size:var(--fs-mono-xs);
  text-transform:uppercase;letter-spacing:.08em;color:var(--claret)}

/* ---- firm dashboard / viz ---- */
.viz-filter{display:flex;flex-wrap:wrap;gap:var(--sp-6);align-items:center;margin:var(--sp-6) 0;
  padding:var(--sp-3) 0;border-top:var(--rule) solid var(--line);border-bottom:var(--rule) solid var(--line)}
.viz-filter .label{margin-right:var(--sp-2)}
.viz-note{font-size:var(--fs-sm);color:var(--ink-faint);font-style:italic}
.viz-grid{display:grid;gap:var(--sp-6);grid-template-columns:1fr}
@media(min-width:52rem){.viz-grid--2{grid-template-columns:1fr 1fr}}
.viz-card{background:var(--paper-2);border:var(--rule) solid var(--line);border-radius:var(--radius-card);
  padding:var(--sp-6);box-shadow:inset 0 0 0 var(--rule) var(--paper-edge)}
.viz-card__head{display:flex;justify-content:space-between;align-items:baseline;gap:var(--sp-3)}
.viz-card__title{font-size:var(--fs-md);margin:0}
.viz-card__caption{font-size:var(--fs-sm);color:var(--ink-soft);margin:.2rem 0 var(--sp-3)}
.viz-toggle{appearance:none;cursor:pointer;background:transparent;border:var(--rule) solid var(--line);
  border-radius:var(--radius);font-family:var(--font-mono);font-size:var(--fs-mono-xs);
  text-transform:uppercase;letter-spacing:.08em;color:var(--ink-soft);padding:.35em .6em;min-height:2em}
.viz-toggle[aria-expanded="true"]{box-shadow:inset 0 0 0 var(--rule) var(--brass);color:var(--ink)}
.viz-chart{margin-top:var(--sp-2)}
.viz-table{margin-top:var(--sp-3)}
.chart-svg text{font-family:var(--font-mono)}
.chart-lbl{font-size:11px;fill:var(--ink-soft)}
.chart-val{font-size:11px;fill:var(--ink);font-weight:600}
.chart-anno{font-size:10px;fill:var(--ink-faint)}
.chart-axis{stroke:var(--line);stroke-width:1}
.legend{display:flex;flex-wrap:wrap;gap:var(--sp-3);margin:var(--sp-3) 0 0;
  font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.06em}
.legend span{display:inline-flex;align-items:center;gap:.4em;color:var(--ink-soft)}
.legend i{width:.85em;height:.85em;border-radius:2px;display:inline-block}
.kpi-hero .kpi-tile__value{font-size:var(--fs-display)}
@media(min-width:64rem){.wrap .kpi-row{grid-template-columns:repeat(3,1fr)}}
.kpi-tile__spark{margin-top:var(--sp-3)}
.kpi-tile__chip{font-family:var(--font-mono);font-size:var(--fs-mono-xs);letter-spacing:.06em;
  text-transform:uppercase;padding:.2em .5em;border-radius:var(--radius);border-left:var(--rule-bold) solid var(--ok)}
.kpi-tile__chip.is-warn{border-left-color:var(--warn);color:var(--warn)}
.downloads{display:flex;flex-wrap:wrap;gap:var(--sp-3);margin-top:var(--sp-6)}
.dl-chip{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;
  letter-spacing:.08em;padding:.5em .8em;border:var(--rule) solid var(--brass);border-radius:var(--radius);
  color:var(--ink);background:var(--brass-wash)}

/* ---- viz interaction: focusable marks, shared tooltip, patterns ---- */
.viz-mark{outline:none}
.viz-mark:focus-visible{outline:var(--rule-bold) solid var(--brass);outline-offset:1px}
.viz-tip{position:fixed;left:0;top:0;z-index:80;pointer-events:none;max-width:min(24rem,86vw);
  display:flex;align-items:baseline;gap:.5em;background:var(--ink);color:var(--ink-invert);
  font-family:var(--font-mono);font-size:var(--fs-mono-xs);line-height:1.35;
  padding:.45em .6em;border-radius:var(--radius);box-shadow:0 6px 20px rgba(0,0,0,.30)}
.viz-tip[hidden]{display:none}
.viz-tip__sw{flex:none;width:.78em;height:.78em;border-radius:2px;align-self:center}
.viz-tip__v{font-weight:700;white-space:nowrap}
.viz-tip__l{opacity:.82}
/* Pattern overlays: off by default, revealed by the PATTERNS toggle. The
   forced-colors media query auto-reveals them even without JS, so series stay
   distinguishable when the OS collapses the palette. */
.viz-pat{opacity:0}
.patterns-on .viz-pat{opacity:1}
@media (forced-colors: active){ .viz-pat{opacity:1} }

@media print{
  .type-toggle,.viz-toggle,.viz-filter,.lib-toolbar,.cta-row,.skip-link,.downloads{display:none !important}
  .viz-tip{display:none !important}          /* tooltips never print */
  .viz-table[hidden]{display:block !important}
  .viz-card,.card{break-inside:avoid}
  /* packet prints as one full-width column (TOC rail is hidden by theme.css) */
  .packet-layout{display:block !important}
  .packet-body{max-width:100% !important}
  .instructor-note{display:none !important}   /* mirror theme's .instructor-notes rule */
}
"""

# --------------------------------------------------------------------------- #
# Shared owned script (progressive enhancement only)
# --------------------------------------------------------------------------- #
PLATFORM_JS = r"""/* platform.js — progressive enhancement for the Practicum Press site. */
(function(){
  'use strict';

  /* ---- large-type toggle ---- */
  var tt = document.getElementById('type-toggle');
  function syncTT(){
    var on = document.documentElement.classList.contains('type-lg');
    if (tt){ tt.setAttribute('aria-pressed', on ? 'true':'false'); }
  }
  syncTT();
  if (tt){
    tt.addEventListener('click', function(){
      var on = document.documentElement.classList.toggle('type-lg');
      try{ localStorage.setItem('sonsteng-type-lg', on ? '1':'0'); }catch(e){}
      syncTT();
    });
  }

  /* ---- matter-library tier toggle ---- */
  var seg = document.querySelector('[data-tier-toggle]');
  if (seg){
    var lib = document.querySelector('[data-tier-active]');
    seg.querySelectorAll('button').forEach(function(b){
      b.addEventListener('click', function(){
        var tier = b.getAttribute('data-tier');
        seg.querySelectorAll('button').forEach(function(x){ x.setAttribute('aria-pressed', x===b ? 'true':'false'); });
        seg.setAttribute('data-side', tier==='real' ? 'real':'meridian');
        if (lib){
          lib.setAttribute('data-tier-active', tier);
          var shown = 0;
          lib.querySelectorAll('[data-tier-card]').forEach(function(c){
            var hide = (tier !== 'all') && (c.getAttribute('data-tier-card') !== tier);
            c.classList.toggle('tier-hidden', hide);
            if (!hide) shown++;
          });
          var cnt = document.querySelector('[data-tier-count]');
          if (cnt) cnt.textContent = (tier==='all' ? 'ALL ' : (tier==='meridian' ? 'MERIDIAN ' : 'REAL-STATE ')) + shown;
        }
      });
    });
  }

  /* ---- viz table twins ---- */
  document.querySelectorAll('.viz-toggle').forEach(function(btn){
    btn.addEventListener('click', function(){
      var id = btn.getAttribute('data-target');
      var tbl = document.getElementById(id);
      if (!tbl) return;
      var open = tbl.hasAttribute('hidden');
      if (open){ tbl.removeAttribute('hidden'); btn.setAttribute('aria-expanded','true'); }
      else { tbl.setAttribute('hidden',''); btn.setAttribute('aria-expanded','false'); }
    });
  });

  /* ---- packet scroll-spy TOC ---- */
  var toc = document.querySelector('.toc-rail');
  if (toc && 'IntersectionObserver' in window){
    var links = {};
    toc.querySelectorAll('a').forEach(function(a){
      var id = a.getAttribute('href');
      if (id && id.charAt(0)==='#') links[id.slice(1)] = a;
    });
    var current = null;
    var obs = new IntersectionObserver(function(entries){
      entries.forEach(function(en){
        if (en.isIntersecting){
          var id = en.target.id;
          if (current) current.removeAttribute('aria-current');
          if (links[id]){ links[id].setAttribute('aria-current','true'); current = links[id]; }
        }
      });
    }, { rootMargin:'-10% 0px -75% 0px', threshold:0 });
    document.querySelectorAll('.part[id]').forEach(function(p){ obs.observe(p); });
  }

  /* ---- firm dashboard: PATTERNS toggle ---- */
  var root = document.documentElement;
  var patBtn = document.getElementById('viz-patterns');
  if (patBtn){
    var forced = window.matchMedia ? window.matchMedia('(forced-colors: active)') : null;
    function applyPatterns(on){
      root.classList.toggle('patterns-on', on);
      patBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    function storedPref(){
      try{ return localStorage.getItem('sonsteng-viz-patterns') === '1'; }catch(e){ return false; }
    }
    /* Auto-on under forced-colors; otherwise honor the saved preference (off by default). */
    applyPatterns((forced && forced.matches) || storedPref());
    patBtn.addEventListener('click', function(){
      var on = !root.classList.contains('patterns-on');
      applyPatterns(on);
      try{ localStorage.setItem('sonsteng-viz-patterns', on ? '1' : '0'); }catch(e){}
    });
    if (forced && forced.addEventListener){
      forced.addEventListener('change', function(e){ applyPatterns(e.matches || storedPref()); });
    }
  }

  /* ---- firm dashboard: shared tooltip (value leads, label follows, swatch) ---- */
  var tip = document.getElementById('viz-tip');
  var marks = document.querySelectorAll('.viz-mark');
  if (tip && marks.length){
    var tSw = tip.querySelector('.viz-tip__sw');
    var tV  = tip.querySelector('.viz-tip__v');
    var tL  = tip.querySelector('.viz-tip__l');
    var tapMark = null;   // set only when a tooltip is pinned open by a touch tap
    var lastPtr = 'mouse';
    function place(cx, cy){
      var pad = 10, r = tip.getBoundingClientRect();
      var x = cx + 14, y = cy + 16;
      if (x + r.width + pad > window.innerWidth) x = cx - r.width - 14;
      if (x < pad) x = pad;
      if (y + r.height + pad > window.innerHeight) y = cy - r.height - 16;
      if (y < pad) y = pad;
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    }
    function show(mark, cx, cy){
      tV.textContent = mark.getAttribute('data-v') || '';   // textContent only — names are untrusted
      tL.textContent = mark.getAttribute('data-l') || '';
      tSw.style.background = mark.getAttribute('data-c') || 'transparent';
      tip.hidden = false;
      place(cx, cy);
    }
    function hide(){ tip.hidden = true; tapMark = null; }
    function edgeTop(mark){ var b = mark.getBoundingClientRect(); return [b.left + b.width / 2, b.top]; }
    function isMark(el){ return el && el.classList && el.classList.contains('viz-mark'); }
    marks.forEach(function(mark){
      mark.addEventListener('pointerdown', function(ev){ lastPtr = ev.pointerType || 'mouse'; });
      mark.addEventListener('pointerenter', function(ev){
        if (ev.pointerType === 'touch') return;   // touch is handled via tap toggle
        show(mark, ev.clientX, ev.clientY);
      });
      mark.addEventListener('pointermove', function(ev){
        if (ev.pointerType === 'touch' || tip.hidden) return;
        place(ev.clientX, ev.clientY);
      });
      mark.addEventListener('pointerleave', function(ev){
        if (ev.pointerType === 'touch' || tapMark) return;
        hide();
      });
      // Keyboard focus mirrors hover (skip when the focus was driven by a touch —
      // touch uses the tap toggle below so the two don't fight).
      mark.addEventListener('focus', function(){
        if (lastPtr === 'touch') return;
        var c = edgeTop(mark); show(mark, c[0], c[1]);
      });
      mark.addEventListener('blur', function(){ if (!tapMark) hide(); });
      mark.addEventListener('click', function(){          // touch tap toggles open/closed
        if (lastPtr !== 'touch') return;
        if (tapMark === mark && !tip.hidden){ hide(); }
        else { tapMark = mark; var c = edgeTop(mark); show(mark, c[0], c[1]); }
      });
    });
    document.addEventListener('click', function(e){   // tap outside any mark dismisses
      if (!isMark(e.target)) hide();
    });
    document.addEventListener('scroll', function(){
      if (tip.hidden) return;
      // A focused mark keeps its tooltip pinned (the browser's focus-scroll must
      // not dismiss it); mouse/touch tooltips dismiss on scroll.
      if (isMark(document.activeElement) && !tapMark){
        var c = edgeTop(document.activeElement); place(c[0], c[1]);
      } else { hide(); }
    }, true);
    window.addEventListener('keydown', function(e){ if (e.key === 'Escape'){ hide();
      if (isMark(document.activeElement)) document.activeElement.blur(); } });
  }
})();
"""

# --------------------------------------------------------------------------- #
# URL helpers between generated pages
# --------------------------------------------------------------------------- #
from urllib.parse import quote

def matter_url_from(relpath, m):
    return up_prefix(relpath) + "matters/" + m["_slug"] + "/index.html"

def _clean_name(s):
    """Drop parentheticals/quotes so 'Jasmine Cordero (\"Jaz\")' == 'Jasmine Cordero (J.C.), …'."""
    s = re.sub(r"\([^)]*\)", " ", s or "")
    s = s.split(",")[0]
    return " ".join(s.replace("“", " ").replace("”", " ").replace('"', " ").split()).lower()

def pick_client_persona(m, manifest_entry):
    """The interviewable client persona for the packet CTA.

    THE client role starts with 'client' (e.g. 'client', 'client (minor …)',
    'client / petitioner') — NOT 'client-side fact witness' and NOT the
    'parent / guardian of the (minor) client'.
    """
    hint = ((manifest_entry or {}).get("client_persona_hint") or {}).get("name", "")
    hint_lead = _clean_name(hint)
    personas = list(m["_personas"].values())
    true_clients = [p for p in personas
                    if re.match(r"^client(?![-‑])",
                                (p.get("identity", {}).get("role") or "").strip().lower())]
    # prefer the persona whose cleaned name matches the manifest hint's leading name
    for p in true_clients:
        nm = _clean_name(p.get("identity", {}).get("name", ""))
        if nm and hint_lead and (nm in hint_lead or hint_lead in nm):
            return p
    if true_clients:
        return true_clients[0]
    # fall back to any client-adjacent role, then to the first persona
    loose = [p for p in personas if "client" in (p.get("identity", {}).get("role") or "").lower()]
    return (loose or personas or [None])[0]

def pick_represented_persona(m, client):
    """First persona flagged rule_4_2.applies — the teaching-moment entry."""
    for p in m["_personas"].values():
        if client is not None and p.get("id") == client.get("id"):
            continue
        if (p.get("rule_4_2") or {}).get("applies"):
            return p
    return None

def chat_href(relpath, m, persona, represented):
    up = up_prefix(relpath)
    ident = persona.get("identity", {})
    qs = ("?matter=" + quote(m["id"])
          + "&persona=" + quote(persona["id"])
          + "&title=" + quote(m.get("caption", ""))
          + "&client=" + quote(ident.get("name", ""))
          + "&role=" + quote(ident.get("role", "") or "Client")
          + "&packet=" + quote("../matters/" + m["_slug"] + "/", safe="/.")
          + "&represented=" + ("1" if represented else "0"))
    return up + "chat/index.html" + qs

def critique_href(relpath, m):
    return (up_prefix(relpath) + "chat/critique.html?matter=" + quote(m["id"])
            + "&title=" + quote(m.get("caption", "")))

# The keyless scripted-sample consultation. Every entry point across the site links
# to the SAME recording (m05 · Devon Halvard) so professors can experience a client
# interview + debrief before any API key exists. Backed by app/chat/sample-m05.json.
SAMPLE_MATTER_ID = "m05"
SAMPLE_PERSONA_ID = "m05.per.halvard"
SAMPLE_TITLE = "State of Meridian v. Devon R. Halvard"
SAMPLE_CLIENT = "Devon Halvard"

def sample_href(relpath):
    return (up_prefix(relpath) + "chat/index.html?matter=" + quote(SAMPLE_MATTER_ID)
            + "&persona=" + quote(SAMPLE_PERSONA_ID)
            + "&title=" + quote(SAMPLE_TITLE)
            + "&client=" + quote(SAMPLE_CLIENT)
            + "&role=" + quote("Client (criminal defendant)")
            + "&sample=1")

# --------------------------------------------------------------------------- #
# Page — platform home
# --------------------------------------------------------------------------- #
CENTAUR_BLURB = (
    "Every exercise here ships with a machine-readable rubric and a revise-and-repeat loop — "
    "the two halves of a human+AI (“centaur”) pedagogy. AI gives the first-pass rubric "
    "critique and unlimited reps; scarce faculty time is reserved for the high-value coaching "
    "only a human can give. The simulated client interviews, the rubric critiques, and the firm "
    "dashboard below are that layer, live."
)

def build_home(corpus):
    rel = "index.html"
    n_matters = len(corpus["matters"])
    skills = corpus["skills"]["skills"]
    n_surveyed = sum(1 for s in skills if not s.get("extension"))
    n_tasks = len(corpus["tasks"]["tasks"])

    vols = []
    mod_counts = defaultdict(set)
    task_by_module = defaultdict(int)
    for t in corpus["tasks"]["tasks"]:
        task_by_module[t.get("module")] += 1
        for ref in t.get("exercise_refs") or []:
            mod_counts[t.get("module")].add(ref.split(".")[0])
    for code, meta in MODULE_META.items():
        acc = {"brass": "", "claret": " volume--claret", "green": " volume--green"}[meta["accent"]]
        vols.append("""
    <a class="card volume{acc}" href="modules/{lo}.html">
      <div class="volume__num" aria-hidden="true">{code}</div>
      <h3>{title}</h3>
      <p class="matter-card__premise">{thesis}</p>
      <p class="card__meta">{nt} TASKS · {nm} LINKED MATTERS</p>
      <span class="arrow-link">Open volume</span>
    </a>""".format(acc=acc, lo=code.lower(), code=esc(code), title=esc(meta["title"]),
                   thesis=esc(meta["thesis"]), nt=task_by_module.get(code, 0),
                   nm=len(mod_counts.get(code, ()))))

    body = """
<section class="hero reveal">
  <p class="eyebrow">A LIVING CASEBOOK · 50 YEARS OF METHOD</p>
  <h1>The practicum, rendered as a working law firm.</h1>
  <p class="lede">Twenty deep simulated matters across ten practice shapes and two jurisdiction
  tiers, a surveyed skills taxonomy mapped to FOLIO, simulated client interviews, and the complete
  business of a two-lawyer firm — every page generated from one open data spine.</p>
</section>

<div class="brass-rule" role="presentation"></div>

<section aria-labelledby="volumes-h">
  <div class="section-head"><p class="eyebrow">THE THREE VOLUMES</p>
  <h2 id="volumes-h">Curriculum modules</h2></div>
  <div class="volumes stagger">{vols}</div>
</section>

<section aria-labelledby="explore-h">
  <div class="section-head"><p class="eyebrow">THE APPARATUS</p>
  <h2 id="explore-h">Explore the practicum</h2></div>
  <div class="entry-cards stagger">
    <a class="card" href="{sample}">
      <p class="card__meta">NO KEY REQUIRED</p>
      <h3>Watch a sample consultation ▸</h3>
      <p class="matter-card__premise">A recorded client interview — Devon Halvard, charged with DWI —
      played back turn by turn, then graded. See the consultation room and the debrief in action
      before you bring your own key. Scripted sample, not a live AI client.</p>
      <span class="arrow-link">Play the sample</span>
    </a>
    <a class="card" href="skills/index.html">
      <p class="card__meta">TAXONOMY</p>
      <h3>Skills browser</h3>
      <p class="matter-card__premise">{ns} surveyed lawyering skills — {nt} tasks with subtasks,
      FOLIO mappings, and the matters that exercise each.</p>
      <span class="arrow-link">Browse skills</span>
    </a>
    <a class="card" href="matters/index.html">
      <p class="card__meta">MATTER LIBRARY</p>
      <h3>{nm} simulated matters</h3>
      <p class="matter-card__premise">Ten practice shapes, each in the fictional State of Meridian
      and again in a real jurisdiction. Full packets, case files, rubrics, and interviews.</p>
      <span class="arrow-link">Open the library</span>
    </a>
    <a class="card" href="firm/index.html">
      <p class="card__meta">BUSINESS OF LAW</p>
      <h3>Firm dashboard</h3>
      <p class="matter-card__premise">Ellingboe &amp; Ravndal LLP — the two-lawyer practicum firm.
      Fees, realization, AR aging, trust accounting, and budget, charted and taught.</p>
      <span class="arrow-link">Open the ledger</span>
    </a>
    <a class="card" href="templates/index.html">
      <p class="card__meta">COURSE DELIVERABLES</p>
      <h3>Deliverable templates</h3>
      <p class="matter-card__premise">The six recurring course handouts — time sheet, engagement
      letter, interview plan, settlement plan, portfolio, and reflective report — each with its
      grading note. Print-ready.</p>
      <span class="arrow-link">Open the templates</span>
    </a>
    <div class="card">
      <p class="card__meta">ABOUT THE METHOD</p>
      <h3>The centaur layer</h3>
      <p class="matter-card__premise">{blurb}</p>
      <div class="chips" style="margin-top:var(--sp-3)">
        <span class="chip chip--coming-soon">FACULTY PORTAL · COMING SOON</span>
        <span class="chip chip--coming-soon">ACCOUNTS · COMING SOON</span>
      </div>
    </div>
  </div>
</section>
""".format(vols="".join(vols), ns=n_surveyed, nt=n_tasks, nm=n_matters,
           blurb=esc(CENTAUR_BLURB), sample=esc(sample_href(rel)))
    write_file(rel, page_shell(rel, "Platform Home", "PLATFORM · HOME", [], body))

# --------------------------------------------------------------------------- #
# Page — module covers
# --------------------------------------------------------------------------- #
def build_modules(corpus):
    tasks = corpus["tasks"]["tasks"]
    skills_by_id = {s["id"]: s for s in corpus["skills"]["skills"]}
    for code, meta in MODULE_META.items():
        rel = "modules/{lo}.html".format(lo=code.lower())
        mod_tasks = [t for t in tasks if t.get("module") == code]
        # group by skill
        by_skill = OrderedDict()
        for t in mod_tasks:
            by_skill.setdefault(t["skill_id"], []).append(t)
        linked_matter_ids = sorted({ref.split(".")[0] for t in mod_tasks for ref in (t.get("exercise_refs") or [])})
        linked = [corpus["by_id"][mid] for mid in linked_matter_ids if mid in corpus["by_id"]]

        rows = []
        for sid, ts in by_skill.items():
            sk = skills_by_id.get(sid, {})
            task_lines = []
            for t in ts:
                refs = sorted({r.split(".")[0] for r in (t.get("exercise_refs") or [])})
                chips = " ".join(
                    '<a class="chip chip--matter" href="../matters/{slug}/index.html">{mid}</a>'.format(
                        slug=esc(corpus["by_id"][r]["_slug"]), mid=esc(r.upper()))
                    for r in refs if r in corpus["by_id"])
                task_lines.append(
                    '<div class="index-row"><div class="index-row__main">'
                    '<span class="task-name">{name}</span> '
                    '<span class="bloom">{bloom}</span>'
                    '<div class="chips" style="margin-top:.3rem">{chips}</div></div>'
                    '<span class="index-row__code">{tid}</span></div>'.format(
                        name=esc(t["name"]), bloom=esc(t.get("bloom_level", "")),
                        chips=chips, tid=esc(t["id"])))
            rows.append("""
  <section class="section-head" aria-label="{sn}">
    <h3><a class="link" href="../skills/index.html#{sid}">{sn}</a>
      <span class="skill-id">{sid}</span></h3>
    <div class="index-rows">{lines}</div>
  </section>""".format(sn=esc(sk.get("name", sid)), sid=esc(sid), lines="".join(task_lines)))

        matter_cards = "".join(
            '<a class="card" href="../matters/{slug}/index.html">'
            '<div class="chips">{tc}</div>'
            '<p class="matter-card__caption">{cap}</p>'
            '<span class="arrow-link">Open packet</span></a>'.format(
                slug=esc(m["_slug"]),
                tc=tier_chip("meridian" if m.get("tier") == "meridian" else "real", m.get("jurisdiction")),
                cap=esc(m.get("caption", "")))
            for m in linked)

        # --- the volume prose: the real handbook teaching for this module ---
        volume_md = ((corpus.get("curriculum") or {}).get("volumes") or {}).get(code, "")
        prose_section = ""
        if volume_md.strip():
            prose_section = """
<section class="volume-prose reveal" aria-label="The volume">
  <p class="eyebrow">THE VOLUME · HOW THIS MODULE TEACHES</p>
  <div class="prose">{prose}</div>
</section>
<div class="brass-rule" role="presentation"></div>
<section class="templates-cta reveal" aria-labelledby="mod-tpl-h">
  <div class="section-head"><p class="eyebrow">DELIVERABLES</p>
  <h2 id="mod-tpl-h">Course templates</h2></div>
  <p>The deliverables named above — time sheets, engagement letters, interview plans,
  settlement plans, and the reflective portfolio — share a common set of handout templates,
  each with its grading note.</p>
  <p><a class="btn" href="../templates/index.html">Open the deliverable templates</a></p>
</section>""".format(prose=markdown(volume_md))

        body = """
<div class="module module--{accent}">
<section class="reveal">
  <div class="module-numeral" aria-hidden="true">{code}</div>
  <h1>Module {n} — {title}</h1>
  <p class="lede"><em>{thesis}</em></p>
  <p class="card__meta">{ntasks} TASKS · {nsk} SKILLS · {nm} LINKED MATTERS</p>
</section>
<div class="brass-rule" role="presentation"></div>
{prose_section}
<section aria-label="Tasks in this module">
  <p class="eyebrow">RULED INDEX · TASKS BY SKILL</p>
  {rows}
</section>
<section aria-labelledby="mod-matters-h">
  <div class="section-head"><p class="eyebrow">WORKED ON</p><h2 id="mod-matters-h">Linked matters</h2></div>
  <div class="grid grid--3 stagger">{mcards}</div>
</section>
</div>
""".format(accent=("brass" if meta["accent"] == "brass" else meta["accent"]),
           code=esc(code), n=code[1], title=esc(meta["title"]), thesis=esc(meta["thesis"]),
           ntasks=len(mod_tasks), nsk=len(by_skill), nm=len(linked),
           prose_section=prose_section, rows="".join(rows), mcards=matter_cards)
        write_file(rel, page_shell(
            rel, "Module {c} — {t}".format(c=code, t=meta["title"]),
            "{c} · MODULES".format(c=code),
            [("Home", "../index.html"), ("Modules", None), (code, None)],
            body, body_class="module--" + meta["accent"]))

# --------------------------------------------------------------------------- #
# Page — course deliverable templates (single print-friendly handout page)
# --------------------------------------------------------------------------- #
def build_templates(corpus):
    rel = "templates/index.html"
    templates = ((corpus.get("curriculum") or {}).get("templates") or [])
    present = [t for t in templates if (t.get("md") or "").strip()]

    toc_items = []
    parts = []
    for idx, t in enumerate(present, start=1):
        anchor = "tpl-" + t["stem"]
        num = "{:02d}".format(idx)
        toc_items.append('<a href="#{a}">{n} · {ttl}</a>'.format(a=anchor, n=num, ttl=esc(t["title"])))
        parts.append("""
  <section class="part template-doc" id="{a}" aria-labelledby="{a}-h">
    <div class="part__head"><span class="part__num" aria-hidden="true">{n}</span>
      <div>
        <p class="eyebrow">{kicker}</p>
        <h2 id="{a}-h">{ttl}</h2>
      </div>
    </div>
    <div class="prose">{body}</div>
  </section>""".format(a=anchor, n=num, kicker=esc(t["kicker"]),
                       ttl=esc(t["title"]), body=markdown(t["md"])))

    header = """
<header class="reveal">
  <p class="eyebrow">THE PRACTICUM PRESS · COURSE DELIVERABLES</p>
  <h1>Deliverable templates</h1>
  <p class="lede">The handout templates for the six recurring course deliverables — the time
  sheet, engagement-letter checklist, client-interview plan, settlement &amp; negotiation plan,
  learning portfolio, and reflective report. Each carries its own &ldquo;how it&rsquo;s
  graded&rdquo; note. These pages are built to print; use your browser&rsquo;s print command for
  a clean, black-on-white handout.</p>
  <div class="chips" aria-label="Related modules">
    <a class="chip chip--matter" href="../modules/m1.html">M1 · FOUNDATIONAL</a>
    <a class="chip chip--matter" href="../modules/m2.html">M2 · SUBSTANTIVE + SKILLS</a>
    <a class="chip chip--matter" href="../modules/m3.html">M3 · TRANSITION</a>
  </div>
</header>"""

    body = """
{header}
<div class="brass-rule" role="presentation"></div>
<div class="packet-layout">
  <nav class="toc-rail" aria-label="Templates">
    {toc}
  </nav>
  <div class="packet-body">
    {parts}
  </div>
</div>""".format(header=header, toc="\n    ".join(toc_items), parts="".join(parts))

    write_file(rel, page_shell(
        rel, "Deliverable templates", "TEMPLATES · COURSE DELIVERABLES",
        [("Home", "../index.html"), ("Templates", None)], body))

# --------------------------------------------------------------------------- #
# Page — skills browser
# --------------------------------------------------------------------------- #
def build_skills(corpus):
    rel = "skills/index.html"
    skills = corpus["skills"]["skills"]
    tasks_by_skill = defaultdict(list)
    for t in corpus["tasks"]["tasks"]:
        tasks_by_skill[t["skill_id"]].append(t)

    def render_skill(s, open_first=False):
        sid = s["id"]
        ts = tasks_by_skill.get(sid, [])
        fol = folio_chip(s.get("folio"), s.get("no_folio_equivalent"))
        survey = s.get("survey") or {}
        surv_bits = []
        if "importance" in survey:
            surv_bits.append("IMPORTANCE {v}%".format(v=survey["importance"]))
        if "preparedness" in survey:
            surv_bits.append("PREPARED {v}%".format(v=survey["preparedness"]))
        surv = ('<span class="skill-id">' + " · ".join(surv_bits) + "</span>") if surv_bits else ""
        task_html = []
        for t in ts:
            refs = sorted({r.split(".")[0] for r in (t.get("exercise_refs") or [])})
            chips = " ".join(
                '<a class="chip chip--matter" href="../matters/{slug}/index.html">{mid}</a>'.format(
                    slug=esc(corpus["by_id"][r]["_slug"]), mid=esc(r.upper()))
                for r in refs if r in corpus["by_id"])
            subs = "".join(
                '<li class="subtask"><strong>{n}</strong> — {d} <span class="skill-id">{i}</span></li>'.format(
                    n=esc(st["name"]), d=esc(st.get("description", "")), i=esc(st["id"]))
                for st in (t.get("subtasks") or []))
            tf = folio_chip(t.get("folio"), t.get("no_folio_equivalent"))
            task_html.append("""
      <div class="task-block">
        <p style="margin:0"><span class="task-name">{name}</span>
          <span class="skill-id">{tid}</span> <span class="bloom">BLOOM · {bloom} · {mod}</span></p>
        <p class="subtask" style="margin:.2rem 0 .4rem">{desc}</p>
        <div class="chips">{tf} {chips}</div>
        <ul style="margin:.4rem 0 0">{subs}</ul>
      </div>""".format(name=esc(t["name"]), tid=esc(t["id"]), bloom=esc(t.get("bloom_level", "")),
                       mod=esc(t.get("module", "")), desc=esc(t.get("description", "")),
                       tf=tf, chips=chips, subs=subs))
        alt = ('<p class="subtask" style="margin:.2rem 0 0">Survey phrasing also recorded as: '
               '<em>{a}</em></p>'.format(a=esc(s["alt_name"]))) if s.get("alt_name") else ""
        return """
  <details class="card skill-card" id="{sid}"{op}>
    <summary>
      <span class="skill-card__name">{name}</span>
      <span class="skill-id">{sid}</span>
      {fol} {surv}
    </summary>
    {alt}
    {tasks}
  </details>""".format(sid=esc(sid), op=" open" if open_first else "", name=esc(s["name"]),
                       fol=fol, surv=surv, alt=alt,
                       tasks="".join(task_html) or '<p class="subtask">No decomposed tasks recorded.</p>')

    lp = [s for s in skills if not s.get("extension") and s["category"] == "legal_practice"]
    pm = [s for s in skills if not s.get("extension") and s["category"] == "practice_management"]
    ext = [s for s in skills if s.get("extension")]

    body = """
<section class="reveal">
  <p class="eyebrow">TABLE OF AUTHORITIES · {n} SURVEYED SKILLS</p>
  <h1>Skills browser</h1>
  <p class="lede">Sonsteng&rsquo;s seventeen Legal Practice skills and nine Law Practice Management
  skills, decomposed into the tasks most lawyers most often perform — each mapped into the FOLIO
  ontology where a sound mapping exists, and cross-linked to the matters that exercise it.</p>
</section>
<div class="brass-rule" role="presentation"></div>

<section aria-labelledby="lp-h">
  <div class="section-head"><p class="eyebrow">SURVEYED · 17</p>
  <h2 id="lp-h">Legal Practice skills</h2></div>
  {lp}
</section>

<section aria-labelledby="pm-h">
  <div class="section-head"><p class="eyebrow">SURVEYED · 9</p>
  <h2 id="pm-h">Law Practice Management skills</h2></div>
  {pm}
</section>

<section aria-labelledby="ext-h">
  <div class="ext-header section-head">
    <p class="eyebrow" style="color:var(--claret)">EXTENSION · NOT PART OF THE SURVEYED 26</p>
    <h2 id="ext-h">AI-era extension set</h2>
    <p class="matter-card__premise">Added for the centaur layer; kept visually and structurally
    separate from the surveyed canon.</p>
  </div>
  {ext}
</section>
""".format(n=len(lp) + len(pm),
           lp="".join(render_skill(s, open_first=(i == 0)) for i, s in enumerate(lp)),
           pm="".join(render_skill(s) for s in pm),
           ext="".join(render_skill(s) for s in ext))
    write_file(rel, page_shell(rel, "Skills Browser", "TAXONOMY · SKILLS",
                               [("Home", "../index.html"), ("Skills", None)], body))

# --------------------------------------------------------------------------- #
# Page — about/third-party (footer target; content from repo THIRD-PARTY.md)
# --------------------------------------------------------------------------- #
def build_third_party():
    rel = "about/third-party.html"
    tp_path = os.path.join(ROOT, "THIRD-PARTY.md")
    md = ""
    if os.path.exists(tp_path):
        with open(tp_path, "r", encoding="utf-8") as fh:
            md = fh.read()
    body = """
<section class="reveal prose">
  <p class="eyebrow">ATTRIBUTIONS</p>
  <h1>Third-party components</h1>
  {content}
</section>""".format(content=markdown(md) if md else "<p>No third-party components recorded.</p>")
    write_file(rel, page_shell(rel, "Third-Party", "ABOUT · THIRD-PARTY",
                               [("Home", "../index.html"), ("Third-party", None)], body))

# --------------------------------------------------------------------------- #
# Page — matter library (shape-first, Meridian ⇄ real segmented toggle)
# --------------------------------------------------------------------------- #
def build_matter_library(corpus):
    rel = "matters/index.html"
    man_by_id = {m["id"]: m for m in corpus["manifest"]["matters"]}
    by_shape = OrderedDict((k, {"meridian": None, "real": None}) for k in SHAPE_LABELS)
    for m in corpus["matters"]:
        tier = "meridian" if m.get("tier") == "meridian" else "real"
        if m.get("shape") in by_shape:
            by_shape[m["shape"]][tier] = m

    def matter_card(m, tier):
        man = man_by_id.get(m["id"], {})
        premise = man.get("premise", "")
        if len(premise) > 260:
            premise = premise[:257].rsplit(" ", 1)[0] + "…"
        side_chips = " ".join(
            '<span class="chip">{l}</span>'.format(l=esc(s.get("label", s.get("role_id", ""))[:38]))
            for s in (m.get("sides") or []))
        two_sided_conf = any((s.get("confidential_fact_refs") for s in (m.get("sides") or [])))
        lock = ('<div class="split-rule" role="presentation"></div>'
                '<p class="card__meta">⚿ TWO-SIDED · PER-SIDE CONFIDENTIAL FACTS</p>') if two_sided_conf else ""
        return """
      <article class="card" data-tier-card="{tier}">
        <div class="chips">{tc} <span class="chip">{fee}</span> <span class="chip chip--folio">{mid}</span></div>
        <p class="matter-card__caption">{cap}</p>
        <p class="matter-card__premise">{prem}</p>
        <div class="chips">{sides}</div>
        {lock}
        <p style="margin-top:var(--sp-3)"><a class="arrow-link" href="{slug}/index.html">OPEN PACKET</a></p>
      </article>""".format(
            tier=tier, tc=tier_chip(m.get("tier") if m.get("tier") == "meridian" else "real", m.get("jurisdiction")),
            fee=esc((m.get("fee_type") or "").upper()), mid=esc(m["id"].upper()),
            cap=esc(m.get("caption", "")), prem=esc(premise), sides=side_chips, lock=lock,
            slug=esc(m["_slug"]))

    rows = []
    for shape, pair in by_shape.items():
        cards = []
        for tier in ("meridian", "real"):
            m = pair[tier]
            if m:
                cards.append(matter_card(m, tier))
        rows.append("""
  <div class="shape-row">
    <div>
      <p class="eyebrow">SHAPE</p>
      <h2 class="shape-row__label" style="font-size:var(--fs-lg)">{label}</h2>
    </div>
    <div class="shape-row__cards">{cards}</div>
  </div>""".format(label=esc(SHAPE_LABELS[shape]), cards="".join(cards)))

    body = """
<section class="reveal">
  <p class="eyebrow">THE MATTER LIBRARY · 10 SHAPES × 2 TIERS</p>
  <h1>Simulated matters</h1>
  <p class="lede">Every practice shape appears twice: once in the fictional State of Meridian —
  a complete canon with its own courts and citation scheme — and once in a real jurisdiction,
  where finding the governing law is part of the exercise.</p>
</section>

<div class="lib-toolbar">
  <p class="card__meta" style="margin:0">SHOWING <span data-tier-count>ALL 20</span> MATTERS</p>
  <div class="segmented-toggle" data-tier-toggle role="group" aria-label="Jurisdiction tier">
    <button type="button" data-tier="all" aria-pressed="true">BOTH TIERS</button>
    <button type="button" data-tier="meridian" aria-pressed="false">⌘ MERIDIAN</button>
    <button type="button" data-tier="real" aria-pressed="false">REAL STATES</button>
  </div>
</div>
<div class="brass-rule" role="presentation"></div>

<div data-tier-active="all">
{rows}
</div>
""".format(rows="".join(rows))
    write_file(rel, page_shell(rel, "Matter Library", "MATTERS · LIBRARY",
                               [("Home", "../index.html"), ("Matters", None)], body))

# --------------------------------------------------------------------------- #
# Packet pages
# --------------------------------------------------------------------------- #
SECTION_ORDER = ["intro", "objectives", "activities", "instructions",
                 "case_file", "history", "considerations", "substantive_info"]

INSTRUCTOR_EXCLUDE = ("facts.md", "instructor-notes.md")

def _read_md(matter_dir, relfile):
    path = os.path.join(matter_dir, relfile)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

def render_doc_card(matter_dir, relfile, meta_label):
    md = _read_md(matter_dir, relfile)
    if md is None:
        return ""
    return """
  <article class="doc-card">
    <p class="doc-card__meta">{meta}</p>
    {body}
  </article>""".format(meta=esc(meta_label), body=markdown(md))

def render_ledger_rows(entries, cols, totals=None):
    body = []
    for e in entries:
        tds = []
        for key, label, is_num, fmt in cols:
            v = e.get(key, "")
            if fmt:
                v = fmt(v)
            tds.append('<td{c}>{v}</td>'.format(c=' class="num"' if is_num else "", v=esc(v)))
        body.append("<tr>" + "".join(tds) + "</tr>")
    tfoot = ""
    if totals:
        tds = "".join('<td{c}>{v}</td>'.format(c=' class="num"' if isnum else "", v=esc(v))
                      for v, isnum in totals)
        tfoot = "<tfoot><tr>" + tds + "</tr></tfoot>"
    th = "".join('<th{c}>{l}</th>'.format(c=' class="num"' if is_num else "", l=esc(label))
                 for _, label, is_num, _ in cols)
    return ('<div class="tablewrap"><table class="ledger"><thead><tr>' + th
            + "</tr></thead><tbody>" + "".join(body) + "</tbody>" + tfoot + "</table></div>")

def build_business_section(m):
    biz = m.get("_business")
    if not biz:
        return ""
    parts = []
    # engagement letter doc-card
    letter = (biz.get("engagement") or {}).get("letter_md")
    if letter:
        parts.append(render_doc_card(m["_dir"], letter, "BUSINESS EXHIBIT · ENGAGEMENT LETTER"))
    # intake + conflicts summary
    intake = biz.get("intake") or {}
    conf = biz.get("conflicts_check") or {}
    parts.append("""
  <article class="doc-card">
    <p class="doc-card__meta">BUSINESS EXHIBIT · INTAKE &amp; CONFLICTS</p>
    <p><strong>Intake ({d}).</strong> {s}</p>
    <p><strong>Conflicts check ({cd}) — {r}.</strong> {cn}</p>
  </article>""".format(d=esc(intake.get("intake_date", "")), s=esc(intake.get("matter_summary", "")),
                       cd=esc(conf.get("checked_date", "")), r=esc((conf.get("result") or "").upper()),
                       cn=esc(conf.get("notes", ""))))
    # time entries -> billing statement ledger
    te = biz.get("time_entries") or []
    if te:
        total_amt = sum(float(t.get("hours", 0)) * float(t.get("rate", 0)) for t in te)
        total_hrs = sum(float(t.get("hours", 0)) for t in te)
        cols = [
            ("date", "Date", False, None),
            ("timekeeper_id", "TK", False, None),
            ("narrative", "Narrative", False, None),
            ("hours", "Hours", True, lambda v: "{:.1f}".format(float(v))),
            ("rate", "Rate", True, lambda v: money(v)),
            ("_amount", "Amount", True, None),
        ]
        for t in te:
            t["_amount"] = money(float(t.get("hours", 0)) * float(t.get("rate", 0)), cents=True)
        note = ""
        if (biz.get("engagement") or {}).get("fee_type") == "contingency":
            note = ('<p class="viz-note">Contingency engagement — time is recorded to show effort '
                    'invested; the fee is a percentage of recovery, not these amounts.</p>')
        parts.append("""
  <article class="doc-card">
    <p class="doc-card__meta">BUSINESS EXHIBIT · BILLING STATEMENT ({n} TIME ENTRIES)</p>
    {note}
    {tbl}
  </article>""".format(n=len(te), note=note,
                       tbl=render_ledger_rows(
                           te, cols,
                           totals=[("Total", False), ("", False), ("", False),
                                   ("{:.1f}".format(total_hrs), True), ("", False),
                                   (money(total_amt, cents=True), True)])))
    # invoices
    invs = biz.get("invoices") or []
    if invs:
        cols = [
            ("id", "Invoice", False, None),
            ("date", "Date", False, None),
            ("fees", "Fees", True, lambda v: money(v, cents=True)),
            ("expenses", "Expenses", True, lambda v: money(v, cents=True)),
            ("payments_received", "Paid", True, lambda v: money(v, cents=True)),
            ("balance_due", "Balance", True, lambda v: money(v, cents=True)),
        ]
        parts.append("""
  <article class="doc-card">
    <p class="doc-card__meta">BUSINESS EXHIBIT · INVOICES</p>
    {tbl}
  </article>""".format(tbl=render_ledger_rows(invs, cols)))
    # trust ledger
    tr = biz.get("trust_entries") or []
    if tr:
        cols = [
            ("date", "Date", False, None),
            ("type", "Type", False, None),
            ("amount", "Amount", True, lambda v: money(v, cents=True)),
            ("running_balance", "Running balance", True, lambda v: money(v, cents=True)),
        ]
        parts.append("""
  <article class="doc-card">
    <p class="doc-card__meta">BUSINESS EXHIBIT · CLIENT TRUST LEDGER</p>
    <p class="viz-note">Client money is not firm money: every deposit and disbursement must
    reconcile to the penny.</p>
    {tbl}
  </article>""".format(tbl=render_ledger_rows(tr, cols)))
    return "".join(parts)

def build_rubric_section(m, rel):
    ru = m.get("_rubric")
    if not ru:
        return ""
    rows = []
    for c in ru.get("criteria", []):
        skill_chip = ('<a class="chip chip--skill" href="{up}skills/index.html#{sid}">{sid}</a>'.format(
            up=up_prefix(rel), sid=esc(c.get("skill_id", "")))) if c.get("skill_id") else ""
        task_chip = ('<span class="chip">{t}</span>'.format(t=esc(c["task_id"]))) if c.get("task_id") else ""
        subrows = "".join(
            '<tr><td style="padding-left:2rem">— {n}</td><td>{d}</td><td></td>'
            '<td class="num">{w}</td></tr>'.format(
                n=esc(s["name"]), d=esc(s.get("description", "")), w=esc(s.get("weight_points", "")))
            for s in (c.get("subcriteria") or []))
        rows.append(
            '<tr><td><strong>{n}</strong></td><td>{d}</td>'
            '<td><span class="chips">{sk} {tk}</span></td>'
            '<td class="num"><strong>{w}</strong></td></tr>{subs}'.format(
                n=esc(c["name"]), d=esc(c.get("description", "")), sk=skill_chip, tk=task_chip,
                w=esc(c.get("weight_points", "")), subs=subrows))
    grades = " · ".join("{g} ≥ {p}".format(g=esc(g["grade"]), p=esc(g["points"]))
                        for g in (ru.get("letter_grade_map") or []))
    return """
  <div class="tablewrap"><table class="ledger">
    <caption>RUBRIC · DECLARED TOTAL {tot} POINTS</caption>
    <thead><tr><th>Criterion</th><th>Description</th><th>Maps to</th><th class="num">Points</th></tr></thead>
    <tbody>{rows}</tbody>
    <tfoot><tr><td>Total</td><td></td><td></td><td class="num">{tot}</td></tr></tfoot>
  </table></div>
  <p class="mono" style="font-size:var(--fs-mono-xs);color:var(--ink-soft)">LETTER GRADES · {grades}</p>
""".format(tot=esc(ru.get("declared_total", "")), rows="".join(rows), grades=grades)

def build_packet_pages(corpus):
    """Returns list of (relpath, size) for budget accounting."""
    man_by_id = {m["id"]: m for m in corpus["manifest"]["matters"]}
    out_sizes = []
    for m in corpus["matters"]:
        out_sizes.extend(build_one_packet(corpus, m, man_by_id.get(m["id"], {})))
    return out_sizes

def render_case_file_cards(m, files):
    cards = []
    exhibits = {e.get("title", ""): e.get("id", "") for e in (m.get("exhibits") or [])}
    for f in files:
        base = os.path.basename(f)
        if base in INSTRUCTOR_EXCLUDE or not f.startswith("case-file/"):
            continue  # instructor-side or out-of-scope: never rendered
        label = base.replace(".md", "").replace("-", " ").upper()
        kind = "WITNESS STATEMENT" if base.startswith(("witness", "statement")) else "CASE-FILE DOCUMENT"
        if label.startswith(kind):        # avoid "WITNESS STATEMENT · WITNESS STATEMENT X"
            meta = "CASE FILE · " + label
        else:
            meta = kind + " · " + label
        cards.append(render_doc_card(m["_dir"], f, meta))
    return cards

def build_one_packet(corpus, m, man):
    slug = m["_slug"]
    rel = "matters/{s}/index.html".format(s=slug)
    up = up_prefix(rel)
    ex = m.get("_exercise")
    sections = (ex or {}).get("sections", {})
    juris = corpus["juris"].get(m.get("jurisdiction") if m.get("tier") == "meridian" else (m.get("jurisdiction") or "").upper(), {})

    # --- TOC + parts
    toc_items, parts_html = [], []
    case_file_cards = []
    for idx, key in enumerate(SECTION_ORDER, start=1):
        sec = sections.get(key)
        if not sec:
            continue
        num = "{:02d}".format(idx)
        title = sec.get("title", key.replace("_", " ").title())
        anchor = "part-" + key.replace("_", "-")
        toc_items.append('<a href="#{a}">{n} · {t}</a>'.format(a=anchor, n=num, t=esc(title)))
        if key == "case_file":
            files = sec.get("files") or []
            case_file_cards = render_case_file_cards(m, files)
            inner = ('<p>The case file contains {n} documents — witness statements and exhibits. '
                     'Work only from these materials and from what you develop in your interviews.</p>'
                     .format(n=len(case_file_cards)))
            parts_html.append((key, num, title, anchor, inner))
        else:
            parts_html.append((key, num, title, anchor, markdown(sec.get("body_md", ""))))

    # --- per-side confidential note (m04/m14 pattern)
    conf_sides = [s for s in (m.get("sides") or []) if s.get("confidential_fact_refs")]
    side_conf_html = ""
    if conf_sides:
        blocks = []
        for s in conf_sides:
            refs = " ".join('<span class="chip">{r}</span>'.format(r=esc(r))
                            for r in s["confidential_fact_refs"])
            blocks.append("""
    <details class="side-conf">
      <summary>CONFIDENTIAL TO {label} · {n} FACTS</summary>
      <p class="matter-card__premise" style="margin-top:var(--sp-3)">This side holds confidential
      facts known only to it — students take one side and receive only their own side&rsquo;s
      confidential sheet (distributed by the instructor; surfaced in interview through the persona
      engine). Fact anchors:</p>
      <p class="chips">{refs}</p>
    </details>""".format(label=esc(s.get("label", "")), n=len(s["confidential_fact_refs"]), refs=refs))
        side_conf_html = """
  <section class="part" id="side-confidential" aria-labelledby="sideconf-h">
    <div class="part__head"><span class="part__num" aria-hidden="true">§</span>
      <h2 id="sideconf-h">Per-side confidential facts</h2></div>
    <p>This is a two-sided negotiation: <strong>students take one side.</strong> Each side&rsquo;s
    confidential facts are listed by anchor only — the content stays with the side that holds it.</p>
    {blocks}
  </section>""".format(blocks="".join(blocks))

    # --- personas / interview CTA
    client = pick_client_persona(m, man)
    represented = pick_represented_persona(m, client)
    persona_rows = []
    for p in m["_personas"].values():
        ident = p.get("identity", {})
        is_client = client is not None and p["id"] == client["id"]
        is_rep = (p.get("rule_4_2") or {}).get("applies")
        chips = []
        if is_client:
            chips.append('<span class="chip chip--tier-volunteered">YOUR CLIENT</span>')
        if is_rep:
            chips.append('<span class="chip chip--tier-rapport">REPRESENTED · RULE 4.2</span>')
        if is_client:
            cta = '<a class="btn" href="{h}">Interview the client</a>'.format(
                h=esc(chat_href(rel, m, p, False)))
        elif is_rep and represented is not None and p["id"] == represented["id"]:
            cta = '<a class="btn btn--claret" href="{h}">Attempt interview (Rule 4.2)</a>'.format(
                h=esc(chat_href(rel, m, p, True)))
        else:
            cta = '<a class="btn btn--ghost" href="{h}">Interview</a>'.format(
                h=esc(chat_href(rel, m, p, bool(is_rep))))
        persona_rows.append("""
    <div class="persona-line">
      <span class="persona-line__name">{name}</span>
      <span class="card__meta">{role}</span>
      <span class="chips">{chips}</span>
      <span style="margin-left:auto">{cta}</span>
    </div>""".format(name=esc(ident.get("name", "")), role=esc(ident.get("role", "")),
                     chips=" ".join(chips), cta=cta))

    # Keyless sample: both DWI packets (Meridian m05 + real-state m15) link to the m05 recording,
    # so it can be experienced with no API key before any live interview.
    sample_cta = ""
    if m["id"] in ("m05", "m15"):
        sample_cta = ('<a class="btn btn--ghost" href="{h}">Watch a sample consultation ▸</a>'
                      .format(h=esc(sample_href(rel))))

    interview_html = """
  <section class="part no-print" id="interview" aria-labelledby="interview-h">
    <div class="part__head"><span class="part__num" aria-hidden="true">☎</span>
      <h2 id="interview-h">Interviews &amp; critique</h2></div>
    <p>Conduct your simulated interviews through the persona engine. The client is yours to
    interview; the represented persona is the Rule 4.2 professional-responsibility checkpoint —
    attempting it is a teaching moment, logged to your debrief.
    No API key yet? Watch a fully recorded sample interview and debrief first.</p>
    {rows}
    <div class="cta-row">
      {sample}
      <a class="btn" href="{crit}">Submit a deliverable for critique</a>
    </div>
  </section>""".format(rows="".join(persona_rows), crit=esc(critique_href(rel, m)), sample=sample_cta)

    # --- rubric
    rubric_html = build_rubric_section(m, rel)
    rubric_section = """
  <section class="part" id="rubric" aria-labelledby="rubric-h">
    <div class="part__head"><span class="part__num" aria-hidden="true">✓</span>
      <h2 id="rubric-h">Rubric</h2></div>
    {r}
  </section>""".format(r=rubric_html) if rubric_html else ""

    # --- business
    business_html = build_business_section(m)
    business_section = """
  <section class="part" id="business" aria-labelledby="business-h">
    <div class="part__head"><span class="part__num" aria-hidden="true">$</span>
      <h2 id="business-h">Business of the matter</h2></div>
    <p>Every matter carries its business layer — the engagement, the clock, and (where client
    funds are held) the trust ledger. The <a class="link" href="{up}firm/index.html">firm
    dashboard</a> aggregates all twenty.</p>
    {b}
  </section>""".format(up=up, b=business_html) if business_html else ""

    # --- skills chips (bidirectional with skills browser)
    skills_by_id = {s["id"]: s for s in corpus["skills"]["skills"]}
    skill_chips = " ".join(
        '<a class="chip chip--skill" href="{up}skills/index.html#{sid}" title="{name}">{sid}</a>'.format(
            up=up, sid=esc(sid), name=esc(skills_by_id.get(sid, {}).get("name", "")))
        for sid in (m.get("skill_refs") or []))

    # --- caption header
    jname = juris.get("name", m.get("jurisdiction", ""))
    tier = "meridian" if m.get("tier") == "meridian" else "real"
    header = """
<header class="reveal">
  <div class="chips">{tc} <span class="chip">{fee} FEE</span> <span class="chip chip--folio">{mid}</span></div>
  <h1 style="margin-top:var(--sp-3)">{cap}</h1>
  <p class="lede">{shape} · {jname}</p>
  <div class="chips" aria-label="Skills exercised">{skills}</div>
</header>""".format(tc=tier_chip(tier, m.get("jurisdiction")), fee=esc((m.get("fee_type") or "").upper()),
                    mid=esc(m["id"].upper()), cap=esc(m.get("caption", "")),
                    shape=esc(SHAPE_LABELS.get(m.get("shape"), m.get("shape", ""))),
                    jname=esc(jname), skills=skill_chips)

    instructor_note = """
  <p class="instructor-note">Instructor materials (master fact pattern, teaching notes, answer
  guidance) are maintained separately and are not part of the student packet.
  <span class="chip chip--coming-soon">FACULTY PORTAL · COMING SOON</span></p>"""

    toc_extra = []
    if conf_sides:
        toc_extra.append('<a href="#side-confidential">§ · CONFIDENTIAL</a>')
    toc_extra.append('<a href="#business">$ · BUSINESS</a>')
    toc_extra.append('<a href="#rubric">✓ · RUBRIC</a>')
    toc_extra.append('<a href="#interview">☎ · INTERVIEW</a>')

    def assemble(case_file_inline, cf_link_html=""):
        parts_out = []
        for key, num, title, anchor, inner in parts_html:
            if key == "case_file":
                content = inner + (("".join(case_file_cards)) if case_file_inline else cf_link_html)
            else:
                content = inner
            parts_out.append("""
  <section class="part" id="{a}" aria-labelledby="{a}-h">
    <div class="part__head"><span class="part__num" aria-hidden="true">{n}</span>
      <h2 id="{a}-h">{t}</h2></div>
    {c}
  </section>""".format(a=anchor, n=num, t=esc(title), c=content))
        body = """
{header}
<div class="brass-rule" role="presentation"></div>
<div class="packet-layout">
  <nav class="toc-rail" aria-label="Packet contents">
    {toc}
    {toc_extra}
  </nav>
  <div class="packet-body">
    {parts}
    {side_conf}
    {business}
    {rubric}
    {interview}
    {instructor}
  </div>
</div>""".format(header=header, toc="\n    ".join(toc_items), toc_extra="\n    ".join(toc_extra),
                 parts="".join(parts_out), side_conf=side_conf_html, business=business_section,
                 rubric=rubric_section, interview=interview_html, instructor=instructor_note)
        return page_shell(rel, m.get("caption", slug),
                          "M2 · MATTERS · " + slug.upper().replace("-", "·")[:34],
                          [("Home", "../../index.html"), ("Matters", "../index.html"),
                           (m["id"].upper(), None)], body)

    # budget: try inline; if > 250KB split case file to sub-page
    html_full = assemble(True)
    written = []
    if len(html_full.encode("utf-8")) > 250_000 and case_file_cards:
        sub_rel = "matters/{s}/case-file.html".format(s=slug)
        cf_body = """
<section class="reveal">
  <p class="eyebrow">CASE FILE · {mid}</p>
  <h1>{cap} — case file</h1>
  <p class="lede"><a class="link" href="index.html">← Back to the packet</a></p>
</section>
<div class="brass-rule" role="presentation"></div>
{cards}""".format(mid=esc(m["id"].upper()), cap=esc(m.get("caption", "")),
                  cards="".join(case_file_cards))
        sub_html = page_shell(sub_rel, m.get("caption", slug) + " — Case File",
                              "M2 · MATTERS · CASE FILE",
                              [("Home", "../../index.html"), ("Matters", "../index.html"),
                               (m["id"].upper(), "index.html"), ("Case file", None)], cf_body)
        write_file(sub_rel, sub_html)
        written.append((sub_rel, len(sub_html.encode("utf-8"))))
        link_html = ('<p><a class="btn" href="case-file.html">Open the case file '
                     '({n} documents)</a></p>'.format(n=len(case_file_cards)))
        html_full = assemble(False, link_html)
    write_file(rel, html_full)
    written.append((rel, len(html_full.encode("utf-8"))))
    return written

# --------------------------------------------------------------------------- #
# Firm dashboard — per docs/research/firm-dashboard-viz-spec.md
#
# Palette-reconciliation (binding note in the spec): page chrome/cards/type use
# Practicum Press tokens; CHART MARK slots keep the spec's validated hexes
# (both palettes passed the dataviz validator; Press brass/claret fail the
# spec's categorical-contrast rules on paper, so the validated hexes stay).
# The Press site has one always-light paper surface, so the light token set is
# used; the spec's dark-mode block is inapplicable here (no dark surface).
# --------------------------------------------------------------------------- #
VIZ = {
    "cat1": "#2a78d6", "cat2": "#1baf7a", "cat3": "#eda100", "cat4": "#008300",
    "ord1": "#86b6ef", "ord2": "#3987e5", "ord3": "#184f95",
    "div_under": "#2a78d6", "div_mid": "#f0efec", "div_over": "#e34948",
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
    "pos": "#006300", "axis": "#c3c2b7", "grid": "#e1e0d9", "muted": "#898781",
}
FEE_COLORS = {"hourly": VIZ["cat1"], "contingency": VIZ["cat2"],
              "flat": VIZ["cat3"], "retainer": VIZ["cat4"]}
# Pattern slots (used only when PATTERNS is on / under forced-colors). Categorical
# charts get distinct angle+density per slot; ordinal/severity ramps order density
# by magnitude (sparse -> dense). Single-hue charts (book, trust) carry no pattern —
# one series has nothing to disambiguate when the palette collapses.
FEE_PAT = {"hourly": "p45_5", "contingency": "p135_5", "flat": "p45_3", "retainer": "p135_3"}
UTIL_PAT = {"FIRM-TK-01": "p45_5", "FIRM-TK-02": "p135_5"}
FUNNEL_PAT = ["p45_6", "p45_4", "p45_3"]          # worked -> billed -> collected
AR_PAT = {"b0_30": "p45_6", "b31_60": "p45_5", "b61_90": "p45_4", "b90_plus": "p45_3"}
BUDGET_PAT = {True: "p45_5", False: "p135_5"}      # favorable / unfavorable
AR_BUCKETS = [("b0_30", "0–30", VIZ["good"], "✓"), ("b31_60", "31–60", VIZ["warning"], "◔"),
              ("b61_90", "61–90", VIZ["serious"], "◑"), ("b90_plus", "90+", VIZ["critical"], "⚠")]

def csv_bytes(headers, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

def kpi_tile(label, value, delta_html="", chip_html="", hero=False, lesson=""):
    return """
  <div class="kpi-tile{h}">
    <p class="kpi-tile__label">{label}</p>
    <p class="kpi-tile__value">{value}</p>
    {delta}{chip}
    <p class="viz-note" style="margin:.4rem 0 0">{lesson}</p>
  </div>""".format(h=" kpi-hero" if hero else "", label=esc(label), value=esc(value),
                   delta=delta_html, chip=chip_html, lesson=esc(lesson))

def build_firm_dashboard(corpus):
    rel = "firm/index.html"
    firm = corpus["firm"]
    matters = corpus["matters"]
    man_by_id = {m["id"]: m for m in corpus["manifest"]["matters"]}
    client_names = {c["id"]: c["name"] for c in firm.get("clients", [])}

    # ---------- derived figures ----------
    funnel = firm["funnel"]
    worked, billed, collected = funnel["worked"], funnel["billed"], funnel["collected"]
    realization = billed / worked
    collection = collected / billed
    writedowns = worked - billed
    writeoffs = billed - collected
    ar = firm["ar_aging"]
    ar_total = sum(ar.values())
    ar_over90 = ar["days_over_90"]
    ar_over90_pct = ar_over90 / ar_total
    trust = firm["trust"]
    budget = firm["budget"]
    fee_rev = next(b for b in budget if b["id"] == "FIRM-B-01")
    budget_var = (fee_rev["actual"] - fee_rev["budget"]) / fee_rev["budget"]

    fees_rows = []
    for m in matters:
        f = matter_fees(m)
        fees_rows.append({
            "matter_id": m["id"], "caption": m.get("caption", ""),
            "client": client_names.get(m.get("client_id"), ""),
            "fee_type": m.get("fee_type", ""), "fees": f,
        })
    fees_rows.sort(key=lambda r: -r["fees"])
    fees_total = sum(r["fees"] for r in fees_rows)
    cum = 0.0
    top80_idx = len(fees_rows)
    for i, r in enumerate(fees_rows):
        cum += r["fees"]
        r["cum_pct"] = cum / fees_total
        if r["cum_pct"] >= 0.8 and top80_idx == len(fees_rows):
            top80_idx = i + 1

    fee_mix = defaultdict(lambda: {"fees": 0.0, "count": 0})
    for r in fees_rows:
        fee_mix[r["fee_type"]]["fees"] += r["fees"]
        fee_mix[r["fee_type"]]["count"] += 1
    fee_order = ["hourly", "contingency", "flat", "retainer"]     # fixed, never recolored

    util = firm["utilization_monthly"]
    months = sorted({u["month"] for u in util})
    util_by = {(u["month"], u["timekeeper_id"]): u["billable_hours"] for u in util}
    target_h = util[0].get("target_hours", 150) if util else 150
    tk_names = {t["id"]: t["name"] for t in firm.get("timekeepers", [])}

    # ---------- CSV twins ----------
    csvs = []
    csvs.append(("book-of-business.csv", csv_bytes(
        ["matter_id", "caption", "client", "fee_type", "fees", "cumulative_pct"],
        [[r["matter_id"], r["caption"], r["client"], r["fee_type"],
          "{:.2f}".format(r["fees"]), "{:.1f}".format(r["cum_pct"] * 100)] for r in fees_rows])))
    csvs.append(("fee-mix.csv", csv_bytes(
        ["fee_type", "matters", "fees", "pct_of_fees"],
        [[ft, fee_mix[ft]["count"], "{:.2f}".format(fee_mix[ft]["fees"]),
          "{:.1f}".format(fee_mix[ft]["fees"] / fees_total * 100)] for ft in fee_order])))
    csvs.append(("utilization.csv", csv_bytes(
        ["month", "FIRM-TK-01", "FIRM-TK-02", "total", "pct_of_target"],
        [[mo, util_by.get((mo, "FIRM-TK-01"), 0), util_by.get((mo, "FIRM-TK-02"), 0),
          util_by.get((mo, "FIRM-TK-01"), 0) + util_by.get((mo, "FIRM-TK-02"), 0),
          "{:.0f}".format((util_by.get((mo, "FIRM-TK-01"), 0) + util_by.get((mo, "FIRM-TK-02"), 0))
                          / (2 * target_h) * 100)] for mo in months])))
    csvs.append(("realization-funnel.csv", csv_bytes(
        ["stage", "amount", "pct_of_worked", "step_loss", "step_rate"],
        [["worked", worked, "100.0", "", ""],
         ["billed", billed, "{:.1f}".format(billed / worked * 100),
          writedowns, "{:.1f}".format(realization * 100)],
         ["collected", collected, "{:.1f}".format(collected / worked * 100),
          writeoffs, "{:.1f}".format(collection * 100)]])))
    ar_rows_csv = [["FIRM TOTAL", ar["current"] + ar["days_1_30"], ar["days_31_60"],
                    ar["days_61_90"], ar["days_over_90"], ar_total]]
    for t in firm.get("ar_top_matters", []):
        b = t["buckets"]
        ar_rows_csv.append([t["matter_id"] + " · " + t["client_name"], b["b0_30"], b["b31_60"],
                            b["b61_90"], b["b90_plus"], t["total"]])
    csvs.append(("ar-aging.csv", csv_bytes(
        ["row", "b0_30", "b31_60", "b61_90", "b90_plus", "total"], ar_rows_csv)))
    csvs.append(("trust-balances.csv", csv_bytes(
        ["matter_id", "client", "kind", "amount", "reconciled", "last_reconciled"],
        [[h["matter_id"], client_names.get(man_by_id.get(h["matter_id"], {}).get("client_id"), ""),
          h["kind"], h["amount"], "yes" if trust["three_way_reconciled"] else "no",
          trust["last_reconciled"]] for h in trust["holdings"]])))
    csvs.append(("budget-vs-actual.csv", csv_bytes(
        ["line", "budget", "actual", "variance", "variance_pct", "favorable"],
        [[b["name"], b["budget"], b["actual"], b["actual"] - b["budget"],
          "{:.1f}".format((b["actual"] - b["budget"]) / b["budget"] * 100),
          "yes" if b["variance_is_good"] else "no"] for b in budget])))
    for name, data in csvs:
        write_file(os.path.join("firm", "csv", name), data)

    # ---------- KPI row ----------
    kpis = []
    kpis.append(kpi_tile("Fees collected · trailing 12 mo", money_compact(collected),
                         delta_html='<p class="kpi-tile__delta">PERIOD ENDS {d}</p>'.format(d=esc(firm["as_of_date"])),
                         hero=True, lesson="The number the firm lives on."))
    kpis.append(kpi_tile("Realization rate", "{:.1f}%".format(realization * 100),
                         delta_html='<p class="kpi-tile__delta">TARGET {t:.0f}%</p>'.format(t=firm["realization_target"] * 100)
                         if realization >= firm["realization_target"] else
                         '<p class="kpi-tile__delta is-down">TARGET {t:.0f}% · BELOW</p>'.format(t=firm["realization_target"] * 100),
                         lesson="Write-downs erode worked value."))
    kpis.append(kpi_tile("Collection rate", "{:.1f}%".format(collection * 100),
                         delta_html='<p class="kpi-tile__delta">TARGET {t:.0f}%</p>'.format(t=firm["collection_target"] * 100)
                         if collection >= firm["collection_target"] else
                         '<p class="kpi-tile__delta is-down">TARGET {t:.0f}% · BELOW</p>'.format(t=firm["collection_target"] * 100),
                         lesson="Billing is not cash."))
    over_chip = ('<span class="kpi-tile__chip is-warn">⚠ OVER 10% OF AR</span>'
                 if ar_over90_pct > 0.10 else
                 '<span class="kpi-tile__chip">✓ UNDER 10% OF AR</span>')
    kpis.append(kpi_tile("AR over 90 days", money_compact(ar_over90),
                         delta_html='<p class="kpi-tile__delta">{p:.1f}% OF {t}</p>'.format(
                             p=ar_over90_pct * 100, t=money_compact(ar_total)),
                         chip_html=over_chip, lesson="Old AR rarely collects."))
    rec_chip = ('<span class="kpi-tile__chip">✓ RECONCILED {d}</span>'.format(d=esc(trust["last_reconciled"]))
                if trust["three_way_reconciled"] else
                '<span class="kpi-tile__chip is-warn">⚠ DISCREPANCY</span>')
    kpis.append(kpi_tile("Trust balance", money_compact(trust["balance"]),
                         chip_html=rec_chip, lesson="Client money is never firm money."))
    var_cls = "is-down" if budget_var < 0 else "is-up"
    kpis.append(kpi_tile("Fee-revenue vs plan", "{:+.1f}%".format(budget_var * 100),
                         delta_html='<p class="kpi-tile__delta {c}">VS BUDGET {b}</p>'.format(
                             c=var_cls, b=money_compact(fee_rev["budget"])),
                         lesson="Plan versus reality is the discipline."))

    # ---------- Chart 1: book of business ----------
    W, LBL, BARMAX = 640, 168, 640 - 168 - 76
    rowh, gap = 20, 6
    maxf = fees_rows[0]["fees"] if fees_rows else 1
    H = (rowh + gap) * len(fees_rows) + 34
    parts = []
    hits = []
    boundary_y = (rowh + gap) * top80_idx - gap / 2 + 8
    parts.append('<line x1="{x0}" y1="8" x2="{x0}" y2="{h}" class="chart-axis"/>'.format(x0=LBL, h=H - 22))
    for i, r in enumerate(fees_rows):
        y = 8 + i * (rowh + gap)
        w = BARMAX * r["fees"] / maxf
        parts.append(_text(LBL - 6, y + 14, r["matter_id"].upper() + " " + (r["client"][:16] or ""), anchor="end"))
        viz_mark(parts, hits, LBL, y, w, rowh, VIZ["cat1"],
                 money(r["fees"]),
                 "{m} · {c} · {ft} · {p:.1f}% of book".format(
                     m=r["matter_id"].upper(), c=r["client"], ft=r["fee_type"],
                     p=r["fees"] / fees_total * 100),
                 VIZ["cat1"])
        if i < 3:
            parts.append(_text(LBL + w + 5, y + 14, money_compact(r["fees"]), cls="chart-val"))
    parts.append('<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{c}" stroke-width="1" stroke-dasharray="4 3"/>'.format(
        x0=LBL, x1=W - 8, y=boundary_y, c=VIZ["axis"]))
    parts.append(_text(W - 8, boundary_y - 4, "Top {n} = 80% of fees".format(n=top80_idx),
                       cls="chart-anno", anchor="end"))
    svg1 = _svg(W, H, "".join(parts) + "".join(hits), "Fees by matter, sorted descending")
    tbl1 = _table("Book of business — fees by matter",
                  ["Matter", "Client", "Fee type", "Fees", "Cumulative %"],
                  [[r["matter_id"].upper(), r["client"], r["fee_type"],
                    money(r["fees"]), "{:.1f}%".format(r["cum_pct"] * 100)] for r in fees_rows],
                  num_cols={3, 4})
    chart1 = chart_card("viz1", "Book of business — fees by matter",
                        "Revenue concentration: a handful of matters carry the book. Fees shown are "
                        "billed-to-date (worked value on contingency matters).",
                        svg1, tbl1)

    # ---------- Chart 2: fee-arrangement mix ----------
    W2, H2, LBL2 = 640, 120, 120
    barw = W2 - LBL2 - 16
    parts = []
    hits = []
    y = 14
    x = LBL2
    parts.append(_text(LBL2 - 6, y + 16, "$ share", anchor="end"))
    for ft in fee_order:
        share = fee_mix[ft]["fees"] / fees_total
        w = barw * share
        viz_mark(parts, hits, x + 1, y, max(w - 2, 0), 24, FEE_COLORS[ft],
                 money(fee_mix[ft]["fees"]),
                 "{n} · {p:.1f}% of fees".format(n=ft.upper(), p=share * 100),
                 FEE_COLORS[ft], pat=FEE_PAT[ft], seg=True)
        if share > 0.09:
            lum_ink = "#ffffff" if ft in ("hourly", "contingency", "retainer") else "#0b0b0b"
            parts.append(_text(x + w / 2, y + 16, "{:.0f}%".format(share * 100),
                               cls="chart-val", anchor="middle",
                               extra='fill="{c}"'.format(c=lum_ink)))
        x += w
    y = 62
    x = LBL2
    parts.append(_text(LBL2 - 6, y + 16, "matters", anchor="end"))
    for ft in fee_order:
        share = fee_mix[ft]["count"] / len(fees_rows)
        w = barw * share
        viz_mark(parts, hits, x + 1, y, max(w - 2, 0), 24, FEE_COLORS[ft],
                 "{n} matters".format(n=fee_mix[ft]["count"]),
                 "{n} · {p:.1f}% of matters".format(n=ft.upper(), p=share * 100),
                 FEE_COLORS[ft], pat=FEE_PAT[ft], seg=True)
        if share > 0.09:
            lum_ink = "#ffffff" if ft in ("hourly", "contingency", "retainer") else "#0b0b0b"
            parts.append(_text(x + w / 2, y + 16, str(fee_mix[ft]["count"]),
                               cls="chart-val", anchor="middle", extra='fill="{c}"'.format(c=lum_ink)))
        x += w
    svg2 = _svg(W2, H2, "".join(parts) + "".join(hits), "Fee-arrangement mix, dollar share and matter count")
    legend2 = ('<div class="legend">' + "".join(
        '<span><i style="background:{c}"></i>{n}</span>'.format(c=FEE_COLORS[ft], n=esc(ft.upper()))
        for ft in fee_order) + "</div>")
    tbl2 = _table("Fee-arrangement mix", ["Fee type", "Matters", "Fees", "% of fees"],
                  [[ft, fee_mix[ft]["count"], money(fee_mix[ft]["fees"]),
                    "{:.1f}%".format(fee_mix[ft]["fees"] / fees_total * 100)] for ft in fee_order],
                  num_cols={1, 2, 3})
    chart2 = chart_card("viz2", "Fee-arrangement mix",
                        "How you are paid shapes risk and cash timing: hourly bills monthly, "
                        "contingency pays at the end or never, flat and retainer pay up front.",
                        svg2 + legend2, tbl2)

    # ---------- Chart 3: utilization ----------
    W3, H3, PAD3 = 640, 220, 40
    plot_w = W3 - PAD3 - 10
    plot_h = H3 - 52
    max_h = 180.0
    groupw = plot_w / len(months)
    bw = min(12, groupw / 3)
    half = groupw / 2
    tk_a = tk_names.get("FIRM-TK-01", "Timekeeper A")
    tk_b = tk_names.get("FIRM-TK-02", "Timekeeper B")
    parts = []
    hits = []
    for gy in (0, 60, 120, 180):
        yy = 16 + plot_h * (1 - gy / max_h)
        parts.append('<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{c}" stroke-width="1"/>'.format(
            x0=PAD3, x1=W3 - 10, y=round(yy, 1), c=VIZ["grid"]))
        parts.append(_text(PAD3 - 4, yy + 4, str(gy), cls="chart-anno", anchor="end"))
    ty = 16 + plot_h * (1 - target_h / max_h)
    for i, mo in enumerate(months):
        gx = PAD3 + i * groupw + groupw / 2
        a = util_by.get((mo, "FIRM-TK-01"), 0)
        b = util_by.get((mo, "FIRM-TK-02"), 0)
        ha, hb = plot_h * a / max_h, plot_h * b / max_h
        viz_mark(parts, hits, gx - bw - 1, 16 + plot_h - ha, bw, ha, VIZ["cat1"],
                 "{a}h".format(a=a), "{mo} · {tk}".format(mo=mo, tk=tk_a), VIZ["cat1"],
                 pat=UTIL_PAT["FIRM-TK-01"], hit=(gx - half, 16, half, plot_h))
        viz_mark(parts, hits, gx + 1, 16 + plot_h - hb, bw, hb, VIZ["cat2"],
                 "{b}h".format(b=b), "{mo} · {tk}".format(mo=mo, tk=tk_b), VIZ["cat2"],
                 pat=UTIL_PAT["FIRM-TK-02"], hit=(gx, 16, half, plot_h))
        if i == len(months) - 1:
            parts.append(_text(gx - bw / 2 - 1, 16 + plot_h - ha - 4, str(a), cls="chart-val", anchor="middle"))
            parts.append(_text(gx + bw / 2 + 1, 16 + plot_h - hb - 4, str(b), cls="chart-val", anchor="middle"))
        if i % 2 == 0:
            parts.append(_text(gx, H3 - 18, mo[2:].replace("-", "/"), cls="chart-anno", anchor="middle"))
    parts.append('<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="#0b0b0b" stroke-width="2" opacity=".55"/>'.format(
        x0=PAD3, x1=W3 - 10, y=round(ty, 1)))
    parts.append(_text(W3 - 12, ty - 5, "Target {t:.0f}h".format(t=target_h), cls="chart-anno", anchor="end"))
    svg3 = _svg(W3, H3, "".join(parts) + "".join(hits), "Monthly billable hours by timekeeper against a 150-hour target")
    legend3 = ('<div class="legend">'
               '<span><i style="background:{c1}"></i>{a}</span>'
               '<span><i style="background:{c2}"></i>{b}</span></div>').format(
        c1=VIZ["cat1"], a=esc(tk_names.get("FIRM-TK-01", "TK-01")),
        c2=VIZ["cat2"], b=esc(tk_names.get("FIRM-TK-02", "TK-02")))
    tbl3 = _table("Utilization — billable hours vs target",
                  ["Month", tk_names.get("FIRM-TK-01", "A"), tk_names.get("FIRM-TK-02", "B"),
                   "Total", "% of target"],
                  [[mo, util_by.get((mo, "FIRM-TK-01"), 0), util_by.get((mo, "FIRM-TK-02"), 0),
                    util_by.get((mo, "FIRM-TK-01"), 0) + util_by.get((mo, "FIRM-TK-02"), 0),
                    "{:.0f}%".format((util_by.get((mo, "FIRM-TK-01"), 0) + util_by.get((mo, "FIRM-TK-02"), 0)) / (2 * target_h) * 100)]
                   for mo in months], num_cols={1, 2, 3, 4})
    chart3 = chart_card("viz3", "Utilization — billable hours vs target",
                        "Time is inventory: hours not worked this month are never sold later.",
                        svg3 + legend3, tbl3)

    # ---------- Chart 4: realization funnel ----------
    W4, H4, LBL4 = 640, 190, 110
    barmax4 = W4 - LBL4 - 110
    stages = [("Worked", worked, VIZ["ord1"]), ("Billed", billed, VIZ["ord2"]),
              ("Collected", collected, VIZ["ord3"])]
    parts = []
    hits = []
    y = 12
    for i, (name, val, col) in enumerate(stages):
        w = barmax4 * val / worked
        parts.append(_text(LBL4 - 6, y + 18, name, anchor="end"))
        viz_mark(parts, hits, LBL4, y, w, 26, col,
                 money(val), "{s} · {p:.1f}% of worked".format(s=name, p=val / worked * 100),
                 col, pat=FUNNEL_PAT[i])
        parts.append(_text(LBL4 + w + 6, y + 18, money(val), cls="chart-val"))
        if i == 0:
            anno = "−{d} write-downs · realization {r:.0f}%".format(d=money_compact(writedowns), r=realization * 100)
        elif i == 1:
            anno = "−{d} write-offs · collection {c:.0f}%".format(d=money_compact(writeoffs), c=collection * 100)
        else:
            anno = None
        if anno:
            parts.append(_text(LBL4 + 8, y + 26 + 15, anno, cls="chart-anno"))
        y += 26 + 32
    svg4 = _svg(W4, H4, "".join(parts) + "".join(hits), "Realization funnel from worked to billed to collected")
    tbl4 = _table("Realization funnel", ["Stage", "$", "% of worked", "Step loss", "Step rate"],
                  [["Worked", money(worked), "100.0%", "—", "—"],
                   ["Billed", money(billed), "{:.1f}%".format(billed / worked * 100),
                    money(writedowns), "{:.1f}%".format(realization * 100)],
                   ["Collected", money(collected), "{:.1f}%".format(collected / worked * 100),
                    money(writeoffs), "{:.1f}%".format(collection * 100)]],
                  num_cols={1, 2, 3, 4})
    chart4 = chart_card("viz4", "Realization funnel: worked → billed → collected",
                        "The crown lesson: every dollar leaks twice — once when you bill it, "
                        "again when you try to collect it.", svg4, tbl4)

    # ---------- Chart 5: AR aging ----------
    ar_display = [{"label": "FIRM TOTAL", "buckets": {
        "b0_30": ar["current"] + ar["days_1_30"], "b31_60": ar["days_31_60"],
        "b61_90": ar["days_61_90"], "b90_plus": ar["days_over_90"]},
        "total": ar_total}]
    for t in firm.get("ar_top_matters", []):
        ar_display.append({"label": t["matter_id"].upper() + " · " + t["client_name"],
                           "buckets": t["buckets"], "total": t["total"]})
    W5, LBL5 = 640, 190
    barmax5 = W5 - LBL5 - 84
    rowh5, gap5 = 22, 8
    H5 = 10 + len(ar_display) * (rowh5 + gap5) + 6
    maxar = max(r["total"] for r in ar_display)
    parts = []
    hits = []
    for i, r in enumerate(ar_display):
        y = 10 + i * (rowh5 + gap5)
        parts.append(_text(LBL5 - 6, y + 15, r["label"][:26], anchor="end"))
        x = LBL5
        for key, blabel, col, icon in AR_BUCKETS:
            v = r["buckets"].get(key, 0)
            if v <= 0:
                continue
            w = barmax5 * v / maxar
            viz_mark(parts, hits, x, y, max(w - 2, 1), rowh5, col,
                     money(v), "{ic} {b} days · {row}".format(ic=icon, b=blabel, row=r["label"]),
                     col, pat=AR_PAT[key], seg=True)
            if key == "b90_plus":
                parts.append(_text(x + max(w - 2, 1) + 5, y + 15, "⚠ " + money_compact(v), cls="chart-val"))
            x += w
    svg5 = _svg(W5, H5, "".join(parts) + "".join(hits), "Accounts receivable aging by bucket, firm total and top matters")
    legend5 = ('<div class="legend">' + "".join(
        '<span><i style="background:{c}"></i>{ic} {b} DAYS</span>'.format(c=c, ic=ic, b=b)
        for _, b, c, ic in AR_BUCKETS) + "</div>")
    tbl5 = _table("AR aging", ["Row", "✓ 0–30", "◔ 31–60", "◑ 61–90", "⚠ 90+", "Total"],
                  [[r["label"], money(r["buckets"].get("b0_30", 0)), money(r["buckets"].get("b31_60", 0)),
                    money(r["buckets"].get("b61_90", 0)), money(r["buckets"].get("b90_plus", 0)),
                    money(r["total"])] for r in ar_display], num_cols={1, 2, 3, 4, 5})
    chart5 = chart_card("viz5", "AR aging",
                        "Old invoices don't pay: past 90 days, collection odds fall off a cliff. "
                        "Every bucket keeps its icon and label — never color alone.",
                        svg5 + legend5, tbl5)

    # ---------- Chart 6: trust balances ----------
    holdings = trust["holdings"]
    W6, LBL6 = 640, 190
    barmax6 = W6 - LBL6 - 110
    rowh6, gap6 = 22, 10
    H6 = 12 + len(holdings) * (rowh6 + gap6)
    maxt = max(h["amount"] for h in holdings)
    rec_word = "✓ reconciled" if trust["three_way_reconciled"] else "⚠ discrepancy"
    parts = []
    hits = []
    for i, h in enumerate(holdings):
        y = 12 + i * (rowh6 + gap6)
        cname = client_names.get(man_by_id.get(h["matter_id"], {}).get("client_id"), "")
        parts.append(_text(LBL6 - 6, y + 15, h["matter_id"].upper() + " · " + cname[:18], anchor="end"))
        w = barmax6 * h["amount"] / maxt
        viz_mark(parts, hits, LBL6, y, w, rowh6, VIZ["cat1"],
                 money(h["amount"]),
                 "{m} · {c} · {r}".format(m=h["matter_id"].upper(), c=cname, r=rec_word),
                 VIZ["cat1"])
        parts.append(_text(LBL6 + w + 6, y + 15, money(h["amount"]) + "  ✓", cls="chart-val"))
    svg6 = _svg(W6, H6, "".join(parts) + "".join(hits), "Trust balances by matter, each reconciled")
    banner = ('<p class="kpi-tile__chip" style="display:inline-block;margin-bottom:var(--sp-2)">'
              '✓ TRUST LEDGER VS BANK · BALANCED AT {b} · THREE-WAY RECONCILED {d}</p>').format(
        b=money(trust["balance"]), d=esc(trust["last_reconciled"])) if trust["three_way_reconciled"] else (
        '<p class="kpi-tile__chip is-warn" style="display:inline-block">⚠ TRUST DISCREPANCY</p>')
    tbl6 = _table("Trust balances & reconciliation",
                  ["Matter", "Client", "Kind", "Balance", "Reconciled?", "Last reconciled"],
                  [[h["matter_id"].upper(),
                    client_names.get(man_by_id.get(h["matter_id"], {}).get("client_id"), ""),
                    h["kind"], money(h["amount"]),
                    "✓ yes" if trust["three_way_reconciled"] else "⚠ no",
                    trust["last_reconciled"]] for h in holdings], num_cols={3})
    chart6 = chart_card("viz6", "Trust balances & reconciliation",
                        "To-the-penny or it's an ethics violation: client funds reconcile "
                        "three ways — ledger, bank, and per-client — every month.",
                        banner + svg6, tbl6)

    # ---------- Chart 7: budget vs actual ----------
    W7, LBL7 = 640, 210
    center = LBL7 + (W7 - LBL7 - 90) / 2
    halfmax = (W7 - LBL7 - 90) / 2
    rowh7, gap7 = 20, 8
    H7 = 14 + len(budget) * (rowh7 + gap7)
    maxvar = max(abs(b["actual"] - b["budget"]) for b in budget)
    parts = ['<line x1="{c}" y1="6" x2="{c}" y2="{h}" class="chart-axis"/>'.format(c=center, h=H7 - 8)]
    hits = []
    for i, b in enumerate(budget):
        y = 14 + i * (rowh7 + gap7)
        var = b["actual"] - b["budget"]
        col = VIZ["div_under"] if b["variance_is_good"] else VIZ["div_over"]
        w = halfmax * abs(var) / maxvar
        vlabel = "{n} · {g}".format(n=b["name"], g="Favorable" if b["variance_is_good"] else "Unfavorable")
        vval = ("+" if var >= 0 else "−") + money(abs(var))
        parts.append(_text(LBL7 - 6, y + 14, b["name"][:26], anchor="end"))
        pat = BUDGET_PAT[bool(b["variance_is_good"])]
        if var >= 0:
            viz_mark(parts, hits, center, y, w, rowh7, col, vval, vlabel, col, pat=pat)
            parts.append(_text(center + w + 5, y + 14, "{s}{v}".format(s="+", v=money_compact(var)), cls="chart-val"))
        else:
            viz_mark(parts, hits, center - w, y, w, rowh7, col, vval, vlabel, col, pat=pat)
            if w > 70:   # long bar: label would collide with the row label — set it inside
                parts.append(_text(center - w + 6, y + 14, "−" + money_compact(abs(var)),
                                   cls="chart-val", extra='fill="#ffffff"'))
            else:
                parts.append(_text(center - w - 5, y + 14, "−" + money_compact(abs(var)),
                                   cls="chart-val", anchor="end"))
    svg7 = _svg(W7, H7, "".join(parts) + "".join(hits), "Budget versus actual variance by line, centered on zero")
    legend7 = ('<div class="legend">'
               '<span><i style="background:{f}"></i>✓ FAVORABLE</span>'
               '<span><i style="background:{u}"></i>⚠ UNFAVORABLE</span></div>').format(
        f=VIZ["div_under"], u=VIZ["div_over"])
    tbl7 = _table("Budget vs actual", ["Line", "Budget", "Actual", "Variance", "%", "Favorable?"],
                  [[b["name"], money(b["budget"]), money(b["actual"]),
                    ("+" if b["actual"] >= b["budget"] else "−") + money(abs(b["actual"] - b["budget"])),
                    "{:+.1f}%".format((b["actual"] - b["budget"]) / b["budget"] * 100),
                    "✓ yes" if b["variance_is_good"] else "⚠ no"] for b in budget],
                  num_cols={1, 2, 3, 4})
    chart7 = chart_card("viz7", "Budget vs actual",
                        "Watching divergence is the discipline: variance is colored by whether "
                        "it is favorable, not by its sign — over on revenue is good; over on "
                        "expense is bad.", svg7 + legend7, tbl7)

    # ---------- downloads ----------
    dls = ['<a class="dl-chip" href="../data/firm.json" download>RAW FIRM.JSON</a>']
    for name, _ in csvs:
        dls.append('<a class="dl-chip" href="csv/{n}" download>{l}</a>'.format(
            n=esc(name), l=esc(name.replace(".csv", "").replace("-", " ").upper() + " CSV")))

    ident = firm["identity"]
    body = """
<section class="reveal">
  <p class="eyebrow">THE PRACTICE LEDGER · AS OF {asof}</p>
  <h1>{name}</h1>
  <p class="lede">{note} Every figure below is generated from the same open dataset the
  matters use — <span class="mono">data/firm/firm.json</span> — so the money on this page
  reconciles with the billing statements inside each packet.</p>
</section>

<div class="viz-filter" role="group" aria-label="Reporting filters">
  <span><span class="label">PERIOD</span> <span class="chip">TRAILING 12 MO</span></span>
  <span><span class="label">TIMEKEEPER</span> <span class="chip">ALL</span></span>
  <span><span class="label">STATUS</span> <span class="chip">ALL MATTERS</span></span>
  <button type="button" class="viz-toggle mono" id="viz-patterns" aria-pressed="false" title="Overlay line patterns on chart fills (accessibility / print)">PATTERNS</button>
  <span class="viz-note">One reporting period ships tonight; the dataset is a single trailing-12-month snapshot.</span>
</div>
{defs}

<section aria-label="Key performance indicators">
  <div class="kpi-row">{kpis}</div>
</section>

<div class="viz-grid viz-grid--2" style="margin-top:var(--sp-8)">
  {c1}{c2}
</div>
<div class="viz-grid viz-grid--2" style="margin-top:var(--sp-6)">
  {c3}{c4}
</div>
<div class="viz-grid" style="margin-top:var(--sp-6)">
  {c5}{c6}{c7}
</div>

<section class="no-print" aria-labelledby="dl-h" style="margin-top:var(--sp-8)">
  <div class="section-head"><p class="eyebrow">TAKE THE DATA WITH YOU</p>
  <h2 id="dl-h">Downloads</h2></div>
  <div class="downloads">{dls}</div>
</section>

<div id="viz-tip" class="viz-tip" aria-hidden="true" hidden><span class="viz-tip__sw" aria-hidden="true"></span><span class="viz-tip__v"></span><span class="viz-tip__l"></span></div>
""".format(asof=esc(firm["as_of_date"]), name=esc(ident["name"]),
           note=esc(ident.get("letterhead_note", "")), kpis="".join(kpis), defs=_pattern_defs(),
           c1=chart1, c2=chart2, c3=chart3, c4=chart4, c5=chart5, c6=chart6, c7=chart7,
           dls="".join(dls))
    write_file(rel, page_shell(rel, "Firm Dashboard", "FIRM · PRACTICE LEDGER",
                               [("Home", "../index.html"), ("Firm", None)], body))

# --------------------------------------------------------------------------- #
# data/index.json — the machine catalog (agent/LMS entry point)
# Student-safe spine copies only: matter.json / exercise.json / rubric.json /
# taxonomy / firm. NEVER personas, facts.md, or instructor notes.
# --------------------------------------------------------------------------- #
def build_data_catalog(corpus):
    # copy student-safe spine files under site/platform/data/
    os.makedirs(os.path.join(OUT, "data", "taxonomy"), exist_ok=True)
    for f in ("skills.json", "tasks.json", "folio-crosswalk.json"):
        shutil.copyfile(os.path.join(DATA, "taxonomy", f), os.path.join(OUT, "data", "taxonomy", f))
    shutil.copyfile(os.path.join(DATA, "firm", "firm.json"), os.path.join(OUT, "data", "firm.json"))
    api_src = os.path.join(ROOT, "app", "worker", "API-CONTRACTS.md")
    if os.path.exists(api_src):
        shutil.copyfile(api_src, os.path.join(OUT, "data", "api-contracts.md"))

    matters_cat = []
    for m in corpus["matters"]:
        slug = m["_slug"]
        mdir = os.path.join(OUT, "data", "matters", slug)
        os.makedirs(mdir, exist_ok=True)
        # student-safe copies
        shutil.copyfile(os.path.join(m["_dir"], "matter.json"), os.path.join(mdir, "matter.json"))
        if m.get("_exercise") is not None:
            shutil.copyfile(os.path.join(m["_dir"], "exercise", "exercise.json"),
                            os.path.join(mdir, "exercise.json"))
        if m.get("_rubric") is not None:
            shutil.copyfile(os.path.join(m["_dir"], "rubric.json"), os.path.join(mdir, "rubric.json"))

        personas_cat = []
        for p in m["_personas"].values():
            ident = p.get("identity", {})
            personas_cat.append({
                "id": p["id"],
                "name": ident.get("name"),
                "role": ident.get("role"),
                "interviewable_by": p.get("interviewable_by", []),
                "represented": bool((p.get("rule_4_2") or {}).get("applies")),
            })
        ru = m.get("_rubric") or {}
        rubric_summary = {
            "id": ru.get("id"),
            "declared_total": ru.get("declared_total"),
            "criteria": [{"id": c["id"], "name": c["name"],
                          "weight_points": c.get("weight_points"),
                          "skill_id": c.get("skill_id"), "task_id": c.get("task_id")}
                         for c in ru.get("criteria", [])],
        }
        matters_cat.append({
            "id": m["id"],
            "slug": slug,
            "caption": m.get("caption"),
            "shape": m.get("shape"),
            "tier": "meridian" if m.get("tier") == "meridian" else "real",
            "jurisdiction": m.get("jurisdiction"),
            "fee_type": m.get("fee_type"),
            "sides": [{"role_id": s.get("role_id"), "label": s.get("label")}
                      for s in (m.get("sides") or [])],
            "personas": personas_cat,
            "packet_url": "../matters/{s}/index.html".format(s=slug),
            "data": {
                "matter": "matters/{s}/matter.json".format(s=slug),
                "exercise": "matters/{s}/exercise.json".format(s=slug) if m.get("_exercise") else None,
                "rubric": "matters/{s}/rubric.json".format(s=slug) if m.get("_rubric") else None,
            },
            "rubric_summary": rubric_summary,
        })

    skills = corpus["skills"]["skills"]
    catalog = {
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/platform/data/index.json",
        "title": "Sonsteng Practicum — machine catalog",
        "description": ("Agent/LMS entry point for the practicum data spine. All paths are "
                        "relative to this file. Instructor-side materials (master fact patterns, "
                        "instructor notes, persona disclosure tiers) are intentionally absent."),
        "generated_by": "tools/build_site.py",
        "matters": matters_cat,
        "taxonomy": {
            "skills_count": len(skills),
            "surveyed_count": sum(1 for s in skills if not s.get("extension")),
            "extension_count": sum(1 for s in skills if s.get("extension")),
            "tasks_count": len(corpus["tasks"]["tasks"]),
            "files": {
                "skills": "taxonomy/skills.json",
                "tasks": "taxonomy/tasks.json",
                "folio_crosswalk": "taxonomy/folio-crosswalk.json",
            },
        },
        "firm": {"file": "firm.json", "name": corpus["firm"]["identity"]["name"],
                 "as_of_date": corpus["firm"]["as_of_date"]},
        "chat": {
            "ui": "../chat/index.html",
            "critique_ui": "../chat/critique.html",
            "api_contracts": "api-contracts.md",
        },
        "pages": {
            "home": "../index.html",
            "skills_browser": "../skills/index.html",
            "matter_library": "../matters/index.html",
            "firm_dashboard": "../firm/index.html",
        },
    }
    write_file(os.path.join("data", "index.json"),
               json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    return catalog

# --------------------------------------------------------------------------- #
# Link checker — parse generated HTML, assert every internal href/src resolves
# --------------------------------------------------------------------------- #
_HREF_RE = re.compile(r'(?:href|src)="([^"]+)"')
_ID_RE = re.compile(r'id="([^"]+)"')

def check_links():
    errors = []
    # assets/ is input-only (design gallery); it is not part of generated nav.
    pages = sorted(p for p in glob.glob(os.path.join(OUT, "**", "*.html"), recursive=True)
                   if not os.path.relpath(p, OUT).startswith("assets" + os.sep))
    ids_cache = {}

    def ids_of(path):
        if path not in ids_cache:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    ids_cache[path] = set(_ID_RE.findall(fh.read()))
            except OSError:
                ids_cache[path] = set()
        return ids_cache[path]

    for page in pages:
        rel = os.path.relpath(page, OUT)
        with open(page, "r", encoding="utf-8") as fh:
            content = fh.read()
        for url in _HREF_RE.findall(content):
            if url.startswith(("http://", "https://", "mailto:", "data:", "javascript:")):
                if url.startswith(("http://", "https://")):
                    errors.append("{p}: EXTERNAL request {u}".format(p=rel, u=url))
                continue
            frag = None
            if "#" in url:
                url, frag = url.split("#", 1)
            url = url.split("?", 1)[0]
            if not url:  # same-page fragment
                if frag and frag not in ids_of(page):
                    errors.append("{p}: missing anchor #{f}".format(p=rel, f=frag))
                continue
            target = os.path.normpath(os.path.join(os.path.dirname(page), url))
            if url.endswith("/"):
                target = os.path.join(target, "index.html")
            if not os.path.exists(target):
                errors.append("{p}: broken link {u}".format(p=rel, u=url))
                continue
            if frag and target.endswith(".html") and frag not in ids_of(target):
                errors.append("{p}: missing anchor {u}#{f}".format(p=rel, u=url, f=frag))
    return len(pages), errors

# --------------------------------------------------------------------------- #
# Instructor-leak guard — belt-and-braces sweep of every generated page
# --------------------------------------------------------------------------- #
def check_no_instructor_leaks(corpus):
    """Assert no concealed/rapport-gated persona fact text appears in output."""
    leaks = []
    needles = []
    for m in corpus["matters"]:
        for p in m["_personas"].values():
            disc = p.get("disclosure") or {}
            for tier in ("rapport_gated", "concealed"):
                for f in disc.get(tier) or []:
                    t = (f.get("text") or "").strip()
                    if len(t) > 40:
                        needles.append((p["id"], tier, t[:60]))
    pages = glob.glob(os.path.join(OUT, "**", "*.html"), recursive=True)
    hay = []
    for pg in pages:
        with open(pg, "r", encoding="utf-8") as fh:
            hay.append((os.path.relpath(pg, OUT), fh.read()))
    for pid, tier, frag in needles:
        for rel, content in hay:
            if frag in content:
                leaks.append("{p} [{t}] leaked into {r}".format(p=pid, t=tier, r=rel))
    # also assert facts.md / instructor-notes.md file contents never copied
    for bad in ("facts.md", "instructor-notes.md"):
        for f in glob.glob(os.path.join(OUT, "**", bad), recursive=True):
            leaks.append("instructor file copied into output: " + os.path.relpath(f, OUT))
    return leaks

# --------------------------------------------------------------------------- #
# Clean + main
# --------------------------------------------------------------------------- #
PRESERVE = {"assets"}   # input-only; never touched

def clean_output():
    if not os.path.isdir(OUT):
        os.makedirs(OUT, exist_ok=True)
        return
    for entry in os.listdir(OUT):
        if entry in PRESERVE:
            continue
        path = os.path.join(OUT, entry)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

def main(argv):
    do_check = "--check" in argv or True   # link check always runs; --check makes it fatal
    strict = "--check" in argv

    corpus = load_corpus()
    clean_output()
    write_platform_assets()
    copy_chat_app()

    build_home(corpus)
    build_modules(corpus)
    build_templates(corpus)
    build_skills(corpus)
    build_matter_library(corpus)
    packet_sizes = build_packet_pages(corpus)
    build_firm_dashboard(corpus)
    build_third_party()
    catalog = build_data_catalog(corpus)

    # ---- page budget report ----
    all_pages = []
    for pg in glob.glob(os.path.join(OUT, "**", "*.html"), recursive=True):
        all_pages.append((os.path.relpath(pg, OUT), os.path.getsize(pg)))
    all_pages.sort(key=lambda x: -x[1])
    over_target = [(p, s) for p, s in all_pages if s > 150_000]
    over_ceiling = [(p, s) for p, s in all_pages if s > 250_000]

    n_html = len(all_pages)
    n_files = sum(len(files) for _, _, files in os.walk(OUT))

    print("== build ==")
    print("matters: {n} · personas: {p} · packet pages: {pk}".format(
        n=len(corpus["matters"]),
        p=sum(len(m["_personas"]) for m in corpus["matters"]),
        pk=len(packet_sizes)))
    print("html pages: {h} · total files under site/platform/: {t}".format(h=n_html, t=n_files))
    print("catalog: {m} matters · {s} skills · {tk} tasks".format(
        m=len(catalog["matters"]), s=catalog["taxonomy"]["skills_count"],
        tk=catalog["taxonomy"]["tasks_count"]))
    print("\n== page sizes (top 5) ==")
    for p, s in all_pages[:5]:
        print("  {s:>8,} B  {p}".format(s=s, p=p))
    if over_ceiling:
        print("OVER 250KB CEILING: " + ", ".join(p for p, _ in over_ceiling))
    elif over_target:
        print("over 150KB target (allowed, under ceiling): "
              + ", ".join("{p} ({s:,}B)".format(p=p, s=s) for p, s in over_target))
    else:
        print("all pages within the 150KB target.")

    # ---- link check ----
    if do_check:
        n_checked, errors = check_links()
        print("\n== link check ==")
        print("pages scanned: {n}".format(n=n_checked))
        if errors:
            for e in errors:
                print("  BROKEN: " + e)
        else:
            print("all internal links resolve; zero external requests.")
        leaks = check_no_instructor_leaks(corpus)
        print("== instructor-leak sweep ==")
        if leaks:
            for l in leaks:
                print("  LEAK: " + l)
        else:
            print("no instructor-side content in any generated page.")
        if (errors or leaks) and strict:
            return 1
        if over_ceiling:
            return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
