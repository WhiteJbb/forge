"""PromExporter 단위 테스트 (DESIGN.md §5.7, forge_gateway/core/prom.py)"""

import unittest

from forge_gateway.core.prom import PromExporter
from forge_gateway.core.registry import Registry
from forge_gateway.core.throttle import ProviderThrottle
from forge_gateway.settings import ForgeConfig, ModelOverride, ProviderConfig
from forge_gateway.storage.base import RequestMetric


def _config(**overrides) -> ForgeConfig:
    base = dict(
        providers=[
            ProviderConfig(name="nvidia", api_key_env="NVIDIA_API_KEY", free=True, rpm=60,
                            max_concurrent=2),
            ProviderConfig(name="paid", api_key_env="PAID_API_KEY", free=False),
        ],
        models=[
            ModelOverride(id="nvidia:model-a", tier="tier1", capabilities={"code": 9}),
            ModelOverride(id="paid:model-p", tier="tier2"),
        ],
    )
    base.update(overrides)
    return ForgeConfig(**base)


def _metric(**overrides) -> RequestMetric:
    base = dict(
        request_id="req-1",
        timestamp="2026-07-09T00:00:00Z",
        model="nvidia:model-a",
        provider="nvidia",
        tier="tier1",
        task_type="coding",
        attempt=1,
        latency_ms=250.0,
        ttft_ms=None,
        prompt_tokens=100,
        completion_tokens=50,
        had_tools=False,
        success=True,
        status_code=200,
        error_type=None,
        cost=0.01,
    )
    base.update(overrides)
    return RequestMetric(**base)


def _decode(exporter: PromExporter) -> str:
    body, content_type = exporter.render()
    return body.decode("utf-8")


class EventMetricsTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry(_config())
        self.exporter = PromExporter(self.registry)

    def test_success_counter_line_present(self):
        self.exporter.on_record(_metric(success=True))
        text = _decode(self.exporter)
        self.assertIn(
            'forge_requests_total{model="nvidia:model-a",outcome="success",'
            'provider="nvidia",task="coding"} 1.0',
            text,
        )

    def test_error_counter_uses_error_type_label(self):
        self.exporter.on_record(_metric(success=False, error_type="500"))
        text = _decode(self.exporter)
        self.assertIn(
            'forge_requests_total{model="nvidia:model-a",outcome="500",'
            'provider="nvidia",task="coding"} 1.0',
            text,
        )

    def test_error_without_error_type_falls_back_to_error_label(self):
        self.exporter.on_record(_metric(success=False, error_type=None))
        text = _decode(self.exporter)
        self.assertIn('outcome="error"', text)

    def test_latency_histogram_present(self):
        self.exporter.on_record(_metric(latency_ms=250.0))
        text = _decode(self.exporter)
        self.assertIn('forge_request_latency_seconds_bucket', text)
        self.assertIn('forge_request_latency_seconds_sum{model="nvidia:model-a"} 0.25', text)

    def test_ttft_histogram_only_when_present(self):
        self.exporter.on_record(_metric(ttft_ms=None))
        text = _decode(self.exporter)
        # ttft_ms가 None이면 관측치가 없어야 함 (sum 라인이 아예 없음)
        self.assertNotIn("forge_request_ttft_seconds_sum", text)

        self.exporter.on_record(_metric(ttft_ms=500.0))
        text = _decode(self.exporter)
        self.assertIn('forge_request_ttft_seconds_sum{model="nvidia:model-a"} 0.5', text)

    def test_token_and_cost_counters_accumulate(self):
        self.exporter.on_record(_metric(prompt_tokens=100, completion_tokens=50, cost=0.01))
        self.exporter.on_record(_metric(prompt_tokens=200, completion_tokens=25, cost=0.02))
        text = _decode(self.exporter)
        self.assertIn(
            'forge_tokens_total{direction="prompt",model="nvidia:model-a"} 300.0', text
        )
        self.assertIn(
            'forge_tokens_total{direction="completion",model="nvidia:model-a"} 75.0', text
        )
        self.assertIn(
            'forge_cost_dollars_total{model="nvidia:model-a"} 0.03', text
        )


class MalformedMetricTests(unittest.TestCase):
    def test_on_record_never_raises_on_bad_metric(self):
        registry = Registry(_config())
        exporter = PromExporter(registry)

        class Weird:
            """RequestMetric이 아닌, 필드가 없는/이상한 객체."""

            model = None
            provider = None
            task_type = None
            success = False
            error_type = None
            latency_ms = None
            ttft_ms = "not-a-number"
            prompt_tokens = None
            completion_tokens = None
            cost = None

        try:
            exporter.on_record(Weird())  # type: ignore[arg-type]
        except Exception as e:  # pragma: no cover - 테스트 실패 시에만 도달
            self.fail(f"on_record이 예외를 전파함: {e!r}")

        # 이상값을 넣은 후에도 render 자체는 정상 동작해야 한다
        exporter.render()


