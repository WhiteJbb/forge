"""
Forge API Server - FastAPI application with OpenAI-compatible endpoints
"""

import os
import time
import json
import logging
import asyncio
from typing import Optional, Any
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import TIERS, CAPABILITY_MATRIX, settings
from .analyzer import RequestAnalyzer
from .scheduler import Scheduler
from .health_monitor import HealthMonitor
from .metrics import MetricsEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("forge")

# Load NVIDIA API key from environment
nvidia_api_key = os.environ.get("NVIDIA_API_KEY", "")
if nvidia_api_key:
    settings.nvidia_api_key = nvidia_api_key

# Initialize components
analyzer = RequestAnalyzer()
scheduler = Scheduler()
metrics = MetricsEngine()
health_monitor = HealthMonitor(scheduler)


# --- Pydantic Models ---

class ChatMessage(BaseModel):
    role: str
    content: Any  # Can be string or list (multimodal)


class ChatCompletionRequest(BaseModel):
    model: str = "coder"
    messages: list[ChatMessage]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    top_p: Optional[float] = 1.0
    frequency_penalty: Optional[float] = 0.0
    presence_penalty: Optional[float] = 0.0
    stop: Optional[Any] = None
    n: Optional[int] = 1
    user: Optional[str] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "forge"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Forge API Server...")
    await health_monitor.start()
    logger.info("Forge API Server started on port %s", settings.port)

    yield

    # Shutdown
    logger.info("Shutting down Forge API Server...")
    await health_monitor.stop()
    logger.info("Forge API Server stopped")


# --- FastAPI App ---

app = FastAPI(
    title="Forge",
    description="Intelligent AI Gateway for Coding Agents",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Helper Functions ---

def get_api_base_for_model(model: str) -> str:
    """Get API base URL for a model"""
    # All models use NVIDIA API for now
    return "https://integrate.api.nvidia.com/v1"


async def forward_to_provider(
    model: str,
    request_data: dict,
    stream: bool = False,
):
    """Forward request to the actual provider"""
    api_base = get_api_base_for_model(model)
    api_key = settings.nvidia_api_key or os.environ.get("NVIDIA_API_KEY", "")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Update model in request data
    request_data["model"] = model

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        if stream:
            async with client.stream(
                "POST",
                f"{api_base}/chat/completions",
                headers=headers,
                json=request_data,
                timeout=settings.request_timeout,
            ) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk
        else:
            response = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json=request_data,
                timeout=settings.request_timeout,
            )
            yield response


