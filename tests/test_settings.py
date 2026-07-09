"""forge.yaml 로더 검증 테스트 (DESIGN.md §5.9, src/settings.py)"""

import tempfile
import unittest
from pathlib import Path

from src.settings import ConfigError, load_config

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


if __name__ == "__main__":
    unittest.main()
