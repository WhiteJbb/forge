"""Health Monitor — passive 우선 헬스 체크 (DESIGN.md §5.6)

기존 src/health_monitor.py("12모델 × 30초 completion ping")는 무료 티어
rate limit을 스스로 소모하는 자해 행위였다. 이 재설계의 원칙:

1. passive가 1차 신호 — 실트래픽의 성공/실패는 이미 Scheduler가
   entry.health.record_success/record_failure로 반영한다. 여기서는 건드리지 않는다.
2. active probe는 "최근 트래픽이 없는 모델"에 한한 보조 수단이며,
   probe 주기 안에서 모델 간 간격을 두고 순차 실행한다(스태거링) — 전부 한 번에 치지 않는다.
3. provider 생사 확인(list_models)과 모델별 probe는 분리된 체크다.
4. probe 중 429는 실트래픽 기회를 뺏은 대가이므로 실패로 취급하지 않는다.
"""

import asyncio
import logging
import time
from typing import Optional

from ..providers.base import Provider
from ..settings import HealthConfig
from .registry import ModelEntry, Registry

logger = logging.getLogger(__name__)

# provider list_models() 연속 실패/빈 목록 허용 횟수 — 이 이상이면 소속 모델 전체 unhealthy 처리
PROVIDER_FAIL_THRESHOLD = 2


