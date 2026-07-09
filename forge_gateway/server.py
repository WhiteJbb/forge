"""Forge 서버 조립 — lifespan과 미들웨어만 (DESIGN.md §4)

컴포넌트 생성과 배선은 전부 여기서. 각 모듈은 서로를 import하지 않고
계약(설정/타입/프로토콜)만 공유한다.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import admin as admin_api
from .api import anthropic as anthropic_api
from .api import observe as observe_api
from .api import openai as openai_api
from .api.auth import make_auth_dependency
from .core.analyzer import RequestAnalyzer
from .core.health import HealthMonitor
from .core.metrics import MetricsEngine
from .core.policy import PolicyEngine
from .core.pricing import fill_registry_prices
from .core.prom import PromExporter
from .core.registry import Registry
from .core.scheduler import Scheduler
from .core.throttle import ProviderThrottle
from .core.tuner import CapabilityTuner
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
    fill_registry_prices(registry, config)  # litellm 가격표 폴백 (§5.12)
    providers = {p.name: make_provider(p, config.timeouts) for p in config.providers}
    scheduler = Scheduler(config, registry)
    analyzer = RequestAnalyzer()
    metrics = MetricsEngine(config.metrics)
    health = HealthMonitor(registry, providers, config.health)
    policy = PolicyEngine(config, registry)
    throttle = ProviderThrottle(config.providers)
    tuner = CapabilityTuner(registry, metrics, config.tuner)
    prom = PromExporter(registry, throttle)
    metrics.on_record = prom.on_record  # 요청 경로에서 격리 호출됨

    # reload가 교체하는 참조 — lifespan 종료/라우트가 항상 최신 인스턴스를 보게
    runtime = {"monitor": health, "tuner": tuner, "prom": prom}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Forge starting on %s:%s (%d models, %d providers)",
                    config.server.host, config.server.port,
                    len(registry.all()), len(providers))
        metrics.start()  # 동기 — 스키마 초기화 포함 (1회성)
        try:
            discovered = await health.discover()
            for name, added in discovered.items():
                if added:
                    logger.info("discovered %d new models from %s", len(added), name)
            fill_registry_prices(registry, config)  # 신규 발견 모델의 가격도 채움
        except Exception as e:
            logger.warning("auto discovery failed: %s", e)
        await health.warmup()  # 콜드 스타트: tier1 워밍업 (§5.13)
        await health.start()   # 워밍업 후 기동 — 첫 사이클이 tier1을 중복 probe하지 않게
        await tuner.start()    # capability 학습 루프 (§5.11-3)

        yield

        # graceful shutdown: 신규 거부는 uvicorn이, drain 후 flush는 우리가 (§5.13)
        logger.info("Forge shutting down...")
        await runtime["tuner"].stop()
        await runtime["monitor"].stop()
        await metrics.stop()  # 큐 flush 포함
        for provider in deps.providers.values():
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
        policy=policy, throttle=throttle,
    )

    reload_lock = asyncio.Lock()

    async def reload_config_fn() -> dict:
        """forge.yaml 핫 리로드 — 검증 통과 시에만 원자적 교체 (§5.9).

        health 상태는 이관하고, in-flight 요청은 구 provider로 완주(지연 close).
        server/auth 항목 변경은 재시작이 필요하다.
        """
        async with reload_lock:
            new_config = load_config(config_path)  # ConfigError는 admin에서 400 처리

            new_registry = Registry(new_config)
            # discovery로 등록됐던 모델을 재등록해 리로드로 사라지지 않게 유지
            for old_entry in deps.registry.all():
                if (new_registry.get(old_entry.id) is None
                        and old_entry.source == "discovered"
                        and new_config.provider(old_entry.provider) is not None):
                    new_registry.merge_discovered(
                        old_entry.provider, [old_entry.provider_model_id])
            # 기존 health(쿨다운·레이턴시·윈도) 이관 — 리로드가 낙인/학습을 지우면 안 됨
            for entry in new_registry.all():
                old_entry = deps.registry.get(entry.id)
                if old_entry is not None:
                    entry.health = old_entry.health
            fill_registry_prices(new_registry, new_config)

            new_providers = {p.name: make_provider(p, new_config.timeouts)
                             for p in new_config.providers}
            new_health = HealthMonitor(new_registry, new_providers, new_config.health)

            await runtime["tuner"].stop()
            await runtime["monitor"].stop()
            old_providers = dict(deps.providers)

            # 원자적 교체 — 이후 요청은 전부 새 참조를 본다
            deps.config = new_config
            deps.registry = new_registry
            deps.scheduler = Scheduler(new_config, new_registry)
            deps.policy = PolicyEngine(new_config, new_registry)
            deps.throttle = ProviderThrottle(new_config.providers)
            deps.providers = new_providers

            new_throttle = deps.throttle  # 위에서 이미 교체됨
            new_prom = PromExporter(new_registry, new_throttle)
            metrics.on_record = new_prom.on_record
            new_tuner = CapabilityTuner(new_registry, metrics, new_config.tuner)

            runtime["monitor"] = new_health
            runtime["prom"] = new_prom
            runtime["tuner"] = new_tuner
            await new_health.start()
            await new_tuner.start()
            discovered = await new_health.discover()
            fill_registry_prices(new_registry, new_config)

            async def _delayed_close():
                await asyncio.sleep(60)  # in-flight 요청이 구 provider로 완주할 여유
                for p in old_providers.values():
                    try:
                        await p.close()
                    except Exception:
                        pass

            asyncio.create_task(_delayed_close())
            logger.info("config reloaded — %d models, %d providers",
                        len(new_registry.all()), len(new_providers))
            return {
                "status": "reloaded",
                "models": len(new_registry.all()),
                "providers": sorted(new_providers),
                "discovered": {k: len(v) for k, v in discovered.items()},
                "note": "server/auth section changes require a restart",
            }

    app.include_router(openai_api.build_router(deps), dependencies=[Depends(require_key)])
    app.include_router(anthropic_api.build_router(deps), dependencies=[Depends(require_key)])
    app.include_router(observe_api.build_router(deps, runtime))
    app.include_router(admin_api.build_router(deps, reload_config_fn))

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
        "forge_gateway.server:app",
        host=config.server.host,
        port=config.server.port,
        log_level="debug" if config.server.debug else "info",
    )


if __name__ == "__main__":
    main()
