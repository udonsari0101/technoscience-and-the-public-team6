"""Tiny in-process sliding-window rate limiter.

Render runs a single Gunicorn worker, so process memory is enough to slow
down password guessing and runaway chat requests (API cost).

Memory safety: checking a limit never creates state, keys are bounded by
the callers (no raw user input), every key remembers its own window, and
expired keys are pruned periodically with that window.
"""

from __future__ import annotations

import threading
import time
from collections import deque

PRUNE_EVERY = 500  # operations between prune passes
MAX_KEYS = 50_000  # hard ceiling; oldest-idle keys are dropped beyond it


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._windows: dict[str, float] = {}
        self._ops = 0
        self._lock = threading.Lock()

    def _count(self, key: str, window: float, now: float) -> int:
        q = self._hits.get(key)
        if not q:
            return 0
        while q and now - q[0] > window:
            q.popleft()
        return len(q)

    def _append(self, key: str, window: float, now: float) -> None:
        self._hits.setdefault(key, deque()).append(now)
        self._windows[key] = max(window, self._windows.get(key, 0))
        self._ops += 1
        if self._ops % PRUNE_EVERY == 0 or len(self._hits) > MAX_KEYS:
            self._prune(now)

    def allowed(self, key: str, limit: int, window: float) -> bool:
        """True if fewer than `limit` hits were recorded within `window` (read-only)."""
        with self._lock:
            return self._count(key, window, self._clock()) < limit

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record a hit; returns False (and records nothing) when over limit."""
        with self._lock:
            now = self._clock()
            if self._count(key, window, now) >= limit:
                return False
            self._append(key, window, now)
            return True

    def record(self, key: str, window: float) -> None:
        with self._lock:
            self._append(key, window, self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
            self._windows.pop(key, None)

    def _prune(self, now: float) -> None:
        stale = [k for k, q in self._hits.items()
                 if not q or now - q[-1] > self._windows.get(k, 0)]
        for k in stale:
            self._hits.pop(k, None)
            self._windows.pop(k, None)
        if len(self._hits) > MAX_KEYS:
            # Still too many live keys: drop the least recently used ones.
            by_age = sorted(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
            for k in by_age[: len(self._hits) - MAX_KEYS]:
                self._hits.pop(k, None)
                self._windows.pop(k, None)

    def __len__(self) -> int:
        return len(self._hits)


def get_limiter() -> RateLimiter:
    from flask import current_app

    limiter = current_app.extensions.get("askphilo.limiter")
    if limiter is None:
        limiter = current_app.extensions.setdefault("askphilo.limiter", RateLimiter())
    return limiter
