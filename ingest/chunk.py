"""Split ingested documents into retrieval-sized chunks.

A separate step on purpose. The ingesters fetch and normalise; chunking is
the thing you re-tune most while evaluating retrieval, and keeping it apart
means re-chunking never means re-crawling.

Sizing uses the real tokenizer rather than a character heuristic. Arabic runs
about 0.295 tokens per character against roughly 0.25 for English, so a
chars/4 rule silently produces chunks a third larger than intended.

Usage:
    python chunk.py help_center.jsonl marketing.jsonl --out corpus.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

import tiktoken

# o200k_base is the GPT-5 family encoding. Chunk sizes are only meaningful
# against the tokenizer that will actually see them.
ENC = tiktoken.get_encoding("o200k_base")

TARGET_TOKENS = 450
MAX_TOKENS = 700
MIN_TOKENS = 40

TABLE_LINE = re.compile(r"^\s*\|")
HEADING_MAX_CHARS = 80


def n_tokens(text: str) -> int:
    return len(ENC.encode(text))


def is_heading(line: str) -> bool:
    stripped = line.strip()
    return (
        0 < len(stripped) <= HEADING_MAX_CHARS
        and not stripped.endswith((".", "،", ":", "؛"))
        and len(stripped.split()) <= 10
    )


def split_oversize(block: str, limit: int) -> list[str]:
    """Break a single block that exceeds MAX_TOKENS on its own.

    Long legal text arrives as very large paragraphs — the terms and
    conditions page is one 32,000-token document — so a paragraph splitter
    alone leaves chunks no retriever can use. Sentence boundaries first,
    then a hard token split for anything still too long.
    """
    sentences = re.split(r"(?<=[.؟!])\s+|\n", block)
    out, current = [], []
    for sentence in sentences:
        if not sentence.strip():
            continue
        candidate = current + [sentence]
        if n_tokens(" ".join(candidate)) > limit and current:
            out.append(" ".join(current))
            current = [sentence]
        else:
            current = candidate
    if current:
        out.append(" ".join(current))

    final = []
    for piece in out:
        if n_tokens(piece) <= limit:
            final.append(piece)
            continue
        tokens = ENC.encode(piece)
        for start in range(0, len(tokens), limit):
            final.append(ENC.decode(tokens[start:start + limit]))
    return final


def blocks_of(text: str) -> list[str]:
    """Paragraphs, with markdown tables kept whole.

    A pricing table split across two chunks retrieves badly and answers
    worse, so table lines accumulate into one block regardless of length.
    """
    blocks, current, in_table = [], [], False
    for line in text.split("\n"):
        if TABLE_LINE.match(line):
            if not in_table and current:
                blocks.append("\n".join(current))
                current = []
            in_table = True
            current.append(line)
            continue
        if in_table:
            blocks.append("\n".join(current))
            current, in_table = [], False
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def chunk_text(text: str, title: str) -> list[str]:
    """Group blocks up to TARGET_TOKENS, repeating the title for context.

    A retrieved fragment has to say what it is about; without the title, a
    chunk of shipping rates does not identify which document it came from.
    """
    prefix = f"{title}\n\n" if title else ""
    # The prefix is prepended after splitting, so both budgets must leave
    # room for it - otherwise every oversize split lands just over the cap.
    budget = TARGET_TOKENS - n_tokens(prefix)
    hard_limit = MAX_TOKENS - n_tokens(prefix)

    chunks, current, heading = [], [], ""
    for block in blocks_of(text):
        if n_tokens(block) > hard_limit and not TABLE_LINE.match(block):
            if current:
                chunks.append("\n\n".join(current))
                current = []
            chunks.extend(split_oversize(block, hard_limit))
            continue
        if is_heading(block):
            heading = block
        if current and n_tokens("\n\n".join(current + [block])) > budget:
            chunks.append("\n\n".join(current))
            current = [heading] if heading and heading != block else []
        current.append(block)
    if current:
        chunks.append("\n\n".join(current))

    out = []
    for chunk in chunks:
        body = chunk.strip()
        if not body:
            continue
        if out and n_tokens(body) < MIN_TOKENS:
            out[-1] = f"{out[-1]}\n\n{body}"   # absorb a stray fragment
        else:
            out.append(body)
    # The first chunk has nothing behind it, so a document opening with a
    # bare heading would otherwise emit a useless title-only chunk.
    while len(out) > 1 and n_tokens(out[0]) < MIN_TOKENS:
        out[1] = f"{out[0]}\n\n{out[1]}"
        out.pop(0)
    return [prefix + c if not c.startswith(title) else c for c in out]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    records = []
    per_source: list[tuple[str, int, int]] = []
    for path in args.inputs:
        before = len(records)
        docs = 0
        with path.open(encoding="utf-8") as handle:
          for line in handle:
            docs += 1
            doc = json.loads(line)
            pieces = chunk_text(doc["text"], doc.get("doc_title", ""))
            for index, piece in enumerate(pieces):
                record = dict(doc)
                record["text"] = piece
                record["chars"] = len(piece)
                record["tokens"] = n_tokens(piece)
                record["chunk_index"] = index
                record["chunk_count"] = len(pieces)
                record["parent_id"] = doc["id"]
                record["id"] = hashlib.sha256(
                    f"{doc['id']}:{index}".encode()
                ).hexdigest()[:16]
                records.append(record)
        per_source.append((path.name, docs, len(records) - before))

    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Per source, so a source that silently contributed nothing is visible.
    # A single total hides a failed crawl behind a plausible-looking number.
    for name, docs, made in per_source:
        note = "  <- nothing from this source" if made == 0 else ""
        print(f"  {name:26s} {docs:5,} docs -> {made:5,} chunks{note}")

    sizes = sorted(r["tokens"] for r in records)
    total = sum(sizes)
    print(f"{len(records):,} chunks, {total:,} tokens")
    print(f"median={sizes[len(sizes) // 2]}  "
          f"p90={sizes[int(len(sizes) * 0.9)]}  max={sizes[-1]}")
    print(f"over {MAX_TOKENS}: {sum(s > MAX_TOKENS for s in sizes)}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
