"""Minimal in-process rate limiting for abuse-prone public endpoints
(signup, login).

Fixed-window counter keyed by client IP + bucket name. Dependency-free so it
bundles cleanly into the packaged binary. NOTE: the store is per-process, so a
multi-instance hosted deployment should move this to Redis for a global limit —
until then each instance enforces its own window (still a meaningful brake).
"""
import threading
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, status

from .config import get_settings


class _FixedWindow:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.time()
        cutoff = now - window
        with self._lock:
            hits = [t for t in self._hits[key] if t > cutoff]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


_store = _FixedWindow()


def _client_ip(request: Request) -> str:
    # Behind a proxy/load balancer the real client is the first X-Forwarded-For
    # hop; fall back to the socket peer for direct connections.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, limit: int, window_seconds: float):
    """Build a FastAPI dependency enforcing `limit` requests per
    `window_seconds` per client IP for the named bucket."""

    def dep(request: Request) -> None:
        if not get_settings().rate_limit_enabled:
            return
        key = f"{bucket}:{_client_ip(request)}"
        if not _store.allow(key, limit, window_seconds):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests — please slow down and try again shortly.",
            )

    return Depends(dep)
