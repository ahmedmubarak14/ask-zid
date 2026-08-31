# ask-zid — اسأل زد

A question-answering assistant over Zid's knowledge sources. Internal first
(employees and sales hunters), embeddable into Sales Hunter as an **اسأل زد**
widget, and externally facing later.

Deliberately a separate service: Sales Hunter embeds a widget and passes the
signed-in user's token, but never touches the knowledge base or the model key.

## Approach

Retrieval-augmented generation — search the knowledge base first, then answer
strictly from what was retrieved, with citations, and refuse when the sources
do not cover the question.

That choice is measured, not assumed. The corpus was sized before committing:

| Source | Items | Tokens |
|---|---|---|
| help.zid.sa | 531 articles | ~238,000 |
| Knowledge-base PDFs | 109 chunks | ~22,000 |
| Marketing site, pricing, country pages | not yet ingested | — |
| **Total so far** | | **~260,000** |

At 22K tokens the whole corpus would fit in a cached prompt and retrieval
would be pointless overhead. At 260K it does not: per-question cost rises
sharply and, more importantly, answer quality falls when the relevant
passage is buried in a quarter of a million tokens of unrelated context.
Retrieving a few thousand relevant tokens is both cheaper and better.

Fine-tuning is not used. The content changes weekly, retrieval updates
instantly, and fine-tuning cannot produce citations.

## Status

- [x] PDF ingestion with Arabic extraction repair (`ingest/extract.py`)
- [x] Help centre ingestion (`ingest/fetch_help_center.py`)
- [ ] Marketing site, pricing and country pages
- [ ] Embedding + hybrid retrieval (vector + full-text)
- [ ] Answer service with citations and grounded refusal
- [ ] Embeddable widget for Sales Hunter
- [ ] Evaluation set of real employee questions

## Ingestion

See [`ingest/README.md`](ingest/README.md) — it documents the Arabic
extraction defects in the source PDFs and how each is repaired. Both
ingesters emit the same chunk schema so they feed one index.

```bash
cd ingest
pip install -r requirements.txt

python extract.py /path/to/pdfs --report            # inspect quality
python extract.py /path/to/pdfs --out chunks.jsonl
python fetch_help_center.py --out help_center.jsonl
```

## Two fields that carry design weight

Every chunk is written with `audience` and `country`, both deliberate:

- **`audience`** starts as `internal` on every chunk. The eventual external
  launch should be a filter change, not a rewrite — and it must be enforced
  in the database, not in the prompt, so a prompt injection cannot reach
  rows the query never selected.
- **`country`** must be set for the market-specific pages (Egypt, UAE,
  Kuwait, Oman). Pricing and logistics differ per market, so without it a
  question about package pricing retrieves two countries' answers at once
  and the model blends them into a confidently wrong reply.
