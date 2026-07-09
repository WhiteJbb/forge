"""관측 엔드포인트 — /health, /v1/stats, /metrics(Prometheus), /dashboard (DESIGN.md §5.8)

Deps/runtime을 통해 참조를 읽으므로 /admin/reload의 원자적 교체(§5.9) 이후에도
항상 최신 Registry/Exporter를 본다.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from .openai import Deps

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def build_router(deps: Deps, runtime: dict) -> APIRouter:
    """runtime: {"prom": PromExporter, ...} — reload가 항목을 교체한다."""
    router = APIRouter()

    @router.get("/health")
    async def health():
        models = deps.registry.all()
        healthy = sum(1 for m in models if m.health.status == "healthy")
        cooldown = sum(1 for m in models if m.health.status == "cooldown")
        unhealthy = sum(1 for m in models if m.health.status == "unhealthy")
        return {
            "status": "healthy" if healthy > 0 or (healthy + unhealthy == 0) else "degraded",
            "total_models": len(models),
            "healthy": healthy,
            "cooldown": cooldown,
            "unhealthy": unhealthy,
            "models": [m.to_dict() for m in models],
        }

    @router.get("/v1/stats")
    async def stats(days: int = 7):
        return await deps.metrics.range_summary(days)

    @router.get("/metrics")
    async def prometheus_metrics():
        """Prometheus 표준 관례 포맷 (§5.7). JSON 통계는 /v1/stats."""
        exporter = runtime.get("prom")
        if exporter is None:
            return Response(status_code=503, content=b"exporter not ready")
        payload, content_type = exporter.render()
        return Response(content=payload, media_type=content_type)

    @router.get("/dashboard/ui")
    async def dashboard_ui():
        """내장 정적 대시보드 SPA (§5.10)"""
        return FileResponse(_STATIC_DIR / "dashboard.html", media_type="text/html")

    @router.get("/dashboard")
    async def dashboard():
        registry = deps.registry
        tiers = {t: [m.to_dict() for m in registry.by_tier(t)]
                 for t in ("tier1", "tier2", "tier3")}
        return {
            "providers": [
                {"name": p.name, "api_base": p.api_base,
                 "models": sum(1 for m in registry.all() if m.provider == p.name)}
                for p in deps.config.providers
            ],
            "tiers": tiers,
            "cooldown": [m.to_dict() for m in registry.in_cooldown()],
            "throttle": deps.throttle.snapshot() if deps.throttle else {},
            "policies": [p.name for p in deps.config.policies],
            "today": await deps.metrics.today_summary(),
            "total_models": len(registry.all()),
        }

    return router
