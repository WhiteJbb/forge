"""SqliteRepo 단위 테스트 (DESIGN.md §5.7, §6, src/storage/sqlite_repo.py)"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from forge_gateway.storage.base import RequestMetric
from forge_gateway.storage.sqlite_repo import SqliteRepo


class SqliteRepoTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = str(Path(self._tmpdir.name) / "forge_test.db")

    def _metric(self, **kw):
        base = dict(
            request_id="r1",
            timestamp="2026-07-09T10:00:00+00:00",
            model="nvidia:model-a",
            provider="nvidia",
            tier="tier1",
            task_type="coding",
            attempt=1,
            latency_ms=100.0,
            ttft_ms=None,
            prompt_tokens=10,
            completion_tokens=5,
            had_tools=False,
            success=True,
            status_code=200,
            error_type=None,
            cost=0.0,
        )
        base.update(kw)
        return RequestMetric(**base)

    def test_init_schema_creates_tables_fresh(self):
        repo = SqliteRepo(self.db_path)
        repo.init_schema()
        cols = {r["name"] for r in repo._conn.execute("PRAGMA table_info(request_metrics)")}
        self.assertTrue({"id", "request_id", "ttft_ms", "had_tools"} <= cols)
        ds_cols = {r["name"] for r in repo._conn.execute("PRAGMA table_info(daily_summary)")}
        self.assertTrue({"date", "model", "avg_latency_ms", "sum_latency_ms"} <= ds_cols)
        repo.close()

    def test_old_schema_missing_columns_triggers_rebuild(self):
        # 구버전 테이블: ttft_ms/had_tools 등 신규 컬럼이 없는 상태를 재현
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE request_metrics (
                id INTEGER PRIMARY KEY,
                request_id TEXT,
                timestamp TEXT,
                model TEXT,
                provider TEXT,
                success INTEGER
            )
            """
        )
        conn.commit()
        conn.close()

        repo = SqliteRepo(self.db_path)
        repo.init_schema()
        cols = {r["name"] for r in repo._conn.execute("PRAGMA table_info(request_metrics)")}
        self.assertIn("ttft_ms", cols)
        self.assertIn("had_tools", cols)
        self.assertIn("cost", cols)
        repo.close()

    def test_record_batch_daily_summary_avg_latency_accurate(self):
        repo = SqliteRepo(self.db_path)
        repo.init_schema()
        rows = [self._metric(latency_ms=v, request_id=f"r{v}") for v in (100.0, 300.0, 200.0)]
        repo.record_batch(rows)

        row = repo._conn.execute(
            "SELECT avg_latency_ms, total_requests FROM daily_summary WHERE date=? AND model=?",
            ("2026-07-09", "nvidia:model-a"),
        ).fetchone()
        self.assertEqual(row["total_requests"], 3)
        self.assertAlmostEqual(row["avg_latency_ms"], 200.0)
        repo.close()

    def test_record_batch_persists_all_rows(self):
        repo = SqliteRepo(self.db_path)
        repo.init_schema()
        rows = [self._metric(request_id=f"r{i}") for i in range(5)]
        repo.record_batch(rows)
        count = repo._conn.execute("SELECT COUNT(*) AS c FROM request_metrics").fetchone()["c"]
        self.assertEqual(count, 5)
        repo.close()

    def test_recent_requests_returns_latest_first(self):
        repo = SqliteRepo(self.db_path)
        self.addCleanup(repo.close)
        repo.init_schema()
        repo.record_batch([self._metric(request_id=f"r{i}", model=f"m{i}")
                           for i in range(5)])
        recent = repo.recent_requests(3)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0]["model"], "m4")  # 최신순 (id DESC)
        self.assertIn("cost", recent[0])

    def test_prune_deletes_only_old_rows(self):
        repo = SqliteRepo(self.db_path)
        repo.init_schema()
        old = self._metric(timestamp="2020-01-01T00:00:00+00:00", request_id="old")
        new_ts = datetime.now(timezone.utc).isoformat()
        new = self._metric(timestamp=new_ts, request_id="new")
        repo.record_batch([old, new])

        deleted = repo.prune(retention_days=30)
        self.assertEqual(deleted, 1)

        remaining_ids = {
            r["request_id"] for r in repo._conn.execute("SELECT request_id FROM request_metrics")
        }
        self.assertEqual(remaining_ids, {"new"})
        repo.close()


if __name__ == "__main__":
    unittest.main()
