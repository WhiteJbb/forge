"""OpenAI 호환 엔드포인트 — failover 파이프라인의 본체 (DESIGN.md §3, §5.8, §7)

요청 흐름: 인증 → 힌트 추출 → Analyzer → Scheduler(하드 필터→세션 고정→스코어링)
→ Provider 호출 → 실패 유형별 failover → 메트릭 기록(비블로킹) → forge 헤더 첨부.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.analyzer import RequestAnalyzer
from ..core.metrics import MetricsEngine
from ..core.registry import ModelEntry, Registry
from ..core.scheduler import NoCandidateError, Scheduler
from ..core.types import AnalysisResult
from ..providers.base import (
    ContextLengthExceeded,
    Provider,
    ProviderError,
    RateLimited,
    UpstreamBadRequest,
    UpstreamConnectionError,
    UpstreamServerError,
    UpstreamTimeout,
)
from ..settings import ForgeConfig
from ..storage.base import RequestMetric

logger = logging.getLogger("forge.api")

VALID_TASKS = ("coding", "debug", "refactor", "documentation", "testing")
AUTO_ALIASES = ("auto", "coder")  # "coder"는 기존 클라이언트 설정 호환


@dataclass
class Deps:
    """server.py lifespan에서 조립되는 의존성 묶음"""

    config: ForgeConfig
    registry: Registry
    scheduler: Scheduler
    analyzer: RequestAnalyzer
    metrics: MetricsEngine
    providers: dict[str, Provider]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_task_hint(request: Request, model: str) -> tuple[Optional[str], bool]:
    """(task_hint, is_auto_routing) — 힌트 채널: 헤더 > auto:task 별칭 (§5.3)"""
    hint = request.headers.get("x-forge-task", "").strip().lower() or None
    if hint not in VALID_TASKS:
        hint = None

    if model in AUTO_ALIASES:
        return hint, True
    if model.startswith("auto:"):
        alias_task = model.split(":", 1)[1].strip().lower()
        return (alias_task if alias_task in VALID_TASKS else hint), True
    return hint, False


def _compute_cost(entry: ModelEntry, prompt_tokens: int, completion_tokens: int) -> float:
    if entry.price_per_mtok is None:
        return 0.0
    pin, pout = entry.price_per_mtok
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


def _forge_headers(entry: ModelEntry, tier: str, task: str, attempt: int) -> dict[str, str]:
    return {
        "X-Forge-Model": entry.id,
        "X-Forge-Tier": tier,
        "X-Forge-Task": task,
        "X-Forge-Attempt": str(attempt),
    }


def _openai_error(message: str, status_code: int, err_type: str = "forge_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": status_code}},
    )


async def _watch_disconnect(request: Request) -> None:
    """클라이언트 연결이 끊길 때까지 대기 — failover 루프와 race시킨다 (§5.13)"""
    while not await request.is_disconnected():
        await asyncio.sleep(0.5)


class ChatPipeline:
    """chat completion 한 건의 선택→호출→failover→기록 수명주기"""

    def __init__(self, deps: Deps, request: Request, body: dict[str, Any]):
        self.deps = deps
        self.request = request
        self.body = body
        self.request_id = uuid.uuid4().hex
        self.stream: bool = bool(body.get("stream", False))
        self.client_wants_usage = bool(
            (body.get("stream_options") or {}).get("include_usage")
        )
        self.deadline = time.monotonic() + deps.config.timeouts.total_deadline

    # --- 진입점 ---

    async def run(self):
        model = str(self.body.get("model", "auto"))
        task_hint, _ = _extract_task_hint(self.request, model)
        analysis = self.deps.analyzer.analyze(self.body, task_hint=task_hint)

        # 실제 모델 id 지정 시 라우팅 우회 — failover 없음, constraints는 M2 (§5.4)
        direct = self.deps.registry.resolve_client_model(model)
        if direct is not None:
            return await self._attempt_loop(analysis, forced=direct)
        # 알 수 없는 모델명은 auto로 간주하고 정책 라우팅 (§11 별칭 정책)
        return await self._attempt_loop(analysis)

    async def _attempt_loop(self, analysis: AnalysisResult, forced: Optional[ModelEntry] = None):
        exclude: set[str] = set()
        min_ctx = 0
        max_attempts = 1 if forced else self.deps.config.scheduler.max_attempts

        for attempt in range(1, max_attempts + 1):
            if time.monotonic() > self.deadline:
                return _openai_error("total deadline exceeded", 504, "timeout")

            if forced:
                entry, info = forced, {"tier": forced.tier, "task": analysis.task,
                                       "selected_by": "client"}
            else:
                try:
                    entry, info = self.deps.scheduler.select(analysis, exclude, min_ctx)
                except NoCandidateError as e:
                    return _openai_error(e.reason, e.status_code, "no_candidate")

            provider = self.deps.providers[entry.provider]
            logger.info(
                "req=%s attempt=%d model=%s task=%s by=%s",
                self.request_id, attempt, entry.id, analysis.task,
                info.get("selected_by"),
            )

            try:
                if self.stream:
                    return await self._try_stream(entry, provider, analysis, info, attempt)
                return await self._try_nonstream(entry, provider, analysis, info, attempt)

            except (RateLimited, UpstreamServerError, UpstreamTimeout,
                    UpstreamConnectionError, ContextLengthExceeded) as e:
                error_type = self.deps.scheduler.record_failure(entry.id, e)
                self._record(entry, analysis, info, attempt, 0.0, None, 0, 0,
                             success=False, status_code=e.status_code, error_type=error_type)
                exclude.add(entry.id)
                if isinstance(e, ContextLengthExceeded):
                    # 상향 failover: 실패 모델의 (보정 전) 창보다 큰 후보 요구 (§7)
                    min_ctx = max(min_ctx, entry.context_window or analysis.est_prompt_tokens)
                logger.warning("req=%s model=%s failed (%s), trying next",
                               self.request_id, entry.id, error_type)
                continue

            except UpstreamBadRequest as e:
                # 요청 자체 문제 — failover 없이 업스트림 에러 반환 (§7)
                self.deps.scheduler.record_failure(entry.id, e)
                self._record(entry, analysis, info, attempt, 0.0, None, 0, 0,
                             success=False, status_code=e.status_code, error_type="4xx")
                body = e.body or {"error": {"message": str(e), "type": "upstream_error",
                                            "code": e.status_code}}
                return JSONResponse(status_code=e.status_code or 400, content=body)

            except asyncio.CancelledError:
                raise  # 서버 종료 등 — 그대로 전파

        return _openai_error(
            f"all models failed after {max_attempts} attempts", 503, "all_failed"
        )

    # --- 논스트리밍: 업스트림 호출과 disconnect를 race (§5.13 취소 전파) ---

    async def _try_nonstream(self, entry: ModelEntry, provider: Provider,
                             analysis: AnalysisResult, info: dict, attempt: int):
        start = time.monotonic()
        upstream = asyncio.ensure_future(
            provider.chat(entry.provider_model_id, dict(self.body))
        )
        watcher = asyncio.ensure_future(_watch_disconnect(self.request))
        try:
            done, _ = await asyncio.wait({upstream, watcher},
                                         return_when=asyncio.FIRST_COMPLETED)
            if watcher in done and upstream not in done:
                upstream.cancel()
                self._record(entry, analysis, info, attempt,
                             (time.monotonic() - start) * 1000, None, 0, 0,
                             success=False, status_code=None, error_type="cancelled")
                logger.info("req=%s cancelled by client", self.request_id)
                return JSONResponse(status_code=499, content={"error": {
                    "message": "client disconnected", "type": "cancelled", "code": 499}})
            response = upstream.result()  # 예외는 여기서 typed으로 재발생
        finally:
            watcher.cancel()

        latency_ms = (time.monotonic() - start) * 1000
        usage = response.get("usage") or {}
        pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

        self.deps.scheduler.record_success(entry.id, latency_ms)
        self.deps.scheduler.move_pin(analysis.session_key, entry.id)
        self._record(entry, analysis, info, attempt, latency_ms, None, pt, ct,
                     success=True, status_code=200, error_type=None)

        return JSONResponse(
            content=response,
            headers=_forge_headers(entry, info["tier"], analysis.task, attempt),
        )

    # --- 스트리밍: 첫 청크 확보 전까지만 failover 가능 (§5.8) ---

    async def _try_stream(self, entry: ModelEntry, provider: Provider,
                          analysis: AnalysisResult, info: dict, attempt: int):
        start = time.monotonic()
        payload = dict(self.body)
        agen = provider.chat_stream(entry.provider_model_id, payload)

        # 첫 청크 이전 실패(429/5xx/timeout/TTFT)는 typed 예외로 상위 failover 루프에 전달
        try:
            first_chunk = await agen.__anext__()
        except StopAsyncIteration:
            raise UpstreamServerError("empty stream from provider", status_code=502)

        ttft_ms = (time.monotonic() - start) * 1000
        self.deps.scheduler.move_pin(analysis.session_key, entry.id)

        pipeline = self

        async def sse() -> Any:
            usage_pt, usage_ct = 0, 0
            completed = False
            try:
                async for chunk in _prepend(first_chunk, agen):
                    usage = chunk.get("usage")
                    if usage:
                        usage_pt = usage.get("prompt_tokens", usage_pt)
                        usage_ct = usage.get("completion_tokens", usage_ct)
                        # usage 청크는 강제 주입된 것 — 클라이언트가 원했을 때만 전달 (§5.8)
                        if not (chunk.get("choices") or pipeline.client_wants_usage):
                            continue
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                yield b"data: [DONE]\n\n"
                completed = True
            except ProviderError as e:
                # mid-stream 에러 — 재시도 불가, SSE error 이벤트로 전달 (§7)
                err = {"error": {"message": str(e), "type": "upstream_error",
                                 "code": e.status_code}}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
            finally:
                latency_ms = (time.monotonic() - start) * 1000
                if completed:
                    pipeline.deps.scheduler.record_success(entry.id, ttft_ms)  # TTFT 기준 (§5.5)
                    pipeline._record(entry, analysis, info, attempt, latency_ms, ttft_ms,
                                     usage_pt, usage_ct, success=True,
                                     status_code=200, error_type=None)
                else:
                    # 취소(disconnect)는 모델 실패로 집계하지 않는다 (§7)
                    pipeline._record(entry, analysis, info, attempt, latency_ms, ttft_ms,
                                     usage_pt, usage_ct, success=False,
                                     status_code=None, error_type="cancelled")
                await _aclose_quiet(agen)

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers=_forge_headers(entry, info["tier"], analysis.task, attempt),
        )

    # --- 기록 ---

    def _record(self, entry: ModelEntry, analysis: AnalysisResult, info: dict,
                attempt: int, latency_ms: float, ttft_ms: Optional[float],
                prompt_tokens: int, completion_tokens: int, *,
                success: bool, status_code: Optional[int], error_type: Optional[str]) -> None:
        self.deps.metrics.record(RequestMetric(
            request_id=self.request_id,
            timestamp=_utcnow_iso(),
            model=entry.id,
            provider=entry.provider,
            tier=info.get("tier"),
            task_type=analysis.task,
            attempt=attempt,
            latency_ms=round(latency_ms, 1),
            ttft_ms=round(ttft_ms, 1) if ttft_ms is not None else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            had_tools="tools" in analysis.required_features,
            success=success,
            status_code=status_code,
            error_type=error_type,
            cost=_compute_cost(entry, prompt_tokens, completion_tokens),
        ))


async def _prepend(first: dict, agen):
    yield first
    async for chunk in agen:
        yield chunk


async def _aclose_quiet(agen) -> None:
    try:
        await agen.aclose()
    except Exception:
        pass


# --- 라우터 ---

def build_router(deps: Deps) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/chat/completions")
    @router.post("/chat/completions")
    async def chat_completions(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")
        if "messages" not in body:
            raise HTTPException(status_code=400, detail="messages field is required")
        return await ChatPipeline(deps, request, body).run()

    @router.post("/v1/embeddings")
    async def embeddings(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")
        model = str(body.get("model", ""))
        entry = deps.registry.resolve_client_model(model)
        if entry is None:
            # M1: embedding 전용 capability 분류가 없어 명시 모델만 허용
            return _openai_error(
                f"unknown embedding model {model!r} — specify a full model id", 400)
        provider = deps.providers[entry.provider]
        start = time.monotonic()
        try:
            response = await provider.embeddings(entry.provider_model_id, dict(body))
        except UpstreamBadRequest as e:
            return JSONResponse(status_code=e.status_code or 400,
                                content=e.body or {"error": {"message": str(e)}})
        except ProviderError as e:
            error_type = deps.scheduler.record_failure(entry.id, e)
            return _openai_error(str(e), 502, error_type)
        deps.scheduler.record_success(entry.id, (time.monotonic() - start) * 1000)
        return JSONResponse(content=response)

    @router.get("/v1/models")
    @router.get("/models")
    async def list_models():
        created = int(time.time())
        data = [{"id": e.id, "object": "model", "created": created, "owned_by": "forge"}
                for e in deps.registry.all()]
        data += [{"id": alias, "object": "model", "created": created, "owned_by": "forge"}
                 for alias in ("auto", "coder",
                               *(f"auto:{t}" for t in VALID_TASKS))]
        return {"object": "list", "data": data}

    return router
