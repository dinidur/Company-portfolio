"""
Research agent - Recursive Language Model (RLM) pattern.

Instead of stuffing all documents into one prompt, the agent works like a programmer
exploring a big dataset:

  1. EXPLORE   look at the catalog (metadata only, no text) - how many docs, types, dates
  2. SEARCH    one hybrid search to score how relevant each document is
  3. PLAN      the LLM writes a small PYTHON program that selects the documents   (code runs in the sandbox)
  4. DECOMPOSE selected docs are split into batches
  5. RECURSE   each batch goes to a sub-agent that retrieves only the TARGETED sections.
               If a batch is still too big for one call, it splits itself and calls
               sub-agents again (depth + 1)
  6. AGGREGATE python counts (structured analysis) + LLM summary of all findings

Example: "Summarize all outage reports related to payment failures during the last year
and identify recurring root causes."
"""
import asyncio
import json
import re
from collections import Counter
from datetime import date, timedelta

from langchain_core.messages import HumanMessage
from langsmith import traceable

from config.settings import settings
from src.agents.base_agent import agent_node, today, user_from_state
from src.llm.client import ainvoke, parse_json
from src.prompts.system_prompts import RLM_AGGREGATE_PROMPT, RLM_BATCH_PROMPT, RLM_PLANNER_PROMPT
from src.rag.hybrid_search import hybrid_search
from src.rag.loader import document_catalog
from src.security.guardrails import sanitize_retrieved
from src.tools.python_analysis import run_analysis
from src.utils.events import emit
from src.utils.logger import get_logger

log = get_logger("rlm")


# ------------------------------------------------------------------ 1. explore
def explore(access_levels: list[str]) -> tuple[list[dict], dict]:
    catalog = [dict(d) for d in document_catalog() if d["access_level"] in access_levels]
    stats = {
        "documents": len(catalog),
        "by_type": dict(Counter(d["document_type"] for d in catalog)),
        "by_department": dict(Counter(d["department"] for d in catalog)),
        "by_year": dict(Counter(d["created_date"][:4] for d in catalog)),
        "total_chars": sum(d["chars"] for d in catalog),
    }
    return catalog, stats


# ------------------------------------------------------------------ 3. plan
def default_plan(filters: dict, question: str) -> str:
    """Used when the LLM is not available or writes bad code."""
    q = question.lower()
    conds = ['d["relevance"] >= cutoff']
    doc_type = filters.get("document_type") or ("incident" if re.search(r"incident|outage", q) else None)
    if doc_type:
        conds.append(f'd["document_type"] == "{doc_type}"')
    since = filters.get("created_after") or (
        (date.today() - timedelta(days=365)).isoformat() if re.search(r"last (year|12 months)", q) else None)
    if since:
        conds.append(f'd["created_date"] >= "{since}"')
    return (
        "top = max([d['relevance'] for d in catalog] + [0.0001])\n"
        "cutoff = top * 0.35\n"
        f"docs = [d for d in catalog if {' and '.join(conds)}]\n"
        "docs = sorted(docs, key=lambda d: d['relevance'], reverse=True)\n"
        "result = [d['doc_id'] for d in docs][:15]\n"
    )


@traceable(name="rlm_plan")
async def make_plan(question: str, catalog: list[dict], stats: dict, filters: dict) -> tuple[str, list[str], str]:
    source = "llm"
    try:
        example = {k: catalog[0][k] for k in ("doc_id", "title", "document_type", "department", "created_date",
                                              "tags", "relevance")} if catalog else {}
        raw = await ainvoke([HumanMessage(RLM_PLANNER_PROMPT.format(
            question=question, today=today(), stats=json.dumps(stats), example=json.dumps(example)))],
            purpose="rlm_planner")
        code = re.sub(r"```(?:python)?", "", raw).strip()
        selected = await run_analysis(code, {"catalog": catalog})
        if not isinstance(selected, list) or not selected:
            raise ValueError("plan returned no documents")
    except Exception as e:
        source = f"default ({type(e).__name__})"
        code = default_plan(filters, question)
        selected = await run_analysis(code, {"catalog": catalog})
    # security: the plan can only pick documents that are in THIS user's catalog
    allowed = {d["doc_id"] for d in catalog}
    selected = [str(s) for s in selected if str(s) in allowed][:15]
    return code, selected, source


