"""Capability 학습 루프 — 시드 점수를 자기 트래픽으로 보정한다 (DESIGN.md §5.11-3)

수명주기의 3단계: 시드(forge.yaml, 벤치마크 근거) → 관측(request_metrics 집계)
→ 보정(여기). 보정 폭을 ±2로 제한해 일시적 장애가 capability 판단 자체를
뒤집지 않게 한다. 같은 신호로 tools feature 자동 강등도 수행한다.

보정 규칙 (결정적·설명 가능 — 학습형 블랙박스를 피한다는 §3 원칙과 동일):
  실패율 >= 50%          → -2
  실패율 >= 25%          → -1
  실패율 <= 2% (표본 20+) → +1  (상향은 +1로 보수적 — 시드를 크게 넘지 않게)
  그 외                   → 0 (기존 보정 해제)
cancelled는 집계에서 제외된다 (모델 잘못이 아님, §7).
"""

import asyncio
import logging
from typing import Optional

from ..settings import TunerConfig
from .metrics import MetricsEngine
from .registry import Registry
from .scheduler import TASK_TO_CAPABILITY

logger = logging.getLogger(__name__)

DEMOTE_MIN_SAMPLES_FACTOR = 1  # tools 강등도 min_samples 이상의 tools 표본 요구


def _delta_for(failure_rate: float, total: int) -> float:
    if failure_rate >= 0.5:
        return -2.0
    if failure_rate >= 0.25:
        return -1.0
    if failure_rate <= 0.02 and total >= 20:
        return 1.0
    return 0.0


class CapabilityTuner:
    """주기적으로 request_metrics 집계를 읽어 Registry 엔트리를 보정한다."""

    def __init__(self, registry: Registry, metrics: MetricsEngine, config: TunerConfig):
        self._registry = registry
        self._metrics = metrics
        self._config = config
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not self._config.enabled or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Capability Tuner started (interval=%dm, window=%dd)",
                    self._config.interval_minutes, self._config.window_days)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("tuner run failed (ignored)")
            await asyncio.sleep(self._config.interval_minutes * 60)

    async def run_once(self) -> dict:
        """집계 → 보정 1회. 요약을 반환한다 (관측/테스트용)."""
        rows = await self._metrics.capability_stats(self._config.window_days)
        adjusted: list[dict] = []
        demoted: list[str] = []

        # 모델별 tools 실패 누적 (task 무관 — 강등은 모델 단위 판단)
        tools_acc: dict[str, list[int]] = {}

        for row in rows:
            model = row["model"]
            entry = self._registry.get(model)
            if entry is None:
                continue

            total = int(row["total"] or 0)
            failures = int(row["failures"] or 0)
            acc = tools_acc.setdefault(model, [0, 0])
            acc[0] += int(row["tools_total"] or 0)
            acc[1] += int(row["tools_failures"] or 0)

            if total < self._config.min_samples:
                continue

            cap_key = TASK_TO_CAPABILITY.get(row["task_type"], "code")
            delta = _delta_for(failures / total, total)
            previous = entry.capability_adjustments.get(cap_key, 0.0)
            if delta != previous:
                if delta == 0.0:
                    entry.capability_adjustments.pop(cap_key, None)
                else:
                    entry.capability_adjustments[cap_key] = delta
                adjusted.append({"model": model, "capability": cap_key,
                                 "delta": delta, "failure_rate": round(failures / total, 3),
                                 "samples": total})
                logger.info("tuner: %s %s adjustment %+.0f (failure rate %.0f%%, samples %d)",
                            model, cap_key, delta, failures / total * 100, total)

        # tools feature 자동 강등 (§5.11-3): 미검증 discovery 모델의 안전장치
        for model, (t_total, t_fail) in tools_acc.items():
            entry = self._registry.get(model)
            if entry is None or "tools" not in entry.features:
                continue
            if t_total < self._config.min_samples * DEMOTE_MIN_SAMPLES_FACTOR:
                continue
            if t_fail / t_total >= self._config.demote_failure_rate:
                entry.features.discard("tools")
                entry.demoted_features.append("tools")
                demoted.append(model)
                logger.warning(
                    "tuner: %s 'tools' feature demoted - tool-included request failure rate %.0f%% "
                    "(%d/%d). immediately excluded from the hard filter", model,
                    t_fail / t_total * 100, t_fail, t_total)

        return {"adjusted": adjusted, "demoted": demoted, "rows": len(rows)}
