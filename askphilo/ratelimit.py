"""Tiny in-process sliding-window rate limiter.

Render runs a single Gunicorn worker, so process memory is enough to
slow down password guessing and runaway chat requests (API cost).
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _window(self, key: str, window: float) -> deque:
        now = self._clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        return q

    def allowed(self, key: str, limit: int, window: float) -> bool:
        """True if fewer than `limit` hits were recorded within `window`."""
        with self._lock:
            return len(self._window(key, window)) < limit

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record a hit; returns False (and records nothing) when over limit."""
        with self._lock:
            q = self._window(key, window)
            if len(q) >= limit:
                return False
            q.append(self._clock())
            if len(self._hits) > 10000:
                self._prune(window)
            return True

    def record(self, key: str) -> None:
        with self._lock:
            self._hits.setdefault(key, deque()).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _prune(self, window: float) -> None:
        now = self._clock()
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > window]:
            del self._hits[key]


def get_limiter() -> RateLimiter:
    from flask import current_app

    limiter = current_app.extensions.get("askphilo.limiter")
    if limiter is None:
        limiter = current_app.extensions.setdefault("askphilo.limiter", RateLimiter())
    return limiter
