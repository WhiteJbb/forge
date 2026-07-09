"""Model Registry — 모델 메타데이터와 상태의 단일 소스 (DESIGN.md §5.2)

Scheduler / HealthMonitor / Dashboard / (M2) PolicyEngine은 전부 이 Registry만 본다.
상태 저장은 인메모리 (멀티 인스턴스는 M3 StateStore에서).
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..settings import ForgeConfig

WINDOW_SIZE = 50  # availability 슬라이딩 윈도 (§5.5 — 누적 실패율은 영구 낙인이 됨)


class ModelHealth:
    """단일 모델의 런타임 상태 — passive 신호(실트래픽)가 1차 소스 (§5.6)"""

    def __init__(self, ewma_alpha: float = 0.3):
        self._alpha = ewma_alpha
        self.status: Literal["healthy", "unknown", "unhealthy", "cooldown"] = "unknown"
        self.latency_ms: float = 0.0        # EWMA. 스트리밍은 TTFT 기준 (§5.5)
        self.last_used: float = 0.0         # 마지막 실트래픽 시각 (probe 대상 판정용)
        self.last_check: float = 0.0
        self.consecutive_failures: int = 0
        self.last_error: Optional[str] = None
        self.cooldown_until: float = 0.0
        self._window: deque[bool] = deque(maxlen=WINDOW_SIZE)
        # 누적 카운터 (대시보드 표시용 — 스코어링에는 쓰지 않는다)
        self.total_requests: int = 0
        self.total_failures: int = 0
        self.total_429: int = 0
        self.total_5xx: int = 0
        self.total_timeouts: int = 0

    # --- 기록 ---

    def record_success(self, latency_ms: float) -> None:
        now = time.time()
        self.status = "healthy"
        self.last_used = now
        self.last_check = now
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.total_requests += 1
        self._window.append(True)
        if self.latency_ms <= 0:
            self.latency_ms = latency_ms
        else:
            self.latency_ms = self._alpha * latency_ms + (1 - self._alpha) * self.latency_ms

    def record_failure(
        self,
        error_type: str,
        *,
        cooldown_seconds: float,
        max_failures_before_cooldown: int,
        immediate_cooldown: bool = False,
        retry_after: Optional[float] = None,
    ) -> None:
        now = time.time()
        self.last_used = now
        self.last_check = now
        self.consecutive_failures += 1
        self.last_error = error_type
        self.total_requests += 1
        self.total_failures += 1
        self._window.append(False)

        if error_type == "429":
            self.total_429 += 1
        elif error_type.startswith("5"):
            self.total_5xx += 1
        elif error_type == "timeout":
            self.total_timeouts += 1

        # 429는 즉시 쿨다운 (Retry-After 존중), 그 외는 연속 실패 임계 (§5.5)
        if immediate_cooldown:
            self.enter_cooldown(retry_after if retry_after else cooldown_seconds)
        elif self.consecutive_failures >= max_failures_before_cooldown:
            self.enter_cooldown(cooldown_seconds)

    def enter_cooldown(self, seconds: float) -> None:
        self.status = "cooldown"
        self.cooldown_until = time.time() + seconds

    def set_probe_result(self, ok: bool, latency_ms: float = 0.0) -> None:
        """active probe 결과 반영 — 쿨다운 상태는 건드리지 않는다"""
        self.last_check = time.time()
        if self.status == "cooldown":
            return
        if ok:
            self.status = "healthy"
            if latency_ms > 0 and self.latency_ms <= 0:
                self.latency_ms = latency_ms
        else:
            self.status = "unhealthy"

    # --- 조회 ---

    def check_cooldown_expired(self) -> None:
        if self.status == "cooldown" and time.time() > self.cooldown_until:
            self.status = "unknown"
            self.cooldown_until = 0.0
            self.consecutive_failures = 0

    def is_available(self) -> bool:
        self.check_cooldown_expired()
        return self.status not in ("cooldown", "unhealthy")

    def success_rate(self) -> Optional[float]:
        """슬라이딩 윈도 성공률 (0~1). 데이터 없으면 None."""
        if not self._window:
            return None
        return sum(self._window) / len(self._window)

    def cooldown_remaining(self) -> int:
        if self.status == "cooldown" and self.cooldown_until > time.time():
            return int(self.cooldown_until - time.time())
        return 0

    def to_dict(self) -> dict:
        rate = self.success_rate()
        return {
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "cooldown_remaining": self.cooldown_remaining(),
            "consecutive_failures": self.consecutive_failures,
            "success_rate_window": round(rate, 3) if rate is not None else None,
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "total_429": self.total_429,
            "total_5xx": self.total_5xx,
            "total_timeouts": self.total_timeouts,
            "last_error": self.last_error,
        }


@dataclass
class ModelEntry:
    """Registry의 단위 — forge id는 'provider:provider_model_id'"""

    id: str
    provider: str
    provider_model_id: str
    tier: str
    capabilities: dict[str, int]
    features: set[str]
    context_window: Optional[int] = None
    price_per_mtok: Optional[tuple[float, float]] = None  # None = unknown (§5.12)
    source: Literal["config", "discovered"] = "config"
    health: ModelHealth = field(default_factory=ModelHealth)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "tier": self.tier,
            "capabilities": self.capabilities,
            "features": sorted(self.features),
            "context_window": self.context_window,
            "source": self.source,
            **self.health.to_dict(),
        }


class Registry:
    """모델 목록 + 상태의 단일 소스. config 오버라이드가 discovery보다 우선."""

    def __init__(self, config: ForgeConfig):
        self._config = config
        self._entries: dict[str, ModelEntry] = {}
        self._build_from_config()

    def _build_from_config(self) -> None:
        d = self._config.defaults
        cap_keys = ("code", "debug", "refactor", "docs", "context", "speed")
        for m in self._config.models:
            provider, pm_id = m.id.split(":", 1)
            pconf = self._config.provider(provider)
            caps = {k: m.capabilities.get(k, d.capability) for k in cap_keys}
            price = m.price_per_mtok
            if price is None and pconf and pconf.free:
                price = (0.0, 0.0)
            self._entries[m.id] = ModelEntry(
                id=m.id,
                provider=provider,
                provider_model_id=pm_id,
                tier=m.tier or d.tier,
                capabilities=caps,
                features=set(m.features if m.features is not None else d.features),
                context_window=m.context_window,
                price_per_mtok=price,
                source="config",
                health=ModelHealth(self._config.scheduler.latency_ewma_alpha),
            )

    # --- 조회 ---

    def get(self, model_id: str) -> Optional[ModelEntry]:
        return self._entries.get(model_id)

    def all(self) -> list[ModelEntry]:
        return list(self._entries.values())

    def by_tier(self, tier: str) -> list[ModelEntry]:
        return [e for e in self._entries.values() if e.tier == tier]

    def in_cooldown(self) -> list[ModelEntry]:
        return [e for e in self._entries.values() if e.health.status == "cooldown"]

    def resolve_client_model(self, requested: str) -> Optional[ModelEntry]:
        """클라이언트가 보낸 model 값이 실제 모델 id면 해당 엔트리, 아니면 None(→ auto 라우팅).

        'provider:model' 정확 일치 외에 provider 접두어 없는 'model' 일치도 허용
        (기존 클라이언트 설정 호환).
        """
        if requested in self._entries:
            return self._entries[requested]
        matches = [e for e in self._entries.values() if e.provider_model_id == requested]
        return matches[0] if len(matches) == 1 else None

    # --- Auto Discovery 병합 (M2에서 배선, 계약만 확정) ---

    def merge_discovered(self, provider: str, model_ids: list[str]) -> list[str]:
        """discovery 결과를 병합하고 신규 등록된 forge id 목록을 반환한다."""
        d = self._config.defaults
        pconf = self._config.provider(provider)
        added = []
        for pm_id in model_ids:
            forge_id = f"{provider}:{pm_id}"
            if forge_id in self._entries:
                continue
            self._entries[forge_id] = ModelEntry(
                id=forge_id,
                provider=provider,
                provider_model_id=pm_id,
                tier=d.tier,
                capabilities={k: d.capability for k in
                              ("code", "debug", "refactor", "docs", "context", "speed")},
                features=set(d.features),
                price_per_mtok=(0.0, 0.0) if (pconf and pconf.free) else None,
                source="discovered",
                health=ModelHealth(self._config.scheduler.latency_ewma_alpha),
            )
            added.append(forge_id)
        return added
