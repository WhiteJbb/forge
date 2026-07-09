"""LiteLLM SDK 어댑터 (DESIGN.md §5.1)

Provider 프로토콜(base.py)의 유일한 구현체. litellm Python SDK를 프로세스
안에서 직접 호출한다 — 별도 프록시 프로세스 없음.

핵심 규약:
- failover는 Scheduler 책임이므로 litellm 자체 재시도는 끈다 (`num_retries=0`).
- 미지원 OpenAI 파라미터는 드롭 (`drop_params=True`).
- 스트리밍은 usage 강제 수집을 위해 `stream_options.include_usage`를 항상 주입 (§5.8).
- 타임아웃 3단 예산: connect/ttft/total_deadline (§5.13).
- 업스트림 에러는 base.py의 typed 예외로 정규화해서 던진다 (§5.1, §7).
"""

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Optional

import httpx
import litellm

from ..core.types import ProbeResult
from ..settings import ProviderConfig, TimeoutsConfig
from .base import (
    ContextLengthExceeded,
    ProviderError,
    RateLimited,
    UpstreamBadRequest,
    UpstreamConnectionError,
    UpstreamServerError,
    UpstreamTimeout,
)

logger = logging.getLogger(__name__)

# thinking 모델이 응답/스트림에 섞는 비표준 필드 — pass_reasoning=False면 제거 (§5.1)
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_details", "thinking")

# BadRequest 계열의 상태코드 → OpenAI 에러 포맷 type 매핑
_ERROR_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "conflict_error",
    413: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
}

# context length 초과를 알리는 업스트림 메시지 표식 (버전/프로바이더별 문구 차이 흡수)
_CONTEXT_LENGTH_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "context length",
    "too many tokens",
    "reduce the length",
)


def _to_dict(obj: Any) -> dict[str, Any]:
    """litellm 응답 객체(pydantic)를 OpenAI 포맷 dict로 변환."""
    if isinstance(obj, dict):
        return obj
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    as_dict = getattr(obj, "dict", None)  # pydantic v1 호환
    if callable(as_dict):
        return as_dict()
    return dict(obj)


def _strip_reasoning(d: dict[str, Any]) -> dict[str, Any]:
    """choices[].delta / choices[].message에서 reasoning 필드를 제거 (in-place)."""
    for choice in d.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        for container_key in ("delta", "message"):
            container = choice.get(container_key)
            if isinstance(container, dict):
                for field in _REASONING_FIELDS:
                    container.pop(field, None)
    return d


def _looks_like_context_length(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in _CONTEXT_LENGTH_MARKERS)


def _extract_status(e: Exception) -> Optional[int]:
    status = getattr(e, "status_code", None)
    if status is None:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _extract_message(e: Exception) -> str:
    msg = getattr(e, "message", None)
    if not msg:
        msg = str(e)
    return msg or e.__class__.__name__


def _extract_retry_after(e: Exception) -> Optional[float]:
    """RateLimitError 응답 헤더에서 Retry-After 초 값을 추출 (없거나 날짜형이면 None).

    후보 위치가 여럿이고 앞 후보가 '존재하지만 빈' 헤더 객체일 수 있으므로,
    값을 실제로 찾을 때까지 전 후보를 폴스루한다. litellm 1.91.x는 응답 헤더를
    e.litellm_response_headers에 담는다 (시뮬레이터가 발견).
    """
    resp = getattr(e, "response", None)
    candidates = (
        getattr(resp, "headers", None) if resp is not None else None,
        getattr(e, "headers", None),
        getattr(e, "litellm_response_headers", None),
    )
    for headers in candidates:
        if not headers:
            continue
        getter = getattr(headers, "get", None)
        if not callable(getter):
            continue
        value = getter("retry-after") or getter("Retry-After")
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue  # HTTP-date 형식 등 — 다음 후보 시도
    return None


