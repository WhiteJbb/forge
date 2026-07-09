"""Policy Engine 테스트 (DESIGN.md §5.4)"""

import unittest

from forge_gateway.core.policy import PolicyEngine
from forge_gateway.core.registry import Registry
from forge_gateway.core.scheduler import NoCandidateError, Scheduler
from forge_gateway.core.types import AnalysisResult
from forge_gateway.settings import ForgeConfig


def _config(policies: list) -> ForgeConfig:
    return ForgeConfig(**{
        "providers": [
            {"name": "nvidia", "api_key_env": "NVIDIA_API_KEY", "free": True},
            {"name": "paidco", "api_key_env": "PAID_KEY"},
        ],
        "models": [
            {"id": "nvidia:model-a", "tier": "tier1",
             "capabilities": {"code": 9, "docs": 5}, "context_window": 32000},
            {"id": "nvidia:model-b", "tier": "tier2",
             "capabilities": {"code": 6, "docs": 9}, "context_window": 200000},
            {"id": "paidco:model-c", "tier": "tier1",
             "capabilities": {"code": 8}, "price_per_mtok": [3.0, 15.0]},
        ],
        "policies": policies,
    })


def _engine(policies: list):
    config = _config(policies)
    registry = Registry(config)
    return PolicyEngine(config, registry), registry, config


def _analysis(**kw) -> AnalysisResult:
    kw.setdefault("required_features", set())
    return AnalysisResult(**kw)


class PolicyMatchTests(unittest.TestCase):
    def test_first_match_wins(self):
        engine, _, _ = _engine([
            {"name": "docs", "when": {"task": ["documentation"]},
             "route": {"prefer": ["nvidia:model-b"], "fallback": ["tier1"]}},
            {"name": "default", "when": {},
             "route": {"prefer": ["tier1"], "fallback": ["tier2"]}},
        ])
        plan = engine.plan(_analysis(task="documentation"))
        self.assertEqual(plan.policy_name, "docs")
        self.assertEqual(plan.groups[0][0].id, "nvidia:model-b")

        plan = engine.plan(_analysis(task="coding"))
        self.assertEqual(plan.policy_name, "default")
        # tier1 그룹: model-a + model-c
        self.assertEqual({e.id for e in plan.groups[0]},
                         {"nvidia:model-a", "paidco:model-c"})

    def test_no_policies_defaults_to_tier_order(self):
        engine, _, _ = _engine([])
        plan = engine.plan(_analysis(task="coding"))
        self.assertEqual(plan.policy_name, "default")
        self.assertEqual(len(plan.groups), 2)  # tier1, tier2 (tier3 비어 있음)

    def test_when_model_and_client(self):
        engine, _, _ = _engine([
            {"name": "cline-rule", "when": {"client": "cline"},
             "route": {"prefer": ["tier2"]}},
            {"name": "default", "when": {}, "route": {"prefer": ["tier1"]}},
        ])
        plan = engine.plan(_analysis(task="coding"), user_agent="Cline/3.0")
        self.assertEqual(plan.policy_name, "cline-rule")
        plan = engine.plan(_analysis(task="coding"), user_agent="aider")
        self.assertEqual(plan.policy_name, "default")

    def test_when_token_bounds(self):
        engine, _, _ = _engine([
            {"name": "long-context", "when": {"min_prompt_tokens": 60000},
             "route": {"prefer": [{"context_window": ">=128000"}]}},
            {"name": "default", "when": {}, "route": {"prefer": ["tier1"]}},
        ])
        plan = engine.plan(_analysis(task="coding", est_prompt_tokens=70000))
        self.assertEqual(plan.policy_name, "long-context")
        # 속성 셀렉터: context_window >= 128000 → model-b만
        self.assertEqual([e.id for e in plan.groups[0]], ["nvidia:model-b"])


class ConstraintTests(unittest.TestCase):
    def test_allow_paid_false_excludes_paid_and_unknown(self):
        engine, _, _ = _engine([
            {"name": "free-only", "constraints": {"allow_paid": False}},
        ])
        plan = engine.plan(_analysis(task="coding"))
        ids = {e.id for g in plan.groups for e in g}
        self.assertNotIn("paidco:model-c", ids)   # 유료 제외
        self.assertIn("nvidia:model-a", ids)       # free provider → (0,0) 확인됨
        self.assertGreater(plan.rejected_by_constraints, 0)

    def test_max_cost_per_request(self):
        engine, _, _ = _engine([
            {"name": "cheap", "constraints": {"max_cost_per_request": 0.001}},
        ])
        # paid 모델: 10000 tok 입력 × $3/M + 100 tok 출력 × $15/M ≈ $0.0315 > 0.001
        plan = engine.plan(_analysis(task="coding", est_prompt_tokens=10000),
                           max_tokens=100)
        ids = {e.id for g in plan.groups for e in g}
        self.assertNotIn("paidco:model-c", ids)
        self.assertIn("nvidia:model-a", ids)  # 무료 → 비용 0

    def test_exclude_providers(self):
        engine, _, _ = _engine([
            {"name": "no-paidco", "constraints": {"exclude_providers": ["paidco"]}},
        ])
        plan = engine.plan(_analysis(task="coding"))
        ids = {e.id for g in plan.groups for e in g}
        self.assertNotIn("paidco:model-c", ids)

    def test_direct_model_still_constrained(self):
        engine, registry, _ = _engine([
            {"name": "free-only", "constraints": {"allow_paid": False}},
        ])
        paid = registry.get("paidco:model-c")
        self.assertFalse(engine.entry_passes_constraints(paid, _analysis(task="coding")))
        free = registry.get("nvidia:model-a")
        self.assertTrue(engine.entry_passes_constraints(free, _analysis(task="coding")))


class SchedulerGroupTests(unittest.TestCase):
    def test_select_honors_group_order(self):
        engine, registry, config = _engine([
            {"name": "docs", "when": {"task": ["documentation"]},
             "route": {"prefer": ["nvidia:model-b"], "fallback": ["tier1"]}},
        ])
        scheduler = Scheduler(config, registry)
        plan = engine.plan(_analysis(task="documentation"))
        entry, info = scheduler.select(_analysis(task="documentation"),
                                       groups=plan.groups)
        # prefer 그룹(model-b 단독)이 tier 순서보다 우선
        self.assertEqual(entry.id, "nvidia:model-b")

    def test_provider_filter_spreads_traffic(self):
        """rpm 버킷이 빈 provider는 후보에서 제외돼 다른 provider로 분산 (§5.13)"""
        engine, registry, config = _engine([])
        scheduler = Scheduler(config, registry)
        # nvidia 버킷이 빈 상황을 흉내 — paidco만 통과
        entry, _ = scheduler.select(_analysis(task="coding"),
                                    provider_filter=lambda p: p != "nvidia")
        self.assertEqual(entry.provider, "paidco")
        # 전 provider 소진 → 503 스로틀 사유
        with self.assertRaises(NoCandidateError) as ctx:
            scheduler.select(_analysis(task="coding", session_key=""),
                             provider_filter=lambda p: False)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("throttled", ctx.exception.reason)

    def test_constraint_empty_groups_raise(self):
        engine, registry, config = _engine([
            {"name": "nothing", "constraints": {"exclude_providers": ["nvidia", "paidco"]}},
        ])
        scheduler = Scheduler(config, registry)
        plan = engine.plan(_analysis(task="coding"))
        self.assertEqual(plan.groups, [])
        with self.assertRaises(NoCandidateError):
            scheduler.select(_analysis(task="coding"), groups=plan.groups)


if __name__ == "__main__":
    unittest.main()
