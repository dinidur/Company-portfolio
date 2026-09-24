"""
Base helpers for every agent node.

@agent_node("name") wraps a node so that:
- the UI gets "node started / node finished" events (Agent Activity panel)
- the node name is added to state.path (what route the graph took)
- if the node crashes, the error is recorded in state.errors and the graph
  CONTINUES with partial results (graceful degradation, no butterfly effect)
"""
import functools
from datetime import date

from langgraph.errors import GraphInterrupt

from src.security.auth import User
from src.utils.events import emit
from src.utils.helpers import Timer
from src.utils.logger import get_logger

log = get_logger("agents")


def agent_node(name: str, fallback: dict | None = None):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(state, *args, **kwargs):
            emit("node", node=name, status="started")
            with Timer() as t:
                try:
                    update = await fn(state, *args, **kwargs) or {}
                except GraphInterrupt:
                    raise  # human-in-the-loop pause, not an error
                except Exception as e:
                    log.exception("agent failed", extra={"agent": name})
                    emit("node", node=name, status="failed", error=f"{type(e).__name__}: {str(e)[:200]}")
                    update = dict(fallback or {})
                    update["errors"] = [{"agent": name, "error": f"{type(e).__name__}: {str(e)[:300]}"}]
            update.setdefault("path", [])
            update["path"] = list(update["path"]) + [name]
            emit("node", node=name, status="finished", ms=t.ms)
            return update
        return wrapper
    return deco


def user_from_state(state) -> User:
    return User(**state["user"])


def today() -> str:
    return date.today().isoformat()
