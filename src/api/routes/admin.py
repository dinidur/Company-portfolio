from fastapi import APIRouter, Depends

from src.api.dependencies import current_user
from src.memory import store
from src.security.auth import User
from src.security.rate_limiter import rate_limiter
from src.utils.errors import PermissionDeniedError

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise PermissionDeniedError("admin only")
    return user


@router.get("/audit")
async def audit(limit: int = 30, user: User = Depends(_admin)):
    return await store.recent_audit(min(limit, 200))


@router.get("/rate-limit/{username}")
async def rate_status(username: str, user: User = Depends(_admin)):
    return rate_limiter.status(username)
