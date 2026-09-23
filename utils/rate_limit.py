import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    """In-memory sliding-window limiter for the public (unauthenticated)
    Workframe endpoints — the chat widget and the free audit both spend
    OpenAI credits per call, so an open endpoint needs a ceiling. Per-process
    only, which is fine for BFN's single Railway instance."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests — try again in a bit.")
        hits.append(now)


def client_ip(request: Request) -> str:
    # Railway sits behind a proxy; the left-most X-Forwarded-For entry is the client.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
