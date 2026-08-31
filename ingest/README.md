# ask-zid — PDF ingestion

Turns Zid's knowledge-base PDFs into clean, chunked JSONL ready to embed or to
paste straight into a prompt.

```bash
pip install -r requirements.txt

python extract.py /path/to/pdfs --report            # inspect quality first
python extract.py /path/to/pdfs --out chunks.jsonl  # write output
```

## Why this exists

The source PDFs do not extract correctly with any single library. Two defects
appear in all of them, and both are silent — the text *looks* fine in a
terminal, because the terminal applies bidi rendering on the way to your eye.

**1. Arabic arrives as Unicode presentation forms.** Letters come out in
U+FE70–FEFF (contextual glyph shapes) rather than standard Arabic
U+0600–06FF. In the source set that was ~18,400 characters wrong against
~4,900 right, so roughly 80% of the Arabic.

This matters because `ﻣﻨﺘﺠﺎت` and `منتجات` are the same word to a reader and
completely different strings to a tokeniser. Indexed as-is, an Arabic query
matches nothing and the bot looks broken for a reason no prompt change fixes.
`unicodedata.normalize("NFKC", …)` maps them back, cleanly, in every file
tested.

**2. Word and character order arrives visually, not logically.** Which one
depends on the extractor, which is why two are used:

| | Arabic characters | Table structure |
|---|---|---|
| pypdf | correct after NFKC | cells fused into one line |
| pdfplumber | reversed within each run | recovered reliably |
| PyMuPDF | **corrupts lam-alef ligatures** (14 of 15 occurrences) | — |

So: **pypdf for prose, pdfplumber for tables**, each with its own repair.
PyMuPDF was tested and rejected — it turns `خلال` into `خالل`.

## How the repairs work

`arabic.fix_word_order` (prose) applies a simplified bidi reordering: reverse
the token sequence, then flip embedded Latin runs back so an English phrase
inside an Arabic line still reads left to right. This is applied **by rule**,
not by score — reversing a token list does not change which words it contains,
so no dictionary check can tell the two orientations apart.

`arabic.fix_char_order` (table cells) reverses characters within each Arabic
run, but **only if** the result scores better against a frequency word list.
That guard matters: `لا` ("no") is already correct and reversing it would
produce `ال`. The score check leaves it alone.

Every chunk carries a `confidence` field. `low` means Arabic is present but
little of it was recognised — those are listed by `--report` for a human pass.
Currently 6% of chunks.

## Output

One JSON object per line:

```json
{"id": "…", "source_file": "…", "doc_title": "2026 Zid Logistics", "page": 4,
 "type": "table", "lang": "mixed", "audience": "internal", "country": null,
 "confidence": "high", "chars": 156, "text": "…"}
```

- `audience` starts at `internal` on every chunk. Promoting a document to
  `public` should be a deliberate act, enforced at the database level, not a
  prompt instruction — that field is what makes the future external launch a
  filter change rather than a rewrite.
- `country` is unset here and needs filling for the market-specific pages
  (Egypt, UAE, Kuwait, Oman). Pricing and logistics differ per market, so
  without it a question about package pricing retrieves two countries' answers
  at once and the model blends them.
- Tables are never split, and are captioned with their page heading. A chunk
  reading only `| Aramex | 24 |` retrieves poorly and answers worse.

## Known limitations

- Neutral characters (`|`, `:`) can land at the wrong end of a reordered line.
  Cosmetic; meaning and retrieval are unaffected.
- Pages with two side-by-side tables merge into one grid with extra columns.
  One such table is in the logistics file; it is flagged by `--report`.
- Scanned or image-only PDFs are not handled — none in the current set need
  OCR, but a new source might.

## Sources

| Script | Source | Notes |
|---|---|---|
| `extract.py` | knowledge-base PDFs | needs the Arabic repairs above |
| `fetch_help_center.py` | help.zid.sa | WordPress REST + HTML fallback |
| `fetch_marketing.py` | zid.sa + zid.synapze.co | server-rendered, country-tagged |

### Marketing pages

Two things the sitemap does not tell you, both established by testing:

* **The market pages are unlisted.** `/ar/egypt/`, `/ar/uae/`, `/ar/kuwait/`
  and `/ar/oman/` are live and linked from the site but absent from
  `sitemap.xml`. Crawling the sitemap alone drops every market page except
  Saudi — precisely the content the `country` tag exists to separate. They
  are added explicitly, along with `zid.synapze.co`, which is Zid's Syria
  site on its own host.

* **Non-Saudi pages show Saudi prices.** The market pages render a shared,
  unlocalised pricing component: the Kuwait page displays "990 تدفع سنوياً"
  — the Saudi riyal figure — with no currency named. Meanwhile the Egypt
  page has no pricing section at all; its `300`/`500`/`1000` figures are
  order-volume brackets in a lead-qualification quiz.

  Whether Zid actually charges those amounts per market is a business fact
  this ingester cannot verify, so such pages carry `pricing_unverified:
  true` rather than being silently trusted. The answer service should
  decline to quote a price from a flagged chunk.

Pages under `/switchers` carry `competitive: true`. They compare Zid to
named competitors, which is fine internally and a deliberate decision for
the external launch.

Deduplication is conservative — only immediately repeated lines and blocks
are collapsed, because a "drop anything seen before" rule would delete real
price rows where one figure legitimately appears against several packages.

### Pages that need a browser

Twelve zid.sa pages are client-rendered and yield nothing over plain HTTP —
`/zidx25` serves 220 KB of HTML and 127 characters of text. Among them are
`/about`, `/contact` and `/legal`; the rest are interactive tools
(`free-tools/*`) and campaign pages with little to answer from.

`fetch_marketing.py` names them in its summary rather than folding them into
a count, because a page yielding nothing is almost always client-rendered
rather than empty, and a bare number hides which parts of the site the
assistant has no answer for. Recovering them needs a headless browser
(Playwright), which the ingester does not require for anything else.

## Corpus

| Source | Items | Tokens |
|---|---|---|
| PDFs | 8 files -> 109 chunks | 22,273 |
| help.zid.sa | 531 articles | 246,442 |
| zid.sa + Syria site | 93 pages | 117,195 |
| **Total** | **733** | **385,910** |

Counted with the GPT-5 tokenizer, not estimated from character counts —
Arabic runs about 0.295 tokens per character against roughly 0.25 for
English, so a chars/4 rule of thumb understates it by a third.

At the PDF corpus alone, holding everything in a cached prompt would have
beaten retrieval on both cost and reliability. The full corpus settles it
the other way: at nearly 400,000 tokens, retrieval is the right
architecture.
