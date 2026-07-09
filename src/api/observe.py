"""관측 엔드포인트 — /health, /v1/stats, /dashboard (DESIGN.md §5.8)

/metrics는 M3에서 Prometheus 포맷으로 전환 예정. JSON 통계는 /v1/stats.
"""

from fastapi import APIRouter

from ..core.metrics import MetricsEngine
from ..core.registry import Registry
from ..settings import ForgeConfig


def build_router(config: ForgeConfig, registry: Registry, metrics: MetricsEngine) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        models = registry.all()
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
    @router.get("/metrics")  # 하위 호환 — M3에서 Prometheus 포맷으로 교체 (§5.7)
    async def stats(days: int = 7):
        return await metrics.range_summary(days)

    @router.get("/dashboard")
    async def dashboard():
        tiers = {t: [m.to_dict() for m in registry.by_tier(t)]
                 for t in ("tier1", "tier2", "tier3")}
        return {
            "providers": [
                {"name": p.name, "api_base": p.api_base,
                 "models": sum(1 for m in registry.all() if m.provider == p.name)}
                for p in config.providers
            ],
            "tiers": tiers,
            "cooldown": [m.to_dict() for m in registry.in_cooldown()],
            "today": await metrics.today_summary(),
            "total_models": len(registry.all()),
        }

    return router
