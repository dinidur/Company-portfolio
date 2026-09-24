"""
Agent activity events -> the Streamlit "Agent Activity" panel.

Inside a LangGraph run, get_stream_writer() gives us the "custom" stream.
The API forwards these events to the UI as Server-Sent Events.
Outside a graph run (scripts / tests) emit() just does nothing.
"""
import time

from src.utils.logger import get_logger

log = get_logger("events")


def emit(kind: str, **data) -> None:
    event = {"type": kind, "ts": round(time.time(), 3), **data}
    log.debug("event", extra={"event": event})
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(event)
    except Exception:
        pass  # not inside a graph run
