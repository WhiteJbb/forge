"""
Metrics Engine - Tracks and stores metrics for all requests
"""

import time
import sqlite3
import threading
from typing import Optional
from datetime import datetime, timedelta
from .config import settings


class MetricsEngine:
    """Tracks request metrics and stores them in SQLite"""

    def __init__(self, db_path: str = "forge.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection"""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """Initialize database tables"""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Request metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS request_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                tier TEXT,
                task_type TEXT,
                latency_ms REAL,
                token_count INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                error_type TEXT,
                cost REAL DEFAULT 0.0
            )
        """)

        # Daily summary table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                date TEXT NOT NULL,
                model TEXT NOT NULL,
                total_requests INTEGER DEFAULT 0,
                total_success INTEGER DEFAULT 0,
                total_failures INTEGER DEFAULT 0,
                total_429 INTEGER DEFAULT 0,
                total_5xx INTEGER DEFAULT 0,
                total_timeouts INTEGER DEFAULT 0,
                avg_latency_ms REAL DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0,
                PRIMARY KEY (date, model)
            )
        """)

        conn.commit()

    def record_request(
        self,
        model: str,
        tier: Optional[str] = None,
        task_type: Optional[str] = None,
        latency_ms: float = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        success: bool = True,
        error_type: Optional[str] = None,
        cost: float = 0.0,
    ):
        """Record a request metric"""
        conn = self._get_conn()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()
        total_tokens = prompt_tokens + completion_tokens

        cursor.execute("""
            INSERT INTO request_metrics
            (timestamp, model, tier, task_type, latency_ms, token_count,
             prompt_tokens, completion_tokens, success, error_type, cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now, model, tier, task_type, latency_ms, total_tokens,
            prompt_tokens, completion_tokens, 1 if success else 0, error_type, cost
        ))

        # Update daily summary
        date_str = now[:10]
        self._update_daily_summary(
            cursor, date_str, model, success, error_type, latency_ms, total_tokens, cost
        )

        conn.commit()

    def _update_daily_summary(
        self,
        cursor,
        date: str,
        model: str,
        success: bool,
        error_type: Optional[str],
        latency_ms: float,
        tokens: int,
        cost: float,
    ):
        """Update daily summary for a model"""
        # Try to get existing record
        cursor.execute(
            "SELECT * FROM daily_summary WHERE date = ? AND model = ?",
            (date, model)
        )
        row = cursor.fetchone()

        if row:
            # Update existing
            total_req = row["total_requests"] + 1
            total_success = row["total_success"] + (1 if success else 0)
            total_failures = row["total_failures"] + (0 if success else 1)
            total_429 = row["total_429"] + (1 if error_type == "429" else 0)
            total_5xx = row["total_5xx"] + (1 if error_type and error_type.startswith("5") else 0)
            total_timeouts = row["total_timeouts"] + (1 if error_type == "timeout" else 0)

            # Calculate new average latency
            old_total = row["total_requests"]
            old_avg = row["avg_latency_ms"]
            new_avg = ((old_avg * old_total) + latency_ms) / total_req

            total_tokens = row["total_tokens"] + tokens
            total_cost = row["total_cost"] + cost

            cursor.execute("""
                UPDATE daily_summary SET
                    total_requests = ?,
                    total_success = ?,
                    total_failures = ?,
                    total_429 = ?,
                    total_5xx = ?,
                    total_timeouts = ?,
                    avg_latency_ms = ?,
                    total_tokens = ?,
                    total_cost = ?
                WHERE date = ? AND model = ?
            """, (
                total_req, total_success, total_failures,
                total_429, total_5xx, total_timeouts,
                new_avg, total_tokens, total_cost,
                date, model
            ))
        else:
            # Insert new
            cursor.execute("""
                INSERT INTO daily_summary
                (date, model, total_requests, total_success, total_failures,
                 total_429, total_5xx, total_timeouts, avg_latency_ms,
                 total_tokens, total_cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date, model, 1,
                1 if success else 0,
                0 if success else 1,
                1 if error_type == "429" else 0,
                1 if error_type and error_type.startswith("5") else 0,
                1 if error_type == "timeout" else 0,
                latency_ms, tokens, cost
            ))

    def get_today_summary(self) -> dict:
        """Get today's summary metrics"""
        conn = self._get_conn()
        cursor = conn.cursor()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        cursor.execute(
            "SELECT * FROM daily_summary WHERE date = ?",
            (today,)
        )
        rows = cursor.fetchall()

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

        success_rate = (total_success / total_requests * 100) if total_requests > 0 else 0
        failure_rate = (total_failures / total_requests * 100) if total_requests > 0 else 0

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

    def get_model_metrics(self, model: str, days: int = 7) -> list[dict]:
        """Get metrics for a specific model over N days"""
        conn = self._get_conn()
        cursor = conn.cursor()

        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        cursor.execute(
            "SELECT * FROM daily_summary WHERE model = ? AND date >= ? ORDER BY date DESC",
            (model, start_date)
        )
        rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def get_all_metrics(self, days: int = 7) -> dict:
        """Get all metrics summary"""
        conn = self._get_conn()
        cursor = conn.cursor()

        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        cursor.execute(
            "SELECT * FROM daily_summary WHERE date >= ? ORDER BY date DESC, model",
            (start_date,)
        )
        rows = cursor.fetchall()

        # Group by date
        by_date = {}
        for row in rows:
            date = row["date"]
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(dict(row))

        return {
            "days": days,
            "summary": by_date,
            "today": self.get_today_summary(),
        }