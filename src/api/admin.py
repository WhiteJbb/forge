"""관리 엔드포인트 — loopback 전용 (DESIGN.md §5.8)

/admin/reload(핫 리로드)와 /admin/provider(런타임 추가)는 M2 범위 —
M1에서는 쿨다운 수동 해제만 제공한다 (운영 편의).
"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.registry import Registry
from .auth import require_loopback


def build_router(registry: Registry) -> APIRouter:
    router = APIRouter(prefix="/admin", dependencies=[Depends(require_loopback)])

    @router.post("/cooldown/{model_id:path}/clear")
    async def clear_cooldown(model_id: str):
        entry = registry.get(model_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown model {model_id!r}")
        entry.health.status = "unknown"
        entry.health.cooldown_until = 0.0
        entry.health.consecutive_failures = 0
        return {"model": model_id, "status": "cooldown cleared"}

    @router.post("/reload")
    async def reload_config():
        raise HTTPException(status_code=501, detail="hot reload lands in M2")

    @router.post("/provider")
    async def add_provider():
        raise HTTPException(status_code=501, detail="runtime provider add lands in M2")

    return router
