import asyncio
import json

import httpx
import pytest

from elaine.core import DomainError
from elaine.provider import Provider
from elaine.rate_limit import AIRateLimiter


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


async def test_rolling_window_not_fixed_minute():
    clock = FakeTime()
    limiter = AIRateLimiter(clock=clock.clock, sleep=clock.sleep)
    clock.now = 59
    for _ in range(5):
        await limiter.acquire()
    clock.now = 60
    await limiter.acquire()
    assert clock.now == 119
    assert clock.waits == [59]


async def test_concurrent_requests_never_exceed_five():
    clock = FakeTime()
    limiter = AIRateLimiter(clock=clock.clock, sleep=clock.sleep)
    admitted = []

    async def request():
        await limiter.acquire()
        admitted.append(clock.now)

    await asyncio.gather(*(request() for _ in range(16)))
    assert admitted == [0] * 5 + [60] * 5 + [120] * 5 + [180]


async def test_cancelled_wait_does_not_consume_slot_or_deadlock():
    clock = FakeTime()
    waiting = asyncio.Event()

    async def blocked_sleep(seconds):
        waiting.set()
        await asyncio.Future()

    limiter = AIRateLimiter(clock=clock.clock, sleep=blocked_sleep)
    for _ in range(5):
        await limiter.acquire()
    task = asyncio.create_task(limiter.acquire())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    clock.now = 60
    for _ in range(5):
        await asyncio.wait_for(limiter.acquire(), 1)


async def test_chat_retry_summary_and_failed_http_share_budget(env):
    _, _, clock, actor, _, settings = env
    timer = FakeTime()
    starts = []
    responses = [
        (200, "invalid JSON"),
        (200, json.dumps({"kind": "chat", "text": "Halo"})),
        (200, "Ringkasan"),
        (429, ""),
        (200, "Tes"),
        (200, "Keenam"),
    ]

    def handler(request):
        starts.append(timer.now)
        status, content = responses.pop(0)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    provider = Provider(settings, httpx.MockTransport(handler))
    provider.rate_limiter = AIRateLimiter(clock=timer.clock, sleep=timer.sleep)
    try:
        assert (await provider.propose(actor, "Halo", {}, clock.now())).text == "Halo"
        assert await provider.summarize([]) == "Ringkasan"
        with pytest.raises(DomainError, match="429"):
            await provider.request([])
        assert await provider.request([]) == "Tes"
        assert await provider.request([]) == "Keenam"
        assert starts == [0, 0, 0, 0, 0, 60]
    finally:
        await provider.close()
