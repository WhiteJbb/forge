"""MetricsEngine — write-behind 메트릭 엔진 (DESIGN.md §5.7, §5.13)

요청 경로에서는 큐에 put_nowait만 하고(논블로킹), 백그라운드 태스크가
배치로 flush한다. 메트릭 격리(§9-4): repo 예외는 로그만 남기고 삼킨다.
graceful shutdown(§5.13): stop()은 큐를 모두 flush한 뒤 종료한다.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..settings import MetricsConfig
from ..storage.base import MetricsRepository, RequestMetric
from ..storage.sqlite_repo import SqliteRepo

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 10_000
_BATCH_SIZE = 100
_FLUSH_INTERVAL = 1.0            # 초
_DROP_LOG_INTERVAL = 60.0       # 드롭 경고 스로틀: 최대 1회/분


class MetricsEngine:
    """write-behind 큐 + 백그라운드 flush 태스크."""

    def __init__(self, config: MetricsConfig, repo: "MetricsRepository | None" = None):
        self._config = config
        self._repo: MetricsRepository = repo or SqliteRepo(config.db_path)
        self._queue: "asyncio.Queue[RequestMetric]" = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._task: "asyncio.Task | None" = None
        self._running = False
        # 실시간 관측 훅 (Prometheus 등) — record()가 격리 호출. reload 시 교체됨
        self.on_record = None

        # 드롭 경고 스로틀 상태
        self._dropped = 0
        self._last_drop_log = 0.0

        # prune 날짜 변화 감지 상태
        self._last_prune_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """스키마 초기화 + 백그라운드 flush 태스크 기동."""
        if self._running:
            return
        self._repo.init_schema()
        self._running = True
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """graceful shutdown: 신규 수신 중단 → 남은 큐 전부 flush → repo close."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            # flush 루프가 running=False를 보고 종료할 때까지 대기
            await self._task
            self._task = None
        # 루프 종료 후 혹시 남은 항목까지 최종 flush
        await self._drain_all()
        try:
            await asyncio.to_thread(self._repo.close)
        except Exception:
            logger.exception("metrics repo close 실패 (무시)")

    # ------------------------------------------------------------- ingest

    def record(self, metric: "RequestMetric") -> None:
        """요청 경로에서 호출. 절대 블로킹/예외 전파 금지.

        큐가 가득 차면 드롭하고 경고를 1회/분으로 스로틀 로그한다.
        on_record 훅(Prometheus 등)도 여기서 — 실패는 격리.
        """
        if self.on_record is not None:
            try:
                self.on_record(metric)
            except Exception:
                logger.exception("on_record 훅 실패 (무시)")
        try:
            self._queue.put_nowait(metric)
        except asyncio.QueueFull:
            self._dropped += 1
            now = time.monotonic()
            if now - self._last_drop_log >= _DROP_LOG_INTERVAL:
                logger.warning(
                    "메트릭 큐 가득참 — %d건 드롭됨 (최근 1분 스로틀)", self._dropped
                )
                self._last_drop_log = now
                self._dropped = 0
        except Exception:
            # 어떤 경우에도 요청 경로를 죽이지 않는다 (§9-4 메트릭 격리)
            logger.exception("메트릭 record 실패 (무시)")

    # ------------------------------------------------------------- summaries

    async def today_summary(self) -> dict:
        return await asyncio.to_thread(self._repo.today_summary)

    async def range_summary(self, days: int) -> dict:
        return await asyncio.to_thread(self._repo.range_summary, days)

    async def capability_stats(self, days: int) -> "list[dict]":
        """CapabilityTuner 입력 (§5.11) — repo 집계 위임"""
        return await asyncio.to_thread(self._repo.capability_stats, days)

    async def recent_requests(self, limit: int = 50) -> "list[dict]":
        """최근 요청 피드 — 대시보드용. 큐에 남은 미기록분은 최대 1초 지연될 수 있음."""
        return await asyncio.to_thread(self._repo.recent_requests, limit)

    # ------------------------------------------------------------- internal

    async def _flush_loop(self) -> None:
        """배치 100건 또는 1초마다 flush. running=False면 잔여분 비우고 종료."""
        while self._running:
            batch = await self._collect_batch()
            if batch:
                await self._flush(batch)
            self._maybe_prune()

    async def _collect_batch(self) -> "list[RequestMetric]":
        """최대 _FLUSH_INTERVAL 동안 최대 _BATCH_SIZE개까지 모은다."""
        batch: "list[RequestMetric]" = []
        deadline = time.monotonic() + _FLUSH_INTERVAL
        while len(batch) < _BATCH_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break
        return batch

    async def _drain_all(self) -> None:
        """큐에 남은 모든 항목을 배치로 flush (shutdown 경로)."""
        while True:
            batch: "list[RequestMetric]" = []
            while len(batch) < _BATCH_SIZE:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if not batch:
                break
            await self._flush(batch)

    async def _flush(self, batch: "list[RequestMetric]") -> None:
        """repo.record_batch를 to_thread로. 예외는 로그만 남기고 삼킨다(§9-4)."""
        try:
            await asyncio.to_thread(self._repo.record_batch, batch)
        except Exception:
            logger.exception("메트릭 flush 실패 — %d건 유실 (무시)", len(batch))

    def _maybe_prune(self) -> None:
        """날짜가 바뀌면 하루 1회 prune 실행. 예외는 삼킨다."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today == self._last_prune_date:
            return
        self._last_prune_date = today
        retention = self._config.retention_days
        # prune은 동기 repo 호출이므로 백그라운드 태스크로 위임(루프 논블로킹)
        asyncio.create_task(self._run_prune(retention))

    async def _run_prune(self, retention_days: int) -> None:
        try:
            deleted = await asyncio.to_thread(self._repo.prune, retention_days)
            logger.info("메트릭 prune 완료 — %d행 삭제", deleted)
        except Exception:
            logger.exception("메트릭 prune 실패 (무시)")
