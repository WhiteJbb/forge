"""ModelHealth 상태 전이 테스트 (DESIGN.md §5.5/§5.6, src/core/registry.py)"""

import unittest
from unittest.mock import patch

from src.core.registry import ModelHealth


class EwmaLatencyTests(unittest.TestCase):
    def test_first_success_sets_latency_directly(self):
        health = ModelHealth(ewma_alpha=0.3)
        health.record_success(100.0)
        self.assertEqual(health.latency_ms, 100.0)

    def test_subsequent_success_applies_ewma(self):
        health = ModelHealth(ewma_alpha=0.3)
        health.record_success(100.0)
        health.record_success(200.0)
        # ewma = 0.3*200 + 0.7*100 = 130.0
        self.assertAlmostEqual(health.latency_ms, 130.0)

    def test_success_sets_status_healthy_and_resets_failures(self):
        health = ModelHealth()
        health.consecutive_failures = 2
        health.record_success(50.0)
        self.assertEqual(health.status, "healthy")
        self.assertEqual(health.consecutive_failures, 0)


class SlidingWindowSuccessRateTests(unittest.TestCase):
    def test_no_data_returns_none(self):
        health = ModelHealth()
        self.assertIsNone(health.success_rate())

    def test_mixed_window_ratio(self):
        health = ModelHealth()
        health.record_success(10.0)
        health.record_success(10.0)
        health.record_failure(
            "500", cooldown_seconds=300, max_failures_before_cooldown=99
        )
        # 2 success / 3 total = 0.666...
        self.assertAlmostEqual(health.success_rate(), 2 / 3)

    def test_window_is_bounded_to_last_50(self):
        health = ModelHealth()
        for _ in range(40):
            health.record_failure(
                "500", cooldown_seconds=300, max_failures_before_cooldown=999
            )
        for _ in range(20):
            health.record_success(10.0)
        # 윈도 크기 50 — 마지막 20 success + 앞의 실패 30개만 남음 = 20/50
        self.assertAlmostEqual(health.success_rate(), 20 / 50)


class CooldownTests(unittest.TestCase):
    def test_429_triggers_immediate_cooldown_with_retry_after(self):
        health = ModelHealth()
        health.record_failure(
            "429",
            cooldown_seconds=300,
            max_failures_before_cooldown=3,
            immediate_cooldown=True,
            retry_after=45.0,
        )
        self.assertEqual(health.status, "cooldown")
        self.assertLessEqual(health.cooldown_remaining(), 45)
        self.assertGreater(health.cooldown_remaining(), 40)

    def test_429_without_retry_after_uses_default_cooldown(self):
        health = ModelHealth()
        health.record_failure(
            "429",
            cooldown_seconds=300,
            max_failures_before_cooldown=3,
            immediate_cooldown=True,
            retry_after=None,
        )
        self.assertEqual(health.status, "cooldown")
        self.assertGreater(health.cooldown_remaining(), 290)

    def test_single_failure_does_not_cooldown(self):
        health = ModelHealth()
        health.record_failure(
            "500", cooldown_seconds=300, max_failures_before_cooldown=3
        )
        self.assertNotEqual(health.status, "cooldown")
        self.assertTrue(health.is_available())

    def test_three_consecutive_failures_trigger_cooldown(self):
        health = ModelHealth()
        for _ in range(2):
            health.record_failure(
                "500", cooldown_seconds=300, max_failures_before_cooldown=3
            )
        self.assertNotEqual(health.status, "cooldown")
        health.record_failure(
            "500", cooldown_seconds=300, max_failures_before_cooldown=3
        )
        self.assertEqual(health.status, "cooldown")
        self.assertFalse(health.is_available())

    def test_cooldown_expiry_reverts_to_unknown(self):
        health = ModelHealth()
        with patch("src.core.registry.time.time", return_value=1_000_000.0):
            health.enter_cooldown(10.0)  # cooldown_until = 1_000_010.0
        self.assertEqual(health.status, "cooldown")

        with patch("src.core.registry.time.time", return_value=1_000_011.0):
            self.assertTrue(health.is_available())  # is_available()이 만료를 감지
        self.assertEqual(health.status, "unknown")
        self.assertEqual(health.cooldown_until, 0.0)
        self.assertEqual(health.consecutive_failures, 0)


if __name__ == "__main__":
    unittest.main()