def _openai_error_body(message: str, status: Optional[int], e: Exception) -> dict[str, Any]:
    """업스트림 에러를 OpenAI 에러 포맷 {"error": {message,type,code}}로 정규화 (§5.1)."""
    # 업스트림이 이미 OpenAI 포맷 바디를 준 경우 최대한 보존
    body = getattr(e, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body
    err_type = _ERROR_TYPE_BY_STATUS.get(status or 400, "invalid_request_error")
    code = getattr(e, "code", None)
    return {"error": {"message": message, "type": err_type, "code": code}}


class LiteLLMProvider:
    """forge.yaml provider 항목 하나에 대응하는 litellm 어댑터."""

    def __init__(self, config: ProviderConfig, timeouts: TimeoutsConfig):
        self.name = config.name
        self.config = config
        self.timeouts = timeouts
        # list_models / (필요 시) 부가 REST 호출용 공유 클라이언트 — 요청마다 만들지 않는다.
        # litellm 호출은 SDK 내부 공유 풀을 그대로 쓰므로 여기 클라이언트를 태우지 않는다.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=timeouts.connect),
        )

    # ---- 요청 kwargs 조립 ------------------------------------------------

    def _build_kwargs(
        self,
        provider_model_id: str,
        payload: dict[str, Any],
        *,
        stream: bool,
        timeout: float,
    ) -> dict[str, Any]:
        kwargs = dict(payload)  # 클라이언트 payload 얕은 복사
        kwargs.pop("model", None)  # 클라이언트가 준 model은 provider_model_id로 대체
        kwargs["model"] = f"{self.config.litellm_prefix}/{provider_model_id}"
        kwargs["num_retries"] = 0          # failover는 Scheduler 책임 (§5.1)
        kwargs["drop_params"] = True       # 미지원 파라미터 드롭 (§5.1)
        kwargs["timeout"] = timeout
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if stream:
            kwargs["stream"] = True
            # usage 강제 수집 — include_usage 항상 주입 (§5.8)
            stream_options = dict(kwargs.get("stream_options") or {})
            stream_options["include_usage"] = True
            kwargs["stream_options"] = stream_options
        return kwargs

    # ---- 예외 변환 (가장 중요) ------------------------------------------

    def _translate_error(self, e: Exception) -> ProviderError:
        """litellm 예외 → base.py typed 예외. 클래스 매칭 실패 시 status_code로 폴백."""
        if isinstance(e, ProviderError):
            return e
        if isinstance(e, asyncio.TimeoutError):
            return UpstreamTimeout("request timed out")

        status = _extract_status(e)
        message = _extract_message(e)

        def ll(name: str):
            return getattr(litellm, name, None)

        def is_inst(exc_cls) -> bool:
            return exc_cls is not None and isinstance(e, exc_cls)

        context_cls = ll("ContextWindowExceededError")

        # 1) context length 초과 — 명시적 클래스 또는 메시지 표식 (상향 failover 대상, §7)
        if is_inst(context_cls) or _looks_like_context_length(message):
            return ContextLengthExceeded(message, status_code=status or 400)

        # 2) 429
        if is_inst(ll("RateLimitError")) or status == 429:
            return RateLimited(message, retry_after=_extract_retry_after(e))

        # 3) 타임아웃
        if is_inst(ll("Timeout")) or status == 408:
            return UpstreamTimeout(message, status_code=status)

        # 4) 연결 실패
        if is_inst(ll("APIConnectionError")):
            return UpstreamConnectionError(message, status_code=status)

        # 5) 5xx
        if (
            is_inst(ll("InternalServerError"))
            or is_inst(ll("ServiceUnavailableError"))
            or (status is not None and status >= 500)
        ):
            return UpstreamServerError(message, status_code=status or 500)

        # 6) 나머지 4xx → BadRequest (OpenAI 에러 포맷으로 body 정규화)
        if is_inst(ll("BadRequestError")) or (status is not None and 400 <= status < 500):
            resolved = status if (status is not None and 400 <= status < 500) else 400
            return UpstreamBadRequest(
                message,
                status_code=resolved,
                body=_openai_error_body(message, resolved, e),
            )

        # 7) 분류 불가 — failover 가능하도록 서버 에러로 취급
        return UpstreamServerError(message, status_code=status)

    # ---- 응답 정규화 ----------------------------------------------------

    def _normalize(self, obj: Any) -> dict[str, Any]:
        d = _to_dict(obj)
        if not self.config.pass_reasoning:
            _strip_reasoning(d)
        return d

    # ---- Provider 프로토콜 구현 ----------------------------------------

    async def chat(self, provider_model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = self._build_kwargs(
            provider_model_id, payload, stream=False, timeout=self.timeouts.total_deadline
        )
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as e:  # noqa: BLE001 — 전부 typed로 변환
            raise self._translate_error(e) from e
        return self._normalize(resp)

    async def chat_stream(
        self, provider_model_id: str, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        kwargs = self._build_kwargs(
            provider_model_id, payload, stream=True, timeout=self.timeouts.total_deadline
        )
        try:
            stream = await litellm.acompletion(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise self._translate_error(e) from e

        aiter = stream.__aiter__()

        # 첫 청크는 TTFT 예산으로 제한 — 초과 시 클라이언트는 아직 아무것도 못 받았으므로
        # failover 가능한 UpstreamTimeout으로 (§5.8/§5.13)
        try:
            first = await asyncio.wait_for(aiter.__anext__(), timeout=self.timeouts.ttft)
        except asyncio.TimeoutError as e:
            await self._safe_aclose(stream)
            raise UpstreamTimeout(f"TTFT exceeded {self.timeouts.ttft}s") from e
        except StopAsyncIteration:
            return  # 빈 스트림
        except Exception as e:  # noqa: BLE001
            raise self._translate_error(e) from e

        yield self._normalize(first)

        # 첫 청크 이후에는 litellm에 넘긴 total_deadline만 적용 (별도 wait_for 없음)
        while True:
            try:
                chunk = await aiter.__anext__()
            except StopAsyncIteration:
                break
            except Exception as e:  # noqa: BLE001 — mid-stream 에러도 typed로
                raise self._translate_error(e) from e
            yield self._normalize(chunk)

    async def embeddings(self, provider_model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = self._build_kwargs(
            provider_model_id, payload, stream=False, timeout=self.timeouts.total_deadline
        )
        try:
            resp = await litellm.aembedding(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise self._translate_error(e) from e
        return _to_dict(resp)

    async def list_models(self) -> list[str]:
        """GET {api_base}/models 로 프로바이더가 노출하는 model id 목록 (Auto Discovery용).

        실패는 삼키고 빈 목록 + 경고 로그 — discovery 실패가 부팅을 막지 않게.
        """
        if not self.config.api_base:
            logger.warning("provider %s: api_base 없음 — list_models 건너뜀", self.name)
            return []
        url = f"{self.config.api_base.rstrip('/')}/models"
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            resp = await self._http.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("provider %s: list_models 실패 (%s)", self.name, e)
            return []

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            logger.warning("provider %s: 예상 밖 /models 응답 포맷", self.name)
            return []
        ids: list[str] = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
        return ids

    async def probe(self, provider_model_id: str, timeout: float) -> ProbeResult:
        """max_tokens=1 completion으로 생사 확인. 예외는 삼키고 ok=False로 반환 (§5.6)."""
        forge_id = f"{self.name}:{provider_model_id}"
        kwargs = self._build_kwargs(
            provider_model_id,
            {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            stream=False,
            timeout=timeout,
        )
        start = time.monotonic()
        try:
            await litellm.acompletion(**kwargs)
        except Exception as e:  # noqa: BLE001 — probe는 실패를 삼킨다
            return ProbeResult(
                model_id=forge_id,
                ok=False,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error=_extract_message(e),
            )
        return ProbeResult(
            model_id=forge_id,
            ok=True,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    # ---- 내부 유틸 ------------------------------------------------------

    @staticmethod
    async def _safe_aclose(stream: Any) -> None:
        """스트림 리소스 best-effort 정리 (TTFT 타임아웃 등 조기 중단 시)."""
        aclose = getattr(stream, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass
