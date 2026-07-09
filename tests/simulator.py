"""Provider Simulator — 실제 LiteLLMProvider + httpx + 실제 SSE를 통과시키는
mock OpenAI 서버 (DESIGN.md §9-1 "게이트웨이는 실패 경로가 제품이다").

FakeProvider(파이썬 객체 대체)가 아니라, localhost에 진짜 OpenAI 호환 서버를
띄우고 forge.yaml이 그것을 프로바이더로 가리키게 해서 전체 스택
(litellm 어댑터 → 예외 변환 → failover)을 결정론적으로 검증한다.

사용:

    sim = ProviderSimulator().start()
    sim.script("model-a", [rate_limit(), server_error()])
    ...
    sim.stop()

또는 컨텍스트 매니저:

    with ProviderSimulator() as sim:
        sim.script("model-a", [ok(text="hi")])
"""

import json
import socket
import threading
import time
from typing import Any, Optional

import anyio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

_CREATED = 1_700_000_000


# --------------------------------------------------------------------------
# Behavior — 모델별 동작 큐의 한 항목. build()가 실제 응답을 만든다.
# --------------------------------------------------------------------------


class Behavior:
    """한 요청에 대한 시뮬레이터의 동작. 하위 클래스가 build()를 구현한다."""

    def build(self, model: str, body: dict, stream: bool):  # -> Response
        raise NotImplementedError


class _Ok(Behavior):
    def __init__(self, text: str = "hello", usage: tuple = (10, 5)):
        self.text = text
        self.usage = usage

    def build(self, model, body, stream):
        pt, ct = self.usage
        if not stream:
            return JSONResponse(_completion_body(model, self.text, pt, ct))
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(
            _ok_stream(model, self.text, pt, ct, include_usage),
            media_type="text/event-stream",
        )


class _RateLimit(Behavior):
    def __init__(self, retry_after: Optional[int] = None):
        self.retry_after = retry_after

    def build(self, model, body, stream):
        headers = {}
        if self.retry_after is not None:
            headers["Retry-After"] = str(self.retry_after)
        return JSONResponse(
            status_code=429,
            headers=headers,
            content=_error_body("rate limit exceeded", "rate_limit_error",
                                "rate_limit_exceeded"),
        )


class _ServerError(Behavior):
    def __init__(self, status: int = 500):
        self.status = status

    def build(self, model, body, stream):
        return JSONResponse(
            status_code=self.status,
            content=_error_body(f"upstream server error {self.status}",
                                "server_error", None),
        )


class _DelayTTFT(Behavior):
    """첫 바이트 전 지연 후 정상 응답 — TTFT 타임아웃 유발용.

    스트리밍이면 헤더(200)는 즉시 나가고 첫 SSE 청크만 지연되므로
    LiteLLMProvider의 TTFT wait_for가 이를 잡는다 (§5.8/§5.13).
    """

    def __init__(self, seconds: float):
        self.seconds = seconds

    def build(self, model, body, stream):
        if not stream:
            return StreamingResponse(
                _delayed_nonstream(model, self.seconds),
                media_type="application/json",
            )
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(
            _delayed_stream(model, self.seconds, include_usage),
            media_type="text/event-stream",
        )


class _CutMidstream(Behavior):
    """정상 청크 1개를 보낸 뒤 mid-stream 실패를 발생시킨다.

    관측된 사실(spike): 실제 TCP 연결을 그냥 끊으면(생성기 예외) litellm
    스트림 래퍼는 이를 정상 EOF로 처리하고 usage 청크까지 합성해 조용히 완료한다
    — 에러가 표면화되지 않는다. 반면 SSE 스트림에 OpenAI 에러 청크
    (`data: {"error": ...}`)를 흘리면 litellm이 예외로 표면화한다(UpstreamServerError).
    실제 프로바이더도 mid-stream 실패를 에러 청크로 신호하므로, 여기서는 이 방식으로
    "첫 청크 후 실패 → SSE error 이벤트" 경로(§7)를 결정론적으로 재현한다.
    """

    def build(self, model, body, stream):
        return StreamingResponse(_cut_stream(model), media_type="text/event-stream")


class _ContextLength(Behavior):
    def build(self, model, body, stream):
        return JSONResponse(
            status_code=400,
            content=_error_body(
                "This model's maximum context length is exceeded. "
                "Please reduce the length of the messages.",
                "invalid_request_error", "context_length_exceeded"),
        )


# --- behavior 팩토리 (스펙의 이름 그대로) ----------------------------------


def ok(text: str = "hello", usage: tuple = (10, 5)) -> Behavior:
    return _Ok(text, usage)


def rate_limit(retry_after: Optional[int] = None) -> Behavior:
    return _RateLimit(retry_after)


