#!/usr/bin/env python3
"""Fetch publications from Google Scholar and write a single YAML data file.

The script writes one file — `data/publications.yml` — containing every
publication on the configured Scholar profile. The publications page
(`publications.qmd`) reads this file via Quarto's listing feature, so the
listing card title links straight to Scholar and a `Paper PDF` link in the
description points to the publisher's URL when available.

Tagging: each publication's title + venue is matched against keyword rules
to produce one or more category labels (energy, food, optimization, policy,
climate, health, infrastructure, markets, methods). Edit `TAG_RULES` below
to adjust.

Run locally:
    pip install scholarly pyyaml      (or: uv pip install -r scripts/requirements.txt)
    python scripts/fetch_scholar.py

CI:
    The .github/workflows/scholar.yml workflow runs this weekly and commits
    the resulting changes back to the repository.

Notes:
    Google Scholar aggressively rate-limits scraping. If the script fails
    with a CAPTCHA-style error, re-run later. Failed parses on individual
    entries are logged and skipped so a partial profile still produces a
    partial list.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Scholar profile ID from
# https://scholar.google.com/citations?user=<SCHOLAR_ID>
SCHOLAR_ID = os.environ.get("SCHOLAR_ID", "D7Ms9fIAAAAJ")

# Output YAML path, relative to repo root.
OUT_FILE = Path(os.environ.get("PUB_OUT_FILE", "data/publications.yml"))

# Author name, used as the listing author for every entry.
AUTHOR = os.environ.get("AUTHOR", "Sauleh Siddiqui")

# Whether to fill abstracts (slower — one extra request per publication).
FILL_DETAILS = os.environ.get("FILL_DETAILS", "true").lower() in {"1", "true", "yes"}

# Keyword → category rules. Order doesn't matter; all matching tags apply.
# Matching is case-insensitive whole-word against (title + " " + venue).
TAG_RULES: dict[str, list[str]] = {
    "energy": [
        "energy", "electricity", "power", "grid", "fuel", "oil", "gas",
        "natural gas", "petroleum", "renewable", "solar", "wind", "hydrogen",
        "nuclear", "coal", "biofuel", "transmission",
    ],
    "food": [
        "food", "agriculture", "agricultural", "nutrition", "waste",
        "supply chain", "recipe", "crop", "livestock",
    ],
    "optimization": [
        "optimization", "optimisation", "linear programming", "mixed integer",
        "mixed-integer", "integer programming", "convex", "conic",
        "stochastic", "robust optimization", "equilibrium", "mopec", "mpec",
        "epec", "bilevel", "game theor", "complementarity",
    ],
    "policy": [
        "policy", "regulation", "regulatory", "governance", "subsidy",
        "subsidies", "tax", "carbon price",
    ],
    "climate": [
        "climate", "emissions", "greenhouse", "carbon", "decarbon",
        "mitigation", "adaptation",
    ],
    "health": [
        "health", "public health", "epidemic", "epidemiology", "disease",
        "mortality",
    ],
    "infrastructure": [
        "infrastructure", "network", "pipeline", "facility location",
        "transportation",
    ],
    "markets": [
        "market", "auction", "pricing", "trade", "trading", "bidding",
        "competition",
    ],
    "methods": [
        "model", "modelling", "modeling", "framework", "algorithm",
        "decomposition", "method",
    ],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch-scholar")


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def tag_publication(title: str, venue: str) -> list[str]:
    """Return a sorted, deduplicated list of category tags for a publication."""
    haystack = _normalize(f"{title} {venue}")
    tags: set[str] = set()
    for tag, keywords in TAG_RULES.items():
        for kw in keywords:
            # Word-boundary match so "energy" doesn't fire on "synergy".
            if re.search(rf"\b{re.escape(kw.lower())}\b", haystack):
                tags.add(tag)
                break
    return sorted(tags)


# ---------------------------------------------------------------------------
# Scholar fetching
# ---------------------------------------------------------------------------

def fetch_publications(scholar_id: str, fill: bool) -> list[dict[str, Any]]:
    """Pull publications from Scholar and return a list of normalized dicts."""
    try:
        from scholarly import scholarly  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "scholarly is not installed. Run: uv pip install -r scripts/requirements.txt"
        ) from exc

    log.info("Looking up Scholar profile %s ...", scholar_id)
    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["publications"])
    pubs = author.get("publications", [])
    log.info("Profile has %d publications.", len(pubs))

    out: list[dict[str, Any]] = []
    for i, pub in enumerate(pubs, start=1):
        try:
            if fill:
                pub = scholarly.fill(pub)
            bib = pub.get("bib", {}) or {}
            title = (bib.get("title") or "").strip()
            if not title:
                continue
            year_raw = bib.get("pub_year") or bib.get("year")
            try:
                year = int(year_raw) if year_raw else None
            except (TypeError, ValueError):
                year = None
            venue = (
                bib.get("venue") or bib.get("journal") or bib.get("conference")
                or bib.get("publisher") or ""
            ).strip()
            authors = (bib.get("author") or "").strip()
            citations = pub.get("num_citations") or 0
            paper_url = pub.get("pub_url") or pub.get("eprint_url") or ""
            scholar_url = ""
            if pub.get("author_pub_id"):
                scholar_url = (
                    "https://scholar.google.com/citations?view_op=view_citation"
                    f"&hl=en&user={scholar_id}"
                    f"&citation_for_view={pub['author_pub_id']}"
                )
            out.append({
                "title": title,
                "year": year,
                "venue": venue,
                "authors": authors,
                "citations": citations,
                "paper_url": paper_url,
                "scholar_url": scholar_url,
            })
            log.info("[%d/%d] %s (%s)", i, len(pubs), title[:70], year)
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping entry %d: %s", i, exc)
    return out


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def build_listing_entry(pub: dict[str, Any]) -> dict[str, Any]:
    """Convert a fetched publication into the dict shape Quarto's listing wants."""
    year = pub.get("year")
    # Quarto sorts by date — give every entry Jan 1 of its year so sorting works.
    date = f"{year}-01-01" if year else None

    paper_url = pub.get("paper_url") or ""
    scholar_url = pub.get("scholar_url") or ""

    # The title links wherever `path` points. Prefer Scholar (canonical and
    # always works); fall back to the publisher URL if Scholar is missing.
    path = scholar_url or paper_url or ""

    # The description is the second line of the listing card. Put both
    # links there so the user can choose; render as HTML so anchors work.
    # We use Bootstrap Icons for the PDF (always available — Quarto bundles
    # them) and Academic Icons for Scholar (loaded via _includes/header.html
    # on the simple site). The .pub-link-paper / .pub-link-scholar classes
    # are styled in theme.scss as colored buttons.
    desc_parts: list[str] = []
    if paper_url:
        desc_parts.append(
            f'<a class="pub-link pub-link-paper" href="{paper_url}" '
            f'target="_blank" rel="noopener">'
            f'<i class="bi bi-file-earmark-pdf-fill"></i> Paper PDF</a>'
        )
    if scholar_url:
        desc_parts.append(
            f'<a class="pub-link pub-link-scholar" href="{scholar_url}" '
            f'target="_blank" rel="noopener">'
            f'<i class="ai ai-google-scholar"></i> Scholar</a>'
        )
    description = " ".join(desc_parts) if desc_parts else ""

    entry: dict[str, Any] = {
        "title": pub["title"],
        "author": pub.get("authors") or AUTHOR,
        "venue": pub.get("venue") or "",
        "categories": tag_publication(pub["title"], pub.get("venue", "")),
        "citations": pub.get("citations") or 0,
        "description": description,
    }
    if date:
        entry["date"] = date
    if path:
        entry["path"] = path
    return entry


