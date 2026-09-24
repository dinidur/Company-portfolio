"""
Input guard node - first node of every turn.
Validates the request and checks for prompt injection BEFORE any LLM sees it.
"""
from src.agents.base_agent import agent_node
from src.agents.state import reset_turn
from src.memory import store
from src.security.guardrails import check_user_input
from src.utils.events import emit


@agent_node("input_guard")
async def input_guard_node(state: dict) -> dict:
    update = reset_turn()
    result = check_user_input(state.get("question", ""))
    update["guard"] = result
    update["question"] = result["cleaned"]
    emit("validation", stage="input", allowed=result["allowed"], score=result["score"],
         findings=result["findings"])
    if not result["allowed"]:
        update["blocked"] = True
        await store.audit(state["user"]["username"], "blocked_input",
                          {"findings": result["findings"], "question": state.get("question", "")[:300]})
    return update
