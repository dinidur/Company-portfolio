"""
Vector stores.

PineconeStore  -> the real one (namespaces, metadata filter, dense+sparse hybrid).
LocalStore     -> in-memory copy of the same chunks. Used when:
                  1) no PINECONE_API_KEY (local dev / tests), or
                  2) Pinecone is down  -> the retriever falls back here (graceful degradation).

Both return the same Hit dict so the rest of the code does not care which one ran.
"""
import asyncio

import numpy as np

from config.settings import settings
from src.rag import embeddings
from src.rag.loader import NAMESPACES, load_chunk_cache
from src.utils.errors import VectorStoreError
from src.utils.logger import get_logger

log = get_logger("vector_store")


def sparse_dot(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    bmap = dict(zip(b["indices"], b["values"]))
    return float(sum(v * bmap.get(i, 0.0) for i, v in zip(a["indices"], a["values"])))


def match_filter(meta: dict, flt: dict | None) -> bool:
    """Small evaluator for the Pinecone filter syntax, so LocalStore behaves the same."""
    if not flt:
        return True
    for key, cond in flt.items():
        if key == "$and":
            if not all(match_filter(meta, c) for c in cond):
                return False
            continue
        if key == "$or":
            if not any(match_filter(meta, c) for c in cond):
                return False
            continue
        value = meta.get(key)
        if not isinstance(cond, dict):
            cond = {"$eq": cond}
        for op, target in cond.items():
            if op == "$eq" and not (value == target or (isinstance(value, list) and target in value)):
                return False
            if op == "$ne" and value == target:
                return False
            if op == "$in" and not (value in target or (isinstance(value, list) and set(value) & set(target))):
                return False
            if op == "$nin" and value in target:
                return False
            if op == "$gte" and not (value is not None and value >= target):
                return False
            if op == "$lte" and not (value is not None and value <= target):
                return False
            if op == "$gt" and not (value is not None and value > target):
                return False
            if op == "$lt" and not (value is not None and value < target):
                return False
    return True


def _hit(chunk_id, text, meta, dense, sparse, namespace, store):
    return {"id": chunk_id, "text": text, "metadata": meta, "dense_score": round(dense, 4),
            "sparse_score": round(sparse, 4), "namespace": namespace, "store": store}


class LocalStore:
    name = "local"

    def __init__(self):
        self.chunks = load_chunk_cache()
        texts = [c["text"] for c in self.chunks]
        log.info("building local store", extra={"chunks": len(texts)})
        self.dense = np.array(embeddings.embed_documents(texts), dtype=np.float32)
        self.sparse = embeddings.sparse_documents(texts)

    async def query(self, dense: list[float], sparse: dict, namespace: str, flt: dict | None, top_k: int):
        q = np.array(dense, dtype=np.float32)
        hits = []
        for i, c in enumerate(self.chunks):
            m = c["metadata"]
            if m["department"] != namespace or not match_filter(m, flt):
                continue
            d = float(self.dense[i] @ q)
            s = sparse_dot(sparse, self.sparse[i])
            hits.append(_hit(c["id"], c["text"], m, d, s, namespace, self.name))
        # same weighting idea as the Pinecone query; the retriever re-scores properly after
        a = settings.HYBRID_ALPHA
        hits.sort(key=lambda h: a * h["dense_score"] + (1 - a) * h["sparse_score"], reverse=True)
        return hits[:top_k]

    async def fetch_by_doc(self, doc_ids: list[str]) -> list[dict]:
        return [c for c in self.chunks if c["metadata"]["doc_id"] in doc_ids]


class PineconeStore:
    name = "pinecone"

    def __init__(self):
        from pinecone import Pinecone
        if not settings.PINECONE_API_KEY:
            raise VectorStoreError("PINECONE_API_KEY is not set")
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self.index = self.pc.Index(settings.PINECONE_INDEX)

    @staticmethod
    def _scale(dense: list[float], sparse: dict, alpha: float):
        """Pinecone hybrid trick: dotproduct index, weight dense by alpha and sparse by (1-alpha)."""
        return ([v * alpha for v in dense],
                {"indices": sparse["indices"], "values": [v * (1 - alpha) for v in sparse["values"]]})

    async def query(self, dense: list[float], sparse: dict, namespace: str, flt: dict | None, top_k: int):
        d_scaled, s_scaled = self._scale(dense, sparse, settings.HYBRID_ALPHA)
        kwargs = dict(top_k=top_k, vector=d_scaled, namespace=namespace, filter=flt,
                      include_metadata=True, include_values=True)
        if s_scaled["indices"]:
            kwargs["sparse_vector"] = s_scaled
        try:
            # pinecone client is sync -> run in a thread so the event loop is never blocked
            res = await asyncio.to_thread(self.index.query, **kwargs)
        except Exception as e:
            raise VectorStoreError(f"pinecone query failed: {e}", namespace=namespace) from e

        hits = []
        for m in res.matches:
            meta = dict(m.metadata or {})
            text = meta.pop("text", "")
            # recompute the two parts separately so the UI can explain the ranking
            values = list(m.values or [])
            dense_part = float(np.dot(values, dense)) if values else float(m.score)
            sv = getattr(m, "sparse_values", None)
            sv = {"indices": list(sv.indices), "values": list(sv.values)} if sv is not None and \
                hasattr(sv, "indices") else (sv or {})
            hits.append(_hit(m.id, text, meta, dense_part, sparse_dot(sparse, sv), namespace, self.name))
        return hits

    async def fetch_by_doc(self, doc_ids: list[str]) -> list[dict]:
        # we keep the same chunks locally (chunks.json), cheaper than a fetch per id
        return [c for c in load_chunk_cache() if c["metadata"]["doc_id"] in doc_ids]

    # ---------- ingestion ----------
    def ensure_index(self):
        from pinecone import ServerlessSpec
        if not self.pc.has_index(settings.PINECONE_INDEX):
            log.info("creating pinecone index", extra={"index": settings.PINECONE_INDEX})
            self.pc.create_index(name=settings.PINECONE_INDEX, dimension=settings.DENSE_DIM, metric="dotproduct",
                                 spec=ServerlessSpec(cloud=settings.PINECONE_CLOUD, region=settings.PINECONE_REGION))
        self.index = self.pc.Index(settings.PINECONE_INDEX)

    def upsert_chunks(self, chunks: list[dict]):
        texts = [c["text"] for c in chunks]
        dense = embeddings.embed_documents(texts)
        sparse = embeddings.sparse_documents(texts)
        by_ns: dict[str, list] = {}
        for c, d, s in zip(chunks, dense, sparse):
            meta = dict(c["metadata"])
            meta["text"] = c["text"]
            by_ns.setdefault(meta["department"], []).append(
                {"id": c["id"], "values": d, "sparse_values": s, "metadata": meta})
        for ns, vectors in by_ns.items():
            self.index.upsert(vectors=vectors, namespace=ns, batch_size=50)
            log.info("upserted", extra={"namespace": ns, "count": len(vectors)})


_primary = None
_local = None


def get_local_store() -> LocalStore:
    global _local
    if _local is None:
        _local = LocalStore()
    return _local


def get_primary_store():
    """Pinecone if configured, else local. Never raises - falls back to local."""
    global _primary
    if _primary is None:
        try:
            _primary = PineconeStore()
        except Exception as e:
            log.warning("pinecone not available, using local store", extra={"error": str(e)})
            _primary = get_local_store()
    return _primary


__all__ = ["NAMESPACES", "get_primary_store", "get_local_store", "match_filter"]
