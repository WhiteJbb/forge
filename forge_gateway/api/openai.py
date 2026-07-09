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
from ..core.policy import PolicyEngine, RoutePlan
from ..core.registry import ModelEntry, Registry
from ..core.scheduler import NoCandidateError, Scheduler
from ..core.throttle import ProviderThrottle
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
from . import anthropic_convert

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
    policy: Optional[PolicyEngine] = None  # None이면 기본 tier 라우팅 (하위 호환)
    throttle: Optional[ProviderThrottle] = None  # None이면 선제 스로틀 없음


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


def _forge_headers(entry: ModelEntry, tier: str, task: str, attempt: int,
                   policy: Optional[str] = None) -> dict[str, str]:
    headers = {
        "X-Forge-Model": entry.id,
        "X-Forge-Tier": tier,
        "X-Forge-Task": task,
        "X-Forge-Attempt": str(attempt),
    }
    if policy:
        headers["X-Forge-Policy"] = policy
    return headers


def _anthropic_event(name: str, data: dict) -> bytes:
    """Anthropic SSE는 event: 줄을 포함한다 (§5.8)"""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _release_slot(slot_cm) -> None:
    """수동 진입한 스로틀 슬롯을 best-effort 해제 (스트리밍 경로 전용)"""
    if slot_cm is None:
        return
    try:
        await slot_cm.__aexit__(None, None, None)
    except Exception:
        pass


def _openai_error(message: str, status_code: int, err_type: str = "forge_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": status_code}},
    )


async def _watch_disconnect(request: Request) -> None:
    """클라이언트 연결이 끊길 때까지 대기 — failover 루프와 race시킨다 (§5.13)"""
    while not await request.is_disconnected():
        await asyncio.sleep(0.5)


def _chunk_has_payload(chunk: dict) -> bool:
    """스트리밍 청크가 실제 내용(델타/종료 신호)을 담고 있는지 — usage 전용 청크 판별용"""
    for choice in chunk.get("choices") or []:
        if choice.get("finish_reason"):
            return True
        delta = choice.get("delta") or {}
        if any(delta.get(k) for k in
               ("content", "tool_calls", "function_call", "role", "refusal")):
            return True
    return False


