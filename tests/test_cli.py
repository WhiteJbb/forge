"""forge CLI 테스트 — init/doctor/models의 순수 로직 (네트워크 없이) (DESIGN.md §8.1)

doctor의 네트워크 단계(list_models)는 src.cli._list_provider_models를 모킹해 검증한다.
start는 서버 기동을 요구하므로 이 파일에서 다루지 않는다.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from src import cli
from src.settings import load_config

_PROVIDER_ENV_KEYS = ("NVIDIA_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "FORGE_API_KEY")


class _EnvIsolatedTestCase(unittest.TestCase):
    """provider API 키 환경변수와 임시 config 경로를 테스트마다 격리한다."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in _PROVIDER_ENV_KEYS:
            os.environ.pop(key, None)

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config_path = Path(self._tmpdir.name) / "forge.yaml"

    def _parse(self, argv):
        return cli.build_parser().parse_args(argv)


class InitCommandTests(_EnvIsolatedTestCase):
    def test_no_keys_creates_valid_config_with_commented_provider(self):
        args = self._parse(["init", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_init(args)

        self.assertEqual(code, 0)
        self.assertTrue(self.config_path.exists())
        config = load_config(self.config_path)
        self.assertEqual(config.providers, [])
        self.assertIn("no provider api key detected", out.getvalue().lower())

    def test_detects_single_key(self):
        os.environ["NVIDIA_API_KEY"] = "dummy"
        args = self._parse(["init", "--config", str(self.config_path)])
        code = cli.cmd_init(args)

        self.assertEqual(code, 0)
        config = load_config(self.config_path)
        self.assertEqual(len(config.providers), 1)
        self.assertEqual(config.providers[0].name, "nvidia")
        self.assertEqual(config.providers[0].api_base, "https://integrate.api.nvidia.com/v1")
        self.assertEqual(config.providers[0].api_key_env, "NVIDIA_API_KEY")

    def test_detects_multiple_keys_including_anthropic_without_api_base(self):
        os.environ["NVIDIA_API_KEY"] = "dummy"
        os.environ["ANTHROPIC_API_KEY"] = "dummy2"
        args = self._parse(["init", "--config", str(self.config_path)])
        code = cli.cmd_init(args)

        self.assertEqual(code, 0)
        config = load_config(self.config_path)
        names = {p.name for p in config.providers}
        self.assertEqual(names, {"nvidia", "anthropic"})
        anthropic = config.provider("anthropic")
        self.assertEqual(anthropic.litellm_prefix, "anthropic")
        self.assertIsNone(anthropic.api_base)

    def test_refuses_existing_file_without_force(self):
        self.config_path.write_text("version: 1\n", encoding="utf-8")
        args = self._parse(["init", "--config", str(self.config_path)])
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_init(args)

        self.assertEqual(code, 1)
        self.assertIn("already exists", err.getvalue())
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), "version: 1\n")

    def test_force_overwrites_existing_file(self):
        self.config_path.write_text("version: 1\n", encoding="utf-8")
        os.environ["OPENROUTER_API_KEY"] = "dummy"
        args = self._parse(["init", "--config", str(self.config_path), "--force"])
        code = cli.cmd_init(args)

        self.assertEqual(code, 0)
        config = load_config(self.config_path)
        self.assertEqual(config.providers[0].name, "openrouter")
        self.assertEqual(config.providers[0].api_base, "https://openrouter.ai/api/v1")


class DoctorCommandTests(_EnvIsolatedTestCase):
    def _write_nvidia_config(self):
        self.config_path.write_text(
            "version: 1\n"
            "providers:\n"
            "  - name: nvidia\n"
            "    api_key_env: NVIDIA_API_KEY\n",
            encoding="utf-8",
        )

    def test_missing_config_file_exits_1_without_traceback(self):
        args = self._parse(["doctor", "--config", str(self.config_path)])
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_doctor(args)

        self.assertEqual(code, 1)
        self.assertIn("config file not found", err.getvalue())

    def test_missing_api_key_is_flagged_as_failure(self):
        self._write_nvidia_config()
        args = self._parse(["doctor", "--config", str(self.config_path)])

        with mock.patch.object(cli, "_list_provider_models", return_value=(True, 5, None)):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.cmd_doctor(args)

        self.assertEqual(code, 1)
        self.assertIn("FAIL", out.getvalue())

    def test_all_ok_when_key_present_and_list_models_succeeds(self):
        self._write_nvidia_config()
        os.environ["NVIDIA_API_KEY"] = "dummy"
        args = self._parse(["doctor", "--config", str(self.config_path)])

        with mock.patch.object(cli, "_list_provider_models", return_value=(True, 5, None)):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.cmd_doctor(args)

        self.assertEqual(code, 0)
        self.assertIn("Overall: OK", out.getvalue())

    def test_list_models_failure_is_reported_without_traceback(self):
        self._write_nvidia_config()
        os.environ["NVIDIA_API_KEY"] = "dummy"
        args = self._parse(["doctor", "--config", str(self.config_path)])

        with mock.patch.object(
            cli, "_list_provider_models",
            return_value=(False, 0, "UpstreamConnectionError: refused"),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.cmd_doctor(args)

        text = out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("UpstreamConnectionError", text)
        self.assertNotIn("Traceback", text)

    def test_no_providers_configured_is_not_a_failure(self):
        self.config_path.write_text("version: 1\n", encoding="utf-8")
        args = self._parse(["doctor", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_doctor(args)

        self.assertEqual(code, 0)
        self.assertIn("No providers configured", out.getvalue())


class ModelsCommandTests(_EnvIsolatedTestCase):
    def _write_two_tier_config(self):
        self.config_path.write_text(
            "version: 1\n"
            "providers:\n"
            "  - name: nvidia\n"
            "    free: true\n"
            "models:\n"
            '  - id: "nvidia:model-a"\n'
            "    tier: tier1\n"
            '  - id: "nvidia:model-b"\n'
            "    tier: tier2\n",
            encoding="utf-8",
        )

    def test_lists_config_model_ids_and_health_hint(self):
        self._write_two_tier_config()
        args = self._parse(["models", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_models(args)

        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("nvidia:model-a", text)
        self.assertIn("nvidia:model-b", text)
        self.assertIn("/health", text)

    def test_tier_filter_excludes_other_tiers(self):
        self._write_two_tier_config()
        args = self._parse(["models", "--config", str(self.config_path), "--tier", "tier1"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_models(args)

        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("nvidia:model-a", text)
        self.assertNotIn("nvidia:model-b", text)

    def test_missing_config_exits_1(self):
        args = self._parse(["models", "--config", str(self.config_path)])
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_models(args)

        self.assertEqual(code, 1)
        self.assertIn("config file not found", err.getvalue())


if __name__ == "__main__":
    unittest.main()
