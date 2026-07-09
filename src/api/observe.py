"""관측 엔드포인트 — /health, /v1/stats, /dashboard (DESIGN.md §5.8)

Deps를 통해 참조를 읽으므로 /admin/reload의 원자적 교체(§5.9) 이후에도
항상 최신 Registry/설정을 본다. /metrics는 M3에서 Prometheus 포맷으로 전환 예정.
"""

from fastapi import APIRouter

from .openai import Deps


def build_router(deps: Deps) -> APIRouter:
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
    @router.get("/metrics")  # 하위 호환 — M3에서 Prometheus 포맷으로 교체 (§5.7)
    async def stats(days: int = 7):
        return await deps.metrics.range_summary(days)

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
