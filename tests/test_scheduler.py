"""Scheduler 선택 파이프라인 테스트 (DESIGN.md §5.5, src/core/scheduler.py)

하드 필터 → 세션 고정 → 스코어링 순서를 검증한다.
"""

import time
import unittest
from unittest.mock import patch

from forge_gateway.core.registry import Registry
from forge_gateway.core.scheduler import NoCandidateError, Scheduler
from forge_gateway.core.types import AnalysisResult
from forge_gateway.providers.base import ContextLengthExceeded, RateLimited, UpstreamServerError
from forge_gateway.settings import ForgeConfig, ModelOverride, ProviderConfig


def _make_scheduler(models, **sched_overrides):
    config = ForgeConfig(
        providers=[ProviderConfig(name="nvidia", api_key_env="NVIDIA_API_KEY", free=True)],
        models=models,
        scheduler={
            "cooldown_seconds": 300,
            "max_failures_before_cooldown": 3,
            "max_attempts": 4,
            "session_affinity": True,
            "session_ttl_minutes": 30,
            **sched_overrides,
        },
    )
    registry = Registry(config)
    scheduler = Scheduler(config, registry)
    return scheduler, registry


def _analysis(task="coding", est_tokens=100, features=None, session_key=""):
    return AnalysisResult(
        task=task,
        est_prompt_tokens=est_tokens,
        required_features=features or set(),
        session_key=session_key,
    )


class HardFilterFeatureTests(unittest.TestCase):
    def test_model_missing_feature_excluded(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:no-tools", tier="tier1", features=["streaming"]),
            ]
        )
        with self.assertRaises(NoCandidateError) as ctx:
            scheduler.select(_analysis(features={"tools"}))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("tools", ctx.exception.reason)

    def test_model_with_feature_selected(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:has-tools", tier="tier1", features=["tools", "streaming"]),
            ]
        )
        entry, info = scheduler.select(_analysis(features={"tools"}))
        self.assertEqual(entry.id, "nvidia:has-tools")


class HardFilterContextTests(unittest.TestCase):
    def test_est_tokens_over_90pct_context_excluded(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:small-ctx", tier="tier1", context_window=1000),
            ]
        )
        # 950 > 0.9*1000 = 900 -> 제외되어 후보 없음
        with self.assertRaises(NoCandidateError) as ctx:
            scheduler.select(_analysis(est_tokens=950))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_est_tokens_under_90pct_context_included(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:small-ctx", tier="tier1", context_window=1000),
            ]
        )
        entry, _ = scheduler.select(_analysis(est_tokens=800))
        self.assertEqual(entry.id, "nvidia:small-ctx")

    def test_min_context_window_restricts_candidates(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:small", tier="tier1", context_window=1000),
                ModelOverride(id="nvidia:big", tier="tier2", context_window=200000),
            ]
        )
        # min_context_window=1000 -> small(1000)은 <= min이라 탈락, big만 통과
        entry, _ = scheduler.select(_analysis(est_tokens=100), min_context_window=1000)
        self.assertEqual(entry.id, "nvidia:big")


