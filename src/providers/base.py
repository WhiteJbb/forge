"""Provider 계층 계약 (DESIGN.md §5.1)

failover 로직(§7)은 여기 정의된 예외 타입에 의존한다:
- RateLimited        → 즉시 쿨다운 + 다음 후보
- UpstreamServerError / UpstreamTimeout / UpstreamConnectionError → 다음 후보
- ContextLengthExceeded → 더 큰 컨텍스트 창 후보로 상향 failover
- UpstreamBadRequest → failover 없이 에러 반환 (요청 자체 문제)
"""

from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable

from ..core.types import ProbeResult
from ..settings import ProviderConfig, TimeoutsConfig


class ProviderError(Exception):
    """업스트림 호출 실패의 공통 베이스"""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimited(ProviderError):
    """429 — retry_after는 Retry-After 헤더 초 값 (없으면 None)"""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class UpstreamServerError(ProviderError):
    """5xx"""


class UpstreamTimeout(ProviderError):
    """connect/read/TTFT 타임아웃"""


class UpstreamConnectionError(ProviderError):
    """연결 실패"""


class ContextLengthExceeded(ProviderError):
    """400 context_length_exceeded — 유일하게 상향 failover하는 4xx (§7)"""


class UpstreamBadRequest(ProviderError):
    """그 외 4xx — failover하지 않고 OpenAI 에러 포맷으로 반환"""

    def __init__(self, message: str, status_code: int = 400, body: Optional[dict] = None):
        super().__init__(message, status_code=status_code)
        self.body = body  # 업스트림 에러 바디 (OpenAI 포맷으로 정규화된 것)


@runtime_checkable
class Provider(Protocol):
    """모든 프로바이더 구현체의 계약.

    - chat/chat_stream의 payload는 OpenAI chat.completions 포맷 dict
      (model 필드는 구현체가 provider_model_id로 교체한다)
    - chat_stream은 OpenAI 스트리밍 청크 dict를 yield한다.
      usage 수집을 위해 stream_options.include_usage를 항상 주입하고,
      마지막 usage 청크도 그대로 yield한다 (제거 여부는 API 계층 책임, §5.8)
    - reasoning_content 등 비표준 필드는 pass_reasoning=False면 제거 (§5.1)
    - 모든 실패는 위의 typed exception으로 변환해서 던진다
    """

    name: str
    config: ProviderConfig

    async def chat(self, provider_model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """논스트리밍 completion. OpenAI 응답 dict 반환."""
        ...

    def chat_stream(
        self, provider_model_id: str, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """스트리밍 completion. 첫 청크 이전 실패는 typed exception으로."""
        ...

    async def embeddings(self, provider_model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def list_models(self) -> list[str]:
        """provider가 노출하는 model id 목록 (Auto Discovery용, forge 접두어 없음)"""
        ...

    async def probe(self, provider_model_id: str, timeout: float) -> ProbeResult:
        """max_tokens=1 completion으로 생사 확인 (§5.6)"""
        ...

    async def close(self) -> None:
        ...


def make_provider(config: ProviderConfig, timeouts: TimeoutsConfig) -> Provider:
    """설정 기반 프로바이더 팩토리 — 현재는 LiteLLM 구현체 하나"""
    from .litellm_provider import LiteLLMProvider

    return LiteLLMProvider(config, timeouts)
