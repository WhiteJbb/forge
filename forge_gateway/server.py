"""Forge 서버 조립 — lifespan과 미들웨어만 (DESIGN.md §4)

컴포넌트 생성과 배선은 전부 여기서. 각 모듈은 서로를 import하지 않고
계약(설정/타입/프로토콜)만 공유한다.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as _pkg_version

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
# 업스트림 에러 메시지는 임의의 유니코드를 담을 수 있다 - Windows 콘솔(cp949/ascii 등
# 좁은 코드페이지)에서 로그에 그 문자가 그대로 찍히면 UnicodeEncodeError로 로그 자체가
# 죽어 진짜 에러 내용이 가려진다. 인코딩 실패는 예외 대신 이스케이프로 대체.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")
logger = logging.getLogger("forge")


def create_app(config_path: str = "forge.yaml") -> FastAPI:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        # 설정 오류는 부팅 중단 — 명확한 메시지로 (§5.9)
        raise SystemExit(f"forge: {e}") from e

    # loopback 밖으로 바인딩하면서 인증 미설정이면 누구나 이 게이트웨이로 provider API
    # 키를 소모할 수 있다 (§8.3) — 차단하지 않고 경고만 남긴다(로컬 개발 편의 유지).
    if config.server.host not in ("127.0.0.1", "::1", "localhost") and not config.auth.api_key:
        logger.warning(
            "server.host=%s는 loopback이 아닌데 FORGE_API_KEY가 설정되지 않았습니다 — "
            "누구나 이 주소로 접근해 등록된 provider API 키를 소모할 수 있습니다. "
            ".env에 FORGE_API_KEY를 설정하세요.", config.server.host)

    registry = Registry(config)
    fill_registry_prices(registry, config)  # litellm 가격표 폴백 (§5.12)

    # 유료 프로바이더가 키 감지로 조용히 편입되면 지출 인지 없이 과금될 수 있다 (§5.12)
    paid_auto = [p.name for p in config.providers if p.auto_registered and not p.free]
    if paid_auto:
        has_guard = any(r.constraints is not None for r in config.policies)
        logger.warning(
            "유료 프로바이더 자동 등록됨: %s — 지출 제한이 필요하면 "
            "`forge guard --no-paid` 또는 `forge guard --max-cost <USD>` 실행%s",
            ", ".join(paid_auto),
            "" if has_guard else " (현재 지출 제한 정책 없음)")
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

    try:
        forge_version = _pkg_version("forge-gateway")  # pyproject 단일 소스 (리뷰: 버전 3곳 불일치)
    except PackageNotFoundError:
        forge_version = "0.0.0-dev"

    app = FastAPI(
        title="Forge",
        description="Intelligent AI Gateway for Coding Agents",
        version=forge_version,
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
        # 알려진 한계: chunked 요청(Content-Length 없음)은 이 검사를 우회한다 —
        # loopback 기본 바인딩 전제라 수용, 외부 바인딩 시 리버스 프록시에서 제한 권장
        length = request.headers.get("content-length")
        if length:
            try:
                too_big = int(length) > max_body
            except ValueError:
                return JSONResponse(status_code=400, content={"error": {
                    "message": "invalid Content-Length header",
                    "type": "invalid_request_error", "code": 400}})
            if too_big:
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
    background_tasks: set = set()  # create_task 결과의 강한 참조 (GC 방지, 리뷰 #8)

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
            # 기존 health(쿨다운·레이턴시·윈도) 이관 — 리로드가 낙인/학습을 지우면 안 됨.
            # ewma_alpha는 설정이 바뀌었을 수 있으므로 새 값으로 갱신 (리뷰 #12)
            for entry in new_registry.all():
                old_entry = deps.registry.get(entry.id)
                if old_entry is not None:
                    entry.health = old_entry.health
                    entry.health._alpha = new_config.scheduler.latency_ewma_alpha
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

            # 강한 참조 유지 — asyncio task는 약참조라 GC로 사라질 수 있다 (리뷰 #8)
            task = asyncio.create_task(_delayed_close())
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
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
    app.include_router(observe_api.build_router(deps, runtime, require_key))
    # /admin/*: loopback 제한(라우터 내부) + API 키 이중 검증 (§5.8 계약, 리뷰 #5)
    app.include_router(admin_api.build_router(deps, reload_config_fn),
                       dependencies=[Depends(require_key)])

    @app.get("/")
    async def root():
        return {
            "name": "Forge",
            "description": "Intelligent AI Gateway for Coding Agents",
            "version": app.version,
            "endpoints": ["/v1/chat/completions", "/v1/embeddings", "/v1/models",
                          "/health", "/v1/stats", "/dashboard"],
        }

    app.state.forge_config = config
    return app


# 주의: 모듈 레벨에서 create_app()을 실행하지 않는다 — import 부작용(설정/.env 로드,
# 설정 없으면 SystemExit)이 테스트와 도구를 오염시킨다. 진입점은 main()/CLI(forge start).


# 박스문자 배너(LiteLLM 스타일). Windows에서 stdout이 실제 인터랙티브 콘솔에 붙어있으면
# (isatty=True) Python 3.6+가 코드페이지(cp949 등)를 무시하고 콘솔 API로 유니코드를
# 직접 그리므로 정상 출력된다(PEP 528). 다만 로그 리다이렉트(`forge start > forge.log`)나
# 백그라운드 서비스처럼 stdout이 파이프로 캡처되는 경우 isatty=False가 되어 레거시
# 코드페이지로 폴백하면서 UnicodeEncodeError로 부팅이 죽을 수 있다 — 그런 경우에만
# 아래 ASCII 버전으로 조용히 대체한다(print_banner의 try/except).
_BANNER_ART_UNICODE = r"""
  ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
  ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
  █████╗  ██║   ██║██████╔╝██║  ███╗█████╗
  ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
  ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
  ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
"""

# 순수 ASCII 폴백 — 위 유니코드 배너가 인코딩 실패할 때만 쓴다.
_BANNER_ART_ASCII = r"""
  ##########     ######     ########       ########   ##########
  ##########     ######     ########       ########   ##########
  ##           ##      ##   ##      ##   ##           ##
  ##           ##      ##   ##      ##   ##           ##
  ######       ##      ##   ########     ##    ####   ########
  ######       ##      ##   ########     ##    ####   ########
  ##           ##      ##   ##    ##     ##      ##   ##
  ##           ##      ##   ##    ##     ##      ##   ##
  ##             ######     ##      ##     ########   ##########
  ##             ######     ##      ##     ########   ##########
"""


def _print_art() -> None:
    try:
        print(_BANNER_ART_UNICODE)
    except UnicodeEncodeError:
        print(_BANNER_ART_ASCII)


def print_banner(config) -> None:
    """기동 직후 ASCII 아트 + '그래서 뭘 하면 되는지' 안내 (U2 — CLI start와 main 공용)"""
    base = f"http://{config.server.host}:{config.server.port}"
    rule = "-" * 58
    _print_art()
    print(rule)
    print(f"""
  Dashboard      {base}/dashboard/ui
  Health         {base}/health

  Connect a coding agent (model: "auto"):
    Cline/Continue/Aider   base URL  {base}/v1
    Claude Code            ANTHROPIC_BASE_URL={base}

  Spend guard:   forge guard --no-paid   |   forge guard --max-cost 0.05
""")
    print(rule)


def main():
    import uvicorn

    app = create_app()
    config = app.state.forge_config
    print_banner(config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="debug" if config.server.debug else "info",
    )


if __name__ == "__main__":
    main()
