"""Fetch help.zid.sa into the same chunk format the PDF extractor produces.

The help centre is WordPress, and its REST API is open, which makes it a far
better source than crawling HTML: structured records, real titles, categories,
and a `modified` timestamp that makes incremental re-fetching trivial.

Two things are not obvious and both were established by testing:

  * Roughly a third of the content types (faqs, tips, zid-academy, promotion)
    return an EMPTY `content.rendered` from the REST API. Their bodies are
    short one-or-two sentence answers that live only in the rendered page,
    under `.panel-content.inner`. They are not empty pages — skipping them
    would silently drop 185 answers, many of them the pricing and payout
    questions employees actually ask. Those are fetched over HTTP as a
    fallback.

  * Unlike the PDFs, this text needs **no** direction repair. Web content is
    already in logical order with standard Arabic codepoints; running the
    PDF bidi fixes over it would corrupt correct text. Only the shared
    normalisation (digits, whitespace) is applied.

robots.txt disallows /en/, so English URLs are skipped.

Usage:
    python fetch_help_center.py --out help_center.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import arabic
import webfetch

BASE = "https://help.zid.sa"
TYPES = ["posts", "pages", "faqs", "tips", "zid-academy", "promotion", "zid-faqs"]

# Selectors carrying the body on pages the REST API returns empty for.
BODY_SELECTORS = [".panel-content.inner", ".post-desc"]

# Chrome that appears inside those containers on every page.
BOILERPLATE = re.compile(
    r"(مشاركة|متصفح الكمبيوتر|سوف تتعلم:|آخر تحديث\s+\w+\s+\d+,\s*\d{4})"
)

DELAY = 1.0


def fetch(url: str, as_json: bool = True):
    """Fetch a URL; a 400 from the REST API means the last page was passed."""
    try:
        raw = webfetch.get(url)
    except RuntimeError as exc:
        if as_json and "HTTP 400" in str(exc):
            return None
        raise
    return json.loads(raw) if as_json else raw


def to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n").strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def scrape_body(url: str) -> str:
    """Recover the body of a page whose REST content is empty."""
    html = fetch(url, as_json=False)
    soup = BeautifulSoup(html or "", "lxml")
    for selector in BODY_SELECTORS:
        element = soup.select_one(selector)
        if not element:
            continue
        lines = [
            line.strip()
            for line in element.get_text("\n").split("\n")
            if line.strip() and not BOILERPLATE.fullmatch(line.strip())
        ]
        text = "\n".join(lines)
        text = BOILERPLATE.sub(" ", text)
        return re.sub(r"\s{2,}", " ", text).strip()
    return ""


def collect() -> list[dict]:
    records, scraped = [], 0
    for kind in TYPES:
        page = 1
        while True:
            batch = fetch(
                f"{BASE}/wp-json/wp/v2/{kind}?per_page=100&page={page}"
                "&_fields=id,link,title,content,date,modified"
            )
            if not batch:
                break
            for item in batch:
                link = item["link"]
                if "/en/" in link:  # disallowed by robots.txt
                    continue
                title = to_text(item.get("title", {}).get("rendered", ""))
                body = to_text(item.get("content", {}).get("rendered", ""))
                if len(body) < 200:
                    body = scrape_body(link)
                    scraped += 1
                    time.sleep(DELAY)
                if not body.strip():
                    continue
                records.append(build(kind, item, title, body))
            page += 1
            time.sleep(DELAY)
        print(f"{kind:14s} {sum(1 for r in records if r['wp_type'] == kind):4d} items")
    print(f"({scraped} bodies recovered by HTML fallback)")
    return records


def build(kind: str, item: dict, title: str, body: str) -> dict:
    # Web text is already logical-order standard Arabic - only the shared
    # normalisation applies here, never the PDF direction repairs.
    text = arabic.normalize(f"{title}\n\n{body}")
    return {
        "id": hashlib.sha256(item["link"].encode()).hexdigest()[:16],
        "source_file": item["link"],
        "doc_title": title,
        "page": None,
        "type": "article",
        "wp_type": kind,
        "lang": "ar" if arabic.arabic_ratio(text) > 0.6 else "mixed",
        "audience": "internal",
        "country": None,
        "confidence": "high",
        "modified": item.get("modified", ""),
        # Content hash, so a re-fetch only re-embeds what actually changed.
        "content_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        "chars": len(text),
        "text": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("help_center.jsonl"))
    args = parser.parse_args()

    records = collect()
    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    total = sum(r["chars"] for r in records)
    print(f"\n{len(records)} articles, {total:,} chars -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
