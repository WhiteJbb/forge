"""SQLite MetricsRepository 구현 (DESIGN.md §5.7, §6)

- 단일 스레드 사용 전제(MetricsEngine의 to_thread 직렬 호출)지만
  check_same_thread=False + 자체 락으로 방어한다.
- 스키마 불일치(구버전 컬럼) 감지 시 테이블을 DROP 후 재생성한다 —
  로컬 개발 데이터라 손실 무방(사용자 승인 완료).
- daily_summary 갱신은 read-modify-write 대신 UPSERT.
"""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .base import RequestMetric

# request_metrics의 기대 컬럼 집합 (DESIGN.md §6). 이 중 하나라도 없으면 재생성.
_RM_COLUMNS = {
    "id", "request_id", "timestamp", "model", "provider", "tier",
    "task_type", "attempt", "latency_ms", "ttft_ms", "prompt_tokens",
    "completion_tokens", "had_tools", "success", "status_code",
    "error_type", "cost",
}

# daily_summary의 기대 컬럼. sum_latency_ms 보조 컬럼으로 avg를 UPSERT 안에서 계산.
_DS_COLUMNS = {
    "date", "model", "total_requests", "total_success", "total_failures",
    "total_429", "total_5xx", "total_timeouts", "avg_latency_ms",
    "sum_latency_ms", "total_tokens", "total_cost",
}