async def handle_completion_request(request_data: dict, stream: bool = False):
    """Handle chat completion request with model selection and failover"""
    messages = request_data.get("messages", [])

    # Analyze request
    analysis = analyzer.analyze([m.model_dump() if hasattr(m, "model_dump") else m for m in messages])
    task_type = analysis.get("task", "coding")
    logger.info(f"Request analyzed: task={task_type}, confidence={analysis.get('confidence', 0):.2f}")

    # Try models with failover
    exclude_models = []
    max_retries = 3

    for attempt in range(max_retries):
        # Select best model
        selected_model, selection_info = scheduler.select_model(
            task_type=task_type,
            exclude_models=exclude_models,
        )

        if not selected_model:
            logger.error("No available models after %d attempts", attempt)
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "No available models",
                    "task_type": task_type,
                    "attempts": attempt,
                },
            )

        logger.info(
            f"Attempt {attempt + 1}: Selected model={selected_model} "
            f"(tier={selection_info.get('tier')}, score={selection_info.get('score', 0):.2f})"
        )

        # Forward request
        api_base = get_api_base_for_model(selected_model)
        api_key = settings.nvidia_api_key or os.environ.get("NVIDIA_API_KEY", "")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        request_data["model"] = selected_model

        start_time = time.time()

        try:
            if stream:
                # Streaming response
                return await _stream_response(
                    selected_model,
                    selection_info.get("tier", "tier1"),
                    task_type,
                    api_base,
                    headers,
                    request_data,
                    start_time,
                )
            else:
                # Non-streaming response
                async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                    response = await client.post(
                        f"{api_base}/chat/completions",
                        headers=headers,
                        json=request_data,
                        timeout=settings.request_timeout,
                    )

                    latency_ms = (time.time() - start_time) * 1000

                    if response.status_code == 200:
                        # Success
                        scheduler.record_success(selected_model, latency_ms)

                        # Parse response for token counts
                        response_data = response.json()
                        usage = response_data.get("usage", {})
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        completion_tokens = usage.get("completion_tokens", 0)

                        # Record metrics
                        metrics.record_request(
                            model=selected_model,
                            tier=selection_info.get("tier"),
                            task_type=task_type,
                            latency_ms=latency_ms,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            success=True,
                        )

                        # Add Forge metadata to response
                        response_data["forge"] = {
                            "model": selected_model,
                            "tier": selection_info.get("tier"),
                            "task_type": task_type,
                            "latency_ms": round(latency_ms, 1),
                            "score": round(selection_info.get("score", 0), 2),
                            "analysis": analysis,
                        }

                        return JSONResponse(
                            content=response_data,
                            status_code=200,
                            media_type="application/json",
                        )

                    elif response.status_code == 429:
                        # Rate limited - try next model
                        logger.warning(f"Model {selected_model}: 429 rate limited")
                        scheduler.record_failure(selected_model, "429")
                        metrics.record_request(
                            model=selected_model,
                            tier=selection_info.get("tier"),
                            task_type=task_type,
                            latency_ms=latency_ms,
                            success=False,
                            error_type="429",
                        )
                        exclude_models.append(selected_model)
                        continue

                    else:
                        # Other error
                        logger.error(f"Model {selected_model}: HTTP {response.status_code}")
                        error_type = str(response.status_code)
                        scheduler.record_failure(selected_model, error_type)
                        metrics.record_request(
                            model=selected_model,
                            tier=selection_info.get("tier"),
                            task_type=task_type,
                            latency_ms=latency_ms,
                            success=False,
                            error_type=error_type,
                        )
                        exclude_models.append(selected_model)

                        # If 5xx, try next model
                        if 500 <= response.status_code < 600:
                            continue
                        else:
                            # Return error for 4xx (except 429)
                            return JSONResponse(
                                content=response.json(),
                                status_code=response.status_code,
                            )

        except httpx.TimeoutException:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"Model {selected_model}: timeout after {latency_ms:.0f}ms")
            scheduler.record_failure(selected_model, "timeout")
            metrics.record_request(
                model=selected_model,
                tier=selection_info.get("tier"),
                task_type=task_type,
                latency_ms=latency_ms,
                success=False,
                error_type="timeout",
            )
            exclude_models.append(selected_model)
            continue

        except httpx.ConnectError as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"Model {selected_model}: connection error - {e}")
            scheduler.record_failure(selected_model, "connect_error")
            metrics.record_request(
                model=selected_model,
                tier=selection_info.get("tier"),
                task_type=task_type,
                latency_ms=latency_ms,
                success=False,
                error_type="connect_error",
            )
            exclude_models.append(selected_model)
            continue

    # All attempts failed
    raise HTTPException(
        status_code=503,
        detail={
            "error": "All models failed",
            "task_type": task_type,
            "attempts": max_retries,
            "excluded_models": exclude_models,
        },
    )


