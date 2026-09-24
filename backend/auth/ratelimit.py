"""In-memory sliding-window limiter for failed logins.

State lives in the process: with several workers each keeps its own counters, so the
effective limit is multiplied by the worker count. Put a proxy-level limit in front for
multi-worker deployments.
"""
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional


class LoginRateLimiter:
    def __init__(self, max_per_user: int, max_per_ip: int, window_seconds: int):
        self.max_per_user = max_per_user
        self.max_per_ip = max_per_ip
        self.window = window_seconds
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def _count(self, key: str, now: float) -> tuple[int, float]:
        """Drops failures older than the window; returns (count, oldest timestamp)."""
        q = self._failures.get(key)
        if not q:
            return 0, now
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            del self._failures[key]
            return 0, now
        return len(q), q[0]

    def retry_after(self, ip: str, username: str) -> Optional[int]:
        """Seconds to wait if the caller is locked out, otherwise None."""
        now = time.monotonic()
        with self._lock:
            for key, limit in ((f"u:{ip}:{username}", self.max_per_user), (f"ip:{ip}", self.max_per_ip)):
                count, oldest = self._count(key, now)
                if count >= limit:
                    return max(1, int(oldest + self.window - now) + 1)
        return None

    def record_failure(self, ip: str, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures[f"u:{ip}:{username}"].append(now)
            self._failures[f"ip:{ip}"].append(now)

    def reset(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop(f"u:{ip}:{username}", None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
