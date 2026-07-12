"""멀티 API 키 로테이션 통합 테스트 (DecisionLog 2026-07-12, §5.13).

test_openai_integration의 ScriptedProvider/FakeMetrics 패턴을 재사용하되, Deps에
실제 ProviderThrottle을 주입해 429 키 귀책 파이프라인을 end-to-end로 검증한다.
- 멀티 키 provider의 429는 사용한 키만 쿨다운, 남은 키로 같은 모델 재시도
- 전 키 429면 모델 쿨다운 후 다른 provider로 failover
- 단일 키 provider의 429는 기존처럼 즉시 모델 쿨다운 (회귀 방지)

밀폐: 임시 config에 auto_providers:false, 키 환경변수는 patch.dict로 격리.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_gateway.api.openai import Deps, build_router
from forge_gateway.core.analyzer import RequestAnalyzer
from forge_gateway.core.registry import Registry
from forge_gateway.core.scheduler import Scheduler
from forge_gateway.core.throttle import ProviderThrottle
from forge_gateway.providers.base import RateLimited
from forge_gateway.settings import load_config

from tests.test_openai_integration import FakeMetrics, ScriptedProvider, _chat_response

# multi: 키 2개(무rpm — 429 쿨다운만으로 로테이션), solo: 키 1개. big(tier1)이 항상
# 먼저 뽑히므로 재시도/failover 경로가 결정적이다.
TWO_PROVIDER_YAML = """
version: 1
auto_providers: false
providers:
  - name: multi
    api_key_envs: [MK_KEY_1, MK_KEY_2]
  - name: solo
    api_key_env: SOLO_KEY
models:
  - id: "multi:big"
    tier: tier1
    features: [tools, streaming]
    context_window: 100000
  - id: "solo:small"
    tier: tier2
    features: [tools, streaming]
    context_window: 100000
defaults:
  capability: 7
  tier: tier3
  features: [tools, streaming]
scheduler:
  cooldown_seconds: 300
  max_failures_before_cooldown: 3
  max_attempts: 4
  session_affinity: true
  session_ttl_minutes: 30
metrics:
  db_path: "unused-in-integration-tests.db"
"""

SOLO_YAML = """
version: 1
auto_providers: false
providers:
  - name: solo
    api_key_env: SOLO_KEY
models:
  - id: "solo:small"
    tier: tier1
    features: [tools, streaming]
    context_window: 100000
defaults:
  capability: 7
  tier: tier3
  features: [tools, streaming]
scheduler:
  cooldown_seconds: 300
  max_failures_before_cooldown: 3
  max_attempts: 4
  session_affinity: true
  session_ttl_minutes: 30
metrics:
  db_path: "unused-in-integration-tests.db"
"""

_ENV = {"MK_KEY_1": "sk-key-one", "MK_KEY_2": "sk-key-two", "SOLO_KEY": "sk-solo"}


class MultiKeyIntegrationTest(unittest.TestCase):
    def _build(self, yaml_text, provider_scripts):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "forge.yaml"
        path.write_text(yaml_text, encoding="utf-8")

        with mock.patch.dict(os.environ, _ENV, clear=False):
            config = load_config(path)
            throttle = ProviderThrottle(config.providers)  # api_keys를 여기서 해석
        registry = Registry(config)
        scheduler = Scheduler(config, registry)
        providers = {
            name: ScriptedProvider(name, config.provider(name), scripts)
            for name, scripts in provider_scripts.items()
        }
        deps = Deps(
            config=config,
            registry=registry,
            scheduler=scheduler,
            analyzer=RequestAnalyzer(),
            metrics=FakeMetrics(),
            providers=providers,
            throttle=throttle,
        )
        app = FastAPI()
        app.include_router(build_router(deps))
        client = TestClient(app)
        return client, registry, providers, deps

    def test_429_on_one_key_retries_same_model_with_other_key(self):
        """멀티 키 provider: 첫 키 429 → 같은 모델을 다른 key_index로 재시도 → 성공.
        모델 health는 쿨다운되지 않아야 한다(429는 키 귀책)."""
        client, registry, providers, deps = self._build(
            TWO_PROVIDER_YAML,
            {"multi": {"big": [RateLimited("rate limited"),
                               _chat_response(model="multi:big")]},
             "solo": {"small": [_chat_response(model="solo:small")]}},
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["x-forge-model"], "multi:big")  # 같은 모델
        self.assertEqual(resp.headers["x-forge-attempt"], "2")
        # 두 번의 호출이 서로 다른 키로 나갔다
        self.assertEqual(providers["multi"].key_indices, [0, 1])
        # 모델은 쿨다운되지 않았다 (429 키 귀책)
        self.assertEqual(registry.get("multi:big").health.status, "healthy")
        # solo로 failover하지 않았다
        self.assertEqual(providers["solo"].calls, [])

    def test_all_keys_429_cools_model_and_fails_over(self):
        """두 키 모두 429 → 모델 쿨다운 후 다른 provider 후보로 failover."""
        client, registry, providers, deps = self._build(
            TWO_PROVIDER_YAML,
            {"multi": {"big": [RateLimited("rl"), RateLimited("rl")]},
             "solo": {"small": [_chat_response(model="solo:small")]}},
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["x-forge-model"], "solo:small")  # failover
        self.assertEqual(providers["multi"].key_indices, [0, 1])  # 두 키 모두 시도됨
        # 전 키 소진 → 모델 쿨다운
        self.assertEqual(registry.get("multi:big").health.status, "cooldown")

    def test_single_key_429_cools_model_immediately(self):
        """단일 키 provider의 429는 기존처럼 즉시 모델 쿨다운 (회귀 방지)."""
        client, registry, providers, deps = self._build(
            SOLO_YAML,
            {"solo": {"small": [RateLimited("rl")]}},
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(providers["solo"].key_indices, [0])
        self.assertEqual(registry.get("solo:small").health.status, "cooldown")


if __name__ == "__main__":
    unittest.main()