async def _stream_response(
    model: str,
    tier: str,
    task_type: str,
    api_base: str,
    headers: dict,
    request_data: dict,
    start_time: float,
):
    """Handle streaming response"""
    async def stream_generator():
        success = True
        error_type = None
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{api_base}/chat/completions",
                    headers=headers,
                    json=request_data,
                    timeout=settings.request_timeout,
                ) as response:
                    if response.status_code != 200:
                        success = False
                        error_type = str(response.status_code)
                        # Read error body
                        error_body = await response.aread()
                        yield error_body
                        return

                    async for chunk in response.aiter_bytes():
                        yield chunk

        except Exception as e:
            success = False
            error_type = type(e).__name__
            logger.error(f"Stream error: {e}")
            yield f'data: {json.dumps({"error": str(e)})}\n\n'.encode()

        finally:
            latency_ms = (time.time() - start_time) * 1000
            if success:
                scheduler.record_success(model, latency_ms)
            else:
                scheduler.record_failure(model, error_type or "unknown")

            metrics.record_request(
                model=model,
                tier=tier,
                task_type=task_type,
                latency_ms=latency_ms,
                success=success,
                error_type=error_type,
            )

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
    )


# --- API Routes ---

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Forge",
        "description": "Intelligent AI Gateway for Coding Agents",
        "version": "0.1.0",
        "endpoints": [
            "/v1/chat/completions",
            "/v1/models",
            "/health",
            "/dashboard",
            "/metrics",
            "/providers",
        ],
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    models = scheduler.get_all_models_status()
    healthy_count = sum(1 for m in models if m["status"] == "healthy")
    cooldown_count = sum(1 for m in models if m["in_cooldown"])
    unhealthy_count = sum(1 for m in models if m["status"] == "unhealthy")

    return {
        "status": "healthy" if healthy_count > 0 else "degraded",
        "total_models": len(models),
        "healthy": healthy_count,
        "cooldown": cooldown_count,
        "unhealthy": unhealthy_count,
        "models": models,
    }


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """List available models (OpenAI compatible)"""
    models = []
    created = int(time.time())

    for tier_name, tier_config in TIERS.items():
        for model_id in tier_config.models:
            models.append(
                ModelInfo(
                    id=model_id,
                    created=created,
                    owned_by="forge",
                )
            )

    # Also add the alias
    models.append(
        ModelInfo(
            id="coder",
            created=created,
            owned_by="forge",
        )
    )

    return ModelsResponse(data=models)


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request):
    """Chat completion endpoint (OpenAI compatible)"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate request
    if "messages" not in body:
        raise HTTPException(status_code=400, detail="messages field is required")

    stream = body.get("stream", False)

    return await handle_completion_request(body, stream=stream)


@app.get("/dashboard")
async def dashboard():
    """Dashboard data endpoint"""
    models = scheduler.get_all_models_status()
    cooldown_models = scheduler.get_cooldown_models()
    today_metrics = metrics.get_today_summary()

    # Group models by tier
    tiers = {}
    for tier_name in TIERS:
        tiers[tier_name] = scheduler.get_models_by_tier(tier_name)

    return {
        "providers": [
            {"name": "NVIDIA", "status": "active", "api_base": "https://integrate.api.nvidia.com/v1"},
        ],
        "tiers": tiers,
        "cooldown": cooldown_models,
        "today": today_metrics,
        "total_models": len(models),
    }


@app.get("/metrics")
async def get_metrics(days: int = 7):
    """Get metrics"""
    return metrics.get_all_metrics(days=days)


@app.get("/providers")
async def get_providers():
    """Get provider status"""
    return {
        "providers": [
            {
                "name": "NVIDIA",
                "status": "active",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "models": sum(len(t.models) for t in TIERS.values()),
            },
        ]
    }


@app.post("/admin/reload")
async def reload_config():
    """Reload configuration"""
    # TODO: Implement hot reload
    return {"status": "reload not implemented yet"}


@app.post("/admin/provider")
async def add_provider(request: Request):
    """Add a new provider"""
    body = await request.json()
    # TODO: Implement dynamic provider addition
    return {"status": "not implemented yet", "received": body}


# --- Main Entry Point ---

def main():
    """Run the Forge server"""
    import uvicorn
    uvicorn.run(
        "src.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info" if not settings.debug else "debug",
    )


if __name__ == "__main__":
    main()