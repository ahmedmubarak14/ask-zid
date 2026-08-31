"""Turn Zid's knowledge-base PDFs into clean, chunked JSONL ready for indexing.

Two extractors are used deliberately, because neither is sufficient alone:

  * pypdf      - character-correct Arabic (after NFKC), but fuses table cells
                 into unreadable single lines. Used for prose.
  * pdfplumber - recovers table structure (cells, columns, headers) reliably,
                 but emits Arabic characters in visual order. Used for tables,
                 with the character order repaired in arabic.fix_char_order.

Tables are emitted as whole chunks. Splitting a pricing table in half produces
two chunks that each retrieve badly and answer wrongly, which is worse than a
chunk slightly over the target size.

Usage:
    python extract.py <pdf-dir> --out chunks.jsonl
    python extract.py <pdf-dir> --report        # human review, no output file
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import warnings

import pdfplumber
from pypdf import PdfReader

import arabic

warnings.filterwarnings("ignore")

# Target chunk size in characters. Arabic runs ~2-3 characters per token, so
# this lands near 500-800 tokens - large enough to carry context, small enough
# that several fit in one prompt.
TARGET_CHARS = 1800
MIN_CHARS = 200

# A line that is short, has no sentence punctuation and is followed by body
# text is almost always a heading. Headings are repeated into each chunk so a
# retrieved fragment still says what it is about.
HEADING_MAX_CHARS = 80


def doc_title(path: pathlib.Path) -> str:
    """Recover a readable title from the upload-mangled filename."""
    # Uploads arrive prefixed with one or more hex ids; strip them all.
    name = re.sub(r"^(?:[0-9a-f]{6,}[_-]+)+", "", path.stem)
    name = re.sub(r"[_]+", " ", name).strip(" -_")
    return re.sub(r"\s{2,}", " ", name) or path.stem


def extract_prose(path: pathlib.Path) -> list[tuple[int, str]]:
    """Per-page prose lines, normalised and re-ordered. Returns (page, text)."""
    pages = []
    reader = PdfReader(str(path))
    for number, page in enumerate(reader.pages, start=1):
        lines = []
        for raw in (page.extract_text() or "").split("\n"):
            line = arabic.normalize(raw)
            if not line:
                continue
            fixed, _ = arabic.fix_word_order(line)
            lines.append(fixed)
        if lines:
            pages.append((number, "\n".join(lines)))
    return pages


def extract_tables(path: pathlib.Path) -> list[tuple[int, str]]:
    """Per-page tables rendered as markdown. Returns (page, markdown)."""
    out = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables() or []:
                rows = []
                for row in table:
                    cells = []
                    for cell in row:
                        text = arabic.normalize(cell or "").replace("\n", " ")
                        text, _ = arabic.fix_char_order(text)
                        cells.append(text.strip())
                    if any(cells):
                        rows.append(cells)
                if len(rows) < 2:
                    continue
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                md = [
                    "| " + " | ".join(rows[0]) + " |",
                    "| " + " | ".join(["---"] * width) + " |",
                ]
                md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                out.append((number, "\n".join(md)))
    return out


def is_heading(line: str) -> bool:
    return (
        len(line) <= HEADING_MAX_CHARS
        and not line.rstrip().endswith((".", "،", ":"))
        and len(line.split()) <= 10
    )


def chunk_prose(text: str) -> list[str]:
    """Split prose on blank lines, keeping the most recent heading as context."""
    chunks, current, heading = [], [], ""
    for line in text.split("\n"):
        if is_heading(line) and not current:
            heading = line
        current.append(line)
        if sum(len(x) for x in current) >= TARGET_CHARS:
            chunks.append("\n".join(current))
            current = [heading] if heading else []
    if current and sum(len(x) for x in current) >= MIN_CHARS:
        chunks.append("\n".join(current))
    elif current and chunks:
        chunks[-1] += "\n" + "\n".join(current)
    elif current:
        chunks.append("\n".join(current))
    return chunks


def page_context(text: str) -> str:
    """The first heading-like lines of a page, used to caption its tables.

    A table chunk that reads only "| Aramex | 24 |" retrieves poorly and
    answers worse - nothing in it says which package or weight tier the price
    belongs to. Prefixing the page's heading restores that.
    """
    headings = [line for line in text.split("\n") if line.strip() and is_heading(line)]
    return " — ".join(headings[:2])


def build(path: pathlib.Path) -> list[dict]:
    title = doc_title(path)
    records = []

    prose = extract_prose(path)
    # An Arabic or otherwise unusable filename leaves nothing to title with;
    # fall back to the document's own first heading.
    if prose and re.fullmatch(r"[0-9a-f]*", re.sub(r"[^0-9A-Za-z]", "", title).lower() or "x") :
        first = next(
            (l.strip() for l in prose[0][1].split("\n")
             if is_heading(l) and len(re.findall(r"[^\W\d_]", l)) >= 3),
            "",
        )
        title = first or title
    context = {page: page_context(text) for page, text in prose}

    for page, text in prose:
        for part in chunk_prose(text):
            records.append(_record(title, path, page, "prose", part))

    for page, md in extract_tables(path):
        caption = " — ".join(x for x in (title, context.get(page, "")) if x)
        body = f"{caption}\n\n{md}" if caption else md
        records.append(_record(title, path, page, "table", body))

    return records


def _record(title: str, path: pathlib.Path, page: int, kind: str, text: str) -> dict:
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    ratio = arabic.arabic_ratio(text)
    return {
        "id": digest,
        "source_file": path.name,
        "doc_title": title,
        "page": page,
        "type": kind,
        "lang": "ar" if ratio > 0.6 else ("en" if ratio < 0.2 else "mixed"),
        # Every chunk starts internal-only. Flipping a document to "public" is
        # the deliberate act that exposes it to an external audience later.
        "audience": "internal",
        "country": None,
        "confidence": arabic.confidence(text),
        "chars": len(text),
        "text": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument("--report", action="store_true",
                        help="print a human-review summary instead of writing output")
    args = parser.parse_args()

    files = sorted(args.pdf_dir.glob("*.pdf"))
    if not files:
        print(f"no PDFs found in {args.pdf_dir}", file=sys.stderr)
        return 1

    all_records = []
    for path in files:
        records = build(path)
        all_records.extend(records)
        if args.report:
            low = [r for r in records if r["confidence"] == "low"]
            tables = sum(r["type"] == "table" for r in records)
            print(f"\n=== {doc_title(path)}")
            print(f"    {len(records)} chunks ({tables} tables), "
                  f"{sum(r['chars'] for r in records):,} chars, "
                  f"{len(low)} low-confidence")
            sample = next((r for r in records if r["type"] == "prose"), None)
            if sample:
                for line in sample["text"].split("\n")[:3]:
                    print(f"      {line[:100]}")
            for r in low[:3]:
                print(f"    ! review p{r['page']} ({r['type']}): {r['text'][:90]}")

    total_chars = sum(r["chars"] for r in all_records)
    print(f"\n{len(all_records)} chunks from {len(files)} files, "
          f"{total_chars:,} chars (~{total_chars // 3:,} tokens est.)")
    low = sum(r["confidence"] == "low" for r in all_records)
    print(f"{low} chunks flagged low-confidence for human review "
          f"({low / max(len(all_records), 1):.0%})")

    if args.out:
        with args.out.open("w", encoding="utf-8") as fh:
            for record in all_records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
