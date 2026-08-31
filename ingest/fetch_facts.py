"""Ingest curated facts you maintain by hand.

Some answers are not on any page a crawler can read. Zid's subscription
prices are the clearest case: the pricing page renders them from values it
keeps apart from their labels, so no amount of scraping recovers "the
Professional package costs X". They exist only in someone's head, a deck, or
a system the assistant has no access to.

This is where those go — plain Markdown or text files in `facts/`, edited by
whoever owns the answer. They are ingested like any other source, but marked
`curated: true` so the answer service can prefer them when a crawled page
and a maintained fact disagree.

A file per topic keeps the citation meaningful: a chunk citing
"facts/pricing.md" tells a reader exactly what to update when it goes stale.

Usage:
    python fetch_facts.py ../facts --out ../data/facts.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import arabic

SUFFIXES = {".md", ".markdown", ".txt"}

# "country: KW" or "audience: public" on the first lines of a file.
FRONT_MATTER = re.compile(r"^([a-z_]+):\s*(\S+)\s*$")


def read_front_matter(text: str) -> tuple[dict, str]:
    """Optional `key: value` lines at the top, ended by a blank line."""
    meta, lines = {}, text.splitlines()
    index = 0
    for index, line in enumerate(lines):
        if not line.strip():
            index += 1
            break
        match = FRONT_MATTER.match(line)
        if not match:
            index = 0
            break
        meta[match.group(1)] = match.group(2)
    return meta, "\n".join(lines[index:]) if meta else text


def build(path: pathlib.Path, root: pathlib.Path) -> dict | None:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    meta, body = read_front_matter(raw)
    title = meta.get("title") or path.stem.replace("_", " ").replace("-", " ")
    text = arabic.normalize(f"{title}\n\n{body}")
    rel = path.relative_to(root).as_posix()
    return {
        "id": hashlib.sha256(rel.encode()).hexdigest()[:16],
        "source_file": f"facts/{rel}",
        "doc_title": title,
        "page": None,
        "type": "curated",
        "wp_type": None,
        "lang": "ar" if arabic.arabic_ratio(text) > 0.6 else "mixed",
        "audience": meta.get("audience", "internal"),
        "country": meta.get("country"),
        # Maintained by a person who owns the answer, so it outranks a page
        # that merely mentions the subject.
        "curated": True,
        "confidence": "high",
        "content_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        "chars": len(text),
        "text": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"no facts folder at {args.folder}, skipping")
        args.out.write_text("", encoding="utf-8")
        return 0

    records = []
    for path in sorted(args.folder.rglob("*")):
        if path.suffix.lower() in SUFFIXES:
            record = build(path, args.folder)
            if record:
                records.append(record)
                print(f"  {record['chars']:6,}  {record['source_file']}")

    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{len(records)} curated file(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
