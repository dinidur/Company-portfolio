"""
Chat endpoints - Server-Sent Events (SSE) stream.

Each line is `data: {json}`. Event types:
  node / agent_state / retrieval / rlm / tool_call / memory / validation / approval  -> Agent Activity panel
  token          -> part of the answer (streaming)
  answer_reset   -> the validator asked for a retry, clear the answer box
  approval_required -> graph paused (human-in-the-loop), call /chat/resume
  done           -> final answer, sources, validation, path, run_id (for feedback / LangSmith)
  error          -> something failed, message is safe to show
"""
import json
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from config.settings import settings
from src.agents.orchestrator import graph
from src.api.dependencies import current_user, rate_limited_user
from src.api.schemas import ChatRequest, ResumeRequest
from src.security.auth import User
from src.utils.errors import AppError
from src.utils.logger import get_logger, session_var

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("api.chat")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _config(user: User, session_id: str, run_id: str) -> dict:
    # thread_id -> checkpointer (short-term memory). metadata.session_id -> LangSmith "threads" view
    return {"configurable": {"thread_id": f"{user.username}:{session_id}"}, "run_id": run_id,
            "run_name": "chat_turn", "tags": [f"role:{user.role}", settings.ENV],
            "metadata": {"user": user.username, "role": user.role, "session_id": session_id}}


async def _run_graph(inputs, config: dict, run_id: str):
    yield _sse({"type": "start", "run_id": run_id})
    try:
        async for mode, chunk in graph.astream(inputs, config, stream_mode=["custom", "updates"]):
            if mode == "custom":
                yield _sse(chunk)
            elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                payload = chunk["__interrupt__"][0].value
                yield _sse({"type": "approval_required", **payload, "run_id": run_id})
                return
        state = (await graph.aget_state(config)).values
        yield _sse({
            "type": "done", "run_id": run_id, "answer": state.get("answer", ""),
            "sources": [{k: s.get(k) for k in ("n", "doc_id", "title", "document_type", "created_date",
                                               "access_level", "sections")} for s in state.get("sources", [])],
            "validation": state.get("validation", {}), "path": state.get("path", []),
            "errors": state.get("errors", []), "intent": state.get("intent"), "plan": state.get("plan"),
        })
    except AppError as e:
        log.warning("graph app error", extra={"code": e.code, "error": str(e)})
        yield _sse({"type": "error", "code": e.code, "message": e.user_message})
    except Exception:
        log.exception("graph crashed")
        yield _sse({"type": "error", "code": "internal_error", "message": "Something went wrong. Please try again."})


@router.post("/stream")
async def chat_stream(body: ChatRequest, user: User = Depends(rate_limited_user)):
    session_var.set(body.session_id)
    run_id = str(uuid.uuid4())
    config = _config(user, body.session_id, run_id)
    inputs = {"messages": [HumanMessage(body.message)], "question": body.message, "user": user.public(),
              "session_id": body.session_id, "errors": [], "path": []}
    log.info("chat request", extra={"chars": len(body.message)})
    return StreamingResponse(_run_graph(inputs, config, run_id), media_type="text/event-stream")


@router.post("/resume")
async def chat_resume(body: ResumeRequest, user: User = Depends(current_user)):
    session_var.set(body.session_id)
    run_id = str(uuid.uuid4())
    config = _config(user, body.session_id, run_id)
    return StreamingResponse(_run_graph(Command(resume={"approved": body.approved}), config, run_id),
                             media_type="text/event-stream")


@router.get("/history/{session_id}")
async def history(session_id: str, user: User = Depends(current_user)):
    snapshot = await graph.aget_state({"configurable": {"thread_id": f"{user.username}:{session_id}"}})
    msgs = snapshot.values.get("messages", []) if snapshot.values else []
    return [{"role": m.type, "content": m.content} for m in msgs]
