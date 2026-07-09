"""forge.yaml 로더 검증 테스트 (DESIGN.md §5.9, forge_gateway/settings.py)"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_gateway.settings import ConfigError, load_config, load_dotenv

VALID_YAML = """
version: 1
providers:
  - name: nvidia
    api_key_env: NVIDIA_API_KEY
    free: true
models:
  - id: "nvidia:model-a"
    tier: tier1
    capabilities: { code: 9 }
defaults:
  capability: 7
  tier: tier3
  features: [tools, streaming]
"""

# provider가 "nvidia"만 정의되어 있는데 모델은 "openrouter"를 참조 -> ConfigError
UNKNOWN_PROVIDER_YAML = """
version: 1
providers:
  - name: nvidia
    api_key_env: NVIDIA_API_KEY
models:
  - id: "openrouter:some-model"
    tier: tier1
"""

# api_key_env에 실제 키 문자열이 리터럴로 들어감 -> 검증 실패
LITERAL_KEY_YAML = """
version: 1
providers:
  - name: nvidia
    api_key_env: "nvapi-abcdefghijklmnop"
"""

INVALID_YAML_SYNTAX = """
version: 1
providers: [this is not valid yaml: ][
"""


class SettingsLoadTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _write(self, content: str) -> Path:
        path = Path(self._tmpdir.name) / "forge.yaml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_valid_config_loads(self):
        path = self._write(VALID_YAML)
        config = load_config(path)
        self.assertEqual(config.version, 1)
        self.assertEqual(len(config.providers), 1)
        self.assertEqual(config.providers[0].name, "nvidia")
        self.assertEqual(len(config.models), 1)
        self.assertEqual(config.models[0].id, "nvidia:model-a")

    def test_model_referencing_unknown_provider_raises_config_error(self):
        path = self._write(UNKNOWN_PROVIDER_YAML)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("openrouter", str(ctx.exception))

    def test_api_key_env_literal_key_rejected(self):
        path = self._write(LITERAL_KEY_YAML)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        # pydantic 에러 메시지에 검증기 문구가 포함되어 있어야 함
        self.assertIn("environment variable", str(ctx.exception))

    def test_missing_file_raises_config_error(self):
        missing = Path(self._tmpdir.name) / "does-not-exist.yaml"
        with self.assertRaises(ConfigError):
            load_config(missing)

    def test_invalid_yaml_syntax_raises_config_error(self):
        path = self._write(INVALID_YAML_SYNTAX)
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_non_mapping_top_level_raises_config_error(self):
        path = self._write("- just\n- a\n- list\n")
        with self.assertRaises(ConfigError):
            load_config(path)


class DotenvTests(unittest.TestCase):
    """load_dotenv — run_forge.bat 없이도 .env가 잡혀야 한다 (§8.3)"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)

    def _write_env(self, content: str) -> Path:
        path = self.root / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_key_values_with_comments_quotes_export(self):
        path = self._write_env(
            "# 주석\n"
            "FORGE_T1=plain\n"
            'FORGE_T2="quoted value"\n'
            "export FORGE_T3='single'\n"
            "\n"
            "not-a-pair\n"
        )
        with patch.dict(os.environ, {}, clear=False):
            for k in ("FORGE_T1", "FORGE_T2", "FORGE_T3"):
                os.environ.pop(k, None)
            loaded = load_dotenv(path)
            self.assertEqual(loaded, 3)
            self.assertEqual(os.environ["FORGE_T1"], "plain")
            self.assertEqual(os.environ["FORGE_T2"], "quoted value")
            self.assertEqual(os.environ["FORGE_T3"], "single")

    def test_does_not_override_existing_env(self):
        path = self._write_env("FORGE_T4=from_file\n")
        with patch.dict(os.environ, {"FORGE_T4": "from_shell"}):
            loaded = load_dotenv(path)
            self.assertEqual(loaded, 0)
            self.assertEqual(os.environ["FORGE_T4"], "from_shell")

    def test_missing_file_returns_zero(self):
        self.assertEqual(load_dotenv(self.root / "nope.env"), 0)

    def test_load_config_pulls_env_from_config_dir(self):
        (self.root / "forge.yaml").write_text(VALID_YAML, encoding="utf-8")
        self._write_env("FORGE_T5=via_load_config\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_T5", None)
            load_config(self.root / "forge.yaml")
            self.assertEqual(os.environ.get("FORGE_T5"), "via_load_config")


if __name__ == "__main__":
    unittest.main()
