#!/usr/bin/env python3
"""Build index.html from the data files and the page template.

Usage (from the repository root):
    python scripts/build.py

Inputs
    data/publications.json    one record per paper (source of truth)
    data/research_areas.json  research domains and themes
    data/metrics.json         Google Scholar metrics
    templates/index.html      page template with %%TOKEN%% placeholders
    assets/profile.jpg        optional portrait; shown in the header if present
Output
    index.html

Only the Python standard library is used.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEMPLATE = ROOT / "templates" / "index.html"
OUTPUT = ROOT / "index.html"
PORTRAIT = "assets/profile.jpg"

SELF_SURNAME = "Rahimi"
TREEMAP_W, TREEMAP_H = 640, 460
REQUIRED = ("id", "year", "title", "authors", "journal", "url", "domain", "theme", "citations", "language")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------- helpers
def esc(text) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def safe_title(title: str) -> str:
    """Escape a title but keep <em> for species names."""
    parts = re.split(r"(</?em>)", title)
    return "".join(p if p in ("<em>", "</em>") else esc(p) for p in parts)


def plain(title: str) -> str:
    return re.sub(r"<[^>]+>", "", title)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def load_json(name: str):
    with open(DATA / name, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- validation
def validate(pubs, areas):
    domains = {d["id"] for d in areas["domains"]}
    themes = {t["id"]: t for t in areas["themes"]}
    errors = []
    seen_ids, seen_dois = set(), set()
    for i, p in enumerate(pubs):
        where = f"record {i} ({p.get('title', '?')[:50]})"
        for key in REQUIRED:
            if p.get(key) in (None, "", []):
                errors.append(f"{where}: missing '{key}'")
        if p.get("id") in seen_ids:
            errors.append(f"{where}: duplicate id {p['id']}")
        seen_ids.add(p.get("id"))
        doi = (p.get("doi") or "").lower()
        if doi:
            if doi in seen_dois:
                errors.append(f"{where}: duplicate DOI {doi}")
            seen_dois.add(doi)
            if p.get("url") != f"https://doi.org/{doi}":
                errors.append(f"{where}: url should be https://doi.org/{doi}")
        if p.get("domain") not in domains:
            errors.append(f"{where}: unknown domain {p.get('domain')}")
        t = themes.get(p.get("theme"))
        if not t:
            errors.append(f"{where}: unknown theme {p.get('theme')} (assign one from data/research_areas.json)")
        elif t["domain"] != p.get("domain"):
            errors.append(f"{where}: theme {t['id']} belongs to domain {t['domain']}, not {p.get('domain')}")
    if errors:
        print("Data problems found:\n  " + "\n  ".join(errors))
        sys.exit(1)


# --------------------------------------------------------------------------- treemap
def _worst(row, side):
    s = sum(a for _, a in row)
    big, small = max(a for _, a in row), min(a for _, a in row)
    return max(side * side * big / (s * s), (s * s) / (side * side * small))


def squarify(items, x, y, w, h):
    """Squarified treemap (Bruls, Huizing & van Wijk 2000).

    items: list of (key, weight) with weight > 0. Returns [(key, x, y, w, h)].
    """
    total = sum(v for _, v in items)
    if total <= 0 or w <= 0 or h <= 0:
        return []
    scale = w * h / total
    areas = [(k, v * scale) for k, v in sorted(items, key=lambda kv: -kv[1])]
    out = []
    while areas:
        side = min(w, h)
        row, i = [areas[0]], 1
        while i < len(areas) and _worst(row + [areas[i]], side) <= _worst(row, side):
            row.append(areas[i])
            i += 1
        areas = areas[i:]
        s = sum(a for _, a in row)
        if w >= h:  # column along the left edge
            cw, cy = s / h, y
            for k, a in row:
                ch = a / cw
                out.append((k, x, cy, cw, ch))
                cy += ch
            x, w = x + cw, w - cw
        else:  # row along the top edge
            rh, cx = s / w, x
            for k, a in row:
                rw = a / rh
                out.append((k, cx, y, rw, rh))
                cx += rw
            y, h = y + rh, h - rh
    return out


def shade(pid: str) -> float:
    """Deterministic 0.80–1.00 opacity so neighbouring parcels read like fields."""
    n = int(hashlib.sha1(pid.encode()).hexdigest()[:4], 16) / 0xFFFF
    return round(0.80 + 0.20 * n, 2)


def render_treemap(pubs, areas):
    order = [d["id"] for d in areas["domains"]]
    weight = {p["id"]: p["citations"] + 2 for p in pubs}
    by_dom = OrderedDict((d, [p for p in pubs if p["domain"] == d]) for d in order)
    dom_items = [(d, sum(weight[p["id"]] for p in ps)) for d, ps in by_dom.items() if ps]
    dom_gap, parcel_gap = 3.0, 1.2
    parts = []
    for d, dx, dy, dw, dh in squarify(dom_items, 0, 0, TREEMAP_W, TREEMAP_H):
        ix, iy, iw, ih = dx + dom_gap / 2, dy + dom_gap / 2, dw - dom_gap, dh - dom_gap
        papers = sorted(by_dom[d], key=lambda p: (-weight[p["id"]], -p["year"]))
        cells = squarify([(p["id"], weight[p["id"]]) for p in papers], ix, iy, iw, ih)
        lookup = {p["id"]: p for p in papers}
        for pid, x, y, w, h in cells:
            p = lookup[pid]
            gx, gy = x + parcel_gap / 2, y + parcel_gap / 2
            gw, gh = max(w - parcel_gap, 0.5), max(h - parcel_gap, 0.5)
            rows = "rows-v" if gh >= gw else "rows-h"
            cites = plural(p["citations"], "citation")
            label = f"{p['year']}, {p['journal']}: {plain(p['title'])} ({cites})"
            marker = ""
            if p.get("award"):
                marker = (f'<circle class="award-dot" cx="{gx + gw / 2:.1f}" cy="{gy + gh / 2:.1f}" r="5"/>')
            parts.append(
                f'<g class="parcel d-{p["domain"]}" data-id="{esc(pid)}" data-label="{esc(label)}">'
                f'<rect x="{gx:.1f}" y="{gy:.1f}" width="{gw:.1f}" height="{gh:.1f}" fill-opacity="{shade(pid)}"/>'
                f'<rect class="rows" x="{gx:.1f}" y="{gy:.1f}" width="{gw:.1f}" height="{gh:.1f}" fill="url(#{rows})"/>'
                f"{marker}</g>"
            )
    total = len(pubs)
    desc = (f"Treemap of {total} papers grouped by research area; each rectangle is one paper "
            "and its area grows with the paper's Google Scholar citations.")
    return (
        f'<svg id="treemap" class="treemap" viewBox="0 0 {TREEMAP_W} {TREEMAP_H}" role="img" '
        f'aria-labelledby="treemap-title treemap-desc" preserveAspectRatio="xMidYMid meet">'
        f'<title id="treemap-title">Publications as field parcels</title>'
        f'<desc id="treemap-desc">{esc(desc)}</desc>'
        + "".join(parts)
        + "</svg>"
    )


# --------------------------------------------------------------------------- citation text
def authors_html(authors):
    out = []
    for a in authors:
        if a.split()[-1] == SELF_SURNAME and a.split()[0].startswith("E"):
            out.append(f"<b>{esc(a)}</b>")
        else:
            out.append(esc(a))
    return ", ".join(out)


def source_html(p):
    s = f"<i>{esc(p['journal'])}</i>"
    loc = ""
    if p.get("volume"):
        loc = esc(p["volume"])
        if p.get("issue"):
            loc += f"({esc(p['issue'])})"
    if p.get("pages"):
        loc = f"{loc}, {esc(p['pages'])}" if loc else esc(p["pages"])
    if not loc:
        return f"{s}, online first"
    return f"{s} {loc}"


def pub_item(p, themes):
    theme = themes[p["theme"]]["name"]
    extra = []
    if p.get("doi"):
        link_text = f"doi:{p['doi']}"
    elif p.get("identifier") and p["url"].startswith("https://dorl.net/"):
        link_text = p["identifier"]
    else:
        link_text = "Journal page"
        if p.get("identifier"):
            extra.append(f'<span class="ident">{esc(p["identifier"])}</span>')
    if p["language"] == "fa":
        extra.append('<span class="flag">In Persian</span>')
    if p.get("award"):
        extra.append(f'<span class="flag award">{esc(p["award"])}</span>')
    cites = (f'<span class="cites"><span class="n">{p["citations"]}</span> '
             f'{"citation" if p["citations"] == 1 else "citations"}</span>') if p["citations"] else ""
    search = " ".join([plain(p["title"]), " ".join(p["authors"]), p["journal"], theme, str(p["year"]),
                       p.get("doi") or ""])
    return (
        f'<li class="pub d-{p["domain"]}" id="{esc(p["id"])}" data-year="{p["year"]}" '
        f'data-domain="{p["domain"]}" data-theme="{p["theme"]}" data-cites="{p["citations"]}" '
        f'data-journal="{esc(p["journal"])}" data-search="{esc(search)}">'
        f'<div class="pub-body">'
        f'<a class="pub-title" href="{esc(p["url"])}" rel="noopener">{safe_title(p["title"])}</a>'
        f'<p class="pub-cite">{authors_html(p["authors"])}. {source_html(p)} ({p["year"]}).</p>'
        f'<p class="pub-meta"><span class="theme">{esc(theme)}</span>'
        f'<a class="pub-link" href="{esc(p["url"])}" rel="noopener">{esc(link_text)}</a>'
        f'{"".join(extra)}</p>'
        f"</div>{cites}</li>"
    )


def render_publications(pubs, themes):
    groups = OrderedDict()
    for p in sorted(pubs, key=lambda p: (-p["year"], -p["citations"], plain(p["title"]).lower())):
        groups.setdefault(p["year"], []).append(p)
    out = []
    for year, ps in groups.items():
        out.append(
            f'<section class="pub-group"><h3 class="group-head"><span>{year}</span>'
            f'<span class="group-count">{plural(len(ps), "paper")}</span></h3>'
            f'<ol class="pubs">{"".join(pub_item(p, themes) for p in ps)}</ol></section>'
        )
    return "".join(out)


# --------------------------------------------------------------------------- other fragments
def render_areas(pubs, areas):
    dcount = Counter(p["domain"] for p in pubs)
    tcount = Counter(p["theme"] for p in pubs)
    blocks = []
    for d in areas["domains"]:
        themes = [t for t in areas["themes"] if t["domain"] == d["id"] and tcount[t["id"]]]
        items = "".join(
            f'<li><a href="#publications" data-theme="{t["id"]}">{esc(t["name"])}</a>'
            f'<span class="count">{tcount[t["id"]]}</span></li>'
            for t in themes
        )
        blocks.append(
            f'<article class="area d-{d["id"]}">'
            f'<div class="area-head"><svg class="swatch" viewBox="0 0 48 32" aria-hidden="true">'
            f'<rect width="48" height="32" rx="2"/><rect class="rows" width="48" height="32" rx="2" fill="url(#rows-h)"/></svg>'
            f'<h3>{esc(d["name"])}</h3></div>'
            f'<p>{esc(d["summary"])}</p>'
            f'<ul class="themes">{items}</ul>'
            f'<a class="area-all" href="#publications" data-domain="{d["id"]}">'
            f'Show all {dcount[d["id"]]} papers in this area</a>'
            f"</article>"
        )
    return "".join(blocks)


def render_domain_filters(pubs, areas):
    dcount = Counter(p["domain"] for p in pubs)
    btns = [f'<button type="button" class="chip" data-domain="all" aria-pressed="true">All <span>{len(pubs)}</span></button>']
    for d in areas["domains"]:
        btns.append(
            f'<button type="button" class="chip d-{d["id"]}" data-domain="{d["id"]}" aria-pressed="false">'
            f'<i class="dot" aria-hidden="true"></i>{esc(d.get("chip", d["short"]))} <span>{dcount[d["id"]]}</span></button>'
        )
    return "".join(btns)


def render_legend(areas):
    return "".join(
        f'<li class="d-{d["id"]}"><i class="dot" aria-hidden="true"></i>{esc(d["short"])}</li>'
        for d in areas["domains"]
    )


def render_journals(pubs):
    counts = Counter(p["journal"] for p in pubs)
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return "".join(
        f'<li><a href="#publications" data-journal="{esc(j)}">{esc(j)}</a><span class="count">{n}</span></li>'
        for j, n in items
    )


def render_cites_chart(metrics):
    per_year = sorted((int(y), int(v)) for y, v in metrics["citations_per_year"].items())
    if not per_year:
        return ""
    retrieved = dt.date.fromisoformat(metrics["retrieved"])
    peak = max(v for _, v in per_year) or 1
    bw, gap, top, base = 34, 12, 20, 112
    w = len(per_year) * (bw + gap) - gap
    bars = []
    for i, (year, v) in enumerate(per_year):
        x = i * (bw + gap)
        h = (base - top) * v / peak
        partial = year == retrieved.year and retrieved < dt.date(year, 12, 31)
        cls = "col partial" if partial else "col"
        bars.append(
            f'<rect class="{cls}" x="{x}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="2"/>'
            + (f'<rect class="rows" x="{x}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="2" fill="url(#rows-v)"/>' if partial else "")
            + f'<text class="val" x="{x + bw / 2}" y="{base - h - 6:.1f}">{v}</text>'
            f'<text class="yr" x="{x + bw / 2}" y="{base + 16}">{year}</text>'
        )
    summary = ", ".join(f"{y}: {v}" for y, v in per_year)
    return (
        f'<svg class="cites-chart" viewBox="0 0 {w} {base + 22}" role="img" aria-label="Citations per year, {esc(summary)}">'
        + "".join(bars) + "</svg>"
    )


def render_jsonld(pubs, metrics):
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "Ehsan Rahimi",
        "honorificPrefix": "Dr.",
        "jobTitle": "Postdoctoral Researcher",
        "affiliation": {"@type": "CollegeOrUniversity", "name": "Gyeongkuk National University",
                        "address": "Andong, South Korea"},
        "alumniOf": [
            {"@type": "CollegeOrUniversity", "name": "Shahid Beheshti University"},
            {"@type": "CollegeOrUniversity", "name": "Gorgan University of Agricultural Sciences and Natural Resources"},
            {"@type": "CollegeOrUniversity", "name": "Khatam Al Anbia Behbahan University of Technology"},
        ],
        "email": "mailto:ehsanrahimi666@gmail.com",
        "identifier": {"@type": "PropertyValue", "propertyID": "ORCID", "value": "0000-0002-8401-472X"},
        "sameAs": [
            "https://orcid.org/0000-0002-8401-472X",
            metrics["profile_url"],
            "https://www.linkedin.com/in/ehsan-rahimi-288a10149/",
        ],
        "knowsAbout": ["Pollination ecology", "Landscape ecology", "Remote sensing",
                       "Species distribution modelling", "Biodiversity conservation", "Deep learning"],
        "award": sorted({p["award"] for p in pubs if p.get("award")}),
    }
    return json.dumps(person, ensure_ascii=False, indent=1).replace("</", "<\\/")


def render_portrait():
    if (ROOT / PORTRAIT).exists():
        return f'<img class="portrait" src="{PORTRAIT}" alt="Portrait of Ehsan Rahimi" width="112" height="112">'
    return ""


# --------------------------------------------------------------------------- main
def main():
    pubs = load_json("publications.json")
    areas = load_json("research_areas.json")
    metrics = load_json("metrics.json")
    validate(pubs, areas)
    themes = {t["id"]: t for t in areas["themes"]}

    n_pubs = len(pubs)
    n_journals = len({p["journal"] for p in pubs})
    first_year = min(p["year"] for p in pubs)
    retrieved = dt.date.fromisoformat(metrics["retrieved"])
    retrieved_txt = f"{retrieved.day} {retrieved:%B %Y}"

    taxonomy = {
        "domains": [{"id": d["id"], "name": d["name"]} for d in areas["domains"]],
        "themes": [{"id": t["id"], "domain": t["domain"], "name": t["name"]} for t in areas["themes"]],
    }

    tokens = {
        "N_PUBS": str(n_pubs),
        "N_JOURNALS": str(n_journals),
        "FIRST_YEAR": str(first_year),
        "CITATIONS": f"{metrics['citations']:,}",
        "H_INDEX": str(metrics["h_index"]),
        "I10_INDEX": str(metrics["i10_index"]),
        "SCHOLAR_URL": esc(metrics["profile_url"]),
        "RETRIEVED": retrieved_txt,
        "RETRIEVED_ISO": metrics["retrieved"],
        "YEAR_NOW": str(dt.date.today().year),
        "TREEMAP": render_treemap(pubs, areas),
        "LEGEND": render_legend(areas),
        "AREAS": render_areas(pubs, areas),
        "DOMAIN_FILTERS": render_domain_filters(pubs, areas),
        "PUBLICATIONS": render_publications(pubs, themes),
        "JOURNALS": render_journals(pubs),
        "CITES_CHART": render_cites_chart(metrics),
        "JSONLD": render_jsonld(pubs, metrics),
        "TAXONOMY": json.dumps(taxonomy, ensure_ascii=False).replace("</", "<\\/"),
        "PORTRAIT": render_portrait(),
    }

    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in tokens.items():
        page = page.replace(f"%%{key}%%", value)
    leftover = sorted(set(re.findall(r"%%[A-Z0-9_]+%%", page)))
    if leftover:
        print("Unfilled template tokens:", ", ".join(leftover))
        sys.exit(1)
    OUTPUT.write_text(page, encoding="utf-8", newline="\n")

    by_domain = Counter(p["domain"] for p in pubs)
    print(f"Built {OUTPUT.relative_to(ROOT)}: {n_pubs} papers, {n_journals} journals, "
          f"{metrics['citations']} citations, h-index {metrics['h_index']} "
          f"(Scholar, {metrics['retrieved']}); by area: {dict(by_domain)}")


if __name__ == "__main__":
    main()