def server_error(status: int = 500) -> Behavior:
    return _ServerError(status)


def delay_ttft(seconds: float) -> Behavior:
    return _DelayTTFT(seconds)


def cut_midstream() -> Behavior:
    return _CutMidstream()


def context_length() -> Behavior:
    return _ContextLength()


# --------------------------------------------------------------------------
# 응답 바디/스트림 빌더
# --------------------------------------------------------------------------


def _completion_body(model: str, text: str, pt: int, ct: int) -> dict:
    return {
        "id": "chatcmpl-sim",
        "object": "chat.completion",
        "created": _CREATED,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "total_tokens": pt + ct},
    }


def _chunk(model: str, delta: dict, finish: Optional[str] = None) -> dict:
    return {
        "id": "chatcmpl-sim",
        "object": "chat.completion.chunk",
        "created": _CREATED,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _usage_chunk(model: str, pt: int, ct: int) -> dict:
    return {
        "id": "chatcmpl-sim",
        "object": "chat.completion.chunk",
        "created": _CREATED,
        "model": model,
        "choices": [],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "total_tokens": pt + ct},
    }


def _sse(obj: Any) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def _content_chunks(model: str, text: str) -> list:
    # role 델타 + 내용 2조각 + finish
    mid = max(1, len(text) // 2)
    return [
        _chunk(model, {"role": "assistant", "content": ""}),
        _chunk(model, {"content": text[:mid]}),
        _chunk(model, {"content": text[mid:]}),
        _chunk(model, {}, finish="stop"),
    ]


async def _ok_stream(model, text, pt, ct, include_usage):
    for c in _content_chunks(model, text):
        yield _sse(c)
    if include_usage:
        yield _sse(_usage_chunk(model, pt, ct))
    yield b"data: [DONE]\n\n"


async def _delayed_stream(model, seconds, include_usage):
    # 헤더(200)는 Starlette가 먼저 보내고, 첫 청크만 지연된다 → TTFT 타임아웃 유발
    await anyio.sleep(seconds)
    async for part in _ok_stream(model, "hello", 10, 5, include_usage):
        yield part


async def _delayed_nonstream(model, seconds):
    await anyio.sleep(seconds)
    yield json.dumps(_completion_body(model, "hello", 10, 5)).encode()


async def _cut_stream(model):
    # 정상 청크 1개 → 에러 청크. litellm이 mid-stream 예외로 표면화한다.
    yield _sse(_chunk(model, {"role": "assistant", "content": "par"}))
    yield _sse(_error_body("upstream connection lost mid-stream",
                           "server_error", None))


def _error_body(message: str, err_type: str, code: Optional[str]) -> dict:
    return {"error": {"message": message, "type": err_type, "code": code}}


# --------------------------------------------------------------------------
# ProviderSimulator
# --------------------------------------------------------------------------


class ProviderSimulator:
    """OpenAI 호환 mock 서버. 백그라운드 스레드에서 uvicorn을 돌린다."""

    def __init__(self):
        self.scripts: dict[str, list[Behavior]] = {}
        self.requests: list[tuple[str, bool]] = []   # (model, stream) 누적
        self.port: Optional[int] = None
        self._app = self._build_app()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

    # --- 스크립트 ---

    def script(self, model_id: str, behaviors: list[Behavior]) -> None:
        """모델별 동작 큐를 지정. 큐가 소진되면 기본 ok."""
        self.scripts[model_id] = list(behaviors)

    def _pop(self, model_id: str) -> Behavior:
        queue = self.scripts.get(model_id)
        if queue:
            return queue.pop(0)
        return ok()  # 큐가 비면 기본 정상 응답

    def known_models(self) -> list[str]:
        ids = set(self.scripts.keys()) | {"model-a", "model-b", "model-c"}
        return sorted(ids)

    # --- FastAPI 앱 ---

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            body = await request.json()
            model = str(body.get("model", ""))
            stream = bool(body.get("stream", False))
            self.requests.append((model, stream))
            return self._pop(model).build(model, body, stream)

        @app.get("/v1/models")
        async def list_models():
            return {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "sim"}
                for m in self.known_models()
            ]}

        return app

    # --- 기동/정지 ---

    def start(self) -> "ProviderSimulator":
        # 포트 0을 우리가 직접 바인딩해 실제 포트를 미리 확보한다.
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]

        config = uvicorn.Config(
            self._app, log_level="warning", lifespan="off", access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self._sock]}, daemon=True
        )
        self._thread.start()

        # 기동 완료 대기 (폴링)
        deadline = time.monotonic() + 10.0
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("simulator failed to start within 10s")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server = None
        self._thread = None

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    # --- 컨텍스트 매니저 ---

    def __enter__(self) -> "ProviderSimulator":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
