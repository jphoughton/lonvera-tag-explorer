"""In-memory per-IP sliding-window rate limits for projection writes (per process, best effort; resets on restart)."""
from __future__ import annotations

import collections
import os
import threading
import time

DEFAULT_MUTATIONS_PER_MIN = 120
DEFAULT_UPLOADS_PER_HOUR = 20
DEFAULT_PREVIEWS_PER_MIN = 600


class SlidingWindow:
    """Allow at most `limit` hits per key in any `window` seconds."""

    def __init__(self, limit: int, window_sec: float):
        self.limit = int(limit)
        self.window = float(window_sec)
        self._hits: dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> int:
        """Record a hit. Returns 0 when allowed, else the seconds until the oldest hit leaves the window (the hit is not recorded)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > 5000:                           # bound memory
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]:
                    self._hits.pop(k, None)
            q = self._hits.setdefault(key, collections.deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            return 0


class Limits:
    """The two limiters of one app. Limits come from app config or env at construction: PROJ_RATE_MUTATIONS_PER_MIN (120), PROJ_RATE_UPLOADS_PER_HOUR (20), PROJ_RATE_PREVIEWS_PER_MIN (600)."""

    def __init__(self, mutations_per_min: int = DEFAULT_MUTATIONS_PER_MIN, uploads_per_hour: int = DEFAULT_UPLOADS_PER_HOUR,
                 previews_per_min: int = DEFAULT_PREVIEWS_PER_MIN):
        self.mutations = SlidingWindow(mutations_per_min, 60)
        self.uploads = SlidingWindow(uploads_per_hour, 3600)
        self.previews = SlidingWindow(previews_per_min, 60)          # POSTs that compute and write nothing (a path ending /preview): a burst of edits needs one each

    @classmethod
    def from_config(cls, cfg) -> "Limits":
        def pick(name: str, default: int) -> int:                      # app config first (create_app overrides), then the environment
            return int(cfg.get(name) or os.environ.get(name) or default)

        return cls(pick("PROJ_RATE_MUTATIONS_PER_MIN", DEFAULT_MUTATIONS_PER_MIN), pick("PROJ_RATE_UPLOADS_PER_HOUR", DEFAULT_UPLOADS_PER_HOUR),
                   pick("PROJ_RATE_PREVIEWS_PER_MIN", DEFAULT_PREVIEWS_PER_MIN))
