"""Anthropic Messages API 엔드포인트 — Claude Code용 (DESIGN.md §5.8)

/v1/messages 요청을 내부 표준(OpenAI) 포맷으로 변환해 동일한
Analyzer→Policy→Scheduler 파이프라인에 태우고, 응답을 Anthropic 포맷
(스트리밍은 message_start/content_block_delta 등 이벤트 시퀀스)으로 역변환한다.

변환은 anthropic_convert.py, failover 파이프라인은 openai.ChatPipeline(dialect).
클라이언트 설정: ANTHROPIC_BASE_URL=http://127.0.0.1:4000
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .anthropic_convert import request_to_openai
from .openai import ChatPipeline, Deps


def _anthropic_error(message: str, status_code: int,
                     err_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": err_type, "message": message}},
    )


def build_router(deps: Deps) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/messages")
    async def messages(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _anthropic_error("invalid JSON body", 400)

        if not body.get("messages"):
            return _anthropic_error("messages field is required", 400)
        if "max_tokens" not in body:
            # Anthropic Messages API에서 max_tokens는 필수
            return _anthropic_error("max_tokens field is required", 400)

        try:
            openai_body = request_to_openai(body)
        except Exception as e:
            return _anthropic_error(f"unsupported request shape: {e}", 400)

        return await ChatPipeline(deps, request, openai_body,
                                  dialect="anthropic").run()

    return router
