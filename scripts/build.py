#!/usr/bin/env python3
"""Build index.html from the data files and the page template.

Usage (from the repository root):
    python scripts/build.py

Inputs
    data/publications.json    one record per paper (source of truth)
    data/research_areas.json  research domains and themes
    data/metrics.json         Google Scholar metrics
    templates/index.html      page template with %%TOKEN%% placeholders
    data/recognition.json     awards (with certificate links) and editorial roles
    data/datasets.json        GitHub and Zenodo links and published datasets
    assets/profile.jpg        optional portrait; shown in the header if present
Output
    index.html

Only the Python standard library is used.
"""
from __future__ import annotations

import datetime as dt
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
STAR = ('<svg class="star" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 1.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L10 '
        '14.9l-5.2 2.7 1-5.8L1.5 7.7l5.9-.8z"/></svg>')
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
def awards_of(p):
    a = p.get("award")
    if not a:
        return []
    return [a] if isinstance(a, str) else list(a)


def render_mix(pubs, areas):
    """Stacked bar of papers per research area, each segment a link that filters the list."""
    dcount = Counter(p["domain"] for p in pubs)
    total = sum(dcount.values()) or 1
    segs, keys = [], []
    for d in areas["domains"]:
        n = dcount[d["id"]]
        if not n:
            continue
        pct = 100 * n / total
        segs.append(f'<a class="mix-seg d-{d["id"]}" href="#publications" data-domain="{d["id"]}" '
                    f'style="flex-grow:{n}" title="{esc(d["name"])}: {plural(n, "paper")}">'
                    f'<span>{round(pct)}%</span></a>')
        keys.append(f'<li class="d-{d["id"]}"><i class="dot" aria-hidden="true"></i>'
                    f'<a href="#publications" data-domain="{d["id"]}">{esc(d["short"])}</a> <b>{n}</b></li>')
    return (f'<div class="mix-bar" role="img" aria-label="Papers by research area">{"".join(segs)}</div>'
            f'<ul class="mix-key">{"".join(keys)}</ul>')


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
    for a in awards_of(p):
        extra.append(f'<a class="flag award" href="#recognition">{STAR}{esc(a)}</a>')
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


def render_journals(pubs):
    counts = Counter(p["journal"] for p in pubs)
    doms = {}
    for p in pubs:
        doms.setdefault(p["journal"], Counter())[p["domain"]] += 1
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return "".join(
        f'<li class="d-{doms[j].most_common(1)[0][0]}"><a href="#publications" data-journal="{esc(j)}">'
        f'<span>{esc(j)}</span><span class="count">{n}</span></a></li>'
        for j, n in items
    )


POSTDOC_START = dt.date(2022, 6, 1)


def postdoc_years():
    """Completed years since the postdoc started (June 2022)."""
    t = dt.date.today()
    return t.year - POSTDOC_START.year - ((t.month, t.day) < (POSTDOC_START.month, POSTDOC_START.day))


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


def award_link(link):
    url = link["url"]
    if url.endswith(".pdf"):
        return (f'<a class="aw-link pdf" href="{esc(url)}" target="_blank" rel="noopener">'
                f'{esc(link["label"])}</a>')
    return f'<a class="aw-link" href="{esc(url)}" rel="noopener">{esc(link["label"])}</a>'


def render_awards(recog, pubs):
    by_id = {p["id"]: p for p in pubs}
    cards = []
    for a in recog["awards"]:
        p = by_id.get(a.get("paper"))
        paper = ""
        if p:
            paper = (f'<p class="aw-paper"><a href="{esc(p["url"])}" rel="noopener">{safe_title(p["title"])}</a> '
                     f'<span class="muted"><i>{esc(p["journal"])}</i>, {p["year"]}</span></p>')
        links = "".join(award_link(l) for l in a.get("links", []))
        if a.get("thumb"):
            main = next((l["url"] for l in a.get("links", []) if l["url"].endswith(".pdf")), a["links"][0]["url"])
            visual = (f'<a class="aw-thumb" href="{esc(main)}" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">'
                      f'<img src="{esc(a["thumb"])}" alt="" loading="lazy"></a>')
        else:
            visual = f'<div class="aw-thumb aw-medal" aria-hidden="true">{MEDAL}</div>'
        cards.append(
            f'<article class="aw-card d-{a["tone"]}" id="{esc(a["id"])}">{visual}'
            f'<div class="aw-body"><p class="aw-kicker">{esc(a["body"])} · {a["year"]}</p>'
            f'<h3>{esc(a["title"])}</h3>{paper}<p class="aw-text">{esc(a["text"])}</p>'
            f'<p class="aw-links">{links}</p></div></article>'
        )
    return "".join(cards)


def render_honours(recog):
    items = "".join(
        f'<li class="d-{a["tone"]}"><a href="#{esc(a["id"])}">{STAR}<span>{esc(a["title"])}'
        f'<small>{esc(a["body"])}</small></span></a></li>' for a in recog["awards"])
    for e in recog["editorial"]:
        items += (f'<li class="d-bio"><a href="{esc(e["url"])}" rel="noopener">{PEN}<span>{esc(e["role"])}, {esc(e["venue"])}'
                  f'<small>Special Issue on pollinator diversity</small></span></a></li>')
    return items


