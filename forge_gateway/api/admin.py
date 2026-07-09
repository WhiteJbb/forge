"""관리 엔드포인트 — loopback 전용 (DESIGN.md §5.8)

/admin/reload: forge.yaml 재파싱 → 검증 통과 시에만 원자적 교체 (§5.9).
기존 모델의 health 상태는 보존되고, in-flight 요청은 구 참조로 완주한다.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..settings import ConfigError
from .auth import require_loopback
from .openai import Deps


def build_router(deps: Deps, reload_fn) -> APIRouter:
    router = APIRouter(prefix="/admin", dependencies=[Depends(require_loopback)])

    @router.post("/cooldown/{model_id:path}/clear")
    async def clear_cooldown(model_id: str):
        entry = deps.registry.get(model_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown model {model_id!r}")
        entry.health.status = "unknown"
        entry.health.cooldown_until = 0.0
        entry.health.consecutive_failures = 0
        return {"model": model_id, "status": "cooldown cleared"}

    @router.post("/reload")
    async def reload_config():
        try:
            return await reload_fn()
        except ConfigError as e:
            # 검증 실패 — 기존 설정 유지 (§5.9)
            raise HTTPException(status_code=400, detail=f"reload rejected: {e}")

    @router.post("/provider")
    async def add_provider():
        raise HTTPException(
            status_code=501,
            detail="edit forge.yaml and call POST /admin/reload instead",
        )

    return router
