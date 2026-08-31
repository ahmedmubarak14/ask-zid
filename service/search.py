"""Hybrid retrieval over the chunked corpus.

Vector search alone misses exact terms — package names, carrier names,
ticket codes, figures like 2,300 — because an embedding blurs precisely the
tokens a merchant quotes. Keyword search alone misses paraphrase, which in
Arabic is most of the traffic: "كم تاخذون عمولة" and "ما هي رسوم المعاملات"
share no words at all. Neither is sufficient, so both run and their rankings
are fused.

Fusion is Reciprocal Rank Fusion: each list contributes 1/(k+rank), which
combines orderings without needing the two scores to be on a comparable
scale. Cosine similarity and a keyword overlap count are not comparable, and
normalising them against each other invents a relationship that isn't there.

No database. At this corpus size the vectors are a few megabytes and a dot
product over all of them takes milliseconds — pgvector earns its place at
production scale, not here.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from vectors import sanitise

RRF_K = 60
ARABIC_PREFIXES = ("ال", "بال", "كال", "فال", "وال", "لل")


def normalise_token(token: str) -> str:
    """Fold the orthographic variation that would otherwise split a term.

    Arabic writes the same word several ways — أ/إ/آ for alef, ة/ه word-final,
    ى/ي — and merchants type all of them. The definite article is stripped so
    "الباقة" and "باقة" match.
    """
    token = re.sub(r"[إأآا]", "ا", token)
    token = token.replace("ة", "ه").replace("ى", "ي").replace("ـ", "")
    for prefix in ARABIC_PREFIXES:
        if len(token) > len(prefix) + 2 and token.startswith(prefix):
            return token[len(prefix):]
    return token


def tokenise(text: str) -> list[str]:
    return [normalise_token(t) for t in re.findall(r"[\w؀-ۿ]+", text.lower())]


class Index:
    """The corpus, and its vectors once they exist.

    Vectors are optional at construction. The server has to be able to start
    and explain itself before anything is embedded — refusing to boot without
    a vectors file means the only way to build one is the command line, which
    is the friction this avoids.
    """

    def __init__(self, corpus_path: pathlib.Path,
                 vectors_path: pathlib.Path | None = None):
        self.rows = [json.loads(line) for line in corpus_path.open(encoding="utf-8")]
        self.n = len(self.rows)
        self.vectors: np.ndarray | None = None
        if vectors_path and vectors_path.exists():
            store = np.load(vectors_path, allow_pickle=True)
            vectors = store["vectors"]
            # A stale vectors file is worse than none: it would silently
            # answer from the wrong chunks. Mismatched length means rebuild.
            if len(vectors) == self.n:
                vectors, broken = sanitise(vectors)
                if broken:
                    print(f"warning: {broken} vector(s) were not finite and "
                          f"have been zeroed; rebuild the index to fix")
                self.vectors = vectors

        self.tokens = [set(tokenise(r["text"])) for r in self.rows]
        # Rarer words should count for more; without it every chunk containing
        # "زد" scores alike and the signal is lost in the most common term.
        self.df: dict[str, int] = {}
        for token_set in self.tokens:
            for token in token_set:
                self.df[token] = self.df.get(token, 0) + 1

    @property
    def ready(self) -> bool:
        return self.vectors is not None

    def _allowed(self, audience: str, country: str | None) -> np.ndarray:
        mask = np.ones(self.n, dtype=bool)
        if audience != "internal":
            mask &= np.array([r.get("audience") == audience for r in self.rows])
        if country:
            # Chunks with no country apply everywhere; a tagged chunk only
            # answers for its own market, so Saudi and Egyptian pricing can
            # never be merged into one answer.
            mask &= np.array([
                r.get("country") in (None, country) for r in self.rows
            ])
        return mask

    def keyword_scores(self, query: str, mask: np.ndarray) -> list[tuple[int, float]]:
        terms = set(tokenise(query))
        scored = []
        for i in np.flatnonzero(mask):
            overlap = terms & self.tokens[i]
            if not overlap:
                continue
            score = sum(np.log(1 + self.n / self.df[t]) for t in overlap)
            scored.append((int(i), float(score)))
        return sorted(scored, key=lambda x: -x[1])

    def vector_scores(self, query_vector: np.ndarray,
                      mask: np.ndarray) -> list[tuple[int, float]]:
        sims = self.vectors @ query_vector
        sims[~mask] = -np.inf
        order = np.argsort(-sims)[:100]
        return [(int(i), float(sims[i])) for i in order if np.isfinite(sims[i])]

    def search(self, query: str, query_vector: np.ndarray | None, k: int = 6,
               audience: str = "internal", country: str | None = None) -> list[dict]:
        mask = self._allowed(audience, country)
        if not mask.any():
            return []
        lists = [self.keyword_scores(query, mask)]
        if self.vectors is not None and query_vector is not None:
            lists.insert(0, self.vector_scores(query_vector, mask))
        ranked: dict[int, float] = {}
        for results in lists:
            for rank, (index, _) in enumerate(results[:50]):
                ranked[index] = ranked.get(index, 0.0) + 1.0 / (RRF_K + rank + 1)

        out = []
        for index, score in sorted(ranked.items(), key=lambda x: -x[1])[:k]:
            row = dict(self.rows[index])
            row["score"] = score
            out.append(row)
        return out
