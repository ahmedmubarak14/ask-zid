"""Show why a question retrieved what it did.

Six rounds of this project were spent guessing whether a missing answer meant
the content was absent, the chunk was unretrievable, or the index was stale.
Those look identical from the outside — the assistant says it does not know —
and they have completely different fixes. This answers the question directly.

    python why.py "كم سعر باقة النمو"
    python why.py "كم سعر باقة النمو" --expect "سعر باقة النمو"

With --expect it reports where the chunk containing that text ranks, which is
the number that matters: "present but at rank 40" and "absent" produce the
same silence from the assistant and need opposite responses.

Runs without an API key on keyword scoring alone. Pass a key (or set one) to
include the vector half and see the fused ranking the service actually uses.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "service"))
sys.path.insert(0, str(ROOT))

from config import resolve_key
from search import Index, tokenise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--expect", default="",
                        help="text the right chunk should contain")
    parser.add_argument("--corpus", type=pathlib.Path, default=ROOT / "data/corpus.jsonl")
    parser.add_argument("--vectors", type=pathlib.Path, default=ROOT / "data/vectors.npz")
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args()

    if not args.corpus.exists():
        print(f"no corpus at {args.corpus} — run: make crawl && make corpus")
        return 1

    index = Index(args.corpus, args.vectors)
    print(f"corpus: {index.n:,} chunks   vectors: "
          f"{'loaded' if index.ready else 'MISSING — keyword scoring only'}")

    # 1. Is the text in the corpus at all? Nothing else matters if it is not.
    if args.expect:
        holders = [i for i, r in enumerate(index.rows) if args.expect in r["text"]]
        if not holders:
            print(f"\n\"{args.expect}\" appears in NO chunk.")
            print("  -> the content is missing from the corpus; this is an "
                  "ingestion problem, not a retrieval one.")
            return 0
        print(f"\n\"{args.expect}\" appears in {len(holders)} chunk(s): "
              f"{', '.join(index.rows[i]['doc_title'][:30] for i in holders[:3])}")

    # 2. Does the query's vocabulary even reach it?
    terms = tokenise(args.question)
    unknown = [t for t in terms if t not in index.df]
    print(f"\nquery terms: {' '.join(terms)}")
    if unknown:
        print(f"  not in any chunk: {' '.join(unknown)}"
              f"   <- these contribute nothing to keyword scoring")

    query_vector = None
    try:
        key = resolve_key()
    except KeyError:
        key = None
    if key and index.ready:
        import serve as serve_mod
        query_vector = serve_mod.embed_query(args.question, key)

    mask = np.ones(index.n, dtype=bool)
    ranked = index.search(args.question, query_vector, k=args.top)
    ids = [r["id"] for r in ranked]

    print(f"\ntop {args.top} "
          f"({'hybrid' if query_vector is not None else 'keyword only'}):")
    for position, row in enumerate(ranked, start=1):
        hit = "  <-- EXPECTED" if args.expect and args.expect in row["text"] else ""
        print(f"  {position:2d}. {row['doc_title'][:52]:54s}{hit}")

    # 3. If it is in the corpus but not in the results, say where it landed.
    if args.expect:
        wanted = {index.rows[i]["id"] for i in holders}
        if wanted & set(ids):
            print("\nThe expected chunk is retrieved. If the answer was still "
                  "wrong, the model ignored what it was given — a prompt "
                  "problem, not a retrieval one.")
        else:
            full = index.keyword_scores(args.question, mask)
            place = next((n for n, (i, _) in enumerate(full, start=1)
                          if index.rows[i]["id"] in wanted), None)
            where = f"rank {place} by keyword score" if place else "unranked"
            print(f"\nThe expected chunk is NOT in the top {args.top} "
                  f"({where}).")
            print("  -> the content exists but loses to other chunks. Make it "
                  "its own smaller chunk, or phrase it the way the question "
                  "is asked — Arabic plurals do not match their singulars.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
