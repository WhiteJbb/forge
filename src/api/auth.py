"""인증 (DESIGN.md §5.8, §8.3)

FORGE_API_KEY 환경변수가 설정된 경우에만 Bearer 검증.
미설정 시 무인증 (로컬 개발 모드). /admin/*은 항상 loopback 한정.
"""

import hmac
from fastapi import HTTPException, Request

from ..settings import AuthConfig


def make_auth_dependency(auth: AuthConfig):
    async def require_api_key(request: Request) -> None:
        expected = auth.api_key
        if not expected:
            return
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() if header else ""
        if not token:
            # Anthropic 관례 (§5.8) — /v1/messages 클라이언트 호환
            token = request.headers.get("x-api-key", "").strip()
        if not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    return require_api_key


async def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="admin endpoints are loopback-only")
