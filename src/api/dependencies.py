"""FastAPI dependencies: current user (JWT) and per-user rate limit."""
from fastapi import Depends, Header

from src.security.auth import User, decode_token
from src.security.rate_limiter import rate_limiter
from src.utils.errors import AuthError
from src.utils.logger import user_var


async def current_user(authorization: str = Header(default="")) -> User:
    if not authorization.lower().startswith("bearer "):
        raise AuthError("missing bearer token")
    user = decode_token(authorization.split(" ", 1)[1].strip())
    user_var.set(user.username)
    return user


async def rate_limited_user(user: User = Depends(current_user)) -> User:
    await rate_limiter.acquire(user.username, user.role)  # raises RateLimitError -> 429
    return user
