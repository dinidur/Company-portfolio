"""
Memory nodes.
- memory_load : at the start - loads long-term profile + relevant past interactions
- memory_save : at the end   - adds the answer to the conversation and saves long-term memory
"""
from langchain_core.messages import AIMessage

from src.agents.base_agent import agent_node
from src.memory import long_term
from src.utils.events import emit
from src.utils.helpers import short


@agent_node("memory_load", fallback={"user_profile": {}, "past_interactions": []})
async def memory_load_node(state: dict) -> dict:
    user = state["user"]["username"]
    profile = await long_term.load_profile(user)
    past = await long_term.relevant_interactions(user, state["question"], state.get("session_id", ""))
    turns = sum(1 for m in state.get("messages", []) if m.type == "human")
    emit("memory", action="loaded", session_turns=turns, profile=profile.get("frequent_topics", []),
         past_interactions=len(past))
    return {"user_profile": profile, "past_interactions": past}


@agent_node("memory_save")
async def memory_save_node(state: dict) -> dict:
    answer = state.get("answer") or ""
    topics = []
    for h in (state.get("retrieval") or {}).get("hits", [])[:3]:
        topics.append(h["metadata"]["document_type"])
    if state.get("research"):
        topics.append("research")
    if state.get("tool_results"):
        topics.extend(r["tool"] for r in state["tool_results"])
    if not state.get("blocked"):
        top = await long_term.save_interaction(state["user"]["username"], state.get("session_id", ""),
                                               state["question"], short(answer, 380), sorted(set(topics)))
    else:
        top = []
    emit("memory", action="saved", short_term="answer added to session", long_term_topics=top)
    return {"messages": [AIMessage(content=answer)]}
