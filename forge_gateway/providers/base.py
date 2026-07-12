"""Provider 계층 계약 (DESIGN.md §5.1)

failover 로직(§7)은 여기 정의된 예외 타입에 의존한다:
- RateLimited        → 즉시 쿨다운 + 다음 후보
- UpstreamServerError / UpstreamTimeout / UpstreamConnectionError → 다음 후보
- ContextLengthExceeded → 더 큰 컨텍스트 창 후보로 상향 failover
- UpstreamBadRequest → failover 없이 에러 반환 (요청 자체 문제)
"""

import re
from typing import Any, AsyncIterator, Iterable, Optional, Protocol, runtime_checkable

from ..core.types import ProbeResult
from ..settings import ProviderConfig, TimeoutsConfig

# provider 키 패턴 — 업스트림 에러 메시지/예외 로그에 키가 에코될 수 있어 마스킹
# (§8.3, 리뷰 #14). 예외를 그대로 로그에 찍는 곳(health.py의 probe/discover 실패 로그
# 등)은 반드시 이걸 거쳐야 한다 — 마스킹은 이 한 곳에서만 정의한다.
# 안정적 공개 접두어가 있는 계열만 정규식으로 커버한다: NVIDIA/OpenAI 계열/Groq/Gemini
# (초기 4계열) + Cerebras(csk-)/x.ai(xai-)/Fireworks(fw_). Together/Cohere/Mistral/
# SambaNova/Zhipu처럼 접두어가 없는 키는 아래 등록 값 정확 일치 마스킹이 담당한다.
_SECRET_RE = re.compile(
    r"\b(nvapi-|sk-(?:or-|ant-|proj-)?|gsk_|AIza|csk-|xai-|fw_)[A-Za-z0-9_\-]{8,}"
)

# 접두어가 없는 provider 키의 유일한 방어 — 실제 등록된 키 값을 정확 일치로 마스킹한다.
# 프로세스 수명 동안 누적하며, 리로드로 키가 빠져도 등록은 유지한다(마스킹은 보수적일수록
# 안전 — 로그에 남은 옛 키도 계속 가린다).
_REGISTERED_SECRETS: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    """마스킹 대상 키 값을 등록한다(누적). 길이 8 미만 값은 무시한다 —
    짧은 문자열은 무관한 로그 텍스트를 오마스킹할 위험이 크기 때문."""
    for v in values:
        if v and len(v) >= 8:
            _REGISTERED_SECRETS.add(v)


def mask_secrets(text: str) -> str:
    text = _SECRET_RE.sub(lambda m: m.group(1) + "***", text)
    # 등록된 값은 정규식이 아니라 str.replace로 치환한다 — 키에 정규식 특수문자가 있어도
    # 이스케이프 문제가 원천적으로 없다. 긴 값부터 치환해 부분 문자열 관계인 두 키가
    # 서로를 가려 일부만 마스킹되는 것을 막는다.
    for value in sorted(_REGISTERED_SECRETS, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


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
    - key_index: 멀티 키 로테이션(§5.13)에서 throttle이 고른 키 슬롯.
      config.api_keys의 인덱스이며, 단일 키 provider는 항상 0.
      probe/list_models/embeddings는 대표 키(0)만 쓴다.
    """

    name: str
    config: ProviderConfig

    async def chat(self, provider_model_id: str, payload: dict[str, Any],
                   *, key_index: int = 0) -> dict[str, Any]:
        """논스트리밍 completion. OpenAI 응답 dict 반환."""
        ...

    def chat_stream(
        self, provider_model_id: str, payload: dict[str, Any], *, key_index: int = 0
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

    # 이 provider의 모든 키(멀티 키 포함)를 마스킹 대상으로 등록 — 서버 기동과
    # reload 경로 양쪽이 이 팩토리를 거치므로 신규/교체된 키도 자동 커버된다.
    register_secrets(config.api_keys)
    return LiteLLMProvider(config, timeouts)
