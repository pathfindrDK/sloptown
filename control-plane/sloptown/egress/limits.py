"""In-memory throttles for the egress proxy: token bucket, sliding RPM window, concurrency.

State lives in one process; run the egress with a single uvicorn worker.
"""

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from .config import EgressConfig


class TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate, self.burst = rate, burst
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class SlidingWindow:
    def __init__(self, limit: int, seconds: float = 60):
        self.limit, self.seconds = limit, seconds
        self.hits: deque[float] = deque()

    def take(self) -> bool:
        now = time.monotonic()
        while self.hits and self.hits[0] <= now - self.seconds:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


class Throttled(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # rate|concurrency|inference_rpm|inference_rpm_global


class Limiter:
    def __init__(self, cfg: EgressConfig):
        self.cfg = cfg
        self._buckets: dict[str, TokenBucket] = defaultdict(lambda: TokenBucket(cfg.rps_per_project, cfg.burst_per_project))
        self._rpm: dict[str, SlidingWindow] = defaultdict(lambda: SlidingWindow(cfg.inference_rpm_per_project))
        self._rpm_global = SlidingWindow(cfg.inference_rpm_global)
        self._open: dict[str, int] = defaultdict(int)

    def check_rate(self, project_id: str) -> None:
        if not self._buckets[project_id].take():
            raise Throttled("rate")

    def check_inference(self, project_id: str) -> None:
        if not self._rpm[project_id].take():
            raise Throttled("inference_rpm")
        if not self._rpm_global.take():
            raise Throttled("inference_rpm_global")

    @asynccontextmanager
    async def slot(self, project_id: str):
        """Holds one concurrency slot for the duration of a request or tunnel."""
        if self._open[project_id] >= self.cfg.max_concurrent_per_project:
            raise Throttled("concurrency")
        self._open[project_id] += 1
        try:
            yield
        finally:
            self._open[project_id] -= 1

    def acquire(self, project_id: str) -> None:
        """Non-context form for streaming responses; pair with release()."""
        if self._open[project_id] >= self.cfg.max_concurrent_per_project:
            raise Throttled("concurrency")
        self._open[project_id] += 1

    def release(self, project_id: str) -> None:
        self._open[project_id] = max(0, self._open[project_id] - 1)

