"""Metrics 저장 계약 — RequestMetric + MetricsRepository (DESIGN.md §5.7, §6)

Repository는 전부 동기 메서드다. 비동기화(요청 경로 논블로킹)는
MetricsEngine이 asyncio.to_thread로 감싸 처리한다.
"""

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class RequestMetric:
    """단일 요청(정확히는 failover 체인의 한 시도)의 메트릭 한 행.

    DESIGN.md §6의 request_metrics 스키마와 1:1 대응한다.
    """

    request_id: str                       # failover 체인 묶음 키
    timestamp: str                        # UTC ISO8601 문자열
    model: str
    provider: str
    tier: Optional[str] = None
    task_type: Optional[str] = None
    attempt: int = 1                      # failover 몇 번째 시도였는지
    latency_ms: float = 0.0
    ttft_ms: Optional[float] = None       # 스트리밍 첫 청크까지 시간 (논스트리밍은 None)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    had_tools: bool = False               # tool 포함 요청 여부 (§5.11 학습 루프 입력)
    success: bool = True
    status_code: Optional[int] = None
    error_type: Optional[str] = None
    cost: float = 0.0


@runtime_checkable
class MetricsRepository(Protocol):
    """메트릭 저장소 프로토콜. SQLite가 기본 구현, PG는 M3에서 추가만 (§5.7)."""

    def init_schema(self) -> None:
        """스키마 생성(및 불일치 시 재생성)."""
        ...

    def record_batch(self, rows: "list[RequestMetric]") -> None:
        """배치를 단일 트랜잭션으로 기록 + daily_summary UPSERT 갱신."""
        ...

    def today_summary(self) -> dict:
        """오늘(UTC) 집계 요약. 기존 src/metrics.py 응답 형태 유지."""
        ...

    def range_summary(self, days: int) -> dict:
        """최근 days일 집계 요약. 기존 src/metrics.py 응답 형태 유지."""
        ...

    def prune(self, retention_days: int) -> int:
        """retention_days 이전 request_metrics 행 삭제, 삭제 건수 반환.

        daily_summary 집계는 보존한다 (§6 보존 정책).
        """
        ...

    def close(self) -> None:
        """커넥션 종료."""
        ...
