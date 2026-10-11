"""Embedding backends.

`st`   sentence-transformers (default BAAI/bge-small-en-v1.5; all-MiniLM-L6-v2 also supported).
`hash` dependency-free feature hashing; used by unit tests and as an offline smoke backend only.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_models: dict[str, object] = {}
_lock = threading.Lock()


class HashEmbedder:
    dim = 384
    name = "hash-384"

    def _vec(self, text: str) -> list[float]:
        toks = re.findall(r"[a-z0-9]+", text.lower())
        feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
        v = [0.0] * self.dim
        for f in feats:
            h = int(hashlib.md5(f.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) % 2 else -1.0
            v[(h >> 8) % self.dim] += 0.5
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def encode_docs(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def encode_query(self, text: str) -> list[float]:
        return self._vec(text)


class STEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self.model = SentenceTransformer(model_name)
        self.is_bge = "bge" in model_name.lower()

    def encode_docs(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()

    def encode_query(self, text: str) -> list[float]:
        t = (BGE_QUERY_PREFIX + text) if self.is_bge else text
        return self.model.encode([t], normalize_embeddings=True, show_progress_bar=False)[0].tolist()


def get_embedder(backend: str, model_name: str):
    key = f"{backend}:{model_name}"
    with _lock:
        if key not in _models:
            _models[key] = HashEmbedder() if backend == "hash" else STEmbedder(model_name)
        return _models[key]