class SqliteRepo:
    """MetricsRepository 프로토콜의 SQLite 구현체."""

    def __init__(self, db_path: str = "forge.db"):
        self.db_path = db_path
        # to_thread 직렬 호출이지만 방어적으로 락을 둔다.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------ schema

    def init_schema(self) -> None:
        with self._lock:
            if self._needs_rebuild("request_metrics", _RM_COLUMNS):
                self._conn.execute("DROP TABLE IF EXISTS request_metrics")
            if self._needs_rebuild("daily_summary", _DS_COLUMNS):
                self._conn.execute("DROP TABLE IF EXISTS daily_summary")

            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS request_metrics (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id        TEXT NOT NULL,
                    timestamp         TEXT NOT NULL,
                    model             TEXT NOT NULL,
                    provider          TEXT NOT NULL,
                    tier              TEXT,
                    task_type         TEXT,
                    attempt           INTEGER DEFAULT 1,
                    latency_ms        REAL,
                    ttft_ms           REAL,
                    prompt_tokens     INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    had_tools         INTEGER DEFAULT 0,
                    success           INTEGER NOT NULL,
                    status_code       INTEGER,
                    error_type        TEXT,
                    cost              REAL DEFAULT 0.0
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rm_ts "
                "ON request_metrics(timestamp)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rm_model_ts "
                "ON request_metrics(model, timestamp)"
            )

            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    date           TEXT NOT NULL,
                    model          TEXT NOT NULL,
                    total_requests INTEGER DEFAULT 0,
                    total_success  INTEGER DEFAULT 0,
                    total_failures INTEGER DEFAULT 0,
                    total_429      INTEGER DEFAULT 0,
                    total_5xx      INTEGER DEFAULT 0,
                    total_timeouts INTEGER DEFAULT 0,
                    avg_latency_ms REAL DEFAULT 0,
                    sum_latency_ms REAL DEFAULT 0,
                    total_tokens   INTEGER DEFAULT 0,
                    total_cost     REAL DEFAULT 0,
                    PRIMARY KEY (date, model)
                )
            """)
            self._conn.commit()

    def _needs_rebuild(self, table: str, expected: set) -> bool:
        """PRAGMA table_info로 기존 컬럼을 읽어 기대 컬럼이 빠졌으면 True.

        테이블이 아예 없으면 재생성 불필요(CREATE IF NOT EXISTS가 처리) → False.
        """
        rows = self._conn.execute(
            "PRAGMA table_info(%s)" % table
        ).fetchall()
        if not rows:
            return False  # 테이블 없음 — DROP 불필요
        existing = {r["name"] for r in rows}
        return not expected.issubset(existing)

    # ------------------------------------------------------------------ write

    def record_batch(self, rows: "list[RequestMetric]") -> None:
        if not rows:
            return
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                for m in rows:
                    self._insert_metric(m)
                    self._upsert_daily(m)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _insert_metric(self, m: "RequestMetric") -> None:
        self._conn.execute("""
            INSERT INTO request_metrics
            (request_id, timestamp, model, provider, tier, task_type, attempt,
             latency_ms, ttft_ms, prompt_tokens, completion_tokens, had_tools,
             success, status_code, error_type, cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m.request_id, m.timestamp, m.model, m.provider, m.tier, m.task_type,
            m.attempt, m.latency_ms, m.ttft_ms, m.prompt_tokens,
            m.completion_tokens, 1 if m.had_tools else 0,
            1 if m.success else 0, m.status_code, m.error_type, m.cost,
        ))

    def _upsert_daily(self, m: "RequestMetric") -> None:
        """daily_summary UPSERT. avg_latency_ms = sum_latency_ms / total_requests
        를 UPSERT 안에서 계산한다(read-modify-write 금지)."""
        date_str = m.timestamp[:10]
        latency = m.latency_ms or 0.0
        tokens = m.prompt_tokens + m.completion_tokens
        is_429 = 1 if _is_429(m) else 0
        is_5xx = 1 if _is_5xx(m) else 0
        is_timeout = 1 if _is_timeout(m) else 0
        succ = 1 if m.success else 0
        fail = 0 if m.success else 1

        self._conn.execute("""
            INSERT INTO daily_summary
            (date, model, total_requests, total_success, total_failures,
             total_429, total_5xx, total_timeouts,
             avg_latency_ms, sum_latency_ms, total_tokens, total_cost)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, model) DO UPDATE SET
                total_requests = total_requests + 1,
                total_success  = total_success  + excluded.total_success,
                total_failures = total_failures + excluded.total_failures,
                total_429      = total_429      + excluded.total_429,
                total_5xx      = total_5xx      + excluded.total_5xx,
                total_timeouts = total_timeouts + excluded.total_timeouts,
                sum_latency_ms = sum_latency_ms + excluded.sum_latency_ms,
                avg_latency_ms = (sum_latency_ms + excluded.sum_latency_ms)
                                 / (total_requests + 1),
                total_tokens   = total_tokens   + excluded.total_tokens,
                total_cost     = total_cost     + excluded.total_cost
        """, (
            date_str, m.model, succ, fail, is_429, is_5xx, is_timeout,
            latency, latency, tokens, m.cost,
        ))

    # ------------------------------------------------------------------ read

    def today_summary(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM daily_summary WHERE date = ?", (today,)
            ).fetchall()
        return _build_today(today, rows)

    def range_summary(self, days: int) -> dict:
        start_date = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM daily_summary WHERE date >= ? "
                "ORDER BY date DESC, model",
                (start_date,),
            ).fetchall()
            today_rows = self._conn.execute(
                "SELECT * FROM daily_summary WHERE date = ?", (today,)
            ).fetchall()

        by_date: dict = {}
        for row in rows:
            by_date.setdefault(row["date"], []).append(dict(row))

        return {
            "days": days,
            "summary": by_date,
            "today": _build_today(today, today_rows),
        }

    # ------------------------------------------------------------------ prune

    def prune(self, retention_days: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM request_metrics WHERE timestamp < ?", (cutoff,)
            )
            deleted = cur.rowcount
            self._conn.commit()
        return max(deleted, 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------- helpers

def _is_429(m: "RequestMetric") -> bool:
    return m.status_code == 429 or m.error_type == "429"


def _is_5xx(m: "RequestMetric") -> bool:
    if m.status_code is not None and 500 <= m.status_code <= 599:
        return True
    return bool(m.error_type and m.error_type.startswith("5"))


def _is_timeout(m: "RequestMetric") -> bool:
    return m.error_type == "timeout"


def _build_today(today: str, rows) -> dict:
    """기존 src/metrics.py get_today_summary와 동일한 dict 구조로 조립."""
    total_requests = 0
    total_success = 0
    total_failures = 0
    total_cost = 0.0
    model_stats = []

    for row in rows:
        total_requests += row["total_requests"]
        total_success += row["total_success"]
        total_failures += row["total_failures"]
        total_cost += row["total_cost"]
        model_stats.append({
            "model": row["model"],
            "requests": row["total_requests"],
            "success": row["total_success"],
            "failures": row["total_failures"],
            "avg_latency_ms": round(row["avg_latency_ms"], 1),
            "tokens": row["total_tokens"],
            "cost": row["total_cost"],
        })

    success_rate = (total_success / total_requests * 100) if total_requests else 0
    failure_rate = (total_failures / total_requests * 100) if total_requests else 0

    return {
        "date": today,
        "total_requests": total_requests,
        "total_success": total_success,
        "total_failures": total_failures,
        "success_rate": round(success_rate, 1),
        "failure_rate": round(failure_rate, 1),
        "total_cost": round(total_cost, 4),
        "models": model_stats,
    }
