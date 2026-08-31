"""Fetch zid.sa marketing pages (and the Syria site) into the shared chunk format.

These pages are server-rendered, so a plain HTTP fetch is enough — no browser
needed. Content lives in <main>; a page carries several of them for different
sections, and one of those repeats its own intro block for responsive
variants.

Deduplication here is deliberately conservative — only immediately repeated
lines and immediately repeated contiguous blocks are collapsed. A more
aggressive "drop any line seen before" rule would quietly delete real price
rows, where the same figure legitimately appears against several packages.
Mild duplication costs a few tokens; a deleted price is a wrong answer.

The `country` field is the point of this ingester. Zid's pricing, logistics
and payment terms differ per market, and the market pages say so. Without the
tag, "كم سعر الباقة؟" retrieves Saudi and Egyptian pricing together and the
model blends them into a confidently wrong reply.

Usage:
    python fetch_marketing.py --out marketing.jsonl
    python fetch_marketing.py --only pricing,egypt --report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time

from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import arabic
import webfetch

BASE_SITE = "https://zid.sa"
SITEMAP = f"{BASE_SITE}/sitemap.xml"
DELAY = 1.0

# Pages the sitemap does not list. The four GCC/Egypt market pages are live
# and linked from the site, but absent from sitemap.xml — relying on the
# sitemap alone would silently drop every market page except Saudi, which is
# exactly the content the country tag exists to separate.
EXTRA_URLS = [
    "https://zid.sa/ar/egypt/",
    "https://zid.sa/ar/uae/",
    "https://zid.sa/ar/kuwait/",
    "https://zid.sa/ar/oman/",
    "https://zid.synapze.co/",
]

# robots.txt disallows /private/ and the testing pages; these are the same
# kind of thing under other names, plus a personal profile page.
SKIP = re.compile(r"/(testing|tesr|private|test-typography|profile)/?|/legal/test")

# Market pages, by URL fragment. Everything else defaults to Saudi.
COUNTRY_BY_PATH = {
    "/egypt": "EG", "/uae": "AE", "/kuwait": "KW", "/oman": "OM",
    "zid.synapze.co": "SY", "terms-conditions-kw": "KW",
}

# Every market page reuses the Saudi pricing component - confirmed by Zid, and
# visible in the output: the Kuwait, UAE and Oman pages all render "990 تدفع
# سنوياً", the Saudi riyal figure, with no currency named. (The Egypt page has
# no pricing section at all; its 300/500/1000 figures are order-volume
# brackets from a lead-qualification quiz.)
#
# So a price on a non-Saudi page is Saudi pricing, not that market's. Zid
# publishes one price list for every market, so the figure itself is correct;
# what would be wrong is presenting it as the merchant's local price. These
# chunks are therefore flagged rather than dropped - the pages carry real
# local content worth retrieving - and the answer service quotes the figure
# while stating plainly that it is the Saudi price list.
BARE_PRICE = re.compile(r"\b\d{2,5}\s*(?:تدفع|/\s*شهر|شهري|سنوي)")
LOCAL_CURRENCY = re.compile(r"(جنيه|درهم|دينار|ريال عماني|\$|USD|EGP|AED|KWD|OMR)")

# Pages making comparative claims about named competitors. Fine internally;
# flagged so the external launch is a deliberate decision, not an oversight.
COMPETITIVE = re.compile(r"/switchers")


def fetch(url: str) -> str:
    return webfetch.get(url)


LINK_RE = re.compile(r'href="(/ar(?:/[^"#?]*)?)"')


def _from_sitemap() -> list[str]:
    try:
        xml = fetch(SITEMAP)
    except Exception as exc:
        print(f"sitemap fetch failed ({type(exc).__name__})")
        return []
    return re.findall(r"<loc>([^<]+)</loc>", xml)


def _by_crawling(depth: int = 2) -> list[str]:
    """Follow links from the Arabic homepage when the sitemap is unusable.

    Fewer pages than the sitemap lists, but the alternative is proceeding
    with only the hard-coded market URLs — which is what silently happened
    once, leaving a corpus with no pricing in it and no sign anything was
    missing.
    """
    visited: set[str] = set()
    found: set[str] = {f"{BASE_SITE}/ar"}
    frontier = [f"{BASE_SITE}/ar"]
    for _ in range(depth):
        nxt = []
        for url in frontier:
            if url in visited:
                continue
            visited.add(url)
            try:
                html = fetch(url)
            except Exception:
                continue
            for path in LINK_RE.findall(html):
                full = BASE_SITE + path
                if full not in found:
                    found.add(full)
                    nxt.append(full)
            time.sleep(DELAY)
        if not nxt:
            break
        frontier = nxt
    return sorted(found)


def sitemap_urls() -> list[str]:
    """Every Arabic page worth fetching, from the sitemap or by crawling."""
    raw = _from_sitemap()
    source = "sitemap"
    arabic = [u for u in raw if "/ar" in u and not SKIP.search(u)]
    if not arabic:
        # Silence here is the dangerous outcome: a blocked or altered
        # sitemap once reduced the crawl to five hard-coded URLs, and the
        # run still reported success.
        print("WARNING: the sitemap yielded no Arabic pages "
              f"({len(raw)} <loc> entries seen). Falling back to crawling links.")
        arabic = [u for u in _by_crawling() if not SKIP.search(u)]
        source = "link crawl"

    # The sitemap repeats some entries, and trailing-slash variants of the
    # same page are the same page.
    seen, out = set(), []
    for url in arabic:
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        # zid.sa 308-redirects to the trailing-slash form; requesting it
        # directly saves a round trip on every page.
        out.append(key + "/")
    print(f"{len(out)} Arabic pages found via {source}")
    return out


def collapse_repeats(lines: list[str]) -> list[str]:
    """Drop immediately repeated lines and immediately repeated blocks."""
    out = []
    for line in lines:
        if out and out[-1] == line:
            continue
        out.append(line)
    # Collapse a run of lines immediately followed by an identical run.
    i = 0
    while i < len(out):
        for size in range(min(12, (len(out) - i) // 2), 1, -1):
            if out[i:i + size] == out[i + size:i + 2 * size]:
                del out[i + size:i + 2 * size]
                break
        i += 1
    return out


# Next.js streams the page as an RSC flight payload inside these calls. On
# client-rendered pages it is the only place the text exists.
_RSC = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\s*\]\)', re.S)
# Prose, as opposed to the class names, URLs and component ids around it.
_PROSE = re.compile(r"[؀-ۿ][؀-ۿ\s،.:؛؟!ـ%\d\u2013\u2014-]{6,}")

_chrome_cache: set[str] | None = None


def _rsc_strings(html: str) -> list[str]:
    """Readable strings from a Next.js flight payload, in order of appearance."""
    parts = []
    for match in _RSC.finditer(html):
        try:
            parts.append(json.loads(match.group(1)))
        except ValueError:
            continue
    seen, out = set(), []
    for found in _PROSE.findall("".join(parts)):
        text = found.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


PLAN_FIELDS = ("billingCycle", "packagePriceMonthly", "packagePriceAnnual",
               "totalCost", "packageDescription")


def _rsc_blob(html: str) -> str:
    parts = []
    for match in _RSC.finditer(html):
        try:
            parts.append(json.loads(match.group(1)))
        except ValueError:
            continue
    return "".join(parts)


def _field(segment: str, key: str) -> str | None:
    match = re.search(rf'"{key}":\s*(?:"((?:[^"\\]|\\.)*)"|null)', segment)
    return match.group(1).strip() if match and match.group(1) else None


def pricing_plans(html: str) -> str:
    """The subscription tiers and their prices, from the page's own JSON.

    The rendered markup shows one tier at a time and the loose strings in the
    payload keep "99" apart from "ريال شهرياً", so reading text alone finds
    package names with no prices beside them — which reads as "Zid does not
    publish its prices" when in fact every figure is present, as structured
    fields on a pricingPlan object.

    Emitted as one labelled block per tier so a chunk of it answers "what
    does X cost" without needing the rest of the page.
    """
    blob = _rsc_blob(html)
    if '"packageName"' not in blob:
        return ""
    lines, seen = [], set()
    for match in re.finditer(r'"packageName":"((?:[^"\\]|\\.)*)"', blob):
        # Each tier appears twice, once per language. The language marker
        # that belongs to this one is the first that follows it; searching a
        # window instead picks up the neighbouring translation's marker and
        # lets every English tier through.
        lang = re.search(r'"languages_id":\{"name":"(\w+)"\}',
                         blob[match.start():match.start() + 1600])
        if not lang or lang.group(1) != "ar":
            continue
        name = match.group(1)
        if not name or name in seen:
            continue
        seen.add(name)
        segment = blob[max(0, match.start() - 1200):match.start() + 1200]
        plan = {key: _field(segment, key) for key in PLAN_FIELDS}
        bits = [f"باقة {name}:"]
        monthly, annual = plan["packagePriceMonthly"], plan["packagePriceAnnual"]
        if monthly:
            bits.append(f"{monthly} {plan['billingCycle'] or 'ريال شهرياً'}")
            if annual:
                bits.append(f"وعند الدفع السنوي {annual}")
        elif annual:
            # Free and enterprise tiers carry only this field, and it holds a
            # phrase ("مجاناً", "قيمة مخصصة") rather than a number.
            bits.append(f"{annual} {plan['billingCycle'] or ''}".strip())
        if plan["totalCost"]:
            bits.append(f"({plan['totalCost']})")
        if plan["packageDescription"]:
            bits.append(f"— {plan['packageDescription']}")
        lines.append(" ".join(bits))
    return ("أسعار باقات زد:\n" + "\n".join(lines)) if lines else ""


def chrome_strings() -> set[str]:
    """Strings common to every page, learned from a URL that does not exist.

    The flight payload carries the whole shared bundle - nav, footer, banner,
    the not-found component - alongside the page's own copy. Fetching a bogus
    path once yields exactly that shared set, so subtracting it leaves the
    page's real content without hand-maintaining a blocklist.
    """
    global _chrome_cache
    if _chrome_cache is None:
        try:
            _chrome_cache = set(_rsc_strings(fetch(f"{BASE_SITE}/ar/__no_such_page__/")))
        except Exception:
            _chrome_cache = set()
    return _chrome_cache


def _lines_from(blocks, strip_header: bool) -> list[str]:
    lines = []
    for block in blocks:
        if block is None:
            continue
        drop = ["nav", "footer"] + (["header"] if strip_header else [])
        for tag in block.find_all(drop):
            tag.decompose()
        lines += [l.strip() for l in block.get_text("\n").split("\n") if l.strip()]
    return lines


def page_text(html: str) -> str:
    """Text of a page, from <main> where possible and <body> otherwise.

    Two traps, both of which silently emptied pages before they were fixed:

    * <header> is NOT stripped inside <main>. Several page types wrap their
      hero and section content in <header>, so removing it discarded the
      entire page — /ar/customers/* went from 1,380 characters to zero. Site
      chrome lives outside <main> anyway.
    * A page can carry <main> elements that are all empty. Falling back only
      when no <main> tag exists is not enough; the fallback has to trigger on
      an empty *result*.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    lines = _lines_from(soup.find_all("main"), strip_header=False)
    if sum(len(l) for l in lines) < 200:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        lines = _lines_from([soup.body], strip_header=True)

    # Merge in anything the flight payload carries that the markup does not.
    # On the pricing page the served HTML yields 3,600 characters while the
    # payload holds package names, tier features and the commission answer —
    # content a reader sees but a parser of the markup alone never gets.
    # Reading order there is approximate, so it is appended rather than
    # interleaved.
    plans = pricing_plans(html)
    if plans:
        lines = lines + plans.split("\n")

    have = {l.strip() for l in lines}
    chrome = chrome_strings()
    extra = [s for s in _rsc_strings(html) if s not in chrome and s.strip() not in have]
    if extra:
        lines = lines + extra

    return "\n".join(collapse_repeats(lines))


def country_for(url: str) -> str:
    for fragment, code in COUNTRY_BY_PATH.items():
        if fragment in url:
            return code
    return "SA"


def title_for(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    heading = soup.find("h1")
    if heading and heading.get_text(strip=True):
        return re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
    if soup.title and soup.title.string:
        return soup.title.string.split("|")[0].strip()
    return url.rstrip("/").rsplit("/", 1)[-1]


def build(url: str, html: str) -> dict | None:
    body = page_text(html)
    if len(body) < 200:
        return None
    title = title_for(html, url)
    text = arabic.normalize(f"{title}\n\n{body}")
    return {
        "id": hashlib.sha256(url.encode()).hexdigest()[:16],
        "source_file": url,
        "doc_title": title,
        "page": None,
        "type": "marketing",
        "wp_type": None,
        "lang": "ar" if arabic.arabic_ratio(text) > 0.6 else "mixed",
        "audience": "internal",
        "country": country_for(url),
        "competitive": bool(COMPETITIVE.search(url)),
        "pricing_not_local": (
            country_for(url) != "SA"
            and bool(BARE_PRICE.search(text))
            and not LOCAL_CURRENCY.search(text)
        ),
        "confidence": "high",
        "content_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        "chars": len(text),
        "text": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("marketing.jsonl"))
    parser.add_argument("--only", default="", help="comma-separated URL substrings")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    urls = sitemap_urls()
    known = {u.rstrip("/") for u in urls}
    urls += [u for u in EXTRA_URLS if u.rstrip("/") not in known]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        urls = [u for u in urls if any(w in u for w in wanted)]

    records, skipped = [], []
    for url in urls:
        try:
            record = build(url, fetch(url))
        except Exception as exc:
            print(f"  ERROR {url}: {type(exc).__name__}")
            continue
        if record is None:
            skipped.append(url)
        else:
            records.append(record)
            if args.report:
                flag = " [competitive]" if record["competitive"] else ""
                print(f"{record['chars']:6d}  {record['country']}  "
                      f"{record['doc_title'][:52]}{flag}")
        time.sleep(DELAY)

    total = sum(r["chars"] for r in records)
    print(f"\n{len(records)} pages, {total:,} chars")
    if skipped:
        # Named, not just counted. A page that yields nothing is almost always
        # client-rendered rather than genuinely empty, and a bare count hides
        # which parts of the site the assistant will have no answer for.
        print(f"\n{len(skipped)} page(s) yielded no text - client-rendered, "
              f"and only reachable with a headless browser:")
        for url in skipped:
            print(f"  {url}")
    by_country: dict[str, int] = {}
    for r in records:
        by_country[r["country"]] = by_country.get(r["country"], 0) + 1
    print("by country:", by_country)
    print("competitive pages:", sum(r["competitive"] for r in records))
    flagged = [r for r in records if r["pricing_not_local"]]
    if flagged:
        print(f"Saudi pricing shown on {len(flagged)} non-Saudi page(s):")
        for r in flagged:
            print(f"  {r['country']}  {r['source_file']}")

    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
