"""골든 라우팅 회귀 하네스 — 스코어링 v2 (DecisionLog 2026-07-12)

스코어 공식은 제품의 핵심 차별점이라, 공식 변경이 라우팅 품질을 조용히
망가뜨리는 회귀를 막기 위해 "기대 순위"를 테이블로 고정한다. 랜덤 동률권을
피하려고 select() 대신 _score/_latency_score와 explain()(결정적)을 단언한다.

시나리오 근거: docs/Research.md 2026-07-11 실측 — 같은 모델이 호스팅에 따라
TTFT 1.5초~18초로 10배 이상 차이나는데, v1 레이턴시 점수는 2초 이상을 전부
0점으로 포화시켜 이 차이를 구분하지 못했다.
"""

import unittest

from forge_gateway.core.registry import Registry
from forge_gateway.core.scheduler import Scheduler
from forge_gateway.core.types import AnalysisResult
from forge_gateway.settings import ForgeConfig, ModelOverride, ProviderConfig


def _make(models):
    config = ForgeConfig(
        providers=[ProviderConfig(name="p", api_key_env="P_KEY", free=True)],
        models=models,
    )
    registry = Registry(config)
    return Scheduler(config, registry), registry


def _analysis(task="coding", est_tokens=100):
    return AnalysisResult(task=task, est_prompt_tokens=est_tokens,
                          required_features=set(), session_key="")


class LatencyScoreGoldenTests(unittest.TestCase):
    """_latency_score의 고정점 — 앵커가 움직이면 라우팅 전체가 움직인다."""

    def setUp(self):
        self.scheduler, self.registry = _make(
            [ModelOverride(id="p:m", tier="tier1")])
        self.entry = self.registry.get("p:m")

    def _score_at(self, ms):
        self.entry.health.latency_ms = ms
        return Scheduler._latency_score(self.entry)

    def test_floor_and_ceiling(self):
        self.assertEqual(self._score_at(150), 10.0)
        self.assertEqual(self._score_at(200), 10.0)
        self.assertEqual(self._score_at(30_000), 0.0)
        self.assertEqual(self._score_at(60_000), 0.0)

    def test_log_scale_differentiates_beyond_two_seconds(self):
        # v1은 2초 이상 전부 0점 — "다소 느림"과 "치명적 느림"이 동점이었다
        s_2500, s_18000 = self._score_at(2_500), self._score_at(18_000)
        self.assertGreater(s_2500, s_18000)
        self.assertGreater(s_2500, 3.0)   # 다소 느림은 중간권 유지
        self.assertLess(s_18000, 1.5)     # 치명적 느림은 바닥권

    def test_strictly_monotonic_decreasing(self):
        scores = [self._score_at(ms) for ms in (500, 2_000, 6_000, 20_000)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_cold_start_prior_from_speed_seed(self):
        self.entry.health.latency_ms = 0.0  # 실측 없음
        self.entry.capabilities["speed"] = 10
        self.assertEqual(Scheduler._latency_score(self.entry), 10.0)
        self.entry.capabilities["speed"] = 9
        self.assertAlmostEqual(Scheduler._latency_score(self.entry), 8.33, places=1)
        self.entry.capabilities["speed"] = 4
        self.assertEqual(Scheduler._latency_score(self.entry), 0.0)
        self.entry.capabilities["speed"] = 0  # 클램프
        self.assertEqual(Scheduler._latency_score(self.entry), 0.0)

    def test_cold_start_without_seed_stays_v1_neutral(self):
        # defaults.capability(7) → 정확히 v1 중립값 5.0 (미시드 모델 동작 불변)
        self.entry.health.latency_ms = 0.0
        self.assertEqual(self.entry.capabilities["speed"], 7)
        self.assertEqual(Scheduler._latency_score(self.entry), 5.0)


class RoutingOrderGoldenTests(unittest.TestCase):
    """기대 순위 테이블 — 공식이 바뀌어도 이 순서는 유지돼야 한다."""

    def test_fast_host_beats_slow_host_same_model(self):
        # Research.md 실측: deepseek-v4-pro가 Fireworks(1.5s) vs NVIDIA(18s)
        scheduler, registry = _make([
            ModelOverride(id="p:fast-host", tier="tier1",
                          capabilities={"code": 10}),
            ModelOverride(id="p:slow-host", tier="tier1",
                          capabilities={"code": 10}),
        ])
        registry.get("p:fast-host").health.record_success(1_500.0)
        registry.get("p:slow-host").health.record_success(18_000.0)
        a = scheduler._score(registry.get("p:fast-host"), _analysis())
        b = scheduler._score(registry.get("p:slow-host"), _analysis())
        self.assertGreater(a, b)
        # explain은 동률권 랜덤 없이 최고점을 보고한다 — E2E 경로 확인
        result = scheduler.explain(_analysis())
        self.assertEqual(result["would_select"]["model"], "p:fast-host")

    def test_seeded_cold_start_beats_measured_slow(self):
        # 신규 등록된 빠른 유료 호스트(speed 9 시드, 미실측)가 실측 18초 무료보다 위
        scheduler, registry = _make([
            ModelOverride(id="p:cold-fast", tier="tier1",
                          capabilities={"code": 10, "speed": 9}),
            ModelOverride(id="p:warm-slow", tier="tier1",
                          capabilities={"code": 10}),
        ])
        registry.get("p:warm-slow").health.record_success(18_000.0)
        a = scheduler._score(registry.get("p:cold-fast"), _analysis())
        b = scheduler._score(registry.get("p:warm-slow"), _analysis())
        self.assertGreater(a, b)

    def test_capability_still_dominates_speed(self):
        # 속도는 실력(capability)을 뒤집지 않는다 — tier1 강모델(느림) > tier3 평범(빠름).
        # heavy-work/hard-tasks 정책의 "느려도 강한 모델" 의도 보존 (DecisionLog 2026-07-11)
        scheduler, registry = _make([
            ModelOverride(id="p:strong-slow", tier="tier1",
                          capabilities={"code": 10}),
            ModelOverride(id="p:mediocre-fast", tier="tier3",
                          capabilities={"code": 7}),
        ])
        registry.get("p:strong-slow").health.record_success(18_000.0)
        registry.get("p:mediocre-fast").health.record_success(1_000.0)
        a = scheduler._score(registry.get("p:strong-slow"), _analysis())
        b = scheduler._score(registry.get("p:mediocre-fast"), _analysis())
        self.assertGreater(a, b)

    def test_measurement_overrides_prior(self):
        # 실측이 들어오면 시드 prior는 무시된다 — 느린 실측이 좋은 시드를 이긴다
        scheduler, registry = _make([
            ModelOverride(id="p:seeded", tier="tier1",
                          capabilities={"code": 10, "speed": 10}),
        ])
        entry = registry.get("p:seeded")
        entry.health.record_success(25_000.0)
        self.assertLess(Scheduler._latency_score(entry), 1.0)


if __name__ == "__main__":
    unittest.main()
