"""요청 처리 의존성 스냅샷 — reload 원자성의 계약 (DESIGN.md §5.9)

Deps는 불변(frozen)이다. /admin/reload는 컴포넌트를 전부 새로 조립한 뒤
DepsRef.current 참조 "하나"만 교체한다 — 요청은 시작 시점에 ref.current를
한 번 읽어 그 스냅샷으로 끝까지 처리하므로, 교체 도중에도 신/구 컴포넌트가
섞여 보이지 않는다 (기존에는 deps.* 필드를 순차 대입해 혼합 창이 있었다).

MetricsEngine과 RequestAnalyzer는 reload를 가로질러 살아남는 장수 컴포넌트다
(큐/무상태) — 새 스냅샷에 같은 인스턴스를 담는다.
"""

from dataclasses import dataclass
from typing import Optional

from ..core.analyzer import RequestAnalyzer
from ..core.metrics import MetricsEngine
from ..core.policy import PolicyEngine
from ..core.registry import Registry
from ..core.scheduler import Scheduler
from ..core.throttle import ProviderThrottle
from ..providers.base import Provider
from ..settings import ForgeConfig


@dataclass(frozen=True)
class Deps:
    """요청 하나가 참조하는 컴포넌트 스냅샷 — server.py가 조립한다."""

    config: ForgeConfig
    registry: Registry
    scheduler: Scheduler
    analyzer: RequestAnalyzer
    metrics: MetricsEngine
    providers: dict[str, Provider]
    policy: Optional[PolicyEngine] = None  # None이면 기본 tier 라우팅 (하위 호환)
    throttle: Optional[ProviderThrottle] = None  # None이면 선제 스로틀 없음


class DepsRef:
    """현재 스냅샷을 가리키는 단일 참조 — reload는 이 속성 대입 하나만 수행한다.

    파이썬 속성 대입은 원자적이므로 락 없이도 요청은 항상 완전한 신/구
    스냅샷 중 하나만 본다.
    """

    __slots__ = ("current",)

    def __init__(self, deps: Deps):
        self.current = deps
