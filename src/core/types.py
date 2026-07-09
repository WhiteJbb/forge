"""컴포넌트 간 공유 계약 타입 (DESIGN.md §5.3, §5.6)"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AnalysisResult:
    """Request Analyzer의 출력 — 힌트일 뿐, 결정은 Policy/Scheduler가 한다"""

    task: str = "coding"                # coding | debug | refactor | documentation | testing
    confidence: float = 0.5
    est_prompt_tokens: int = 0
    required_features: set[str] = field(default_factory=set)  # §5.5 하드 필터 입력
    session_key: str = ""               # §5.5 세션 고정 입력
    language: Optional[str] = None
    keywords_matched: list[str] = field(default_factory=list)


@dataclass
class ProbeResult:
    """Health Monitor probe 결과"""

    model_id: str                       # forge id ("provider:model")
    ok: bool
    latency_ms: float = 0.0
    error: Optional[str] = None
