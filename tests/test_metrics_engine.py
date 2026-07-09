"""MetricsEngine write-behind 큐 테스트 (DESIGN.md §5.7, src/core/metrics.py)

실제 sqlite 파일은 tempfile로만 사용하고 네트워크 호출은 없다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.metrics import MetricsEngine
from src.settings import MetricsConfig
from src.storage.sqlite_repo import SqliteRepo


class RaisingRepo:
    """MetricsRepository 프로토콜을 만족하되 record_batch가 항상 예외를 던지는 가짜 repo."""

    def __init__(self):
        self.init_called = False
        self.close_called = False

    def init_schema(self):
        self.init_called = True

    def record_batch(self, rows):
        raise RuntimeError("simulated repo failure")

    def today_summary(self):
        return {}

    def range_summary(self, days):
        return {}

    def prune(self, retention_days):
        return 0

    def close(self):
        self.close_called = True


class MetricsEngineRecordAndDrainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = str(Path(self._tmpdir.name) / "forge_test.db")
        # 플러시 주기를 줄여 테스트를 빠르게 한다 (src 수정 없이 모듈 상수만 패치)
        self._patcher = patch("src.core.metrics._FLUSH_INTERVAL", 0.05)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _make_metric(self, request_id):
        from src.storage.base import RequestMetric

        return RequestMetric(
            request_id=request_id,
            timestamp="2026-07-09T10:00:00+00:00",
            model="nvidia:model-a",
            provider="nvidia",
            success=True,
        )

    async def test_record_then_stop_persists_all(self):
        engine = MetricsEngine(MetricsConfig(db_path=self.db_path), repo=SqliteRepo(self.db_path))
        engine.start()
        for i in range(10):
            engine.record(self._make_metric(f"req-{i}"))
        await engine.stop()

        # stop()이 repo.close()까지 호출하므로 새 커넥션으로 재조회
        verify_repo = SqliteRepo(self.db_path)
        count = verify_repo._conn.execute(
            "SELECT COUNT(*) AS c FROM request_metrics"
        ).fetchone()["c"]
        verify_repo.close()
        self.assertEqual(count, 10)

    async def test_repo_exception_does_not_propagate(self):
        fake_repo = RaisingRepo()
        engine = MetricsEngine(MetricsConfig(db_path=self.db_path), repo=fake_repo)
        engine.start()
        self.assertTrue(fake_repo.init_called)

        engine.record(self._make_metric("boom-1"))
        engine.record(self._make_metric("boom-2"))

        # record_batch가 예외를 던지지만 stop()은 정상 종료해야 한다
        try:
            await engine.stop()
        except Exception as e:  # pragma: no cover - 실패 시에만 도달
            self.fail(f"stop() propagated repo exception: {e!r}")

        self.assertTrue(fake_repo.close_called)


if __name__ == "__main__":
    unittest.main()
