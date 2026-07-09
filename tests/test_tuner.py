"""Capability 학습 루프 테스트 (DESIGN.md §5.11-3, forge_gateway/core/tuner.py)"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from forge_gateway.core.metrics import MetricsEngine
from forge_gateway.core.registry import Registry
from forge_gateway.core.scheduler import Scheduler
from forge_gateway.core.tuner import CapabilityTuner
from forge_gateway.core.types import AnalysisResult
from forge_gateway.settings import ForgeConfig, MetricsConfig, TunerConfig
from forge_gateway.storage.base import RequestMetric
from forge_gateway.storage.sqlite_repo import SqliteRepo


def _config() -> ForgeConfig:
    return ForgeConfig(**{
        "providers": [{"name": "nvidia", "api_key_env": "NVIDIA_API_KEY", "free": True}],
        "models": [
            {"id": "nvidia:model-a", "tier": "tier1",
             "capabilities": {"code": 9}, "features": ["tools", "streaming"]},
        ],
    })


def _metric(model="nvidia:model-a", task="coding", success=True,
            had_tools=False, error_type=None) -> RequestMetric:
    return RequestMetric(
        request_id="r", timestamp=datetime.now(timezone.utc).isoformat(),
        model=model, provider="nvidia", tier="tier1", task_type=task,
        had_tools=had_tools, success=success, error_type=error_type,
    )


class TunerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.repo = SqliteRepo(str(Path(tmpdir.name) / "t.db"))
        self.repo.init_schema()
        self.addCleanup(self.repo.close)
        self.config = _config()
        self.registry = Registry(self.config)
        self.metrics = MetricsEngine(MetricsConfig(db_path="unused"), repo=self.repo)
        self.tuner = CapabilityTuner(self.registry, self.metrics,
                                     TunerConfig(min_samples=5))

    def _seed(self, rows):
        self.repo.record_batch(rows)

    async def test_high_failure_rate_adjusts_minus_two(self):
        self._seed([_metric(success=(i >= 5)) for i in range(10)])  # 실패율 50%
        result = await self.tuner.run_once()
        entry = self.registry.get("nvidia:model-a")
        self.assertEqual(entry.capability_adjustments.get("code"), -2.0)
        self.assertEqual(len(result["adjusted"]), 1)

    async def test_moderate_failure_rate_adjusts_minus_one(self):
        self._seed([_metric(success=(i >= 3)) for i in range(10)])  # 실패율 30%
        await self.tuner.run_once()
        entry = self.registry.get("nvidia:model-a")
        self.assertEqual(entry.capability_adjustments.get("code"), -1.0)

    async def test_stable_model_gets_plus_one(self):
        self._seed([_metric(success=True) for _ in range(25)])  # 실패율 0%, 표본 25
        await self.tuner.run_once()
        entry = self.registry.get("nvidia:model-a")
        self.assertEqual(entry.capability_adjustments.get("code"), 1.0)

    async def test_below_min_samples_no_adjustment(self):
        self._seed([_metric(success=False) for _ in range(4)])  # 표본 4 < 5
        await self.tuner.run_once()
        entry = self.registry.get("nvidia:model-a")
        self.assertEqual(entry.capability_adjustments, {})

    async def test_cancelled_excluded_from_stats(self):
        # 실패 5건이지만 전부 cancelled → 집계 제외 → 보정 없음
        self._seed([_metric(success=False, error_type="cancelled") for _ in range(6)]
                   + [_metric(success=True) for _ in range(6)])
        await self.tuner.run_once()
        entry = self.registry.get("nvidia:model-a")
        self.assertNotIn("code", entry.capability_adjustments)

    async def test_recovery_clears_adjustment(self):
        entry = self.registry.get("nvidia:model-a")
        entry.capability_adjustments["code"] = -2.0  # 과거 보정이 있던 상태
        self._seed([_metric(success=(i >= 1)) for i in range(10)])  # 실패율 10% → delta 0
        await self.tuner.run_once()
        self.assertNotIn("code", entry.capability_adjustments)

    async def test_tools_demotion(self):
        self._seed([_metric(had_tools=True, success=(i >= 4)) for i in range(6)])  # tools 실패율 4/6
        result = await self.tuner.run_once()
        entry = self.registry.get("nvidia:model-a")
        self.assertNotIn("tools", entry.features)
        self.assertIn("tools", entry.demoted_features)
        self.assertIn("nvidia:model-a", result["demoted"])

    async def test_scheduler_applies_adjustment(self):
        scheduler = Scheduler(self.config, self.registry)
        analysis = AnalysisResult(task="coding")
        entry = self.registry.get("nvidia:model-a")
        base_score = scheduler._score(entry, analysis)
        entry.capability_adjustments["code"] = -2.0
        self.assertLess(scheduler._score(entry, analysis), base_score)


if __name__ == "__main__":
    unittest.main()
