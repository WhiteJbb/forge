"""Prometheus exporter — /metrics 텍스트 포맷 (DESIGN.md §5.7, M3)

전역 REGISTRY를 사용하지 않고 인스턴스마다 자체 CollectorRegistry를 둔다 —
테스트에서 반복 생성하거나 /admin/reload에서 재생성해도 "Duplicated
timeseries" 충돌이 나지 않게 하기 위함.

카운터/히스토그램(이벤트 메트릭)은 on_record()로 요청 경로에서 누적한다.
게이지(스크레이프 시점 상태)는 render() 직전에 Registry/ProviderThrottle의
현재 스냅샷으로 다시 계산한다 — 값이 항상 "지금" 상태를 반영해야 하므로
누적이 아니라 매번 재구성.

discovery로 늘어난 모델(수백 개)까지 게이지에 노출하면 카디널리티가
폭발하므로, 스크레이프 시점 게이지는 config 모델(entry.source == "config")
로만 제한한다. 카운터/히스토그램은 실제 트래픽이 발생한 모델만 자연히
라벨이 생기므로 별도 제한이 필요 없다.
"""

import logging
from typing import Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from ..storage.base import RequestMetric
from .registry import Registry
from .throttle import ProviderThrottle

logger = logging.getLogger(__name__)

_LATENCY_BUCKETS = (0.5, 1, 2, 5, 10, 20, 40, 60, 120, 300)
_TTFT_BUCKETS = (0.2, 0.5, 1, 2, 5, 10, 20, 30)

# ModelHealth.status의 가능한 값 (registry.py 참고) — 게이지 라벨을 고정해
# 스크레이프마다 라벨 집합이 흔들리지 않게 한다.
_HEALTH_STATUSES = ("healthy", "unknown", "unhealthy", "cooldown")


class PromExporter:
    """Registry/ProviderThrottle을 관측해 Prometheus 텍스트 포맷을 만든다."""

    def __init__(self, registry: Registry, throttle: Optional[ProviderThrottle] = None):
        self._registry = registry
        self._throttle = throttle
        self._cr = CollectorRegistry()

        # --- 이벤트 카운터/히스토그램 ---

        self._requests_total = Counter(
            "forge_requests_total",
            "요청 수 (outcome별 — success 또는 error_type)",
            ["model", "provider", "task", "outcome"],
            registry=self._cr,
        )
        self._latency_seconds = Histogram(
            "forge_request_latency_seconds",
            "요청 레이턴시(초)",
            ["model"],
            buckets=_LATENCY_BUCKETS,
            registry=self._cr,
        )
        self._ttft_seconds = Histogram(
            "forge_request_ttft_seconds",
            "스트리밍 첫 토큰까지 시간(초)",
            ["model"],
            buckets=_TTFT_BUCKETS,
            registry=self._cr,
        )
        self._tokens_total = Counter(
            "forge_tokens_total",
            "토큰 수 (direction=prompt/completion)",
            ["model", "direction"],
            registry=self._cr,
        )
        self._cost_dollars_total = Counter(
            "forge_cost_dollars_total",
            "누적 비용(USD)",
            ["model"],
            registry=self._cr,
        )

        # --- 스크레이프 시점 게이지 ---

        self._model_health = Gauge(
            "forge_model_health",
            "모델 상태 (해당 status면 1, 모델당 라벨 하나만 1)",
            ["model", "status"],
            registry=self._cr,
        )
        self._latency_ewma = Gauge(
            "forge_model_latency_ewma_ms",
            "모델 레이턴시 EWMA(ms)",
            ["model"],
            registry=self._cr,
        )
        self._cooldown_remaining = Gauge(
            "forge_model_cooldown_remaining_seconds",
            "쿨다운 잔여 시간(초)",
            ["model"],
            registry=self._cr,
        )

        self._throttle_tokens_remaining: Optional[Gauge] = None
        self._throttle_in_flight: Optional[Gauge] = None
        if self._throttle is not None:
            self._throttle_tokens_remaining = Gauge(
                "forge_throttle_tokens_remaining",
                "provider별 token bucket 잔여량",
                ["provider"],
                registry=self._cr,
            )
            self._throttle_in_flight = Gauge(
                "forge_throttle_in_flight",
                "provider별 현재 in-flight 요청 수",
                ["provider"],
                registry=self._cr,
            )

    # ------------------------------------------------------------- events

    def on_record(self, metric: "RequestMetric") -> None:
        """요청 경로에서 호출된다 — 어떤 예외도 절대 전파하지 않는다."""
        try:
            self._on_record(metric)
        except Exception:
            logger.exception("PromExporter.on_record 실패 (무시)")

    def _on_record(self, metric: "RequestMetric") -> None:
        model = metric.model or "unknown"
        provider = metric.provider or "unknown"
        task = metric.task_type or "unknown"
        outcome = "success" if metric.success else (metric.error_type or "error")

        self._requests_total.labels(
            model=model, provider=provider, task=task, outcome=outcome
        ).inc()

        latency_ms = metric.latency_ms or 0.0
        self._latency_seconds.labels(model=model).observe(latency_ms / 1000.0)

        if metric.ttft_ms is not None:
            self._ttft_seconds.labels(model=model).observe(metric.ttft_ms / 1000.0)

        prompt_tokens = metric.prompt_tokens or 0
        completion_tokens = metric.completion_tokens or 0
        if prompt_tokens:
            self._tokens_total.labels(model=model, direction="prompt").inc(prompt_tokens)
        if completion_tokens:
            self._tokens_total.labels(model=model, direction="completion").inc(completion_tokens)

        cost = metric.cost or 0.0
        if cost:
            self._cost_dollars_total.labels(model=model).inc(cost)

    # ------------------------------------------------------------- gauges

    def _refresh_gauges(self) -> None:
        """Registry/ProviderThrottle의 현재 상태로 게이지를 재구성한다.

        누적이 아니라 매 스크레이프마다 clear 후 다시 채운다 — 그래야
        모델이 사라지거나(예: 향후 config 변경) 상태가 바뀌었을 때 이전
        값이 유령으로 남지 않는다.
        """
        self._model_health.clear()
        self._latency_ewma.clear()
        self._cooldown_remaining.clear()

        for entry in self._registry.all():
            if entry.source != "config":
                continue  # 카디널리티 제한 — discovery 모델은 게이지에서 제외
            for status in _HEALTH_STATUSES:
                value = 1 if entry.health.status == status else 0
                self._model_health.labels(model=entry.id, status=status).set(value)
            self._latency_ewma.labels(model=entry.id).set(entry.health.latency_ms)
            self._cooldown_remaining.labels(model=entry.id).set(
                entry.health.cooldown_remaining()
            )

        if self._throttle is not None:
            assert self._throttle_tokens_remaining is not None
            assert self._throttle_in_flight is not None
            self._throttle_tokens_remaining.clear()
            self._throttle_in_flight.clear()
            snapshot = self._throttle.snapshot()
            for provider_name, stats in snapshot.items():
                tokens_remaining = stats.get("tokens_remaining")
                if tokens_remaining is not None:
                    self._throttle_tokens_remaining.labels(provider=provider_name).set(
                        tokens_remaining
                    )
                self._throttle_in_flight.labels(provider=provider_name).set(
                    stats.get("in_flight", 0)
                )

    # ------------------------------------------------------------- render

    def render(self) -> "tuple[bytes, str]":
        """Prometheus 텍스트 포맷으로 렌더링. (exposition bytes, content_type)."""
        self._refresh_gauges()
        return generate_latest(self._cr), CONTENT_TYPE_LATEST
