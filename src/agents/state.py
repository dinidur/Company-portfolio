"""
Shared LangGraph state.

Design rule (to stop the "butterfly effect"): every agent writes ONLY its own keys.
- supervisor -> intent, plan, sub_tasks, filters, standalone_question
- retrieval  -> retrieval
- research   -> research
- tools      -> tool_calls, tool_results
- response   -> answer, sources
- validator  -> validation
Errors are APPENDED to `errors` (reducer), never overwrite another agent's output.
So a failure in one agent can't silently change what another agent produced; the
response agent sees the partial results + the error list and says what is missing.
"""
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    # conversation (short-term memory, saved by the checkpointer per session/thread)
    messages: Annotated[list, add_messages]
    question: str
    user: dict
    session_id: str

    # input guard
    blocked: bool
    guard: dict

    # memory
    user_profile: dict
    past_interactions: list

    # supervisor
    intent: str
    standalone_question: str
    plan: list[str]
    plan_index: int
    sub_tasks: list[str]
    filters: dict
    plan_reason: str

    # agents
    retrieval: dict
    research: dict
    tool_calls: list
    tool_results: list
    approval: dict

    # response + validation
    sources: list
    answer: str
    validation: dict
    attempts: int

    # bookkeeping (reducers -> append only)
    errors: Annotated[list, operator.add]
    path: Annotated[list, operator.add]


def reset_turn() -> dict[str, Any]:
    """Per-turn keys are cleared at the start of every turn (messages are kept)."""
    return {
        "blocked": False, "guard": {}, "intent": "", "standalone_question": "", "plan": [], "plan_index": 0,
        "sub_tasks": [], "filters": {}, "plan_reason": "", "retrieval": {}, "research": {}, "tool_calls": [],
        "tool_results": [], "approval": {}, "sources": [], "answer": "", "validation": {}, "attempts": 0,
    }
