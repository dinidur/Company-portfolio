"""
Hybrid retrieval = dense (meaning) + sparse (BM25 keywords), then rerank.

    final hybrid score = alpha * dense_score + (1 - alpha) * normalised sparse_score

Why both: dense finds "payment outage" when the doc says "card gateway timeouts";
sparse finds exact IDs like "INC-2026-0114" that embeddings are weak at.

Security: the access_level filter is built HERE from the user's role (server side).
The LLM can add topic filters, but it can never remove the access filter.
"""
import asyncio

from langsmith import traceable

from config.settings import settings
from src.rag import embeddings
from src.rag.vector_store import NAMESPACES, get_local_store, get_primary_store
from src.utils.errors import VectorStoreError
from src.utils.events import emit
from src.utils.helpers import Timer, to_epoch
from src.utils.logger import get_logger

log = get_logger("retrieval")

ALLOWED_FILTER_KEYS = {"document_type", "department", "doc_id", "tags", "created_after", "created_before"}


def build_filter(access_levels: list[str], filters: dict | None = None) -> dict:
    """Role filter is always first and always there."""
    parts = [{"access_level": {"$in": access_levels}}]
    for key, value in (filters or {}).items():
        if key not in ALLOWED_FILTER_KEYS or value in (None, "", []):
            continue  # ignore unknown keys from the LLM (tool parameter validation)
        if key == "created_after":
            parts.append({"created_ts": {"$gte": to_epoch(value)}})
        elif key == "created_before":
            parts.append({"created_ts": {"$lte": to_epoch(value)}})
        elif isinstance(value, list):
            parts.append({key: {"$in": value}})
        else:
            parts.append({key: {"$eq": value}})
    return parts[0] if len(parts) == 1 else {"$and": parts}


def _combine(hits: list[dict], alpha: float) -> list[dict]:
    max_sparse = max([h["sparse_score"] for h in hits] + [1e-9])
    for h in hits:
        h["sparse_norm"] = round(h["sparse_score"] / max_sparse, 4)
        h["hybrid_score"] = round(alpha * h["dense_score"] + (1 - alpha) * h["sparse_norm"], 4)
    return sorted(hits, key=lambda h: h["hybrid_score"], reverse=True)


@traceable(run_type="retriever", name="hybrid_search")
async def hybrid_search(query: str, access_levels: list[str], namespaces: list[str] | None = None,
                        filters: dict | None = None, top_k: int | None = None, rerank: bool = True) -> dict:
    top_k = top_k or settings.TOP_K
    namespaces = [n for n in (namespaces or NAMESPACES) if n in NAMESPACES] or NAMESPACES
    flt = build_filter(access_levels, filters)
    emit("retrieval", status="started", query=query, namespaces=namespaces, filter=flt)

    with Timer() as t_embed:
        dense, sparse = await asyncio.gather(asyncio.to_thread(embeddings.embed_query, query),
                                             asyncio.to_thread(embeddings.sparse_query, query))

    store = get_primary_store()
    degraded = False
    with Timer() as t_search:
        try:
            # one query per namespace, all in parallel (async retrieval)
            results = await asyncio.gather(*[store.query(dense, sparse, ns, flt, settings.CANDIDATE_K)
                                             for ns in namespaces])
        except VectorStoreError as e:
            log.error("vector db failed, falling back to local store", extra={"error": str(e)})
            emit("retrieval", status="degraded", reason="Pinecone unavailable - using local fallback index")
            degraded = True
            local = await asyncio.to_thread(get_local_store)
            results = await asyncio.gather(*[local.query(dense, sparse, ns, flt, settings.CANDIDATE_K)
                                             for ns in namespaces])

    hits = _combine([h for r in results for h in r], settings.HYBRID_ALPHA)[: settings.CANDIDATE_K]

    reranked = False
    if rerank and settings.RERANK_ENABLED and len(hits) > 1:
        try:
            scores = await asyncio.to_thread(embeddings.rerank, query, [h["text"] for h in hits])
            for h, s in zip(hits, scores):
                h["rerank_score"] = round(s, 4)
            hits.sort(key=lambda h: h["rerank_score"], reverse=True)
            reranked = True
        except Exception as e:  # reranker is a bonus, never fail the request for it
            log.warning("rerank failed", extra={"error": str(e)})

    hits = hits[:top_k]
    summary = {
        "query": query, "store": "local-fallback" if degraded else store.name, "degraded": degraded,
        "reranked": reranked, "namespaces": namespaces, "filter": flt, "embed_ms": t_embed.ms,
        "search_ms": t_search.ms, "count": len(hits),
    }
    emit("retrieval", status="done", **summary, top=[
        {"id": h["id"], "title": h["metadata"]["title"], "dense": h["dense_score"], "sparse": h["sparse_norm"],
         "hybrid": h["hybrid_score"], "rerank": h.get("rerank_score")} for h in hits])
    log.info("hybrid search", extra=summary)
    return {"hits": hits, **summary}
