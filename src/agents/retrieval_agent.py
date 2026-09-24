"""
Retrieval agent - RAG operations + vector search.

Uses the standalone question from the supervisor (so follow-ups like
"and what was the fix?" still search well), the supervisor's topic filters
and the user's access levels (server side).
"""
from src.agents.base_agent import agent_node, user_from_state
from src.rag.hybrid_search import hybrid_search
from src.security.guardrails import sanitize_retrieved
from src.utils.errors import VectorStoreError
from src.utils.events import emit


def _next(state):
    return state.get("plan_index", 0) + 1


@agent_node("retrieval")
async def retrieval_node(state: dict) -> dict:
    user = user_from_state(state)
    query = state.get("standalone_question") or state["question"]
    try:
        res = await hybrid_search(query, user.access_levels, filters=state.get("filters"))
        # if the topic filter was too strict, try again without it (only access filter stays)
        if not res["hits"] and state.get("filters"):
            emit("retrieval", status="retry", reason="no hits with filters, retrying without topic filters")
            res = await hybrid_search(query, user.access_levels)
    except VectorStoreError as e:
        return {"retrieval": {"hits": [], "error": str(e)}, "plan_index": _next(state),
                "errors": [{"agent": "retrieval", "error": "knowledge base unavailable"}]}

    hits, flagged = sanitize_retrieved(res["hits"])
    if flagged:
        emit("validation", stage="retrieved_content", injection_removed=flagged)
    res["hits"] = hits
    res["injection_flags"] = flagged
    return {"retrieval": res, "plan_index": _next(state)}