def write_publications_yaml(pubs: list[dict[str, Any]], out_file: Path) -> int:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    entries = [build_listing_entry(p) for p in pubs]
    # Sort newest-first so the file is human-readable; Quarto re-sorts on render.
    entries.sort(key=lambda e: (e.get("date") or ""), reverse=True)
    header = (
        "# Auto-generated by scripts/fetch_scholar.py — do not hand-edit.\n"
        "# Run the script (or wait for the weekly GitHub Action) to refresh.\n"
    )
    body = yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, default_flow_style=False)
    out_file.write_text(header + body, encoding="utf-8")
    log.info("Wrote %d entries to %s.", len(entries), out_file)
    return len(entries)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scholar-id", default=SCHOLAR_ID,
                        help=f"Google Scholar user ID (default: {SCHOLAR_ID})")
    parser.add_argument("--out-file", default=str(OUT_FILE),
                        help=f"Output YAML path (default: {OUT_FILE})")
    parser.add_argument("--no-fill", action="store_true",
                        help="Skip per-publication detail fill (faster, no abstracts).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and print but don't write the YAML file.")
    args = parser.parse_args()

    pubs = fetch_publications(args.scholar_id, fill=not args.no_fill)
    log.info("Fetched %d usable publications.", len(pubs))

    if args.dry_run:
        for p in pubs:
            tags = tag_publication(p["title"], p.get("venue", ""))
            print(f"  {p.get('year') or '----'}  {p['title'][:80]:<82}  [{', '.join(tags)}]")
        return 0

    write_publications_yaml(pubs, Path(args.out_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
