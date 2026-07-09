"""Model Registry 단위 테스트 (DESIGN.md §5.2, src/core/registry.py)"""

import unittest

from forge_gateway.core.registry import Registry
from forge_gateway.settings import ForgeConfig, ModelOverride, ProviderConfig


def _config(**overrides) -> ForgeConfig:
    base = dict(
        providers=[
            ProviderConfig(name="nvidia", api_key_env="NVIDIA_API_KEY", free=True),
            ProviderConfig(name="paid", api_key_env="PAID_API_KEY", free=False),
        ],
        models=[
            ModelOverride(id="nvidia:model-a", tier="tier1", capabilities={"code": 9}),
            ModelOverride(id="paid:model-p", tier="tier2"),
        ],
    )
    base.update(overrides)
    return ForgeConfig(**base)


class RegistryBuildTests(unittest.TestCase):
    def test_capability_defaults_filled(self):
        registry = Registry(_config())
        entry = registry.get("nvidia:model-a")
        self.assertIsNotNone(entry)
        # code는 명시값 9, 나머지는 defaults.capability(7)로 채워져야 함
        self.assertEqual(entry.capabilities["code"], 9)
        for key in ("debug", "refactor", "docs", "context", "speed"):
            self.assertEqual(entry.capabilities[key], 7)

    def test_features_default_when_unspecified(self):
        registry = Registry(_config())
        entry = registry.get("nvidia:model-a")
        self.assertEqual(entry.features, {"tools", "streaming"})

    def test_free_provider_price_defaults_to_zero(self):
        registry = Registry(_config())
        entry = registry.get("nvidia:model-a")
        self.assertEqual(entry.price_per_mtok, (0.0, 0.0))

    def test_paid_provider_without_price_is_unknown(self):
        registry = Registry(_config())
        entry = registry.get("paid:model-p")
        self.assertIsNone(entry.price_per_mtok)

    def test_explicit_price_overrides_free_flag(self):
        config = _config(
            models=[
                ModelOverride(
                    id="nvidia:model-a", tier="tier1", price_per_mtok=(1.5, 3.0)
                ),
            ]
        )
        registry = Registry(config)
        entry = registry.get("nvidia:model-a")
        self.assertEqual(entry.price_per_mtok, (1.5, 3.0))

    def test_tier_defaults_when_unspecified(self):
        config = _config(
            models=[ModelOverride(id="nvidia:model-x")],
        )
        registry = Registry(config)
        entry = registry.get("nvidia:model-x")
        self.assertEqual(entry.tier, "tier3")  # defaults.tier


class RegistryMergeDiscoveredTests(unittest.TestCase):
    def test_new_models_registered_and_returned(self):
        registry = Registry(_config())
        added = registry.merge_discovered("nvidia", ["model-a", "model-new"])
        # model-a는 이미 config로 등록되어 있으므로 신규 아님
        self.assertEqual(added, ["nvidia:model-new"])
        new_entry = registry.get("nvidia:model-new")
        self.assertIsNotNone(new_entry)
        self.assertEqual(new_entry.source, "discovered")
        self.assertEqual(new_entry.tier, "tier3")  # defaults.tier

    def test_existing_config_entry_untouched_by_discovery(self):
        registry = Registry(_config())
        original = registry.get("nvidia:model-a")
        original_caps = dict(original.capabilities)
        registry.merge_discovered("nvidia", ["model-a"])
        after = registry.get("nvidia:model-a")
        self.assertIs(after, original)
        self.assertEqual(after.source, "config")
        self.assertEqual(after.capabilities, original_caps)

    def test_discovered_free_provider_gets_zero_price(self):
        registry = Registry(_config())
        registry.merge_discovered("nvidia", ["brand-new-model"])
        entry = registry.get("nvidia:brand-new-model")
        self.assertEqual(entry.price_per_mtok, (0.0, 0.0))


class ResolveClientModelTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry(_config())

    def test_exact_forge_id_match(self):
        entry = self.registry.resolve_client_model("nvidia:model-a")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, "nvidia:model-a")

    def test_unique_bare_model_id_matches(self):
        entry = self.registry.resolve_client_model("model-a")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, "nvidia:model-a")

    def test_no_match_returns_none(self):
        entry = self.registry.resolve_client_model("does-not-exist")
        self.assertIsNone(entry)

    def test_ambiguous_bare_id_returns_none(self):
        config = _config(
            providers=[
                ProviderConfig(name="nvidia", api_key_env="NVIDIA_API_KEY"),
                ProviderConfig(name="openrouter", api_key_env="OR_API_KEY"),
            ],
            models=[
                ModelOverride(id="nvidia:shared-model", tier="tier1"),
                ModelOverride(id="openrouter:shared-model", tier="tier1"),
            ],
        )
        registry = Registry(config)
        entry = registry.resolve_client_model("shared-model")
        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
