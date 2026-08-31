"""Normalising embeddings without producing NaN.

Dividing by a norm is the obvious way to make unit vectors and it is wrong
whenever a norm is zero: the row becomes NaN, is written to disk, and every
later dot product against it is NaN too. numpy says so only as a
RuntimeWarning, so retrieval keeps running and quietly ranks by garbage —
a failure that looks like poor answer quality rather than a bug.

A zero vector can arrive from an all-whitespace chunk or a truncated
response. Such a row is left as zeros, which scores 0 against every query
and simply never retrieves, instead of contaminating the whole matrix.
"""

from __future__ import annotations

import numpy as np


def unit(vectors: np.ndarray) -> np.ndarray:
    """Row-normalise, leaving zero rows as zeros rather than NaN."""
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim == 1:
        norm = float(np.linalg.norm(array))
        return array / norm if norm > 0 else array
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return np.divide(array, norms, out=np.zeros_like(array), where=norms > 0)


def sanitise(vectors: np.ndarray) -> tuple[np.ndarray, int]:
    """Replace any non-finite value, reporting how many rows were affected.

    Applied to vectors loaded from disk: a file written before this guard
    existed can still contain NaN, and silently answering from it is worse
    than saying so.
    """
    array = np.asarray(vectors, dtype=np.float32)
    bad = ~np.isfinite(array).all(axis=1)
    if bad.any():
        array = np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)
    return array, int(bad.sum())
