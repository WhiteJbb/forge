"""가격표 조회 단위 테스트 (DESIGN.md §5.12, src/core/pricing.py)"""

import sys
import unittest
from unittest import mock

import litellm

from src.core.pricing import fill_registry_prices, resolve_price
from src.core.registry import Registry
from src.settings import ForgeConfig, ModelOverride, ProviderConfig


def _config(**overrides) -> ForgeConfig:
    base = dict(
        providers=[
            ProviderConfig(name="nvidia", api_key_env="NVIDIA_API_KEY", free=True),
            ProviderConfig(
                name="paid",
                api_key_env="PAID_API_KEY",
                free=False,
                litellm_prefix="together_ai",
            ),
        ],
        models=[
            ModelOverride(id="nvidia:model-a", tier="tier1"),
            ModelOverride(id="paid:model-p", tier="tier2"),
            ModelOverride(id="paid:model-q", tier="tier2", price_per_mtok=(1.0, 2.0)),
        ],
    )
    base.update(overrides)
    return ForgeConfig(**base)


class ResolvePriceTests(unittest.TestCase):
    def test_exact_key_match(self):
        with mock.patch.dict(
            litellm.model_cost,
            {"gpt-9000": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002}},
        ):
            result = resolve_price("gpt-9000", "openai")
        self.assertEqual(result, (1.0, 2.0))

    def test_prefix_key_match(self):
        with mock.patch.dict(
            litellm.model_cost,
            {
                "together_ai/model-p": {
                    "input_cost_per_token": 0.0000005,
                    "output_cost_per_token": 0.000001,
                }
            },
        ):
            result = resolve_price("model-p", "together_ai")
        self.assertEqual(result, (0.5, 1.0))

    def test_last_segment_key_match(self):
        with mock.patch.dict(
            litellm.model_cost,
            {"glm-5.2": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000003}},
        ):
            result = resolve_price("z-ai/glm-5.2", "someprefix")
        self.assertEqual(result, (1.0, 3.0))

    def test_not_found_returns_none(self):
        result = resolve_price("definitely-not-a-real-model-xyz", "openai")
        self.assertIsNone(result)

    def test_missing_field_returns_none(self):
        with mock.patch.dict(
            litellm.model_cost,
            {"partial-spec": {"input_cost_per_token": 0.000001}},
        ):
            result = resolve_price("partial-spec", "openai")
        self.assertIsNone(result)

    def test_non_numeric_field_returns_none(self):
        with mock.patch.dict(
            litellm.model_cost,
            {
                "bad-spec": {
                    "input_cost_per_token": "N/A",
                    "output_cost_per_token": 0.000002,
                }
            },
        ):
            result = resolve_price("bad-spec", "openai")
        self.assertIsNone(result)

    def test_conversion_multiplier_accuracy(self):
        with mock.patch.dict(
            litellm.model_cost,
            {
                "precise-spec": {
                    "input_cost_per_token": 0.0000005,
                    "output_cost_per_token": 0.0000015,
                }
            },
        ):
            result = resolve_price("precise-spec", "openai")
        self.assertEqual(result, (0.5, 1.5))

    def test_key_priority_exact_before_prefix(self):
        # 정확 일치 키가 있으면 prefix 후보보다 우선해야 한다
        with mock.patch.dict(
            litellm.model_cost,
            {
                "model-p": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000001},
                "together_ai/model-p": {
                    "input_cost_per_token": 0.000009,
                    "output_cost_per_token": 0.000009,
                },
            },
        ):
            result = resolve_price("model-p", "together_ai")
        self.assertEqual(result, (1.0, 1.0))

    def test_first_matched_key_wins_even_if_invalid(self):
        # 후보1(exact)이 존재하지만 필드가 불완전하면, 후보2(prefix)가 유효해도
        # 폴스루하지 않고 None을 반환해야 한다 (첫 매칭 키에서 결정)
        with mock.patch.dict(
            litellm.model_cost,
            {
                "model-p": {"input_cost_per_token": 0.000001},  # output 필드 없음
                "together_ai/model-p": {
                    "input_cost_per_token": 0.000009,
                    "output_cost_per_token": 0.000009,
                },
            },
        ):
            result = resolve_price("model-p", "together_ai")
        self.assertIsNone(result)

    def test_import_failure_returns_none(self):
        with mock.patch.dict(sys.modules, {"litellm": None}):
            result = resolve_price("gpt-9000", "openai")
        self.assertIsNone(result)


class FillRegistryPricesTests(unittest.TestCase):
    def test_fills_only_none_entries_and_returns_count(self):
        config = _config()
        registry = Registry(config)
        # 초기 상태 확인: model-a는 free provider라 (0,0), model-q는 명시값, model-p는 unknown
        self.assertEqual(registry.get("nvidia:model-a").price_per_mtok, (0.0, 0.0))
        self.assertEqual(registry.get("paid:model-q").price_per_mtok, (1.0, 2.0))
        self.assertIsNone(registry.get("paid:model-p").price_per_mtok)

        with mock.patch.dict(
            litellm.model_cost,
            {
                "together_ai/model-p": {
                    "input_cost_per_token": 0.000002,
                    "output_cost_per_token": 0.000004,
                }
            },
        ):
            filled = fill_registry_prices(registry, config)

        self.assertEqual(filled, 1)
        self.assertEqual(registry.get("paid:model-p").price_per_mtok, (2.0, 4.0))
        # 기존 값들은 그대로 유지
        self.assertEqual(registry.get("nvidia:model-a").price_per_mtok, (0.0, 0.0))
        self.assertEqual(registry.get("paid:model-q").price_per_mtok, (1.0, 2.0))

    def test_no_match_leaves_none_and_returns_zero(self):
        config = _config()
        registry = Registry(config)
        filled = fill_registry_prices(registry, config)
        self.assertEqual(filled, 0)
        self.assertIsNone(registry.get("paid:model-p").price_per_mtok)


if __name__ == "__main__":
    unittest.main()
