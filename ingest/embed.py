"""Embed chunks with OpenAI and store the vectors alongside them.

Multilingual by necessity: an Arabic question has to retrieve an English
document and the reverse, since Zid's sources mix both — often inside one
page. `text-embedding-3-small` handles that and costs about a cent for this
corpus, so the model choice is not where the money goes.

Embeddings are cached by the chunk's content hash. Re-running after a
re-crawl only pays for chunks whose text actually changed.

Usage:
    OPENAI_API_KEY=... python embed.py corpus.jsonl --out vectors.npz
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from config import resolve_key
from vectors import unit

MODEL = "text-embedding-3-small"
DIMS = 1536
BATCH = 128
ENDPOINT = "https://api.openai.com/v1/embeddings"


def embed_batch(texts: list[str], key: str, retries: int = 5) -> list[list[float]]:
    payload = json.dumps({"model": MODEL, "input": texts}).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read())
            return [item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])]
        except urllib.error.HTTPError as exc:
            # 429 and 5xx are worth waiting out; a 400 never becomes valid.
            if exc.code < 429 or attempt == retries - 1:
                raise SystemExit(f"embedding failed ({exc.code}): {exc.read()[:300]!r}")
            time.sleep(2 ** attempt)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--cache", type=pathlib.Path, default=None,
                        help="reuse vectors from a previous run (default: --out)")
    args = parser.parse_args()

    with args.corpus.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    for row in rows:
        row["_key"] = hashlib.sha256(row["text"].encode()).hexdigest()[:16]

    cached: dict[str, np.ndarray] = {}
    cache_path = args.cache or args.out
    if cache_path.exists():
        store = np.load(cache_path, allow_pickle=True)
        cached = dict(zip(store["keys"].tolist(), store["vectors"]))
        print(f"cache: {len(cached)} vectors")

    todo = [r for r in rows if r["_key"] not in cached]
    print(f"{len(rows)} chunks, {len(todo)} to embed")

    try:
        key = resolve_key()
    except KeyError as exc:
        raise SystemExit(str(exc))
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        for row, vector in zip(batch, embed_batch([r["text"] for r in batch], key)):
            cached[row["_key"]] = np.asarray(vector, dtype=np.float32)
        print(f"  {min(start + BATCH, len(todo))}/{len(todo)}", flush=True)

    vectors = np.stack([cached[r["_key"]] for r in rows])
    # Pre-normalise so retrieval is a plain dot product.
    vectors = unit(vectors)
    np.savez_compressed(
        args.out,
        vectors=vectors,
        keys=np.array([r["_key"] for r in rows]),
        ids=np.array([r["id"] for r in rows]),
    )
    print(f"wrote {args.out}  shape={vectors.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
