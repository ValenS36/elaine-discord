"""One rolling request budget shared by all AI calls in a bot process."""

import asyncio
import time
from collections import deque

AI_REQUESTS_PER_MINUTE = 5


class AIRateLimiter:
    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._clock = clock
        self._sleep = sleep
        self._starts = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        # Holding this FIFO lock while sleeping prevents concurrent waiters from
        # consuming the same slot. Cancellation releases the lock without a slot.
        async with self._lock:
            while True:
                now = self._clock()
                while self._starts and now - self._starts[0] >= 60:
                    self._starts.popleft()
                if len(self._starts) < AI_REQUESTS_PER_MINUTE:
                    self._starts.append(now)
                    return
                await self._sleep(max(0, 60 - (now - self._starts[0])))
