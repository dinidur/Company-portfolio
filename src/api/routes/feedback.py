"""Feedback loop (bonus): thumbs up/down -> SQLite + LangSmith feedback on the same run."""
from fastapi import APIRouter, Depends

from config.settings import settings
from src.api.dependencies import current_user
from src.api.schemas import FeedbackRequest
from src.memory import store
from src.security.auth import User
from src.utils.logger import get_logger

router = APIRouter(prefix="/feedback", tags=["feedback"])
log = get_logger("api.feedback")


@router.post("")
async def feedback(body: FeedbackRequest, user: User = Depends(current_user)):
    await store.save_feedback(body.run_id, user.username, body.score, body.comment)
    sent = False
    if settings.LANGSMITH_TRACING:
        try:
            from langsmith import Client
            Client().create_feedback(run_id=body.run_id, key="user_score", score=body.score, comment=body.comment)
            sent = True
        except Exception as e:  # feedback must never break the UI
            log.warning("langsmith feedback failed", extra={"error": str(e)})
    return {"saved": True, "langsmith": sent}


@router.get("/summary")
async def summary(user: User = Depends(current_user)):
    rows = await store.run("SELECT score, COUNT(*) AS n FROM feedback GROUP BY score")
    return {("up" if r["score"] else "down"): r["n"] for r in rows}
