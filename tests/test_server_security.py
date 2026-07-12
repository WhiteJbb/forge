"""보안 하드닝 회귀 테스트 — API 키 마스킹 일관성 + 외부 바인딩 무인증 경고 (ReviewChecklist 보안 섹션)"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from forge_gateway.providers.base import (
    _REGISTERED_SECRETS,
    mask_secrets,
    register_secrets,
)
from forge_gateway.server import create_app

CONFIG_TEXT = """version: 1
auto_providers: false
server:
  host: {host}
providers:
  - name: dummy
    litellm_prefix: openai
    api_base: http://127.0.0.1:1/v1
    api_key_env: DUMMY_KEY
    discovery: false
    free: true
models:
  - id: "dummy:model-a"
    tier: tier1
defaults:
  capability: 7
  tier: tier3
  features: [tools, streaming]
metrics:
  db_path: "{db_path}"
"""


class MaskSecretsTests(unittest.TestCase):
    def test_masks_known_key_prefixes(self):
        for raw in (
            "nvapi-abcdefghijklmnop",
            "sk-or-abcdefghijklmnop",
            "sk-ant-abcdefghijklmnop",
            "gsk_abcdefghijklmnop",
            "AIzaabcdefghijklmnop",
        ):
            masked = mask_secrets(f"error calling upstream with key {raw} in header")
            self.assertNotIn(raw, masked)

    def test_leaves_unrelated_text_untouched(self):
        text = "connection refused to 127.0.0.1:1"
        self.assertEqual(mask_secrets(text), text)

    def test_masks_extended_key_prefixes(self):
        for raw in (
            "csk-abcdefghijklmnop",   # Cerebras
            "xai-abcdefghijklmnop",   # x.ai
            "fw_abcdefghijklmnop",    # Fireworks
        ):
            masked = mask_secrets(f"error calling upstream with key {raw} in header")
            self.assertNotIn(raw, masked)


class RegisteredSecretMaskingTests(unittest.TestCase):
    """접두어가 없는 provider 키는 등록 값 정확 일치로 마스킹한다 (§8.3)."""

    def test_prefixless_key_masked_only_after_registration(self):
        # Together류: 안정적 공개 접두어 없는 40자 랜덤 키
        key = "0f3b9c2d7a184e56b0c9f2a1d8e47c3b6a5f9012"
        # 등록 전에는 접두어 매칭도 안 되므로 원문 그대로여야 한다
        self.assertIn(key, mask_secrets(f"401 token={key}"))
        register_secrets([key])
        self.addCleanup(_REGISTERED_SECRETS.discard, key)
        self.assertNotIn(key, mask_secrets(f"401 token={key}"))

    def test_short_value_is_not_registered(self):
        short = "abc12"  # 5자 < 8 → 오마스킹 방지 위해 무시
        register_secrets([short])
        self.addCleanup(_REGISTERED_SECRETS.discard, short)
        self.assertNotIn(short, _REGISTERED_SECRETS)
        self.assertEqual(mask_secrets(f"value {short} here"), f"value {short} here")

    def test_substring_related_keys_both_masked(self):
        short = "SUBSTRKEY-uniquebase-000000"
        longer = short + "-EXTRATAIL-99999"
        register_secrets([short, longer])
        self.addCleanup(_REGISTERED_SECRETS.discard, short)
        self.addCleanup(_REGISTERED_SECRETS.discard, longer)
        masked = mask_secrets(f"k1={short} k2={longer}")
        self.assertNotIn(short, masked)
        self.assertNotIn(longer, masked)


class CorsLockdownTests(unittest.TestCase):
    """CORS 기본 잠금 — 기본은 미들웨어 없음, 명시 오리진만 허용 (§8.3)."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("FORGE_API_KEY", None)
        os.environ.pop("DUMMY_KEY", None)

    def _make_app(self, cors_line: str = ""):
        db_path = (Path(self._tmpdir) / "m.db").as_posix()
        cfg_path = Path(self._tmpdir) / "forge.yaml"
        body = CONFIG_TEXT.format(host="127.0.0.1", db_path=db_path)
        if cors_line:
            body = body.replace(
                "server:\n  host: 127.0.0.1\n",
                f"server:\n  host: 127.0.0.1\n  cors_origins: {cors_line}\n",
            )
        cfg_path.write_text(body, encoding="utf-8")
        # lifespan 없이 미들웨어 배선만 검증 — TestClient를 with 없이 사용
        return TestClient(create_app(str(cfg_path)))

    def test_no_cors_header_by_default(self):
        client = self._make_app()
        resp = client.get("/", headers={"Origin": "http://evil.example"})
        self.assertNotIn("access-control-allow-origin", resp.headers)

    def test_configured_origin_allowed_with_credentials(self):
        client = self._make_app('["http://localhost:3000"]')
        resp = client.get("/", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"), "http://localhost:3000")
        self.assertEqual(resp.headers.get("access-control-allow-credentials"), "true")

    def test_wildcard_origin_without_credentials(self):
        client = self._make_app('["*"]')
        resp = client.get("/", headers={"Origin": "http://anything.example"})
        # 와일드카드는 반사하지 않고 "*"를 그대로 반환, credentials는 비활성
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")
        self.assertNotEqual(resp.headers.get("access-control-allow-credentials"), "true")


class NonLoopbackAuthWarningTests(unittest.TestCase):
    """server.host가 loopback이 아닌데 FORGE_API_KEY가 없으면 경고해야 한다 (§8.3)"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("FORGE_API_KEY", None)
        os.environ.pop("DUMMY_KEY", None)

    def _make_app(self, host: str):
        db_path = (Path(self._tmpdir) / "m.db").as_posix()
        cfg_path = Path(self._tmpdir) / "forge.yaml"
        cfg_path.write_text(CONFIG_TEXT.format(host=host, db_path=db_path), encoding="utf-8")
        return create_app(str(cfg_path))

    def test_warns_when_non_loopback_without_api_key(self):
        with self.assertLogs("forge", level="WARNING") as ctx:
            self._make_app("0.0.0.0")
        self.assertTrue(any("FORGE_API_KEY" in msg for msg in ctx.output))

    def test_no_warning_when_loopback(self):
        with self.assertNoLogs("forge", level="WARNING"):
            self._make_app("127.0.0.1")

    def test_no_warning_when_api_key_set(self):
        os.environ["FORGE_API_KEY"] = "secret-value"
        with self.assertNoLogs("forge", level="WARNING"):
            self._make_app("0.0.0.0")
