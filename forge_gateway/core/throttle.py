"""선제적 rate limiting — provider별 token bucket + 동시성 세마포어 (DESIGN.md §5.13).

429를 맞기 전에 알려진 무료 티어 한도(rpm/max_concurrent) 직전에서 스스로 조절한다.
- token bucket(rpm): Scheduler 후보 필터가 peek로 여유만 확인하고, dispatch 직전에 acquire로 소모.
- 세마포어(max_concurrent): slot 컨텍스트 매니저로 감싸되, 무한 대기 대신 짧은 타임아웃 후
  TimeoutError를 던져 상위 failover가 다른 provider로 넘어갈 수 있게 한다.

멀티 API 키 로테이션 (DecisionLog 2026-07-12): provider에 키가 여러 개면 rpm은 "키
하나당" 한도이므로 버킷을 키 수만큼 두고(합산 용량 = rpm × 키수), 429 쿨다운도 키
단위로 관리한다. rpm 미설정 provider도 429는 오므로 키 쿨다운 배열은 항상 유지한다.
max_concurrent 세마포어는 인프라 동시성이라 provider 단위 유지.

표준 라이브러리만 사용. 리필은 별도 태스크 없이 호출 시점 lazy 계산.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from ..settings import ProviderConfig


class _Bucket:
    """키 하나의 token bucket. rpm 미설정이면 생성하지 않는다(무제한)."""

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
    """provider별 선제 스로틀 — 키 단위 token bucket(rpm)과 provider 단위 세마포어."""

    def __init__(
        self,
        providers: list[ProviderConfig],
        clock: Callable[[], float] = time.monotonic,
        slot_timeout: float = 30.0,
    ):
        self._clock = clock
        self._slot_timeout = slot_timeout
        # provider별 키 수 — 생성 시점에 해석해 고정 (api_keys는 os.environ을 읽는다)
        self._num_keys: dict[str, int] = {}
        # rpm 설정 provider만: 키당 버킷 리스트 (길이 = num_keys)
        self._key_buckets: dict[str, list[_Bucket]] = {}
        # 모든 등록 provider: 키당 429 쿨다운 "until" monotonic 타임스탬프 (0.0 = 쿨다운 아님)
        self._key_cooldown: dict[str, list[float]] = {}
        # 선택용 라운드로빈 카운터 (동률·무rpm 분산)
        self._rr: dict[str, int] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._limits: dict[str, Optional[int]] = {}  # provider별 max_concurrent
        self._in_flight: dict[str, int] = {}
        self._known: set[str] = set()

        for p in providers:
            n = max(1, len(p.api_keys))
            self._known.add(p.name)
            self._num_keys[p.name] = n
            self._key_cooldown[p.name] = [0.0] * n
            self._rr[p.name] = 0
            self._in_flight[p.name] = 0
            self._limits[p.name] = p.max_concurrent
            if p.rpm is not None:
                self._key_buckets[p.name] = [_Bucket(p.rpm, clock) for _ in range(n)]
            if p.max_concurrent is not None:
                self._sems[p.name] = asyncio.Semaphore(p.max_concurrent)

    def adopt(self, old: "ProviderThrottle") -> None:
        """reload 시 스로틀 상태를 이관한다 (§5.9).

        이관하지 않으면 리로드마다 버킷이 가득 찬 상태로 리셋돼 rpm 한도를
        일시적으로 초과(429 유발)한다. 같은 이름·같은 rpm·같은 키 수인 provider는
        키별 버킷 잔량과 키별 쿨다운을, 같은 max_concurrent인 provider는 세마포어
        객체 자체를 물려받아 구 요청의 release가 새 스로틀에도 반영되게 한다.
        in_flight 카운터는 표시용이라 구 요청 종료 시점의 오차를 허용한다.
        """
        for name in self._known & old._known:
            same_keys = self._num_keys.get(name) == old._num_keys.get(name)
            new_buckets = self._key_buckets.get(name)
            old_buckets = old._key_buckets.get(name)
            if (same_keys and new_buckets is not None and old_buckets is not None
                    and new_buckets[0].capacity == old_buckets[0].capacity):
                for nb, ob in zip(new_buckets, old_buckets):
                    nb.tokens = ob.tokens
                    nb.last = ob.last
            if same_keys and name in old._key_cooldown:
                # 키별 쿨다운은 rpm과 무관하게 보존 (429는 rpm 미설정에서도 온다)
                self._key_cooldown[name] = list(old._key_cooldown[name])
            if (name in self._sems and name in old._sems
                    and self._limits.get(name) == old._limits.get(name)):
                self._sems[name] = old._sems[name]
                self._in_flight[name] = old._in_flight.get(name, 0)

    def num_keys(self, provider_name: str) -> int:
        """provider의 등록 키 수. 미등록이면 1."""
        return self._num_keys.get(provider_name, 1)

    def _select(self, provider_name: str, consume: bool) -> Optional[int]:
        """가용 키 하나를 고른다 — 쿨다운 아니고 (버킷 없거나 tokens>=1)인 키 중
        토큰 최다(동률·무rpm은 라운드로빈)를 선택. consume=True면 토큰 1 소모.
        가용 키 없으면 None. 미등록 provider는 0(소모 없음)."""
        if provider_name not in self._known:
            return 0
        n = self._num_keys[provider_name]
        buckets = self._key_buckets.get(provider_name)  # None이면 무제한(rpm 미설정)
        cooldown = self._key_cooldown[provider_name]
        now = self._clock()

        avail: list[int] = []
        for i in range(n):
            if cooldown[i] > now:
                continue
            if buckets is not None:
                buckets[i]._refill()
                if buckets[i].tokens < 1.0:
                    continue
            avail.append(i)
        if not avail:
            return None

        if buckets is not None:
            top_tokens = max(buckets[i].tokens for i in avail)
            top = [i for i in avail if buckets[i].tokens >= top_tokens - 1e-9]
        else:
            top = avail
        # 라운드로빈 회전 — 동률 키/무rpm 키를 고르게 분산
        rr = self._rr[provider_name]
        self._rr[provider_name] = rr + 1
        choice = top[rr % len(top)]
        if consume and buckets is not None:
            buckets[choice].tokens -= 1.0
        return choice

    def peek(self, provider_name: str) -> bool:
        """토큰을 소모하지 않고 가용 키가 하나라도 있는지 확인 (Scheduler 후보 필터용).

        미등록 provider는 무제한 → True (기존 동작).
        """
        if provider_name not in self._known:
            return True
        n = self._num_keys[provider_name]
        buckets = self._key_buckets.get(provider_name)
        cooldown = self._key_cooldown[provider_name]
        now = self._clock()
        for i in range(n):
            if cooldown[i] > now:
                continue
            if buckets is None:
                return True
            if buckets[i].peek():
                return True
        return False

    def acquire(self, provider_name: str) -> Optional[int]:
        """dispatch 직전 가용 키 하나를 확보하고 토큰 1 소모. 키 인덱스 반환.

        가용 키 없으면 None. 미등록 provider는 0(소모 없음, 기존 consume 동작 유지).
        """
        return self._select(provider_name, consume=True)

    def pick_key(self, provider_name: str) -> int:
        """acquire와 같은 선택 로직이되 토큰 소모 없음 — 직접 지정(forced) 모델의
        rpm 게이트 우회 경로용. 가용 키 없으면 0."""
        choice = self._select(provider_name, consume=False)
        return choice if choice is not None else 0

    def consume(self, provider_name: str) -> bool:
        """dispatch 직전 1토큰 소모. 여유가 없으면 False (하위 호환 래퍼)."""
        return self.acquire(provider_name) is not None

    def refund(self, provider_name: str, key_index: int = 0) -> None:
        """업스트림에 도달하지 못한 요청(슬롯 타임아웃 등)의 토큰 반환 (리뷰 #4)."""
        buckets = self._key_buckets.get(provider_name)
        if buckets is not None and 0 <= key_index < len(buckets):
            bucket = buckets[key_index]
            bucket._refill()
            bucket.tokens = min(bucket.capacity, bucket.tokens + 1.0)

    def cooldown_key(self, provider_name: str, key_index: int, seconds: float) -> None:
        """해당 키를 seconds 동안 쿨다운 (429 귀책, DecisionLog 2026-07-12)."""
        cooldown = self._key_cooldown.get(provider_name)
        if cooldown is not None and 0 <= key_index < len(cooldown):
            until = self._clock() + max(0.0, float(seconds))
            if until > cooldown[key_index]:
                cooldown[key_index] = until

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
        """대시보드용 provider별 상태: tokens_remaining(전 키 합), rpm(키당), in_flight,
        max_concurrent. 키가 2개 이상이면 키별 상세를 'keys'로 덧붙인다."""
        out: dict[str, dict] = {}
        now = self._clock()
        for name in self._known:
            buckets = self._key_buckets.get(name)
            entry: dict = {
                "tokens_remaining": (sum(b.remaining() for b in buckets)
                                     if buckets else None),
                "rpm": int(buckets[0].capacity) if buckets else None,
                "in_flight": self._in_flight.get(name, 0),
                "max_concurrent": self._limits.get(name),
            }
            if self._num_keys.get(name, 1) > 1:
                cooldown = self._key_cooldown[name]
                entry["keys"] = [
                    {
                        "index": i,
                        "tokens_remaining": (buckets[i].remaining()
                                             if buckets else None),
                        "cooldown_remaining_s": max(0.0, round(cooldown[i] - now, 1)),
                    }
                    for i in range(self._num_keys[name])
                ]
            out[name] = entry
        return out
