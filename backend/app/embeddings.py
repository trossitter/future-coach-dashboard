"""Local embeddings via fastembed (ONNX bge-small) — 384-dim, no PyTorch.

The model (~130MB) downloads lazily on first use and is cached in the image's
HF cache dir. Kept local so concept resolution costs no API calls or tokens.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(model_name=MODEL_NAME)


def embed(texts: list[str]) -> list[list[float]]:
    """Embed documents/passages; returns plain float lists (Neo4j-friendly)."""
    return [v.tolist() for v in _model().embed(list(texts))]


# bge-small-en-v1.5's canonical retrieval instruction. fastembed's query_embed()
# does NOT apply it for this model, so we prepend it ourselves.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def embed_query(text: str) -> list[float]:
    """Embed a search query with the bge instruction prefix applied."""
    return embed([BGE_QUERY_INSTRUCTION + text])[0]


def cosine(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))
