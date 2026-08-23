from __future__ import annotations

import hashlib
import math
from typing import Sequence


DIM = 384


def _stable_bucket(gram: str, dim: int) -> int:
    digest = hashlib.md5(gram.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little") % dim


def embed_texts(texts: Sequence[str], *, dim: int = DIM) -> list[list[float]]:
    """Offline character n-gram hashing embedder (Chinese-friendly, no weights)."""
    out: list[list[float]] = []
    for raw in texts:
        text = (raw or "").lower()
        vec = [0.0] * dim
        if not text:
            out.append(vec)
            continue
        for n in (2, 3):
            if len(text) < n:
                continue
            for i in range(len(text) - n + 1):
                vec[_stable_bucket(text[i : i + n], dim)] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        out.append([x / norm for x in vec])
    return out


class CharNgramEmbeddingFunction:
    """Drop-in for Chroma `embedding_function`; no network, no model files."""

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def name(self) -> str:
        return "char_ngram_v1"

    def __call__(self, input: Sequence[str]) -> list[list[float]]:
        return embed_texts(input, dim=self.dim)
