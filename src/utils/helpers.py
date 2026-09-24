import re
import time
from datetime import datetime


def to_epoch(date_str: str) -> int:
    """'2025-01-01' -> epoch seconds. Pinecone filters need numbers for $gte/$lte."""
    return int(datetime.strptime(str(date_str)[:10], "%Y-%m-%d").timestamp())


def short(text: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


class Timer:
    """with Timer() as t: ...  ->  t.ms"""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self._start) * 1000, 1)
