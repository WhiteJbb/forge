"""보안 하드닝 회귀 테스트 — API 키 마스킹 일관성 + 외부 바인딩 무인증 경고 (ReviewChecklist 보안 섹션)"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_gateway.providers.base import mask_secrets
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