class ChatPipeline:
    """chat completion 한 건의 선택→호출→failover→기록 수명주기.

    dialect="anthropic"이면 응답을 Anthropic Messages 포맷으로 역변환한다 (§5.8).
    입력 body는 이미 내부 표준(OpenAI) 포맷이어야 한다 — 변환은 api/anthropic.py 책임.
    """

    def __init__(self, deps: Deps, request: Request, body: dict[str, Any],
                 dialect: str = "openai"):
        self.deps = deps
        self.request = request
        self.body = body
        self.dialect = dialect
        self.request_id = uuid.uuid4().hex
        self.stream: bool = bool(body.get("stream", False))
        self.client_wants_usage = bool(
            (body.get("stream_options") or {}).get("include_usage")
        )
        self.deadline = time.monotonic() + deps.config.timeouts.total_deadline

    def _error(self, message: str, status_code: int, err_type: str) -> JSONResponse:
        if self.dialect == "anthropic":
            return JSONResponse(
                status_code=status_code,
                content={"type": "error",
                         "error": {"type": err_type, "message": message}},
            )
        return _openai_error(message, status_code, err_type)

    # --- 진입점 ---

    async def run(self):
        model = str(self.body.get("model", "auto"))
        task_hint, _ = _extract_task_hint(self.request, model)
        analysis = self.deps.analyzer.analyze(self.body, task_hint=task_hint)
        user_agent = self.request.headers.get("user-agent", "")
        max_tokens = self.body.get("max_tokens")

        # 실제 모델 id 지정 시 라우팅 우회 — failover 없음, constraints는 여전히 적용 (§5.4)
        direct = self.deps.registry.resolve_client_model(model)
        if direct is not None:
            if self.deps.policy is not None and not self.deps.policy.entry_passes_constraints(
                direct, analysis, requested_model=model,
                user_agent=user_agent, max_tokens=max_tokens,
            ):
                return self._error(
                    f"model {model!r} is excluded by policy constraints",
                    403, "policy_constraint",
                )
            return await self._attempt_loop(analysis, forced=direct)

        # 알 수 없는 모델명은 auto로 간주하고 정책 라우팅 (§11 별칭 정책)
        plan: Optional[RoutePlan] = None
        if self.deps.policy is not None:
            plan = self.deps.policy.plan(
                analysis, requested_model=model,
                user_agent=user_agent, max_tokens=max_tokens,
            )
        return await self._attempt_loop(analysis, plan=plan)

    async def _attempt_loop(self, analysis: AnalysisResult,
                            forced: Optional[ModelEntry] = None,
                            plan: Optional[RoutePlan] = None):
        exclude: set[str] = set()
        min_ctx = 0
        max_attempts = 1 if forced else self.deps.config.scheduler.max_attempts

        for attempt in range(1, max_attempts + 1):
            if time.monotonic() > self.deadline:
                return self._error("total deadline exceeded", 504, "timeout")

            if forced:
                entry, info = forced, {"tier": forced.tier, "task": analysis.task,
                                       "selected_by": "client"}
            else:
                try:
                    entry, info = self.deps.scheduler.select(
                        analysis, exclude, min_ctx,
                        groups=plan.groups if plan is not None else None,
                        provider_filter=(self.deps.throttle.peek
                                         if self.deps.throttle else None),
                    )
                except NoCandidateError as e:
                    reason = e.reason
                    if plan is not None and plan.rejected_by_constraints:
                        reason += (f" (policy {plan.policy_name!r} constraints "
                                   f"excluded {plan.rejected_by_constraints} models)")
                    return self._error(reason, e.status_code, "no_candidate")
                if plan is not None:
                    info["policy"] = plan.policy_name

            # 선제 스로틀: dispatch 직전 토큰 소모 — peek과의 race에서 지면 재선택 (§5.13)
            if self.deps.throttle is not None and not self.deps.throttle.consume(entry.provider):
                exclude.add(entry.id)
                continue

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
                if self.dialect == "anthropic":
                    return self._error(str(e), e.status_code or 400, "invalid_request_error")
                body = e.body or {"error": {"message": str(e), "type": "upstream_error",
                                            "code": e.status_code}}
                return JSONResponse(status_code=e.status_code or 400, content=body)

            except asyncio.CancelledError:
                raise  # 서버 종료 등 — 그대로 전파

        return self._error(
            f"all models failed after {max_attempts} attempts", 503, "all_failed"
        )

    # --- 논스트리밍: 업스트림 호출과 disconnect를 race (§5.13 취소 전파) ---

    async def _try_nonstream(self, entry: ModelEntry, provider: Provider,
                             analysis: AnalysisResult, info: dict, attempt: int):
        start = time.monotonic()
        slot_cm = await self._enter_slot(entry.provider)
        try:
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
        finally:
            await _release_slot(slot_cm)

        latency_ms = (time.monotonic() - start) * 1000
        usage = response.get("usage") or {}
        pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

        self.deps.scheduler.record_success(entry.id, latency_ms)
        self.deps.scheduler.move_pin(analysis.session_key, entry.id)
        self._record(entry, analysis, info, attempt, latency_ms, None, pt, ct,
                     success=True, status_code=200, error_type=None)

        if self.dialect == "anthropic":
            response = anthropic_convert.response_to_anthropic(
                response, str(self.body.get("model", "")))
        return JSONResponse(
            content=response,
            headers=_forge_headers(entry, info["tier"], analysis.task, attempt,
                                   info.get("policy")),
        )

    # --- 스트리밍: 첫 청크 확보 전까지만 failover 가능 (§5.8) ---

    async def _try_stream(self, entry: ModelEntry, provider: Provider,
                          analysis: AnalysisResult, info: dict, attempt: int):
        start = time.monotonic()
        payload = dict(self.body)
        slot_cm = await self._enter_slot(entry.provider)
        agen = provider.chat_stream(entry.provider_model_id, payload)

        # 첫 청크 이전 실패(429/5xx/timeout/TTFT)는 typed 예외로 상위 failover 루프에 전달.
        # 슬롯은 스트림 종료(sse finally)까지 유지 — 실패 시 여기서 즉시 해제.
        try:
            first_chunk = await agen.__anext__()
        except StopAsyncIteration:
            await _release_slot(slot_cm)
            raise UpstreamServerError("empty stream from provider", status_code=502)
        except BaseException:
            await _release_slot(slot_cm)
            raise

        ttft_ms = (time.monotonic() - start) * 1000
        self.deps.scheduler.move_pin(analysis.session_key, entry.id)

        pipeline = self

        async def sse() -> Any:
            usage_pt, usage_ct = 0, 0
            completed = False
            conv = (anthropic_convert.OpenAIToAnthropicStream(
                        str(pipeline.body.get("model", "")))
                    if pipeline.dialect == "anthropic" else None)
            try:
                async for chunk in _prepend(first_chunk, agen):
                    usage = chunk.get("usage")
                    if usage:
                        usage_pt = usage.get("prompt_tokens", usage_pt)
                        usage_ct = usage.get("completion_tokens", usage_ct)
                    if conv is not None:
                        for name, data in conv.feed(chunk):
                            yield _anthropic_event(name, data)
                        continue
                    if usage and not pipeline.client_wants_usage:
                        # usage는 강제 주입된 것 — 클라이언트가 원했을 때만 전달 (§5.8).
                        # litellm은 usage 전용 청크에도 빈 delta의 choices를 합성하므로
                        # (시뮬레이터가 발견) choices 유무가 아니라 실제 payload로 판별한다.
                        if not _chunk_has_payload(chunk):
                            continue
                        chunk = dict(chunk)
                        chunk.pop("usage", None)
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                if conv is not None:
                    for name, data in conv.finish():
                        yield _anthropic_event(name, data)
                else:
                    yield b"data: [DONE]\n\n"
                completed = True
            except ProviderError as e:
                # mid-stream 에러 — 재시도 불가, SSE error 이벤트로 전달 (§7)
                if conv is not None:
                    yield _anthropic_event("error", {
                        "type": "error",
                        "error": {"type": "api_error", "message": str(e)}})
                else:
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
                await _release_slot(slot_cm)

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers=_forge_headers(entry, info["tier"], analysis.task, attempt,
                                   info.get("policy")),
        )

    # --- 스로틀 슬롯 (§5.13) ---

    async def _enter_slot(self, provider_name: str):
        """max_concurrent 세마포어 진입. 타임아웃은 failover 가능한 UpstreamTimeout으로."""
        if self.deps.throttle is None:
            return None
        slot_cm = self.deps.throttle.slot(provider_name)
        try:
            await slot_cm.__aenter__()
        except TimeoutError as e:
            raise UpstreamTimeout("provider concurrency slot timeout") from e
        return slot_cm

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

    @router.post("/v1/route/explain")
    async def route_explain(request: Request):
        """드라이런 — 실제 호출 없이 판정·정책 매칭·탈락 사유·스코어표 반환 (§5.8)"""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")

        model = str(body.get("model", "auto"))
        task_hint, _ = _extract_task_hint(request, model)
        analysis = deps.analyzer.analyze(body, task_hint=task_hint)
        user_agent = request.headers.get("user-agent", "")
        max_tokens = body.get("max_tokens")

        result: dict[str, Any] = {
            "analysis": {
                "task": analysis.task,
                "confidence": round(analysis.confidence, 2),
                "est_prompt_tokens": analysis.est_prompt_tokens,
                "required_features": sorted(analysis.required_features),
                "session_key": analysis.session_key,
                "language": analysis.language,
            },
        }

        direct = deps.registry.resolve_client_model(model)
        if direct is not None:
            passes = (deps.policy is None or deps.policy.entry_passes_constraints(
                direct, analysis, requested_model=model,
                user_agent=user_agent, max_tokens=max_tokens))
            result["direct_model"] = {
                "model": direct.id,
                "passes_constraints": passes,
                "note": "client-specified model bypasses routing; no failover",
            }
            return result

        groups = None
        if deps.policy is not None:
            plan = deps.policy.plan(analysis, requested_model=model,
                                    user_agent=user_agent, max_tokens=max_tokens)
            groups = plan.groups
            result["policy"] = {
                "matched": plan.policy_name,
                "rejected_by_constraints": plan.rejected_by_constraints,
            }

        result.update(deps.scheduler.explain(
            analysis, groups=groups,
            provider_filter=(deps.throttle.peek if deps.throttle else None),
        ))
        return result

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
