#!/usr/bin/env python3
"""Refresh the homepage data from ORCID, Crossref and Google Scholar, then rebuild.

Usage (from the repository root):
    python scripts/update_data.py              # ORCID + Scholar, then rebuild index.html
    python scripts/update_data.py --no-scholar # skip Google Scholar (e.g. if it shows a CAPTCHA)
    python scripts/update_data.py --dry-run    # report what would change, write nothing

What it does
  1. ORCID  : lists journal articles on ORCID 0000-0002-8401-472X whose DOI is not yet in
              data/publications.json, fetches their metadata from Crossref (or doi.org for
              DOIs registered elsewhere, e.g. mEDRA) and asks you which research theme each
              one belongs to. Nothing is added without a theme.
  2. Scholar: reads the public Google Scholar profile and updates total citations, h-index,
              i10-index, citations per year and each paper's citation count.
  3. Build  : runs scripts/build.py to regenerate index.html.

Only the Python standard library is used. Google Scholar sometimes blocks automated
requests; when that happens the script keeps the previous numbers and says so.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ORCID_ID = "0000-0002-8401-472X"
SCHOLAR_USER = "vUdulZUAAAAJ"
CONTACT = "ehsanrahimi666@gmail.com"
API_UA = f"ehsan-rahimi-homepage/1.0 (mailto:{CONTACT})"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BINOMIALS = ["Brassica napus", "Canis aureus", "Dama mesopotamica", "Vespa simillima", "Vespa velutina"]
FIELD_ORDER = ["id", "year", "title", "authors", "journal", "volume", "issue", "pages", "doi", "url",
               "identifier", "language", "domain", "theme", "citations", "award", "verified"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------- I/O helpers
def http_get(url, accept="application/json", ua=API_UA, timeout=45, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept,
                                               "Accept-Language": "en-US,en;q=0.9"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            return e.code, ""
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  network error for {url}: {e}")
            return None, ""
    return None, ""


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def norm(text):
    text = html.unescape(text or "").lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similar(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


# --------------------------------------------------------------------------- metadata
def clean_title(t):
    t = html.unescape(t or "").replace("‐", "-").replace("‑", "-").replace(" ", " ")
    t = re.sub(r"<(/?)i>", r"<\1em>", t)
    t = re.sub(r"<(?!/?em>)[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    for b in BINOMIALS:
        if b in t and f"<em>{b}</em>" not in t:
            t = t.replace(b, f"<em>{b}</em>")
    return t


def initials(given):
    out = ""
    for part in re.split(r"([\s\-]+)", (given or "").strip()):
        if not part.strip():
            continue
        out += "-" if part.strip() == "-" else part[0].upper() + "."
    return out


def author_list(people):
    names = []
    for a in people or []:
        fam = (a.get("family") or a.get("literal") or "").strip()
        names.append(f"{initials(a.get('given'))} {fam}".strip())
    return names


def year_from(msg, *keys):
    for k in keys:
        parts = (msg.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def fetch_metadata(doi):
    """Crossref first; doi.org content negotiation for other agencies (mEDRA, KISTI...)."""
    status, body = http_get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    if status == 200:
        m = json.loads(body)["message"]
        pages = m.get("page") or m.get("article-number")
        return {
            "title": clean_title((m.get("title") or [""])[0]),
            "authors": author_list(m.get("author")),
            "journal": html.unescape((m.get("container-title") or [""])[0]),
            "volume": m.get("volume"), "issue": m.get("issue"),
            "pages": pages.replace("-", "–") if pages else None,
            "year": year_from(m, "published-print", "issued"),
            "type": m.get("type"), "verified": "Crossref",
        }
    status, body = http_get(f"https://doi.org/{doi}", accept="application/vnd.citationstyles.csl+json")
    if status == 200 and body.strip().startswith("{"):
        m = json.loads(body)
        journal = m.get("container-title")
        if isinstance(journal, list):
            journal = journal[0] if journal else ""
        pages = m.get("page") or m.get("number")
        return {
            "title": clean_title(m.get("title")),
            "authors": author_list(m.get("author")),
            "journal": html.unescape(journal or ""),
            "volume": m.get("volume"), "issue": m.get("issue"),
            "pages": str(pages).replace("-", "–") if pages else None,
            "year": year_from(m, "issued"),
            "type": m.get("type"), "verified": "doi.org metadata",
        }
    return None


def make_id(year, title, taken):
    stop = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "at", "by", "with", "from"}
    words = [w for w in norm(title).split() if w not in stop]
    pid = f"p{year}-" + "-".join(words[:5])
    while pid in taken:
        pid += "-x"
    return pid


# --------------------------------------------------------------------------- ORCID
def orcid_new_papers(pubs):
    known = {(p.get("doi") or "").lower() for p in pubs if p.get("doi")}
    status, body = http_get(f"https://pub.orcid.org/v3.0/{ORCID_ID}/works")
    if status != 200:
        print(f"ORCID: request failed (HTTP {status}); skipping.")
        return []
    new = []
    for group in json.loads(body).get("group", []):
        summary = group["work-summary"][0]
        if summary.get("type") != "journal-article":
            continue
        ids = group.get("external-ids", {}).get("external-id", [])
        dois = [i["external-id-value"].lower() for i in ids if i["external-id-type"] == "doi"]
        if not dois or any(d in known for d in dois):
            continue
        title = ((summary.get("title") or {}).get("title") or {}).get("value", "")
        # prefer a journal DOI over a preprint DOI in the same group
        dois.sort(key=lambda d: d.startswith("10.21203/") or "preprint" in d)
        new.append({"doi": dois[0], "orcid_title": title})
    return new


def choose_theme(areas, title):
    themes = areas["themes"]
    names = {d["id"]: d["name"] for d in areas["domains"]}
    print(f"\n  Which theme fits: {re.sub(r'<[^>]+>', '', title)}")
    for i, t in enumerate(themes, 1):
        print(f"    {i:2d}. {names[t['domain']]} / {t['name']}")
    while True:
        raw = input("  Theme number (Enter to skip this paper): ").strip()
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(themes):
            return themes[int(raw) - 1]
        print("  Please type one of the numbers above.")


def add_orcid_papers(pubs, areas, dry_run):
    new = orcid_new_papers(pubs)
    if not new:
        print("ORCID: no new journal articles.")
        return 0
    print(f"ORCID: {len(new)} journal article(s) not yet on the page.")
    interactive = sys.stdin.isatty() and not dry_run
    taken = {p["id"] for p in pubs}
    added = 0
    for item in new:
        meta = fetch_metadata(item["doi"])
        if not meta or not meta["title"]:
            print(f"  - {item['doi']}: no metadata found; add it by hand.")
            continue
        print(f"  - {meta['year']}  {meta['journal']}: {re.sub(r'<[^>]+>', '', meta['title'])}  (doi:{item['doi']})")
        if not interactive:
            continue
        theme = choose_theme(areas, meta["title"])
        if not theme:
            print("    skipped")
            continue
        rec = {
            "id": make_id(meta["year"], meta["title"], taken),
            "year": meta["year"], "title": meta["title"], "authors": meta["authors"],
            "journal": meta["journal"], "volume": meta["volume"], "issue": meta["issue"],
            "pages": meta["pages"], "doi": item["doi"], "url": f"https://doi.org/{item['doi']}",
            "identifier": None, "language": "en", "domain": theme["domain"], "theme": theme["id"],
            "citations": 0, "award": None, "verified": meta["verified"],
        }
        taken.add(rec["id"])
        pubs.append({k: rec.get(k) for k in FIELD_ORDER})
        added += 1
        print(f"    added under '{theme['name']}'")
    if new and not interactive and not dry_run:
        print("  Run this script in a terminal to choose a theme for each paper and add it.")
    return added


# --------------------------------------------------------------------------- Scholar
class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def cls(self):
        return (self.attrs.get("class") or "").split()

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def find_all(self, cls, tag=None):
        out = []
        for c in self.children:
            if isinstance(c, _Node):
                if cls in c.cls() and (tag is None or c.tag == tag):
                    out.append(c)
                out.extend(c.find_all(cls, tag))
        return out


class _Tree(HTMLParser):
    VOID = {"br", "img", "input", "meta", "link", "hr", "area", "base", "col", "embed", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _right_px(node):
    m = re.search(r"right:\s*(\d+)px", node.attrs.get("style", ""))
    return int(m.group(1)) if m else None


def scholar_profile():
    rows, metrics, per_year = [], None, {}
    for start in range(0, 500, 100):
        url = (f"https://scholar.google.com/citations?user={SCHOLAR_USER}&hl=en"
               f"&cstart={start}&pagesize=100&sortby=pubdate")
        status, body = http_get(url, accept="text/html", ua=BROWSER_UA)
        if status != 200 or "gsc_rsb_std" not in body:
            return None
        tree = _Tree()
        tree.feed(body)
        root = tree.root
        if metrics is None:
            vals = [n.text().strip() for n in root.find_all("gsc_rsb_std")]
            if len(vals) < 6 or not all(v.isdigit() for v in vals[:6]):
                return None
            metrics = {"citations": int(vals[0]), "h_index": int(vals[2]), "i10_index": int(vals[4])}
            years = {_right_px(n): n.text().strip() for n in root.find_all("gsc_g_t")}
            for bar in root.find_all("gsc_g_a"):
                px = _right_px(bar)
                # bars sit 5 px to the right of their year label
                year = next((y for x, y in years.items() if x is not None and px is not None and abs(px - x - 5) <= 3), None)
                if year:
                    per_year[year] = int(bar.text().strip() or 0)
        page_rows = root.find_all("gsc_a_tr", "tr")
        for tr in page_rows:
            title = tr.find_all("gsc_a_at")
            cites = tr.find_all("gsc_a_ac")
            yr = tr.find_all("gsc_a_h")
            if not title:
                continue
            c = cites[0].text().strip() if cites else ""
            rows.append({"title": title[0].text().strip(), "cites": int(c) if c.isdigit() else 0,
                         "year": yr[0].text().strip() if yr else ""})
        if len(page_rows) < 100:
            break
        time.sleep(4)
    return {"metrics": metrics, "per_year": dict(sorted(per_year.items())), "rows": rows}


def update_scholar(pubs, metrics, dry_run):
    prof = scholar_profile()
    if not prof:
        print("Scholar: profile could not be read (Google may be showing a CAPTCHA). "
              "Keeping the previous numbers; try again later or use --no-scholar.")
        return False
    m = prof["metrics"]
    for key in ("citations", "h_index", "i10_index"):
        if metrics.get(key) != m[key]:
            print(f"Scholar: {key} {metrics.get(key)} -> {m[key]}")
    changed = 0
    matched = set()
    for p in pubs:
        best, score = None, 0.0
        for i, r in enumerate(prof["rows"]):
            s = similar(p["title"], r["title"])
            if s > score:
                best, score = i, s
        if best is not None and score >= 0.85:
            matched.add(best)
            new = prof["rows"][best]["cites"]
            if new != p["citations"]:
                changed += 1
                p["citations"] = new
        else:
            print(f"Scholar: no match for '{re.sub(r'<[^>]+>', '', p['title'])[:70]}' (kept {p['citations']})")
    extra = [r for i, r in enumerate(prof["rows"]) if i not in matched]
    for r in extra:
        print(f"Scholar: listed on Scholar but not on the page: {r['year']} {r['title'][:80]}")
    print(f"Scholar: citation counts changed for {changed} paper(s).")
    if not dry_run:
        metrics.update(m)
        if prof["per_year"]:
            metrics["citations_per_year"] = prof["per_year"]
        metrics["retrieved"] = dt.date.today().isoformat()
    return True


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-orcid", action="store_true", help="skip the ORCID check for new papers")
    ap.add_argument("--no-scholar", action="store_true", help="skip Google Scholar metrics")
    ap.add_argument("--no-build", action="store_true", help="do not rebuild index.html")
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing files")
    args = ap.parse_args()

    pubs = load("publications.json")
    areas = load("research_areas.json")
    metrics = load("metrics.json")

    added = 0 if args.no_orcid else add_orcid_papers(pubs, areas, args.dry_run)
    if not args.no_scholar:
        update_scholar(pubs, metrics, args.dry_run)

    if args.dry_run:
        print("Dry run: no files written.")
        return
    pubs.sort(key=lambda p: (-p["year"], re.sub(r"<[^>]+>", "", p["title"]).lower()))
    save("publications.json", pubs)
    save("metrics.json", metrics)
    print(f"Saved data ({len(pubs)} papers, {added} added).")
    if not args.no_build:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build.py")], check=True)


if __name__ == "__main__":
    main()