class GaugeTests(unittest.TestCase):
    def _registry(self):
        registry = Registry(_config())
        return registry

    def test_health_status_gauge_reflects_current_status(self):
        registry = self._registry()
        entry = registry.get("nvidia:model-a")
        entry.health.record_success(latency_ms=42.0)  # status -> healthy
        exporter = PromExporter(registry)
        text = _decode(exporter)
        self.assertIn(
            'forge_model_health{model="nvidia:model-a",status="healthy"} 1.0', text
        )
        self.assertIn(
            'forge_model_health{model="nvidia:model-a",status="unhealthy"} 0.0', text
        )

    def test_cooldown_remaining_gauge_reflects_cooldown(self):
        registry = self._registry()
        entry = registry.get("nvidia:model-a")
        entry.health.enter_cooldown(120.0)
        exporter = PromExporter(registry)
        text = _decode(exporter)
        self.assertIn(
            'forge_model_health{model="nvidia:model-a",status="cooldown"} 1.0', text
        )
        # 정확한 초는 시간 경과로 흔들릴 수 있으므로 라인 존재 + 양수만 확인
        found = False
        for line in text.splitlines():
            if line.startswith('forge_model_cooldown_remaining_seconds{model="nvidia:model-a"}'):
                value = float(line.rsplit(" ", 1)[1])
                self.assertGreater(value, 0)
                found = True
        self.assertTrue(found, "cooldown gauge 라인을 찾지 못함")

    def test_latency_ewma_gauge_reflects_health(self):
        registry = self._registry()
        entry = registry.get("nvidia:model-a")
        entry.health.record_success(latency_ms=42.0)
        entry.health.record_success(latency_ms=142.0)  # ewma 적용
        exporter = PromExporter(registry)
        text = _decode(exporter)
        expected = entry.health.latency_ms
        found = False
        for line in text.splitlines():
            if line.startswith('forge_model_latency_ewma_ms{model="nvidia:model-a"}'):
                value = float(line.rsplit(" ", 1)[1])
                self.assertAlmostEqual(value, expected, places=3)
                found = True
        self.assertTrue(found)

    def test_discovered_models_excluded_from_gauges(self):
        registry = self._registry()
        registry.merge_discovered("nvidia", ["brand-new-discovered-model"])
        exporter = PromExporter(registry)
        text = _decode(exporter)
        self.assertNotIn("brand-new-discovered-model", text)

    def test_discovered_models_not_excluded_from_counters(self):
        # 카운터는 트래픽이 있는 모델만 자연히 라벨이 생기므로 discovery 여부와 무관
        registry = self._registry()
        registry.merge_discovered("nvidia", ["brand-new-discovered-model"])
        exporter = PromExporter(registry)
        exporter.on_record(_metric(model="nvidia:brand-new-discovered-model"))
        text = _decode(exporter)
        self.assertIn("brand-new-discovered-model", text)

    def test_gauges_refresh_between_scrapes(self):
        registry = self._registry()
        entry = registry.get("nvidia:model-a")
        exporter = PromExporter(registry)
        text1 = _decode(exporter)
        self.assertIn(
            'forge_model_health{model="nvidia:model-a",status="unknown"} 1.0', text1
        )
        entry.health.record_success(latency_ms=10.0)
        text2 = _decode(exporter)
        self.assertIn(
            'forge_model_health{model="nvidia:model-a",status="healthy"} 1.0', text2
        )
        self.assertIn(
            'forge_model_health{model="nvidia:model-a",status="unknown"} 0.0', text2
        )


class ThrottleGaugeTests(unittest.TestCase):
    def test_throttle_gauges_present_when_throttle_given(self):
        registry = Registry(_config())
        throttle = ProviderThrottle(_config().providers)
        exporter = PromExporter(registry, throttle=throttle)
        text = _decode(exporter)
        self.assertIn('forge_throttle_tokens_remaining{provider="nvidia"} 60.0', text)
        self.assertIn('forge_throttle_in_flight{provider="nvidia"} 0.0', text)

    def test_throttle_gauges_absent_when_no_throttle(self):
        registry = Registry(_config())
        exporter = PromExporter(registry, throttle=None)
        text = _decode(exporter)
        self.assertNotIn("forge_throttle_tokens_remaining", text)
        self.assertNotIn("forge_throttle_in_flight", text)


class ContentTypeAndIsolationTests(unittest.TestCase):
    def test_render_returns_bytes_and_content_type(self):
        registry = Registry(_config())
        exporter = PromExporter(registry)
        body, content_type = exporter.render()
        self.assertIsInstance(body, bytes)
        self.assertIn("text/plain", content_type)

    def test_two_instances_do_not_collide(self):
        # 자체 CollectorRegistry를 쓰지 않으면 전역 REGISTRY 등록 시 중복 이름으로 예외
        registry = Registry(_config())
        exporter1 = PromExporter(registry)
        exporter2 = PromExporter(registry)  # 두 번째 생성이 예외 없이 성공해야 함
        exporter1.on_record(_metric())
        exporter2.on_record(_metric())
        exporter1.render()
        exporter2.render()

    def test_many_instances_do_not_collide(self):
        registry = Registry(_config())
        exporters = [PromExporter(registry) for _ in range(10)]
        for exporter in exporters:
            exporter.render()


if __name__ == "__main__":
    unittest.main()