# ------------------------------------------------------------------ 5. recursive sub-agents
def _extract_fallback(chunks: list[dict]) -> list[dict]:
    """No-LLM extraction: read the 'Root cause' section of each document."""
    by_doc: dict[str, dict] = {}
    for c in chunks:
        m = c["metadata"]
        row = by_doc.setdefault(m["doc_id"], {"doc_id": m["doc_id"], "title": m["title"],
                                              "date": m["created_date"], "finding": "", "root_cause": None,
                                              "impact": None})
        cat = re.search(r"Root cause category:\*\*\s*(.+)", c["text"])
        if cat:
            row["root_cause"] = cat.group(1).strip()
        if m.get("section") == "Root cause" or "## Root cause" in c["text"]:
            body = c["text"].split("## Root cause", 1)[-1]
            row["finding"] = re.sub(r"\s+", " ", body.split("**Root cause category", 1)[0]).strip()[:300]
        if m.get("section") == "Customer impact" and not row["impact"]:
            row["impact"] = re.sub(r"\s+", " ", c["text"].split("## Customer impact", 1)[-1]).strip()[:200]
    for row in by_doc.values():
        row["finding"] = row["finding"] or row["title"]
    return list(by_doc.values())


@traceable(name="rlm_sub_agent")
async def analyze_batch(question: str, doc_ids: list[str], access_levels: list[str], depth: int,
                        sem: asyncio.Semaphore, label: str, want_causes: bool = False) -> list[dict]:
    # targeted retrieval: only the best sections of THESE documents, not whole documents
    queries = [question]
    if want_causes:
        queries.append("root cause category and corrective actions")  # task decomposition: 2nd targeted query
    results = await asyncio.gather(*[hybrid_search(q, access_levels, filters={"doc_id": doc_ids},
                                                   top_k=max(3, len(doc_ids) * 2), rerank=False) for q in queries])
    seen, hits = set(), []
    for r in results:
        for h in r["hits"]:
            if h["id"] not in seen:
                seen.add(h["id"])
                hits.append(h)
    chunks, _ = sanitize_retrieved(hits)
    chars = sum(len(c["text"]) for c in chunks)
    emit("rlm", step="sub_agent", batch=label, depth=depth, docs=doc_ids, sections=len(chunks), chars=chars)

    if chars > settings.RLM_MAX_CHARS_PER_CALL and len(doc_ids) > 1 and depth < settings.RLM_MAX_DEPTH:
        mid = len(doc_ids) // 2
        emit("rlm", step="recurse", batch=label, depth=depth, reason=f"{chars} chars > limit, splitting in 2")
        parts = await asyncio.gather(
            analyze_batch(question, doc_ids[:mid], access_levels, depth + 1, sem, label + ".a", want_causes),
            analyze_batch(question, doc_ids[mid:], access_levels, depth + 1, sem, label + ".b", want_causes))
        return [f for p in parts for f in p]

    docs_text = "\n\n".join(f'<document id="{c["metadata"]["doc_id"]}" date="{c["metadata"]["created_date"]}" '
                            f'title="{c["metadata"]["title"]}">\n{c["text"]}\n</document>' for c in chunks)
    async with sem:  # limit parallel LLM calls (free tier rate limits)
        try:
            raw = await ainvoke([HumanMessage(RLM_BATCH_PROMPT.format(depth=depth, task=question,
                                                                      documents=docs_text))],
                                purpose=f"rlm_sub_agent_d{depth}")
            findings = parse_json(raw)
            if not isinstance(findings, list):
                raise ValueError("expected a list")
            # a sub-agent may only report documents it was given (no hallucinated IDs)
            findings = [f for f in findings if isinstance(f, dict) and f.get("doc_id") in doc_ids]
            # keep the structured root cause category from the text if the LLM missed it
            fb = {f["doc_id"]: f for f in _extract_fallback(chunks)}
            for f in findings:
                if not f.get("root_cause") and fb.get(f["doc_id"], {}).get("root_cause"):
                    f["root_cause"] = fb[f["doc_id"]]["root_cause"]
        except Exception as e:
            emit("rlm", step="sub_agent_fallback", batch=label, reason=type(e).__name__)
            findings = _extract_fallback(chunks)
    emit("rlm", step="sub_agent_done", batch=label, depth=depth, findings=len(findings))
    return findings


