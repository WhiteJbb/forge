"""선제적 rate limiting — provider별 token bucket + 동시성 세마포어 (DESIGN.md §5.13).

429를 맞기 전에 알려진 무료 티어 한도(rpm/max_concurrent) 직전에서 스스로 조절한다.
- token bucket(rpm): Scheduler 후보 필터가 peek로 여유만 확인하고, dispatch 직전에 consume로 소모.
- 세마포어(max_concurrent): slot 컨텍스트 매니저로 감싸되, 무한 대기 대신 짧은 타임아웃 후
  TimeoutError를 던져 상위 failover가 다른 provider로 넘어갈 수 있게 한다.

표준 라이브러리만 사용. 리필은 별도 태스크 없이 호출 시점 lazy 계산.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from ..settings import ProviderConfig


class _Bucket:
    """provider 하나의 token bucket. rpm 미설정이면 생성하지 않는다(무제한)."""

    __slots__ = ("capacity", "rate", "tokens", "last", "_clock")

    def __init__(self, rpm: int, clock: Callable[[], float]):
        self.capacity = float(rpm)
        self.rate = rpm / 60.0  # 초당 리필량
        self.tokens = float(rpm)  # 시작 시 가득 참
        self.last = clock()
        self._clock = clock

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self.last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last = now

    def peek(self) -> bool:
        self._refill()
        return self.tokens >= 1.0

    def consume(self) -> bool:
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def remaining(self) -> int:
        self._refill()
        return int(self.tokens)


class ProviderThrottle:
    """provider별 선제 스로틀 — token bucket(rpm)과 세마포어(max_concurrent)."""

    def __init__(
        self,
        providers: list[ProviderConfig],
        clock: Callable[[], float] = time.monotonic,
        slot_timeout: float = 30.0,
    ):
        self._clock = clock
        self._slot_timeout = slot_timeout
        self._buckets: dict[str, _Bucket] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._limits: dict[str, Optional[int]] = {}  # provider별 max_concurrent
        self._in_flight: dict[str, int] = {}
        self._known: set[str] = set()

        for p in providers:
            self._known.add(p.name)
            self._in_flight[p.name] = 0
            self._limits[p.name] = p.max_concurrent
            if p.rpm is not None:
                self._buckets[p.name] = _Bucket(p.rpm, clock)
            if p.max_concurrent is not None:
                self._sems[p.name] = asyncio.Semaphore(p.max_concurrent)

    def adopt(self, old: "ProviderThrottle") -> None:
        """reload 시 스로틀 상태를 이관한다 (§5.9).

        이관하지 않으면 리로드마다 버킷이 가득 찬 상태로 리셋돼 rpm 한도를
        일시적으로 초과(429 유발)한다. 같은 이름·같은 rpm인 provider는 버킷
        잔량을, 같은 max_concurrent인 provider는 세마포어 객체 자체를 물려받아
        구 요청의 release가 새 스로틀에도 반영되게 한다. in_flight 카운터는
        표시용이라 구 요청 종료 시점의 오차를 허용한다.
        """
        for name in self._known & old._known:
            new_bucket, old_bucket = self._buckets.get(name), old._buckets.get(name)
            if (new_bucket is not None and old_bucket is not None
                    and new_bucket.capacity == old_bucket.capacity):
                new_bucket.tokens = old_bucket.tokens
                new_bucket.last = old_bucket.last
            if (name in self._sems and name in old._sems
                    and self._limits.get(name) == old._limits.get(name)):
                self._sems[name] = old._sems[name]
                self._in_flight[name] = old._in_flight.get(name, 0)

    def peek(self, provider_name: str) -> bool:
        """토큰을 소모하지 않고 여유가 있는지 확인 (Scheduler 후보 필터용)."""
        bucket = self._buckets.get(provider_name)
        if bucket is None:  # rpm 미설정 또는 알 수 없는 provider → 무제한
            return True
        return bucket.peek()

    def consume(self, provider_name: str) -> bool:
        """dispatch 직전 1토큰 소모. 여유가 없으면 False."""
        bucket = self._buckets.get(provider_name)
        if bucket is None:
            return True
        return bucket.consume()

    def refund(self, provider_name: str) -> None:
        """업스트림에 도달하지 못한 요청(슬롯 타임아웃 등)의 토큰 반환 (리뷰 #4)"""
        bucket = self._buckets.get(provider_name)
        if bucket is not None:
            bucket._refill()
            bucket.tokens = min(bucket.capacity, bucket.tokens + 1.0)

    @asynccontextmanager
    async def slot(self, provider_name: str):
        """provider 호출을 감싸는 동시성 슬롯.

        max_concurrent 미설정/알 수 없는 provider는 no-op. 세마포어 대기는
        slot_timeout(기본 30초)을 넘기면 TimeoutError를 던진다.
        """
        sem = self._sems.get(provider_name)
        tracked = provider_name in self._in_flight

        if sem is None:  # 무제한 또는 알 수 없는 provider
            if tracked:
                self._in_flight[provider_name] += 1
            try:
                yield
            finally:
                if tracked:
                    self._in_flight[provider_name] -= 1
            return

        try:
            await asyncio.wait_for(sem.acquire(), self._slot_timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"throttle slot for provider {provider_name!r} timed out "
                f"after {self._slot_timeout}s"
            ) from e

        if tracked:
            self._in_flight[provider_name] += 1
        try:
            yield
        finally:
            if tracked:
                self._in_flight[provider_name] -= 1
            sem.release()

    def snapshot(self) -> dict:
        """대시보드용 provider별 상태: tokens_remaining, rpm, in_flight, max_concurrent."""
        out: dict[str, dict] = {}
        for name in self._known:
            bucket = self._buckets.get(name)
            out[name] = {
                "tokens_remaining": bucket.remaining() if bucket else None,
                "rpm": int(bucket.capacity) if bucket else None,
                "in_flight": self._in_flight.get(name, 0),
                "max_concurrent": self._limits.get(name),
            }
        return out
