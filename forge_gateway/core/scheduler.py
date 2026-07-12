"""Scheduler — 선택 파이프라인: 하드 필터 → 세션 고정 → 스코어링 (DESIGN.md §5.5)

M1에서는 Policy Engine이 없으므로 후보 그룹 = tier 순서(tier1→2→3).
M2에서 Policy Engine이 후보 그룹을 공급하면 select()의 candidate_groups만 교체된다.
"""

import math
import random
import time
from collections import OrderedDict
from typing import Optional

from ..providers.base import (
    ContextLengthExceeded,
    ProviderError,
    RateLimited,
    UpstreamConnectionError,
    UpstreamServerError,
    UpstreamTimeout,
)
from ..settings import ForgeConfig
from .registry import ModelEntry, Registry
from .types import AnalysisResult

TIER_ORDER = ("tier1", "tier2", "tier3")
TIER_PRIORITY = {"tier1": 10.0, "tier2": 6.0, "tier3": 3.0}

# 스코어 가중치 (§5.5) — 쿨다운 모델은 후보에서 제외되므로 별도 패널티 항 없음
W_CAPABILITY = 0.30
W_HEALTH = 0.15
W_LATENCY = 0.15
W_AVAILABILITY = 0.10
W_CONTEXT_FIT = 0.10
W_TIER = 0.10
W_FAILURE = 0.10

TASK_TO_CAPABILITY = {
    "coding": "code",
    "debug": "debug",
    "refactor": "refactor",
    "documentation": "docs",
    "testing": "code",
}

# 레이턴시 점수 로그 스케일 구간 (스코어링 v2, DecisionLog 2026-07-12).
# 기존 선형 구간(100ms→10 ~ 2000ms→0)은 2초 이상을 전부 0점으로 포화시켜
# "다소 느림"(1.5s)과 "치명적으로 느림"(18s)을 구분하지 못했다 — 실측으로 같은
# 모델이 호스팅에 따라 TTFT 10배 이상 차이남이 확인됨 (Research.md 2026-07-11).
LATENCY_FLOOR_MS = 200.0    # 이하 = 10점 (TTFT 기준 최상위권)
LATENCY_CEIL_MS = 30_000.0  # 이상 = 0점 (실측 최악값까지 수용)
_LATENCY_LOG_SPAN = math.log10(LATENCY_CEIL_MS) - math.log10(LATENCY_FLOOR_MS)

# 콜드 스타트 prior: 실측 없으면 speed capability를 레이턴시 점수로 환산.
# defaults.capability(7)가 정확히 기존 중립값 5.0에 대응하도록 앵커 —
# 시드 없는 모델의 동작은 v1과 동일하고, 시드된 speed만 차등을 만든다.
_SPEED_NEUTRAL = 7.0
_SPEED_SLOPE = 5.0 / 3.0  # speed 10 → 10점, speed 4 → 0점

MAX_SESSIONS = 10_000


class SessionAffinity:
    """session_key → model_id 고정 (LRU + TTL). 프롬프트 캐시 적중과 대화 내 일관성 (§5.5-1)"""

    def __init__(self, ttl_minutes: int):
        self._ttl = ttl_minutes * 60
        self._map: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, session_key: str) -> Optional[str]:
        if not session_key:
            return None
        item = self._map.get(session_key)
        if item is None:
            return None
        model_id, expires = item
        if time.time() > expires:
            del self._map[session_key]
            return None
        self._map.move_to_end(session_key)
        return model_id

    def pin(self, session_key: str, model_id: str) -> None:
        if not session_key:
            return
        self._map[session_key] = (model_id, time.time() + self._ttl)
        self._map.move_to_end(session_key)
        while len(self._map) > MAX_SESSIONS:
            self._map.popitem(last=False)


