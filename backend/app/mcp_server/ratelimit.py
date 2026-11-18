"""每把權杖的呼叫頻率限制（滑動視窗，單一程序內）。"""

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """視窗內最多 `limit` 次。記憶體內計數：重啟歸零，多個 worker 各算各的（見 docs）。"""

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[uuid.UUID, deque[float]] = defaultdict(deque)

    def check(self, key: uuid.UUID) -> float | None:
        """放行回 None 並記一次；超過回需要等幾秒。"""
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(hits[0] + self.window - now, 0.0)
        hits.append(now)
        return None
