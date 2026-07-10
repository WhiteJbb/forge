"""forge.yaml 로더 검증 테스트 (DESIGN.md §5.9, forge_gateway/settings.py)"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_gateway.settings import (
    PROVIDER_CATALOG,
    ConfigError,
    load_config,
    load_dotenv,
)

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
        # 밀폐: 러너 환경의 실키가 auto_providers로 끼어들지 않게
        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for item in PROVIDER_CATALOG:
            os.environ.pop(item["key_env"], None)

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


class LocalOverlayTests(unittest.TestCase):
    """forge.local.yaml 오버레이 — CLI(forge guard)가 관리하는 기계 전용 파일"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        (self.root / "forge.yaml").write_text(VALID_YAML, encoding="utf-8")
        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for item in PROVIDER_CATALOG:
            os.environ.pop(item["key_env"], None)

    def test_local_policies_prepended(self):
        (self.root / "forge.local.yaml").write_text(
            "policies:\n"
            "  - name: local-guard\n"
            "    constraints: { allow_paid: false }\n",
            encoding="utf-8",
        )
        config = load_config(self.root / "forge.yaml")
        self.assertEqual(config.policies[0].name, "local-guard")
        self.assertFalse(config.policies[0].constraints.allow_paid)

    def test_missing_local_file_is_fine(self):
        config = load_config(self.root / "forge.yaml")
        self.assertEqual(config.policies, [])

    def test_invalid_local_file_raises(self):
        (self.root / "forge.local.yaml").write_text(
            "policies:\n  - name: broken\n", encoding="utf-8")  # route/constraints 없음
        with self.assertRaises(ConfigError):
            load_config(self.root / "forge.yaml")


