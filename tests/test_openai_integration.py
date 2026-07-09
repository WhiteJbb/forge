"""OpenAI 호환 엔드포인트 통합 테스트 — FakeProvider + TestClient (DESIGN.md §3, §5.8, §7)

server.py를 거치지 않고 Deps를 직접 조립한다. 네트워크 호출 없음 — 프로바이더는
전부 스크립트 기반 FakeProvider(ScriptedProvider)로 대체한다.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.openai import Deps, build_router
from src.core.analyzer import RequestAnalyzer
from src.core.registry import Registry
from src.core.scheduler import Scheduler
from src.providers.base import RateLimited, UpstreamBadRequest, UpstreamServerError
from src.settings import load_config

CONFIG_YAML = """
version: 1
providers:
  - name: nvidia
    api_key_env: NVIDIA_API_KEY
    free: true
models:
  - id: "nvidia:model-a"
    tier: tier1
    features: [tools, streaming]
    context_window: 100000
  - id: "nvidia:model-b"
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


class FakeMetrics:
    """MetricsEngine 대역 — 기록만 하고 아무 것도 하지 않는다."""

    def __init__(self):
        self.records = []

    def record(self, metric):
        self.records.append(metric)


class ScriptedProvider:
    """DESIGN.md §9 Provider Simulator의 축소판.

    provider_model_id별로 "동작 큐"를 갖는다. 큐의 각 항목은
    - Exception 인스턴스면 raise
    - dict면 chat()의 정상 응답
    - list[dict]면 chat_stream()의 정상 청크 시퀀스
    """

    def __init__(self, name, config, scripts):
        self.name = name
        self.config = config
        self._scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[tuple[str, str]] = []

    def _pop(self, provider_model_id):
        queue = self._scripts.get(provider_model_id)
        if not queue:
            raise AssertionError(f"no scripted action left for {provider_model_id!r}")
        return queue.pop(0)

    async def chat(self, provider_model_id, payload):
        self.calls.append((provider_model_id, "chat"))
        action = self._pop(provider_model_id)
        if isinstance(action, BaseException):
            raise action
        return action

    async def chat_stream(self, provider_model_id, payload):
        self.calls.append((provider_model_id, "chat_stream"))
        action = self._pop(provider_model_id)
        if isinstance(action, BaseException):
            raise action
        for chunk in action:
            yield chunk

    async def embeddings(self, provider_model_id, payload):
        raise NotImplementedError

    async def list_models(self):
        return []

    async def probe(self, provider_model_id, timeout):
        raise NotImplementedError

    async def close(self):
        pass


def _chat_response(model="nvidia:model-b", prompt_tokens=5, completion_tokens=7):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"hello from {model}"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class OpenAIIntegrationTestCase(unittest.TestCase):
    """공용 조립 헬퍼. 각 테스트가 독립된 config/registry/provider를 갖도록 매번 새로 만든다."""

    def _build(self, scripts):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "forge.yaml"
        path.write_text(CONFIG_YAML, encoding="utf-8")

        config = load_config(path)
        registry = Registry(config)
        scheduler = Scheduler(config, registry)
        analyzer = RequestAnalyzer()
        metrics = FakeMetrics()
        provider = ScriptedProvider("nvidia", config.provider("nvidia"), scripts)

        deps = Deps(
            config=config,
            registry=registry,
            scheduler=scheduler,
            analyzer=analyzer,
            metrics=metrics,
            providers={"nvidia": provider},
        )
        app = FastAPI()
        app.include_router(build_router(deps))
        client = TestClient(app)
        return client, registry, provider, metrics


class NonStreamingFailoverTests(OpenAIIntegrationTestCase):
    def test_a_ratelimited_then_success_failover(self):
        scripts = {
            "model-a": [RateLimited("rate limited")],
            "model-b": [_chat_response()],
        }
        client, registry, provider, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["x-forge-attempt"], "2")
        self.assertEqual(resp.headers["x-forge-model"], "nvidia:model-b")
        self.assertEqual(registry.get("nvidia:model-a").health.status, "cooldown")
        self.assertIn(("model-a", "chat"), provider.calls)
        self.assertIn(("model-b", "chat"), provider.calls)

    def test_b_upstream_bad_request_does_not_failover(self):
        body = {
            "error": {
                "message": "bad request from upstream",
                "type": "invalid_request_error",
                "code": 400,
            }
        }
        scripts = {
            "model-a": [UpstreamBadRequest("bad request", status_code=400, body=body)],
        }
        client, registry, provider, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), body)
        self.assertNotIn(("model-b", "chat"), provider.calls)

    def test_c_candidate_exhaustion_returns_503(self):
        scripts = {
            "model-a": [RateLimited("rl")],
            "model-b": [UpstreamServerError("boom", status_code=502)],
        }
        client, _, _, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)

    def test_f_direct_model_selection_skips_failover(self):
        scripts = {"model-b": [RateLimited("rl")]}
        client, _, provider, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "nvidia:model-b", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("all models failed", resp.json()["error"]["message"])
        self.assertNotIn(("model-a", "chat"), provider.calls)


class StreamingFailoverTests(OpenAIIntegrationTestCase):
    _B_CONTENT_CHUNKS = [
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}],
        },
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]

    def test_d_streaming_failover_before_first_chunk(self):
        scripts = {
            "model-a": [RateLimited("rl")],
            "model-b": [self._B_CONTENT_CHUNKS],
        }
        client, _, _, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["x-forge-model"], "nvidia:model-b")
        text = resp.text
        self.assertIn("data: [DONE]", text)
        self.assertNotIn("rate limited", text.lower())

    def _b_chunks_with_usage(self):
        usage_chunk = {
            "id": "x",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        return list(self._B_CONTENT_CHUNKS) + [usage_chunk]

    def test_e_usage_chunk_removed_when_client_did_not_request_it(self):
        scripts = {"model-a": [self._b_chunks_with_usage()]}
        client, _, _, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "nvidia:model-a",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('"usage"', resp.text)
        self.assertIn("data: [DONE]", resp.text)

    def test_e_usage_chunk_kept_when_client_requested_it(self):
        scripts = {"model-a": [self._b_chunks_with_usage()]}
        client, _, _, _ = self._build(scripts)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "nvidia:model-a",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('"usage"', resp.text)
        self.assertIn('"prompt_tokens": 5', resp.text)


if __name__ == "__main__":
    unittest.main()