# ------------------------------------------------------------------ 6. aggregate
COUNT_CODE = """counts = Counter([f["root_cause"] for f in data if f.get("root_cause")])
by_cause = {}
for f in data:
    if f.get("root_cause"):
        by_cause.setdefault(f["root_cause"], []).append(f["doc_id"])
result = {"root_cause_counts": dict(counts.most_common()), "documents_by_cause": by_cause,
          "documents_analysed": len(data)}
"""


@traceable(name="rlm_aggregate")
async def aggregate(question: str, findings: list[dict]) -> tuple[dict, str]:
    counts = await run_analysis(COUNT_CODE, {"data": findings})
    emit("rlm", step="aggregate", counts=counts["root_cause_counts"])
    try:
        summary = await ainvoke([HumanMessage(RLM_AGGREGATE_PROMPT.format(
            question=question, findings=json.dumps(findings, indent=1)[:12000], counts=json.dumps(counts)))],
            purpose="rlm_aggregate")
    except Exception as e:
        emit("rlm", step="aggregate_fallback", reason=type(e).__name__)
        lines = [f"Analysed {counts['documents_analysed']} documents."]
        for cause, n in counts["root_cause_counts"].items():
            lines.append(f"- {cause}: {n} time(s) ({', '.join(counts['documents_by_cause'][cause])})")
        summary = "\n".join(lines)
    return counts, summary


# ------------------------------------------------------------------ node
@agent_node("research", fallback={"research": {"error": "research failed"}})
async def research_node(state: dict) -> dict:
    user = user_from_state(state)
    question = state.get("standalone_question") or state["question"]
    filters = state.get("filters") or {}

    catalog, stats = explore(user.access_levels)
    emit("rlm", step="explore", **stats)

    search = await hybrid_search(question, user.access_levels, top_k=25, rerank=False)
    relevance: dict[str, float] = {}
    for h in search["hits"]:
        d = h["metadata"]["doc_id"]
        relevance[d] = max(relevance.get(d, 0.0), h["hybrid_score"])
    for d in catalog:
        d["relevance"] = round(relevance.get(d["doc_id"], 0.0), 4)
    emit("rlm", step="search", matched_docs=len(relevance))

    code, selected, plan_source = await make_plan(question, catalog, stats, filters)
    emit("rlm", step="plan", source=plan_source, code=code, selected=selected)
    if not selected:
        return {"research": {"summary": "No matching documents were found.", "selected": [], "findings": [],
                             "plan_code": code}, "plan_index": state.get("plan_index", 0) + 1}

    size = settings.RLM_BATCH_SIZE
    batches = [selected[i:i + size] for i in range(0, len(selected), size)]
    emit("rlm", step="decompose", batches=len(batches), batch_size=size)

    sem = asyncio.Semaphore(settings.RLM_CONCURRENCY)
    want_causes = bool(re.search(r"cause|why|reason", f"{question} {state['question']}", re.I))
    parts = await asyncio.gather(*[analyze_batch(question, b, user.access_levels, 1, sem, f"B{i + 1}", want_causes)
                                   for i, b in enumerate(batches)], return_exceptions=True)
    findings, failed = [], 0
    for p in parts:
        if isinstance(p, Exception):
            failed += 1  # one bad batch does not kill the research
            log.warning("batch failed", extra={"error": str(p)})
        else:
            findings.extend(p)

    counts, summary = await aggregate(question, findings)
    emit("rlm", step="done", findings=len(findings), failed_batches=failed)
    research = {"summary": summary, "selected": selected, "findings": findings, "counts": counts,
                "plan_code": code, "plan_source": plan_source, "batches": len(batches), "failed_batches": failed,
                "stats": stats}
    update = {"research": research, "plan_index": state.get("plan_index", 0) + 1}
    if failed:
        update["errors"] = [{"agent": "research", "error": f"{failed} batch(es) failed, summary is partial"}]
    return update
