"""呼叫頻率限制（滑動視窗，單一程序內）：每把權杖一個、每把權杖每個工具各一個（鍵不同，互不影響）。"""

import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable


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
        self._hits: dict[Hashable, deque[float]] = defaultdict(deque)

    def check(self, key: Hashable, limit: int | None = None) -> float | None:
        """放行回 None 並記一次；超過回需要等幾秒。`limit` 給了就覆寫這次的上限（每個工具各自的上限）。"""
        cap = self.limit if limit is None else limit
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= cap:
            return max(hits[0] + self.window - now, 0.0)
        hits.append(now)
        return None