def render_editorial(recog):
    return "".join(
        f'<article class="ed-card"><p class="aw-kicker">{esc(e["role"])} · {esc(e["venue"])}</p>'
        f'<h3><a href="{esc(e["url"])}" rel="noopener">{esc(e["title"])}</a></h3>'
        f'<p>{esc(e["text"])}</p>'
        f'<p class="aw-links"><a class="aw-link" href="{esc(e["url"])}" rel="noopener">Special Issue page</a></p></article>'
        for e in recog["editorial"])


def render_packages(extras):
    pkgs = extras.get("r_packages") or []
    if not pkgs:
        return ""
    items = "".join(
        f'<li><span class="pkg-head"><span class="pkg-name">{esc(r["name"])}</span>'
        f'<span class="pkg-ver">v{esc(r.get("version", ""))}</span></span>'
        f'<a class="ds-title" href="{esc(r["url"])}" rel="noopener">{esc(r["title"])}</a>'
        f'<span class="pkg-note">{esc(r.get("note") or "")}</span>'
        f'<span class="ds-meta">R package · <a href="{esc(r["url"])}" rel="noopener">{esc(r["url"].replace("https://", ""))}</a></span></li>'
        for r in pkgs)
    return (f'<h3 class="sub-h">R packages</h3><p class="section-lede">Open-source R packages I develop, '
            f'with source code and installation instructions on GitHub.</p>'
            f'<ul class="datasets pkgs">{items}</ul>')


def render_datasets(extras):
    return "".join(
        f'<li><a class="ds-title" href="{esc(d["url"])}" rel="noopener">{esc(d["title"])}</a>'
        f'<span class="ds-meta">{esc(", ".join(d["authors"]))} · Zenodo, {d["year"]} · doi:{esc(d["doi"])}</span></li>'
        for d in extras["datasets"])


MEDAL = ('<svg viewBox="0 0 64 64"><defs><linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">'
         '<stop offset="0" stop-color="#F7D774"/><stop offset=".55" stop-color="#E0A41B"/><stop offset="1" stop-color="#A86E05"/>'
         '</linearGradient></defs><path d="M20 4h10l4 18h-10z" fill="#2B6A8E"/><path d="M44 4H34l-4 18h10z" fill="#3C7A3F"/>'
         '<circle cx="32" cy="40" r="19" fill="url(#gold)"/><circle cx="32" cy="40" r="14" fill="none" stroke="#fff" '
         'stroke-opacity=".55" stroke-width="1.5"/><path d="M32 30l3 6.2 6.8.9-5 4.7 1.3 6.7L32 45.3l-6.1 3.2 1.3-6.7-5-4.7 '
         '6.8-.9z" fill="#fff" fill-opacity=".9"/></svg>')
PEN = ('<svg class="star" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 14.5V17h2.5l8.6-8.6-2.5-2.5zM16.7 5.8a.9.9 0 0 0 '
       '0-1.3l-1.2-1.2a.9.9 0 0 0-1.3 0l-1 1 2.5 2.5z"/></svg>')


def render_jsonld(pubs, metrics, extras):
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
            extras["github"],
        ],
        "knowsAbout": ["Pollination ecology", "Landscape ecology", "Remote sensing",
                       "Species distribution modelling", "Biodiversity conservation", "Deep learning"],
        "award": sorted({a for p in pubs for a in awards_of(p)}),
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
    recog = load_json("recognition.json")
    extras = load_json("datasets.json")
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
        "CITES_CLASS": " long" if len(f"{metrics['citations']:,}") >= 5 else "",
        "H_INDEX": str(metrics["h_index"]),
        "I10_INDEX": str(metrics["i10_index"]),
        "SCHOLAR_URL": esc(metrics["profile_url"]),
        "RETRIEVED": retrieved_txt,
        "RETRIEVED_ISO": metrics["retrieved"],
        "YEAR_NOW": str(dt.date.today().year),
        "MIX": render_mix(pubs, areas),
        "HONOURS": render_honours(recog),
        "AWARDS": render_awards(recog, pubs),
        "EDITORIAL": render_editorial(recog),
        "DATASETS": render_datasets(extras),
        "R_PACKAGES": render_packages(extras),
        "N_DATASETS": str(len(extras["datasets"])),
        "GITHUB_URL": esc(extras["github"]),
        "ZENODO_URL": esc(extras["zenodo"]),
        "N_AWARDS": str(len(recog["awards"])),
        "POSTDOC_YEARS": str(postdoc_years()),
        "AREAS": render_areas(pubs, areas),
        "DOMAIN_FILTERS": render_domain_filters(pubs, areas),
        "PUBLICATIONS": render_publications(pubs, themes),
        "JOURNALS": render_journals(pubs),
        "CITES_CHART": render_cites_chart(metrics),
        "JSONLD": render_jsonld(pubs, metrics, extras),
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