class SessionAffinityTests(unittest.TestCase):
    def test_repeated_selection_same_session_pins_model(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:model-a", tier="tier1"),
                ModelOverride(id="nvidia:model-b", tier="tier1"),
            ]
        )
        analysis = _analysis(session_key="session-1")
        first, _ = scheduler.select(analysis)
        for _ in range(5):
            again, info = scheduler.select(analysis)
            self.assertEqual(again.id, first.id)
            self.assertEqual(info["selected_by"], "session_affinity")

    def test_failover_moves_pin_to_new_model(self):
        """move_pin으로 세션을 다른 tier의 모델로 옮기면, 이후 select()도 그 모델을 반환해야 한다.

        회귀 테스트: 세션 고정 체크가 tier 루프 내부에 있던 시절, 상위 tier에 가용
        후보가 하나라도 있으면 하위 tier로 옮긴 핀이 무시되던 버그를 재현한다.
        (수정: select()가 tier 루프 진입 전에 핀을 전역으로 확인한다 — §5.5-1)
        """
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:model-a", tier="tier1"),
                ModelOverride(id="nvidia:model-b", tier="tier2"),
            ]
        )
        analysis = _analysis(session_key="session-2")
        first, _ = scheduler.select(analysis)
        self.assertEqual(first.id, "nvidia:model-a")

        scheduler.move_pin("session-2", "nvidia:model-b")
        entry, info = scheduler.select(analysis)
        self.assertEqual(entry.id, "nvidia:model-b")
        self.assertEqual(info["selected_by"], "session_affinity")

    def test_ttl_expiry_drops_pin(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(id="nvidia:model-a", tier="tier1"),
            ],
            session_ttl_minutes=30,
        )
        analysis = _analysis(session_key="session-3")
        with patch("forge_gateway.core.scheduler.time.time", return_value=1_000_000.0):
            entry, info = scheduler.select(analysis)
            self.assertEqual(info["selected_by"], "score")

        # TTL(30분=1800초) 만료 후 -> 세션 고정 없이 다시 스코어링으로 선택
        with patch("forge_gateway.core.scheduler.time.time", return_value=1_000_000.0 + 1801.0):
            entry, info = scheduler.select(analysis)
            self.assertEqual(info["selected_by"], "score")


class ScoringTests(unittest.TestCase):
    def test_documentation_task_prefers_high_docs_capability(self):
        scheduler, _ = _make_scheduler(
            [
                ModelOverride(
                    id="nvidia:docs-writer",
                    tier="tier1",
                    capabilities={"code": 5, "debug": 5, "refactor": 5, "docs": 10, "context": 5, "speed": 5},
                ),
                ModelOverride(
                    id="nvidia:docs-weak",
                    tier="tier1",
                    capabilities={"code": 5, "debug": 5, "refactor": 5, "docs": 0, "context": 5, "speed": 5},
                ),
            ]
        )
        analysis = _analysis(task="documentation")
        # 후보 간 점수 차가 크므로(0.30*10 vs 0.30*0) 동률권(90%) 밖 -> 결정적으로 docs-writer 선택
        for _ in range(10):
            entry, info = scheduler.select(_analysis(task="documentation", session_key=""))
            self.assertEqual(entry.id, "nvidia:docs-writer")


class RecordFailureTests(unittest.TestCase):
    def test_rate_limited_classified_as_429_and_immediate_cooldown(self):
        scheduler, registry = _make_scheduler(
            [ModelOverride(id="nvidia:model-a", tier="tier1")]
        )
        error_type = scheduler.record_failure("nvidia:model-a", RateLimited("rate limited"))
        self.assertEqual(error_type, "429")
        entry = registry.get("nvidia:model-a")
        self.assertEqual(entry.health.status, "cooldown")

    def test_context_length_exceeded_shrinks_context_window(self):
        scheduler, registry = _make_scheduler(
            [ModelOverride(id="nvidia:model-a", tier="tier1", context_window=1000)]
        )
        scheduler.record_failure("nvidia:model-a", ContextLengthExceeded("too long"))
        entry = registry.get("nvidia:model-a")
        self.assertEqual(entry.context_window, 800)  # 1000 * 0.8

    def test_5xx_does_not_immediately_cooldown(self):
        scheduler, registry = _make_scheduler(
            [ModelOverride(id="nvidia:model-a", tier="tier1")]
        )
        error_type = scheduler.record_failure(
            "nvidia:model-a", UpstreamServerError("boom", status_code=503)
        )
        self.assertEqual(error_type, "503")
        entry = registry.get("nvidia:model-a")
        self.assertNotEqual(entry.health.status, "cooldown")


class NoCandidateReasonTests(unittest.TestCase):
    def test_no_available_models_when_all_in_cooldown(self):
        scheduler, registry = _make_scheduler(
            [ModelOverride(id="nvidia:model-a", tier="tier1")]
        )
        registry.get("nvidia:model-a").health.enter_cooldown(300)
        with self.assertRaises(NoCandidateError) as ctx:
            scheduler.select(_analysis())
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