class NoCandidateError(Exception):
    """후보가 전부 탈락 — reason은 클라이언트 에러 메시지에 사용 (§5.5-0)"""

    def __init__(self, reason: str, status_code: int = 503):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class Scheduler:
    def __init__(self, config: ForgeConfig, registry: Registry):
        self._config = config
        self._registry = registry
        self._affinity = SessionAffinity(config.scheduler.session_ttl_minutes)

    def adopt_affinity(self, old: "Scheduler") -> None:
        """reload 시 세션 고정을 이관한다 (§5.9) — 리로드가 프롬프트 캐시 적중과
        대화 내 일관성을 지우면 안 된다. 기존 엔트리의 만료 시각은 그대로 유지되고,
        새 TTL 설정은 이후 pin부터 적용된다."""
        self._affinity._map = old._affinity._map

    # --- 선택 ---

    def select(
        self,
        analysis: AnalysisResult,
        exclude: Optional[set[str]] = None,
        min_context_window: int = 0,
        groups: Optional[list[list[ModelEntry]]] = None,
        provider_filter=None,  # Callable[[str], bool] — 스로틀 peek (§5.13)
    ) -> tuple[ModelEntry, dict]:
        """최적 모델을 선택한다. 후보가 없으면 NoCandidateError.

        groups: Policy Engine이 준 순서 있는 후보 그룹 (§5.4). None이면
        기본 라우팅(tier1→2→3) — M1과 동일한 하위 호환.
        min_context_window: context_length_exceeded 상향 failover 시,
        실패한 모델보다 큰 컨텍스트 창을 요구 (§7)
        """
        exclude = exclude or set()
        feature_rejected = 0
        context_rejected = 0
        throttled = 0

        if groups is None:
            groups = [self._registry.by_tier(t) for t in TIER_ORDER]
        allowed_ids = {e.id for g in groups for e in g}

        # 세션 고정은 그룹 순서보다 우선한다 — 고정 모델이 어느 그룹에 있든
        # (정책 후보 포함 + 제외 안 됨 + 가용 + 하드 필터 통과)면 스코어링 없이 그대로 (§5.5-1).
        # 그룹 루프 안에서 확인하면 상위 그룹이 가용해지는 순간 하위 그룹 핀이 무시된다.
        if self._config.scheduler.session_affinity and analysis.session_key:
            pinned_id = self._affinity.get(analysis.session_key)
            if pinned_id and pinned_id not in exclude and pinned_id in allowed_ids:
                pinned = self._registry.get(pinned_id)
                if (
                    pinned is not None
                    and pinned.health.is_available()
                    and (provider_filter is None or provider_filter(pinned.provider))
                    and analysis.required_features <= pinned.features
                    and self._context_fits(pinned, analysis.est_prompt_tokens,
                                           min_context_window)
                ):
                    return pinned, {
                        "tier": pinned.tier,
                        "task": analysis.task,
                        "selected_by": "session_affinity",
                        "score": None,
                    }

        for group in groups:
            candidates = []
            for entry in group:
                if entry.id in exclude or not entry.health.is_available():
                    continue
                if provider_filter is not None and not provider_filter(entry.provider):
                    # 선제 스로틀: rpm 버킷이 빈 provider는 후보에서 잠시 제외 —
                    # 429를 맞기 전에 트래픽이 다른 provider로 분산된다 (§5.13)
                    throttled += 1
                    continue
                if not analysis.required_features <= entry.features:
                    feature_rejected += 1
                    continue
                if not self._context_fits(entry, analysis.est_prompt_tokens, min_context_window):
                    context_rejected += 1
                    continue
                candidates.append(entry)

            if not candidates:
                continue

            scored = sorted(
                ((self._score(e, analysis), e) for e in candidates),
                key=lambda x: x[0],
                reverse=True,
            )
            best_score = scored[0][0]
            top = [e for s, e in scored if s >= best_score * 0.9]
            selected = random.choice(top)  # 동률권 랜덤 = 사실상의 부하 분산

            self._affinity.pin(analysis.session_key, selected.id)
            return selected, {
                "tier": selected.tier,
                "task": analysis.task,
                "selected_by": "score",
                "score": round(best_score, 2),
                "candidates": [
                    {"model": e.id, "score": round(s, 2)} for s, e in scored[:5]
                ],
            }

        # 전 그룹 소진 — 탈락 사유를 구분해 반환 (§5.5-0)
        if throttled and not feature_rejected and not context_rejected:
            raise NoCandidateError(
                "all candidate providers are rate-throttled — retry shortly",
                status_code=503,
            )
        if feature_rejected and not context_rejected:
            missing = ", ".join(sorted(analysis.required_features))
            raise NoCandidateError(
                f"no candidate model supports: {missing}", status_code=400
            )
        if context_rejected:
            raise NoCandidateError(
                f"no candidate model fits estimated context "
                f"({analysis.est_prompt_tokens} tokens)", status_code=400,
            )
        raise NoCandidateError("no available models", status_code=503)

    def explain(
        self,
        analysis: AnalysisResult,
        groups: Optional[list[list[ModelEntry]]] = None,
        provider_filter=None,
    ) -> dict:
        """select()와 동일한 판정을 상태 변경 없이 수행해 사유를 반환한다 (§5.8 route/explain).

        세션 핀을 이동시키지 않고, 동률권 랜덤 대신 최고점을 결정적으로 보고한다.
        """
        if groups is None:
            groups = [self._registry.by_tier(t) for t in TIER_ORDER]
        allowed_ids = {e.id for g in groups for e in g}

        pinned_id = None
        if self._config.scheduler.session_affinity and analysis.session_key:
            pinned_id = self._affinity.get(analysis.session_key)

        out_groups = []
        would_select = None
        for group in groups:
            rows = []
            best: Optional[tuple[float, ModelEntry]] = None
            for entry in group:
                reason = None
                if not entry.health.is_available():
                    reason = f"unavailable ({entry.health.status})"
                elif provider_filter is not None and not provider_filter(entry.provider):
                    reason = "provider rate-throttled"
                elif not analysis.required_features <= entry.features:
                    missing = analysis.required_features - entry.features
                    reason = f"missing features: {', '.join(sorted(missing))}"
                elif not self._context_fits(entry, analysis.est_prompt_tokens, 0):
                    reason = (f"context too small ({entry.context_window} "
                              f"< est {analysis.est_prompt_tokens} tokens)")

                if reason:
                    rows.append({"model": entry.id, "excluded": reason})
                    continue
                score = self._score(entry, analysis)
                rows.append({"model": entry.id, "score": round(score, 2)})
                if best is None or score > best[0]:
                    best = (score, entry)
            out_groups.append(rows)
            if would_select is None and best is not None:
                would_select = {"model": best[1].id, "tier": best[1].tier,
                                "score": round(best[0], 2), "selected_by": "score"}

        # 핀이 유효하면 스코어보다 우선 — select()와 동일 규칙 (§5.5-1)
        if pinned_id and pinned_id in allowed_ids:
            pinned = self._registry.get(pinned_id)
            if (pinned is not None and pinned.health.is_available()
                    and analysis.required_features <= pinned.features
                    and self._context_fits(pinned, analysis.est_prompt_tokens, 0)):
                would_select = {"model": pinned.id, "tier": pinned.tier,
                                "score": None, "selected_by": "session_affinity"}

        return {
            "session_pin": pinned_id,
            "groups": out_groups,
            "would_select": would_select,
        }

    def _context_fits(self, entry: ModelEntry, est_tokens: int, min_window: int) -> bool:
        if entry.context_window is None:
            # 창 크기 미상 — 하드 컷 불가, ContextFit 점수에서만 감점.
            # min_window(상향 failover) 요구도 배제하지 않는다: 기본 설정처럼 전 모델이
            # 미상일 때 전부 탈락시키면 컨텍스트 초과 1회가 fail-closed가 된다 (리뷰 #2)
            return True
        if min_window and entry.context_window <= min_window:
            return False
        return est_tokens <= entry.context_window * 0.9

    # --- 스코어링 (§5.5-2, 부분 점수 전부 0~10) ---

    def _score(self, entry: ModelEntry, analysis: AnalysisResult) -> float:
        cap_key = TASK_TO_CAPABILITY.get(analysis.task, "code")
        capability = float(entry.capabilities.get(cap_key, 7))
        # 학습 루프의 텔레메트리 보정 — 시드(base)를 ±2 이내에서만 움직인다 (§5.11-3)
        adjust = float(entry.capability_adjustments.get(cap_key, 0.0))
        capability = max(0.0, min(10.0, capability + max(-2.0, min(2.0, adjust))))

        h = entry.health
        health = {"healthy": 10.0, "unknown": 5.0}.get(h.status, 0.0)

        latency = self._latency_score(entry)

        rate = h.success_rate()
        availability = 8.0 if rate is None else rate * 10

        context_fit = self._context_fit_score(entry, analysis.est_prompt_tokens)
        tier_priority = TIER_PRIORITY.get(entry.tier, 3.0)
        failure_penalty = min(h.consecutive_failures * 2.0, 10.0)

        score = (
            W_CAPABILITY * capability
            + W_HEALTH * health
            + W_LATENCY * latency
            + W_AVAILABILITY * availability
            + W_CONTEXT_FIT * context_fit
            + W_TIER * tier_priority
            - W_FAILURE * failure_penalty
        )
        return max(score, 0.0)

    @staticmethod
    def _latency_score(entry: ModelEntry) -> float:
        """레이턴시 부분 점수 (0~10, 스코어링 v2 — DecisionLog 2026-07-12).

        실측 EWMA(스트리밍은 TTFT)가 있으면 로그 스케일로 환산 — 200ms 이하 10점,
        30초 이상 0점, 사이는 log10 선형. 실측이 없으면(콜드 스타트) speed
        capability 시드를 prior로 쓴다: speed 7(기본값) = 중립 5.0으로 앵커해
        시드 없는 모델은 v1 동작과 동일하다. 첫 실측이 들어오면 prior는 무시된다.
        """
        ms = entry.health.latency_ms
        if ms <= 0:
            speed = float(entry.capabilities.get("speed", _SPEED_NEUTRAL))
            return max(0.0, min(10.0, 5.0 + (speed - _SPEED_NEUTRAL) * _SPEED_SLOPE))
        if ms <= LATENCY_FLOOR_MS:
            return 10.0
        if ms >= LATENCY_CEIL_MS:
            return 0.0
        position = (math.log10(ms) - math.log10(LATENCY_FLOOR_MS)) / _LATENCY_LOG_SPAN
        return 10.0 * (1.0 - position)

    @staticmethod
    def _context_fit_score(entry: ModelEntry, est_tokens: int) -> float:
        if entry.context_window is None:
            return 5.0
        ratio = est_tokens / entry.context_window if entry.context_window else 1.0
        if ratio <= 0.5:
            return 10.0
        if ratio >= 0.9:
            return 0.0
        return 10.0 * (0.9 - ratio) / 0.4

    # --- 결과 기록 (API 계층이 provider 예외를 그대로 전달) ---

    def record_success(self, model_id: str, latency_ms: float) -> None:
        entry = self._registry.get(model_id)
        if entry:
            entry.health.record_success(latency_ms)

    def record_failure(self, model_id: str, error: Exception) -> str:
        """실패를 상태에 반영하고 metrics용 error_type 문자열을 반환한다."""
        entry = self._registry.get(model_id)
        error_type = self._classify(error)
        if entry:
            sc = self._config.scheduler
            entry.health.record_failure(
                error_type,
                cooldown_seconds=sc.cooldown_seconds,
                max_failures_before_cooldown=sc.max_failures_before_cooldown,
                immediate_cooldown=isinstance(error, RateLimited),
                retry_after=getattr(error, "retry_after", None),
            )
            # 실효 컨텍스트 추정 하향 보정 (§7) — 다음 하드 필터에 즉시 반영
            if isinstance(error, ContextLengthExceeded) and entry.context_window:
                entry.context_window = int(entry.context_window * 0.8)
        return error_type

    def move_pin(self, session_key: str, model_id: str) -> None:
        """failover 시 세션 고정을 새 모델로 이동 (§5.5-1)"""
        self._affinity.pin(session_key, model_id)

    @staticmethod
    def _classify(error: Exception) -> str:
        if isinstance(error, RateLimited):
            return "429"
        if isinstance(error, UpstreamServerError):
            return str(error.status_code or "5xx")
        if isinstance(error, UpstreamTimeout):
            return "timeout"
        if isinstance(error, UpstreamConnectionError):
            return "connect_error"
        if isinstance(error, ContextLengthExceeded):
            return "context_length"
        if isinstance(error, ProviderError):
            return str(error.status_code or "unknown")
        return type(error).__name__
