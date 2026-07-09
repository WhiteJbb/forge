"""ProviderThrottle 테스트 (DESIGN.md §5.13, src/core/throttle.py)

주입 clock으로 token bucket 리필을 결정적으로 검증하고, 세마포어 슬롯의
대기/해제/타임아웃을 asyncio 이벤트로 검증한다. unittest만 사용.
"""

import asyncio
import unittest

from forge_gateway.core.throttle import ProviderThrottle
from forge_gateway.settings import ProviderConfig


class FakeClock:
    """주입용 단조 시계 — advance로 시간을 명시적으로 흘린다."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _throttle(clock=None, slot_timeout=30.0, **kwargs):
    providers = [
        ProviderConfig(name="nvidia", rpm=60, max_concurrent=2),
        ProviderConfig(name="unlimited"),  # rpm/max_concurrent 미설정
    ]
    return ProviderThrottle(providers, clock=clock or FakeClock(), slot_timeout=slot_timeout)


class TokenBucketTest(unittest.TestCase):
    def test_starts_full(self):
        thr = _throttle()
        self.assertTrue(thr.peek("nvidia"))
        self.assertEqual(thr.snapshot()["nvidia"]["tokens_remaining"], 60)

    def test_consume_reduces_tokens(self):
        thr = _throttle()
        self.assertTrue(thr.consume("nvidia"))
        self.assertEqual(thr.snapshot()["nvidia"]["tokens_remaining"], 59)

    def test_peek_does_not_consume(self):
        thr = _throttle()
        for _ in range(5):
            self.assertTrue(thr.peek("nvidia"))
        self.assertEqual(thr.snapshot()["nvidia"]["tokens_remaining"], 60)

    def test_drain_then_empty(self):
        thr = _throttle()
        for _ in range(60):
            self.assertTrue(thr.consume("nvidia"))
        # 소진 후 peek/consume 모두 실패
        self.assertFalse(thr.peek("nvidia"))
        self.assertFalse(thr.consume("nvidia"))

    def test_refill_over_time(self):
        clock = FakeClock()
        thr = _throttle(clock=clock)
        for _ in range(60):
            thr.consume("nvidia")
        self.assertFalse(thr.peek("nvidia"))
        clock.advance(1.0)  # rpm=60 → 초당 1토큰
        self.assertTrue(thr.peek("nvidia"))
        self.assertTrue(thr.consume("nvidia"))
        self.assertFalse(thr.consume("nvidia"))  # 딱 1토큰만 충전됨

    def test_refill_capped_at_capacity(self):
        clock = FakeClock()
        thr = _throttle(clock=clock)
        thr.consume("nvidia")  # 59
        clock.advance(10_000)  # 아무리 지나도 capacity(60) 초과 안 됨
        self.assertEqual(thr.snapshot()["nvidia"]["tokens_remaining"], 60)

    def test_partial_refill(self):
        clock = FakeClock()
        thr = _throttle(clock=clock)
        for _ in range(60):
            thr.consume("nvidia")
        clock.advance(0.5)  # 0.5토큰 → 아직 1 미만
        self.assertFalse(thr.peek("nvidia"))
        clock.advance(0.5)  # 누적 1.0토큰
        self.assertTrue(thr.peek("nvidia"))

    def test_unset_rpm_always_true(self):
        thr = _throttle()
        for _ in range(1000):
            self.assertTrue(thr.peek("unlimited"))
            self.assertTrue(thr.consume("unlimited"))

    def test_unknown_provider_unlimited(self):
        thr = _throttle()
        self.assertTrue(thr.peek("ghost"))
        self.assertTrue(thr.consume("ghost"))

    def test_snapshot_shape(self):
        thr = _throttle()
        thr.consume("nvidia")
        snap = thr.snapshot()
        self.assertEqual(
            snap["nvidia"],
            {"tokens_remaining": 59, "rpm": 60, "in_flight": 0, "max_concurrent": 2},
        )
        self.assertEqual(
            snap["unlimited"],
            {"tokens_remaining": None, "rpm": None, "in_flight": 0, "max_concurrent": None},
        )


class SlotTest(unittest.IsolatedAsyncioTestCase):
    async def test_third_waits_then_enters_on_release(self):
        thr = ProviderThrottle([ProviderConfig(name="nvidia", max_concurrent=2)])
        release = asyncio.Event()
        entered: list = []
        holders_in = asyncio.Semaphore(0)  # 고정 sleep 대신 결정적 동기화 (전체 스위트 부하에서 플레이키였음)

        async def hold():
            async with thr.slot("nvidia"):
                entered.append("hold")
                holders_in.release()
                await release.wait()

        async def third():
            async with thr.slot("nvidia"):
                entered.append("third")

        t1 = asyncio.create_task(hold())
        t2 = asyncio.create_task(hold())
        await asyncio.wait_for(holders_in.acquire(), 5)
        await asyncio.wait_for(holders_in.acquire(), 5)
        self.assertEqual(entered.count("hold"), 2)  # 두 슬롯 점유
        self.assertEqual(thr.snapshot()["nvidia"]["in_flight"], 2)

        t3 = asyncio.create_task(third())
        await asyncio.sleep(0.05)  # '진입 안 함'의 부정 검증만 sleep에 의존 (안전한 방향)
        self.assertNotIn("third", entered)  # 세 번째는 대기 중

        release.set()  # 슬롯 해제 → 세 번째 진입
        await asyncio.wait_for(t3, 5)
        self.assertIn("third", entered)
        await asyncio.wait_for(asyncio.gather(t1, t2), 5)
        self.assertEqual(thr.snapshot()["nvidia"]["in_flight"], 0)

    async def test_slot_timeout_raises(self):
        thr = ProviderThrottle(
            [ProviderConfig(name="nvidia", max_concurrent=1)], slot_timeout=0.05
        )
        release = asyncio.Event()

        async def hold():
            async with thr.slot("nvidia"):
                await release.wait()

        t1 = asyncio.create_task(hold())
        await asyncio.sleep(0.02)  # 유일한 슬롯 점유
        with self.assertRaises(TimeoutError):
            async with thr.slot("nvidia"):
                self.fail("타임아웃 되어야 하므로 진입하면 안 됨")
        release.set()
        await t1

    async def test_slot_noop_without_max_concurrent(self):
        thr = ProviderThrottle([ProviderConfig(name="unlimited")], slot_timeout=0.05)
        # 세마포어가 없어도 여러 슬롯 동시 진입 가능 (블로킹/타임아웃 없음)
        async with thr.slot("unlimited"):
            async with thr.slot("unlimited"):
                self.assertEqual(thr.snapshot()["unlimited"]["in_flight"], 2)

    async def test_slot_unknown_provider_noop(self):
        thr = ProviderThrottle([ProviderConfig(name="nvidia", max_concurrent=1)])
        async with thr.slot("ghost"):
            pass  # 에러 없이 통과


if __name__ == "__main__":
    unittest.main()
