"""
Short-term memory = the conversation of this session.

Stored by the LangGraph checkpointer (thread_id = session_id), so it survives
every turn of the session automatically. We only send the LAST N turns to the
LLM (token cost + less noise); older turns are still in the checkpoint.
"""
from langchain_core.messages import AIMessage, HumanMessage

from config.settings import settings
from src.utils.helpers import short


def format_history(messages: list, turns: int | None = None, exclude_last: bool = True) -> str:
    turns = turns or settings.SHORT_TERM_TURNS
    msgs = list(messages or [])
    if exclude_last and msgs and isinstance(msgs[-1], HumanMessage):
        msgs = msgs[:-1]
    msgs = msgs[-turns * 2:]
    lines = []
    for m in msgs:
        who = "User" if isinstance(m, HumanMessage) else "Assistant" if isinstance(m, AIMessage) else "System"
        lines.append(f"{who}: {short(str(m.content), 400)}")
    return "\n".join(lines) or "(no earlier messages)"


def previous_questions(messages: list) -> list[str]:
    return [str(m.content) for m in messages or [] if isinstance(m, HumanMessage)][:-1]
