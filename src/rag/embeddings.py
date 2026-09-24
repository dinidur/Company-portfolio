"""
Embeddings - all local and free (fastembed, ONNX, no GPU needed).

- dense  : BAAI/bge-small-en-v1.5 (384 dim) -> semantic meaning
- sparse : Qdrant/bm25 -> keyword / BM25 weights (good for IDs like INC-2026-0114)
- rerank : ms-marco MiniLM cross-encoder -> reorders the final candidates

Models are loaded once (lazy) because loading takes a few seconds.

EMBEDDING_BACKEND=hashing is a tiny offline backup (no model download). I use it
for unit tests / CI where HuggingFace is blocked. Quality is much lower, so do
not ingest to Pinecone with it.
"""
import math
import re
import zlib
from functools import lru_cache

import numpy as np

from config.settings import settings
from src.utils.logger import get_logger

log = get_logger("embeddings")


@lru_cache(maxsize=1)
def _dense_model():
    from fastembed import TextEmbedding
    log.info("loading dense model", extra={"model": settings.DENSE_MODEL})
    return TextEmbedding(settings.DENSE_MODEL)


@lru_cache(maxsize=1)
def _sparse_model():
    from fastembed import SparseTextEmbedding
    log.info("loading sparse model", extra={"model": settings.SPARSE_MODEL})
    return SparseTextEmbedding(settings.SPARSE_MODEL)


@lru_cache(maxsize=1)
def _rerank_model():
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    log.info("loading rerank model", extra={"model": settings.RERANK_MODEL})
    return TextCrossEncoder(settings.RERANK_MODEL)


# ---------------- offline hashing backend ----------------
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _hash_dense(text: str) -> list[float]:
    vec = np.zeros(settings.DENSE_DIM, dtype=np.float32)
    toks = _tokens(text)
    for tok in toks + [a + " " + b for a, b in zip(toks, toks[1:])]:
        h = zlib.crc32(tok.encode())
        vec[h % settings.DENSE_DIM] += 1.0 if (h >> 16) & 1 else -1.0
    norm = np.linalg.norm(vec) or 1.0
    return (vec / norm).tolist()


def _hash_sparse(text: str, query: bool = False) -> dict:
    counts: dict[int, float] = {}
    toks = _tokens(text)
    for tok in toks:
        idx = zlib.crc32(tok.encode()) % (2**31)
        counts[idx] = counts.get(idx, 0) + 1
    if query:
        return {"indices": list(counts), "values": [1.0] * len(counts)}
    k1, b, avgdl = 1.2, 0.75, 120
    dl = max(len(toks), 1)
    vals = [tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl)) for tf in counts.values()]
    return {"indices": list(counts), "values": vals}


def _hashing() -> bool:
    return settings.EMBEDDING_BACKEND == "hashing"


def embed_documents(texts: list[str]) -> list[list[float]]:
    if _hashing():
        return [_hash_dense(t) for t in texts]
    return [v.tolist() for v in _dense_model().embed(texts)]


def embed_query(text: str) -> list[float]:
    if _hashing():
        return _hash_dense(text)
    return next(iter(_dense_model().query_embed(text))).tolist()


def sparse_documents(texts: list[str]) -> list[dict]:
    if _hashing():
        return [_hash_sparse(t) for t in texts]
    return [{"indices": s.indices.tolist(), "values": s.values.tolist()} for s in _sparse_model().embed(texts)]


def sparse_query(text: str) -> dict:
    if _hashing():
        return _hash_sparse(text, query=True)
    s = next(iter(_sparse_model().query_embed(text)))
    return {"indices": s.indices.tolist(), "values": s.values.tolist()}


def rerank(query: str, texts: list[str]) -> list[float]:
    if _hashing():  # no cross-encoder offline -> simple token overlap
        q = set(_tokens(query))
        return [len(q & set(_tokens(t))) / math.sqrt(len(q) or 1) for t in texts]
    return [float(x) for x in _rerank_model().rerank(query, texts)]
