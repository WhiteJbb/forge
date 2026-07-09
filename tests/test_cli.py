"""forge CLI 테스트 — init/doctor/models의 순수 로직 (네트워크 없이) (DESIGN.md §8.1)

doctor의 네트워크 단계(list_models)는 forge_gateway.cli._list_provider_models를 모킹해 검증한다.
start는 서버 기동을 요구하므로 이 파일에서 다루지 않는다.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from forge_gateway import cli
from forge_gateway.settings import load_config

from forge_gateway.settings import PROVIDER_CATALOG

_PROVIDER_ENV_KEYS = tuple(item["key_env"] for item in PROVIDER_CATALOG) + ("FORGE_API_KEY",)


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

    def test_discovery_note_appears_when_no_models_and_provider_has_discovery(self):
        self.config_path.write_text(
            "version: 1\n"
            "providers:\n"
            "  - name: nvidia\n"
            "    free: true\n",
            encoding="utf-8",
        )
        args = self._parse(["models", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_models(args)

        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("provider 'nvidia'", text)
        self.assertIn("discovery enabled", text)

    def test_discovery_note_absent_when_models_configured(self):
        self._write_two_tier_config()
        args = self._parse(["models", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_models(args)

        self.assertEqual(code, 0)
        self.assertNotIn("discovery enabled", out.getvalue())

    def test_discovery_note_absent_when_provider_discovery_disabled(self):
        self.config_path.write_text(
            "version: 1\n"
            "providers:\n"
            "  - name: anthropic\n"
            "    litellm_prefix: anthropic\n"
            "    discovery: false\n",
            encoding="utf-8",
        )
        args = self._parse(["models", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_models(args)

        self.assertEqual(code, 0)
        self.assertNotIn("discovery enabled", out.getvalue())


class GuardCommandTests(_EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.config_path.write_text(
            "version: 1\nproviders:\n  - name: nvidia\n    free: true\n",
            encoding="utf-8",
        )
        self.local_path = self.config_path.parent / "forge.local.yaml"

        # guard writes always attempt an auto-reload afterwards; mock httpx so
        # no real network call is made (no server is running in tests).
        import httpx

        patcher = mock.patch("httpx.post", side_effect=httpx.ConnectError("refused"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_paid_creates_local_guard_merged_first(self):
        args = self._parse(["guard", "--config", str(self.config_path), "--no-paid"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_guard(args)

        self.assertEqual(code, 0)
        self.assertTrue(self.local_path.exists())

        config = load_config(self.config_path)
        self.assertEqual(config.policies[0].name, "local-guard")
        self.assertEqual(config.policies[0].constraints.allow_paid, False)
        self.assertIn("applies on next start", out.getvalue().lower())

    def test_max_cost_updates_existing_guard(self):
        args1 = self._parse(["guard", "--config", str(self.config_path), "--no-paid"])
        cli.cmd_guard(args1)

        args2 = self._parse(["guard", "--config", str(self.config_path), "--max-cost", "0.05"])
        code = cli.cmd_guard(args2)

        self.assertEqual(code, 0)
        config = load_config(self.config_path)
        guard = config.policies[0]
        self.assertEqual(guard.name, "local-guard")
        self.assertEqual(guard.constraints.max_cost_per_request, 0.05)
        # earlier --no-paid setting is preserved alongside the new constraint
        self.assertEqual(guard.constraints.allow_paid, False)

    def test_max_cost_zero_is_valid(self):
        args = self._parse(["guard", "--config", str(self.config_path), "--max-cost", "0"])
        code = cli.cmd_guard(args)

        self.assertEqual(code, 0)
        config = load_config(self.config_path)
        self.assertEqual(config.policies[0].constraints.max_cost_per_request, 0.0)

    def test_max_cost_negative_is_rejected(self):
        args = self._parse(["guard", "--config", str(self.config_path), "--max-cost", "-0.01"])
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_guard(args)

        self.assertEqual(code, 1)
        self.assertIn("must not be negative", err.getvalue())
        self.assertFalse(self.local_path.exists())

    def test_off_removes_guard_and_deletes_empty_file(self):
        args1 = self._parse(["guard", "--config", str(self.config_path), "--no-paid"])
        cli.cmd_guard(args1)
        self.assertTrue(self.local_path.exists())

        args2 = self._parse(["guard", "--config", str(self.config_path), "--off"])
        code = cli.cmd_guard(args2)

        self.assertEqual(code, 0)
        self.assertFalse(self.local_path.exists())
        config = load_config(self.config_path)
        self.assertEqual(config.policies, [])

    def test_off_preserves_other_policies_in_local_file(self):
        self.local_path.write_text(
            "policies:\n"
            "  - name: other-policy\n"
            "    constraints:\n"
            "      exclude_providers: [nvidia]\n",
            encoding="utf-8",
        )
        args1 = self._parse(["guard", "--config", str(self.config_path), "--no-paid"])
        cli.cmd_guard(args1)

        config = load_config(self.config_path)
        names = {p.name for p in config.policies}
        self.assertEqual(names, {"local-guard", "other-policy"})

        args2 = self._parse(["guard", "--config", str(self.config_path), "--off"])
        code = cli.cmd_guard(args2)

        self.assertEqual(code, 0)
        self.assertTrue(self.local_path.exists())  # other-policy still needs the file
        config = load_config(self.config_path)
        names = {p.name for p in config.policies}
        self.assertEqual(names, {"other-policy"})

    def test_status_output_when_no_guard_set(self):
        args = self._parse(["guard", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_guard(args)

        self.assertEqual(code, 0)
        self.assertIn("no local guard set", out.getvalue().lower())

    def test_status_output_when_guard_set(self):
        args1 = self._parse(["guard", "--config", str(self.config_path), "--no-paid"])
        cli.cmd_guard(args1)

        args2 = self._parse(["guard", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_guard(args2)

        self.assertEqual(code, 0)
        self.assertIn("allow_paid=False", out.getvalue())

    def test_allow_paid_removes_allow_paid_key_but_keeps_other_constraints(self):
        args1 = self._parse(
            ["guard", "--config", str(self.config_path), "--no-paid", "--max-cost", "0.05"]
        )
        cli.cmd_guard(args1)

        args2 = self._parse(["guard", "--config", str(self.config_path), "--allow-paid"])
        code = cli.cmd_guard(args2)

        self.assertEqual(code, 0)
        config = load_config(self.config_path)
        guard = config.policies[0]
        self.assertEqual(guard.name, "local-guard")
        self.assertIsNone(guard.constraints.allow_paid)
        self.assertEqual(guard.constraints.max_cost_per_request, 0.05)

    def test_allow_paid_on_last_constraint_removes_the_guard_entirely(self):
        args1 = self._parse(["guard", "--config", str(self.config_path), "--no-paid"])
        cli.cmd_guard(args1)

        args2 = self._parse(["guard", "--config", str(self.config_path), "--allow-paid"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_guard(args2)

        self.assertEqual(code, 0)
        self.assertIn("removed", out.getvalue().lower())
        config = load_config(self.config_path)
        self.assertEqual(config.policies, [])

    def test_invalid_local_yaml_rolls_back_and_errors(self):
        # Pre-existing (valid) local file with an unrelated policy.
        self.local_path.write_text(
            "policies:\n"
            "  - name: other-policy\n"
            "    constraints:\n"
            "      exclude_providers: [nvidia]\n",
            encoding="utf-8",
        )
        original = self.local_path.read_text(encoding="utf-8")

        # Force load_config to fail after the guard file is rewritten by making the
        # main config itself invalid — validation runs against args.config, so a
        # broken forge.yaml simulates the "post-write validation fails" path.
        self.config_path.write_text("version: 1\nproviders: not-a-list\n", encoding="utf-8")

        args = self._parse(["guard", "--config", str(self.config_path), "--max-cost", "0.1"])
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_guard(args)

        self.assertEqual(code, 1)
        self.assertIn("rolled back", err.getvalue().lower())
        # local file content is restored to what it was before this invocation
        self.assertEqual(self.local_path.read_text(encoding="utf-8"), original)


class ReloadCommandTests(_EnvIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.config_path.write_text(
            "version: 1\nserver:\n  host: 127.0.0.1\n  port: 4000\n",
            encoding="utf-8",
        )

    def test_success_prints_summary(self):
        args = self._parse(["reload", "--config", str(self.config_path)])

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "reloaded",
            "models": 3,
            "providers": ["nvidia"],
            "discovered": {"nvidia": 1},
            "note": "server/auth section changes require a restart",
        }

        out = io.StringIO()
        with mock.patch("httpx.post", return_value=mock_response) as mock_post:
            with redirect_stdout(out):
                code = cli.cmd_reload(args)

        self.assertEqual(code, 0)
        mock_post.assert_called_once()
        called_url = mock_post.call_args[0][0]
        self.assertEqual(called_url, "http://127.0.0.1:4000/admin/reload")
        self.assertIn("3 model", out.getvalue())

    def test_connection_failure_prints_single_line_and_exits_1(self):
        args = self._parse(["reload", "--config", str(self.config_path)])

        import httpx

        err = io.StringIO()
        with mock.patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with redirect_stderr(err):
                code = cli.cmd_reload(args)

        self.assertEqual(code, 1)
        text = err.getvalue()
        self.assertIn("server not running at", text)
        self.assertIn("changes apply on next start", text)
        self.assertNotIn("Traceback", text)

    def test_missing_config_exits_1(self):
        missing = self.config_path.parent / "does-not-exist.yaml"
        args = self._parse(["reload", "--config", str(missing)])
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.cmd_reload(args)

        self.assertEqual(code, 1)
        self.assertIn("config file not found", err.getvalue())


class PoliciesCommandTests(_EnvIsolatedTestCase):
    def test_lists_policy_names_in_evaluation_order(self):
        self.config_path.write_text(
            "version: 1\n"
            "providers:\n"
            "  - name: nvidia\n"
            "    free: true\n"
            "policies:\n"
            "  - name: coding-route\n"
            "    when:\n"
            "      task: [coding]\n"
            "    route:\n"
            "      prefer: [tier1, tier2]\n"
            "  - name: global-guard\n"
            "    constraints:\n"
            "      allow_paid: false\n",
            encoding="utf-8",
        )
        args = self._parse(["policies", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_policies(args)

        text = out.getvalue()
        self.assertEqual(code, 0)
        lines = [l for l in text.splitlines() if l.strip()]
        coding_idx = next(i for i, l in enumerate(lines) if "coding-route" in l)
        guard_idx = next(i for i, l in enumerate(lines) if "global-guard" in l)
        self.assertLess(coding_idx, guard_idx)
        self.assertIn("task=coding", text)
        self.assertIn("allow_paid=False", text)

    def test_local_guard_appears_before_main_config_policies(self):
        self.config_path.write_text(
            "version: 1\n"
            "providers:\n"
            "  - name: nvidia\n"
            "    free: true\n"
            "policies:\n"
            "  - name: coding-route\n"
            "    when:\n"
            "      task: [coding]\n"
            "    route:\n"
            "      prefer: [tier1]\n",
            encoding="utf-8",
        )
        local_path = self.config_path.parent / "forge.local.yaml"
        local_path.write_text(
            "policies:\n"
            "  - name: local-guard\n"
            "    constraints:\n"
            "      allow_paid: false\n",
            encoding="utf-8",
        )
        args = self._parse(["policies", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_policies(args)

        text = out.getvalue()
        self.assertEqual(code, 0)
        lines = [l for l in text.splitlines() if l.strip()]
        local_idx = next(i for i, l in enumerate(lines) if "local-guard" in l)
        coding_idx = next(i for i, l in enumerate(lines) if "coding-route" in l)
        self.assertLess(local_idx, coding_idx)

    def test_no_policies_configured(self):
        self.config_path.write_text("version: 1\n", encoding="utf-8")
        args = self._parse(["policies", "--config", str(self.config_path)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_policies(args)

        self.assertEqual(code, 0)
        self.assertIn("no policies configured", out.getvalue().lower())


class MainEntryPointTests(unittest.TestCase):
    """main() reconfigures stdout/stderr to utf-8 to survive legacy Windows consoles."""

    def test_reconfigures_stdout_and_stderr_when_supported(self):
        fake_out = mock.Mock()
        fake_err = mock.Mock()
        with mock.patch.object(sys, "stdout", fake_out), \
                mock.patch.object(sys, "stderr", fake_err), \
                mock.patch.object(cli, "_COMMANDS", {"policies": lambda args: 0}):
            code = cli.main(["policies", "--config", "forge.yaml"])

        self.assertEqual(code, 0)
        fake_out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        fake_err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_does_not_crash_when_stream_lacks_reconfigure(self):
        # io.StringIO has no reconfigure() method, exercising the hasattr() guard.
        fake_out = io.StringIO()
        fake_err = io.StringIO()
        with mock.patch.object(sys, "stdout", fake_out), \
                mock.patch.object(sys, "stderr", fake_err), \
                mock.patch.object(cli, "_COMMANDS", {"policies": lambda args: 0}):
            code = cli.main(["policies", "--config", "forge.yaml"])

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
