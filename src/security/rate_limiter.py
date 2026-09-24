"""
Token Bucket rate limiter (per user).

Each user has a bucket with `capacity` tokens. Every request takes 1 token.
Tokens come back at `refill_per_sec`. So a user can burst up to `capacity`
requests, then is limited to the refill rate.

In-memory is fine for one API instance. For many instances move the bucket to
Redis (same logic in a Lua script) - noted in docs/assumptions.md.
"""
import asyncio
import time
from dataclasses import dataclass

from config.settings import settings
from src.security.rbac import rate_limit_for
from src.utils.errors import RateLimitError


@dataclass
class Bucket:
    capacity: float
    refill_per_sec: float
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(self):
        self._buckets: dict[str, Bucket] = {}
        self._lock = asyncio.Lock()

    def _new_bucket(self, role: str | None) -> Bucket:
        cfg = rate_limit_for(role) if role else {}
        cap = float(cfg.get("capacity", settings.RATE_LIMIT_CAPACITY))
        rate = float(cfg.get("refill_per_sec", settings.RATE_LIMIT_REFILL_PER_SEC))
        return Bucket(cap, rate, cap, time.monotonic())

    async def acquire(self, user_key: str, role: str | None = None, cost: float = 1.0) -> dict:
        async with self._lock:
            b = self._buckets.get(user_key) or self._buckets.setdefault(user_key, self._new_bucket(role))
            now = time.monotonic()
            b.tokens = min(b.capacity, b.tokens + (now - b.updated) * b.refill_per_sec)
            b.updated = now
            if b.tokens < cost:
                retry_after = round((cost - b.tokens) / b.refill_per_sec, 1)
                raise RateLimitError("rate limit exceeded", retry_after=retry_after, capacity=b.capacity)
            b.tokens -= cost
            return {"remaining": int(b.tokens), "capacity": int(b.capacity)}

    def status(self, user_key: str) -> dict:
        b = self._buckets.get(user_key)
        return {"tokens": round(b.tokens, 2), "capacity": b.capacity} if b else {}


rate_limiter = TokenBucketLimiter()