class AutoProviderTests(unittest.TestCase):
    """카탈로그 기반 provider 자동 등록 (§8.1 — 키만 .env에 넣으면 끝)"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "forge.yaml"
        # 테스트 환경에 남아 있을 수 있는 카탈로그 키 전부 제거
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for item in PROVIDER_CATALOG:
            os.environ.pop(item["key_env"], None)

    def _load(self, yaml_text: str):
        self.path.write_text(yaml_text, encoding="utf-8")
        return load_config(self.path)

    def test_detected_key_registers_provider(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        config = self._load(VALID_YAML)
        names = {p.name for p in config.providers}
        self.assertIn("openrouter", names)
        added = config.provider("openrouter")
        self.assertTrue(added.auto_registered)
        self.assertEqual(added.api_base, "https://openrouter.ai/api/v1")

    def test_free_tier_providers_registered_when_key_present(self):
        """Cerebras/Gemini — recurring 무료 확인됨 (Research.md 2026-07-09) -> free: true"""
        os.environ["CEREBRAS_API_KEY"] = "csk-test"
        os.environ["GEMINI_API_KEY"] = "gm-test"
        config = self._load(VALID_YAML)
        for name in ("cerebras", "gemini"):
            provider = config.provider(name)
            self.assertIsNotNone(provider, f"{name} should auto-register")
            self.assertTrue(provider.free, f"{name} should be marked free")

    def test_zai_registered_without_free_flag(self):
        """Zhipu는 무료·유료 모델이 혼재 -> 프로바이더 전체를 free로 표시하지 않음"""
        os.environ["ZAI_API_KEY"] = "zai-test"
        config = self._load(VALID_YAML)
        provider = config.provider("zai")
        self.assertIsNotNone(provider)
        self.assertFalse(provider.free)

    def test_sambanova_not_marked_free(self):
        """재검증(2026-07-09) 결과 $5 1회성 트라이얼뿐 — recurring 무료 아님, paid 취급"""
        os.environ["SAMBANOVA_API_KEY"] = "sn-test"
        config = self._load(VALID_YAML)
        provider = config.provider("sambanova")
        self.assertIsNotNone(provider)
        self.assertFalse(provider.free)

    def test_capability_seed_applied_as_config_models(self):
        """벤치마크로 시드된 모델(zai-glm-4.7 등)은 provider 미선언 상태에서도 config.models에
        tier/capabilities가 채워져 들어가야 한다(Research.md 2026-07-09 신규 provider 벤치마크 시드)"""
        os.environ["CEREBRAS_API_KEY"] = "csk-test"
        config = self._load(VALID_YAML)
        override = next((m for m in config.models if m.id == "cerebras:zai-glm-4.7"), None)
        self.assertIsNotNone(override)
        self.assertEqual(override.tier, "tier1")
        self.assertEqual(override.capabilities["code"], 9)

    def test_no_capability_seed_when_key_absent(self):
        config = self._load(VALID_YAML)
        self.assertFalse(any(m.id.startswith("cerebras:") for m in config.models))

    def test_explicit_declaration_wins(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        # providers 목록에 이어붙이기 위해 원본의 providers 섹션 뒤에 삽입
        yaml_text = VALID_YAML.replace(
            "models:",
            "  - name: openrouter\n"
            "    api_base: \"https://my-proxy.example/v1\"\n"
            "    api_key_env: OPENROUTER_API_KEY\n"
            "models:",
        )
        config = self._load(yaml_text)
        matches = [p for p in config.providers if p.name == "openrouter"]
        self.assertEqual(len(matches), 1)  # 중복 등록 없음
        self.assertEqual(matches[0].api_base, "https://my-proxy.example/v1")
        self.assertFalse(matches[0].auto_registered)

    def test_opt_out_flag(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        config = self._load(VALID_YAML + "auto_providers: false\n")
        self.assertIsNone(config.provider("openrouter"))

    def test_anthropic_gets_default_models(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        config = self._load(VALID_YAML)
        provider = config.provider("anthropic")
        self.assertIsNotNone(provider)
        self.assertFalse(provider.discovery)  # OpenAI 호환 /models 없음
        anthropic_models = [m.id for m in config.models if m.id.startswith("anthropic:")]
        self.assertGreaterEqual(len(anthropic_models), 3)

    def test_no_keys_no_changes(self):
        config = self._load(VALID_YAML)
        self.assertEqual({p.name for p in config.providers}, {"nvidia"})

    def test_paid_providers_registered_when_key_present(self):
        """x.ai/Cohere/Together/Fireworks — 공식 OpenAI 호환 엔드포인트 패턴 (Research.md 2026-07-10)"""
        os.environ["XAI_API_KEY"] = "xai-test"
        os.environ["COHERE_API_KEY"] = "co-test"
        os.environ["TOGETHER_API_KEY"] = "together-test"
        os.environ["FIREWORKS_API_KEY"] = "fw-test"
        config = self._load(VALID_YAML)
        for name in ("xai", "cohere", "together", "fireworks"):
            provider = config.provider(name)
            self.assertIsNotNone(provider, f"{name} should auto-register")
            self.assertFalse(provider.free, f"{name} should not be marked free")

    def test_cohere_discovery_enabled_without_capability_seed(self):
        """discovery는 실키로 직접 확인됨(200, OpenAI 포맷) - 가격/코딩 벤치마크를
        공식 1차 소스로 확인 못해 capability_seed는 없음, discovery에 전적으로 위임"""
        os.environ["COHERE_API_KEY"] = "co-test"
        config = self._load(VALID_YAML)
        provider = config.provider("cohere")
        self.assertIsNotNone(provider)
        self.assertTrue(provider.discovery)
        self.assertFalse(any(m.id.startswith("cohere:") for m in config.models))

    def test_together_discovery_stays_enabled(self):
        """Together AI는 GET /v1/models가 OpenAI 포맷으로 동작함을 공식 문서로 확인 -> discovery 유지"""
        os.environ["TOGETHER_API_KEY"] = "together-test"
        config = self._load(VALID_YAML)
        provider = config.provider("together")
        self.assertTrue(provider.discovery)

    def test_capability_seed_price_per_mtok_applied(self):
        """capability_seed의 price_per_mtok이 ModelOverride까지 스레딩되어야 한다
        (공식 pricing 페이지 근거 직접 시딩 — litellm 폴백보다 우선, §5.12)"""
        os.environ["XAI_API_KEY"] = "xai-test"
        config = self._load(VALID_YAML)
        override = next((m for m in config.models if m.id == "xai:grok-4.5"), None)
        self.assertIsNotNone(override)
        self.assertEqual(override.tier, "tier1")
        self.assertEqual(override.price_per_mtok, (2.00, 6.00))

    def test_capability_seed_price_only_leaves_tier_unset(self):
        """가격은 공식 확인됐지만 코딩 벤치마크가 없는 모델은 tier/capabilities 없이
        가격만 시딩 — 근거 없는 tier를 지어내지 않는다 (Research.md 2026-07-10)"""
        os.environ["FIREWORKS_API_KEY"] = "fw-test"
        config = self._load(VALID_YAML)
        override = next(
            (m for m in config.models
             if m.id == "fireworks:accounts/fireworks/models/qwen3p7-plus"), None)
        self.assertIsNotNone(override)
        self.assertIsNone(override.tier)
        self.assertEqual(override.price_per_mtok, (0.40, 1.60))


if __name__ == "__main__":
    unittest.main()