class HealthMonitor:
    """Registry 상태를 passive 신호 위주로 보완하는 능동 probe 스케줄러."""

    def __init__(self, registry: Registry, providers: dict[str, Provider], config: HealthConfig):
        self._registry = registry
        self._providers = providers
        self._config = config
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # provider별 list_models 연속 실패 카운트
        self._provider_fail_streak: dict[str, int] = {}

    # --- 생명주기 (기존 health_monitor.py의 asyncio.Task 패턴) ---

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Health Monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Health Monitor stopped")

    async def _loop(self) -> None:
        # probe_idle_minutes를 초로 환산한 것이 곧 probe 주기(§5.6-2)
        period = max(float(self._config.probe_idle_minutes) * 60.0, 1.0)
        while self._running:
            try:
                await self._check_providers()
                await self._probe_cycle(period)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Health monitor cycle error: {e}")
                await asyncio.sleep(period)

    # --- 모델별 active probe (스태거링) ---

    async def _probe_cycle(self, period: float) -> None:
        targets = self._idle_targets()
        if not targets:
            # probe할 대상이 없어도 주기만큼은 쉰다 (busy loop 방지)
            await asyncio.sleep(period)
            return

        interval = period / len(targets)
        for entry in targets:
            if not self._running:
                return
            # 스태거링 sleep 도중 실트래픽이 들어왔을 수 있으므로 probe 직전 재확인 —
            # 사이클 시작 시점의 스냅샷만 믿으면 방금 쓰인 모델을 뒤늦게 probe해버린다.
            if self._is_idle_target(entry):
                await self._probe_one(entry)
            await asyncio.sleep(interval)

    def _idle_targets(self) -> list[ModelEntry]:
        """probe 대상: config에 명시된(라우팅 로테이션에 있는) 유휴 모델만.

        discovery로 등록된 모델은 probe하지 않는다 — 수백 개를 주기 순회하면
        무료 티어 rate limit을 probe가 소모하는 자해가 된다 (§2-5의 재발 방지).
        그 모델들의 health는 실제로 라우팅될 때 passive 신호로 확립된다.
        """
        return [e for e in self._registry.all()
                if e.source == "config" and self._is_idle_target(e)]

    def _is_idle_target(self, entry: ModelEntry) -> bool:
        h = entry.health
        h.check_cooldown_expired()
        if h.status == "cooldown":
            return False
        idle_seconds = self._config.probe_idle_minutes * 60
        # 실트래픽(last_used)뿐 아니라 최근 probe(last_check)도 "확인됨"으로 취급 —
        # 워밍업 직후 모니터 첫 사이클이 같은 모델을 중복 probe하지 않게
        last_activity = max(h.last_used, h.last_check)
        return time.time() - last_activity >= idle_seconds

    async def _probe_one(self, entry: ModelEntry) -> None:
        provider = self._providers.get(entry.provider)
        if provider is None:
            logger.warning(f"probe {entry.id}: unknown provider {entry.provider!r}")
            return

        try:
            result = await provider.probe(entry.provider_model_id, timeout=self._config.probe_timeout)
        except Exception as e:
            # probe 자체가 예외를 던지는 구현도 있을 수 있으니 방어적으로 로그만 남긴다
            logger.warning(f"probe {entry.id}: error {e}")
            return

        if not result.ok and result.error:
            err = result.error.lower()
            if "429" in err:
                # probe 중 429는 쿨다운/unhealthy로 반영하지 않는다 — probe가 실트래픽
                # 기회를 뺏으면 안 된다(§5.6-4). 로그만 남기고 상태는 그대로 둔다.
                entry.health.last_check = time.time()  # 같은 주기 내 재probe 방지
                logger.info(f"probe {entry.id}: rate limited (429), 상태 변경 없음")
                return
            if "timeout" in err or "timed out" in err:
                # probe 타임아웃은 "느림"이지 "죽음"이 아니다 — 무료 티어 reasoning
                # 모델은 첫 토큰까지 probe_timeout을 넘기기 일쑤(실측). unhealthy로
                # 마킹하면 멀쩡한 tier1이 부팅 직후 라우팅에서 빠진다. 판정은
                # 확정적 오류(4xx/5xx/연결 실패)와 passive 신호에 맡긴다.
                entry.health.last_check = time.time()  # 같은 주기 내 재probe 방지
                logger.info(f"probe {entry.id}: slow (>{self._config.probe_timeout}s), "
                            f"상태 변경 없음")
                return

        entry.health.set_probe_result(result.ok, result.latency_ms)
        if result.ok:
            logger.debug(f"probe {entry.id}: healthy ({result.latency_ms:.0f}ms)")
        else:
            logger.warning(f"probe {entry.id}: unhealthy ({result.error})")

    # --- provider 레벨 생사 확인 ---

    async def _check_providers(self) -> None:
        for name, provider in self._providers.items():
            try:
                models = await provider.list_models()
                ok = bool(models)
            except Exception as e:
                ok = False
                logger.warning(f"provider {name}: list_models failed: {e}")

            if ok:
                self._provider_fail_streak[name] = 0
                continue

            streak = self._provider_fail_streak.get(name, 0) + 1
            self._provider_fail_streak[name] = streak
            logger.warning(f"provider {name}: list_models 빈 목록/실패 ({streak}회 연속)")

            if streak >= PROVIDER_FAIL_THRESHOLD:
                # 소속 모델 전체 unhealthy. 복구는 개별 probe/실트래픽에 맡기고
                # 여기서 unknown으로 되돌리지 않는다(§5.6-3).
                for entry in self._registry.all():
                    if entry.provider == name:
                        entry.health.set_probe_result(False)

    # --- 콜드 스타트 워밍업 (§5.13) ---

    async def warmup(self) -> None:
        """부팅 직후 1회 호출: tier1 모델만 스태거링 없이 병렬 probe해 콜드 스타트를 해소한다."""
        tier1 = self._registry.by_tier("tier1")
        if not tier1:
            return
        await asyncio.gather(*(self._probe_one(e) for e in tier1), return_exceptions=True)

    # --- Auto Discovery ---

    async def discover(self) -> dict[str, list[str]]:
        """discovery=true인 provider의 list_models()를 Registry에 병합한다 (부팅 시 서버가 호출).

        반환값: {provider명: 신규 등록된 forge id 목록} (신규 등록이 없는 provider는 제외)
        """
        result: dict[str, list[str]] = {}
        for name, provider in self._providers.items():
            if not provider.config.discovery:
                continue
            try:
                model_ids = await provider.list_models()
            except Exception as e:
                logger.warning(f"discover: provider {name} list_models failed: {e}")
                continue
            added = self._registry.merge_discovered(name, model_ids)
            if added:
                result[name] = added
        return result
