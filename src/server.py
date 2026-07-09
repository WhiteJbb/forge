"""Forge 서버 조립 — lifespan과 미들웨어만 (DESIGN.md §4)

컴포넌트 생성과 배선은 전부 여기서. 각 모듈은 서로를 import하지 않고
계약(설정/타입/프로토콜)만 공유한다.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import admin as admin_api
from .api import observe as observe_api
from .api import openai as openai_api
from .api.auth import make_auth_dependency
from .core.analyzer import RequestAnalyzer
from .core.health import HealthMonitor
from .core.metrics import MetricsEngine
from .core.registry import Registry
from .core.scheduler import Scheduler
from .providers.base import make_provider
from .settings import ConfigError, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("forge")


def create_app(config_path: str = "forge.yaml") -> FastAPI:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        # 설정 오류는 부팅 중단 — 명확한 메시지로 (§5.9)
        raise SystemExit(f"forge: {e}") from e

    registry = Registry(config)
    providers = {p.name: make_provider(p, config.timeouts) for p in config.providers}
    scheduler = Scheduler(config, registry)
    analyzer = RequestAnalyzer()
    metrics = MetricsEngine(config.metrics)
    health = HealthMonitor(registry, providers, config.health)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Forge starting on %s:%s (%d models, %d providers)",
                    config.server.host, config.server.port,
                    len(registry.all()), len(providers))
        metrics.start()  # 동기 — 스키마 초기화 포함 (1회성)
        await health.start()
        try:
            discovered = await health.discover()
            for name, added in discovered.items():
                if added:
                    logger.info("discovered %d new models from %s", len(added), name)
        except Exception as e:
            logger.warning("auto discovery failed: %s", e)
        await health.warmup()  # 콜드 스타트: tier1 워밍업 (§5.13)

        yield

        # graceful shutdown: 신규 거부는 uvicorn이, drain 후 flush는 우리가 (§5.13)
        logger.info("Forge shutting down...")
        await health.stop()
        await metrics.stop()  # 큐 flush 포함
        for provider in providers.values():
            await provider.close()
        logger.info("Forge stopped")

    app = FastAPI(
        title="Forge",
        description="Intelligent AI Gateway for Coding Agents",
        version="0.2.0-dev",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    max_body = config.server.max_body_mb * 1024 * 1024

    @app.middleware("http")
    async def body_size_limit(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and int(length) > max_body:
            return JSONResponse(status_code=413, content={"error": {
                "message": f"request body exceeds {config.server.max_body_mb}MB",
                "type": "payload_too_large", "code": 413}})
        return await call_next(request)

    require_key = make_auth_dependency(config.auth)
    deps = openai_api.Deps(
        config=config, registry=registry, scheduler=scheduler,
        analyzer=analyzer, metrics=metrics, providers=providers,
    )

    app.include_router(openai_api.build_router(deps), dependencies=[Depends(require_key)])
    app.include_router(observe_api.build_router(config, registry, metrics))
    app.include_router(admin_api.build_router(registry))

    @app.get("/")
    async def root():
        return {
            "name": "Forge",
            "description": "Intelligent AI Gateway for Coding Agents",
            "version": app.version,
            "endpoints": ["/v1/chat/completions", "/v1/embeddings", "/v1/models",
                          "/health", "/v1/stats", "/dashboard"],
        }

    # main()에서 uvicorn에 넘길 수 있게 보관
    app.state.forge_config = config
    return app


app = create_app()


def main():
    import uvicorn

    config = app.state.forge_config
    uvicorn.run(
        "src.server:app",
        host=config.server.host,
        port=config.server.port,
        log_level="debug" if config.server.debug else "info",
    )


if __name__ == "__main__":
    main()
