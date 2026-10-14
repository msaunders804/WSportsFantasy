"""Tiny in-memory sliding-window limiter for the public signup form.

Per process and reset on restart, which is fine for one web instance. Real per-user limits
arrive with accounts (Phase 3).
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self._max = max_events
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._events[key]
            while q and q[0] <= now - self._window:
                q.popleft()
            if len(q) >= self._max:
                return False
            q.append(now)
            return True
