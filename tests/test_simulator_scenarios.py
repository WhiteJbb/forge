"""Provider Simulator 시나리오 테스트 (DESIGN.md §9-1, §7)

FakeProvider(파이썬 객체 대체)를 우회한 진짜 E2E: localhost의 mock OpenAI 서버
(tests/simulator.py)를 실제 LiteLLMProvider + httpx + 실제 SSE로 통과시켜
전체 스택(litellm 어댑터 → 예외 변환 → failover)을 결정론적으로 검증한다.

격리: 쿨다운/레이턴시 상태는 Registry(=앱)에 살아 있으므로 시나리오마다
새 create_app을 만든다. Simulator 서버는 상태가 없어 모듈 전역으로 공유한다.

발견된 소스 버그 2건은 @unittest.expectedFailure로 실행 가능하게 문서화했다
(파일 하단 주석 + 최종 보고 참조). 나머지는 전부 정상 통과한다.
"""

import json
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

# LiteLLMProvider(openai prefix)가 api_key를 요구하므로 임의 키를 심는다.
os.environ.setdefault("SIM_KEY", "sk-sim-test")
# 인증은 FORGE_API_KEY가 없으면 no-op — 테스트 요청에 Bearer가 없어도 통과.
os.environ.pop("FORGE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from forge_gateway.server import create_app  # noqa: E402
from tests.simulator import (  # noqa: E402
    ProviderSimulator,
    context_length,
    cut_midstream,
    delay_ttft,
    ok,
    rate_limit,
    server_error,
)

_SIM: ProviderSimulator = None  # setUpModule에서 기동


def setUpModule():
    global _SIM
    _SIM = ProviderSimulator().start()


def tearDownModule():
    if _SIM is not None:
        _SIM.stop()


def _config_text(api_base: str, db_path: str, rpm=None) -> str:
    """provider 1개(sim), 모델 2개(tier1 model-a / tier2 model-b)의 forge.yaml.

    - ttft=2초로 짧게(TTFT 타임아웃 시나리오용)
    - probe_idle_minutes를 천문학적으로 크게 — 백그라운드 probe가 시나리오에
      개입(스크립트 소모/쿨다운 유발)하지 못하게 한다. (last_used=0이라 idle 판정을
      막으려면 idle_seconds > 현재 epoch여야 한다.)
    - model-a는 작은 컨텍스트(8k), model-b는 큰 컨텍스트(200k) — context_length
      상향 failover 검증용.
    """
    rpm_line = f"    rpm: {rpm}\n" if rpm is not None else ""
    return f"""version: 1
auto_providers: false  # 밀폐 테스트 — 러너 환경의 실키가 provider로 끼어들지 않게
server:
  host: 127.0.0.1
  port: 4000
providers:
  - name: sim
    litellm_prefix: openai
    api_base: {api_base}
    api_key_env: SIM_KEY
    discovery: false
    free: true
{rpm_line}models:
  - id: "sim:model-a"
    tier: tier1
    features: [tools, streaming]
    context_window: 8000
  - id: "sim:model-b"
    tier: tier2
    features: [tools, streaming]
    context_window: 200000
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
timeouts:
  connect: 5
  ttft: 2
  total_deadline: 30
metrics:
  db_path: "{db_path}"
health:
  probe_idle_minutes: 1000000000
  probe_timeout: 5
"""


class SimulatorScenarioTest(unittest.TestCase):
    """공용 조립 — 시나리오마다 새 앱(새 Registry)을 만들어 상태 누수를 막는다."""

    @property
    def sim(self) -> ProviderSimulator:
        return _SIM

    def setUp(self):
        # warmup probe가 이전 테스트의 스크립트를 소모하지 않도록 먼저 비운다.
        _SIM.scripts.clear()
        _SIM.requests.clear()

    def _make_app(self, rpm=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db_path = (Path(tmp) / "m.db").as_posix()
        cfg_path = Path(tmp) / "forge.yaml"
        cfg_path.write_text(_config_text(_SIM.api_base, db_path, rpm), encoding="utf-8")
        return create_app(str(cfg_path))

    @contextmanager
    def _client(self, rpm=None):
        app = self._make_app(rpm)
        with TestClient(app) as client:
            # lifespan warmup/discovery가 sim을 한 번 친 기록을 버려 요청 추적을 깨끗이.
            _SIM.requests.clear()
            yield client

    # --- 공용 요청 헬퍼 ---

    @staticmethod
    def _post(client, model, content, stream=False, **extra):
        body = {"model": model, "messages": [{"role": "user", "content": content}],
                "stream": stream}
        body.update(extra)
        return client.post("/v1/chat/completions", json=body)

    def _cooldown_models(self, client):
        h = client.get("/health").json()
        return {m["id"]: m for m in h["models"] if m["status"] == "cooldown"}

    @staticmethod
    def _stream_content(text: str) -> str:
        """SSE 응답에서 델타 content를 재조립한다 (청크 분할과 무관하게 검증)."""
        out = []
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload.strip() == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    out.append(piece)
        return "".join(out)


# ==========================================================================
# a. 429 → 다음 tier로 failover, model-a 쿨다운
# ==========================================================================


class RateLimitFailoverTests(SimulatorScenarioTest):
    def test_a_429_failover_to_model_b_and_cooldown(self):
        with self._client() as client:
            self.sim.script("model-a", [rate_limit()])
            resp = self._post(client, "auto", "hello a")

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["x-forge-attempt"], "2")
            self.assertEqual(resp.headers["x-forge-model"], "sim:model-b")
            # 실제 호출 순서: model-a(429) → model-b(200)
            self.assertEqual(self.sim.requests,
                             [("model-a", False), ("model-b", False)])
            # /health에서 model-a가 cooldown
            self.assertIn("sim:model-a", self._cooldown_models(client))


# ==========================================================================
# b. Retry-After 존중 (§5.5 / §7)  —— 소스 버그로 미동작 (아래 참조)
# ==========================================================================


class RetryAfterTests(SimulatorScenarioTest):
    def test_b1_429_enters_immediate_cooldown(self):
        """429는 즉시 쿨다운 진입 — 이 부분은 정상 동작한다."""
        with self._client() as client:
            self.sim.script("model-a", [rate_limit(retry_after=7)])
            resp = self._post(client, "auto", "hello b")
            self.assertEqual(resp.status_code, 200)  # model-b로 failover
            cooldown = self._cooldown_models(client)
            self.assertIn("sim:model-a", cooldown)
            # 쿨다운에 실제로 들어갔다(남은 시간 > 0).
            self.assertGreater(cooldown["sim:model-a"]["cooldown_remaining"], 0)

    def test_b2_retry_after_value_is_honored(self):
        """DESIGN §5.5/§7: Retry-After=7이면 쿨다운이 ~7초여야 한다.

        회귀 테스트: litellm 1.91.x는 응답 헤더를 e.litellm_response_headers에
        담는데 이를 조회하지 않아 항상 기본 300초로 들어가던 버그 (수정됨).
        """
        with self._client() as client:
            self.sim.script("model-a", [rate_limit(retry_after=7)])
            self._post(client, "auto", "hello b2")
            remaining = self._cooldown_models(client)["sim:model-a"]["cooldown_remaining"]
            # 기대: Retry-After 값(~7초). 실제: ~300초 → 실패.
            self.assertLessEqual(remaining, 30)


# ==========================================================================
# c. 5xx → failover / 일반 4xx → failover 없이 반환
# ==========================================================================


class ServerErrorFailoverTests(SimulatorScenarioTest):
    def test_c1_5xx_failover(self):
        with self._client() as client:
            self.sim.script("model-a", [server_error(500)])
            resp = self._post(client, "auto", "hello c1")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["x-forge-attempt"], "2")
            self.assertEqual(resp.headers["x-forge-model"], "sim:model-b")
            self.assertEqual(self.sim.requests,
                             [("model-a", False), ("model-b", False)])

    def test_c2_generic_4xx_no_failover(self):
        """일반 400은 요청 자체 문제 — failover하지 않고 업스트림 에러를 그대로 반환."""
        with self._client() as client:
            # server_error(400): 고정 behavior 세트로 만드는 일반 4xx (context_length 아님)
            self.sim.script("model-a", [server_error(400)])
            resp = self._post(client, "auto", "hello c2")
            self.assertEqual(resp.status_code, 400)
            # model-b는 시도조차 하지 않는다.
            self.assertEqual(self.sim.requests, [("model-a", False)])
            self.assertNotIn(("model-b", False), self.sim.requests)


# ==========================================================================
# d. context_length 400 → 상향 failover (더 큰 컨텍스트 창으로)
# ==========================================================================


class ContextLengthFailoverTests(SimulatorScenarioTest):
    def test_d_context_length_upgrades_to_larger_window(self):
        with self._client() as client:
            self.sim.script("model-a", [context_length()])
            resp = self._post(client, "auto", "hello d")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["x-forge-model"], "sim:model-b")  # 200k 창
            self.assertEqual(self.sim.requests,
                             [("model-a", False), ("model-b", False)])


# ==========================================================================
# e. 스트리밍 — SSE [DONE], usage 청크 처리 (§5.8)
# ==========================================================================


class StreamingTests(SimulatorScenarioTest):
    def test_e1_stream_has_done_and_content(self):
        with self._client() as client:
            self.sim.script("model-a", [ok(text="streamed body")])
            resp = self._post(client, "sim:model-a", "hello e1", stream=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("data: [DONE]", resp.text)
            # 청크로 쪼개진 델타를 재조립하면 원문과 일치한다.
            self.assertEqual(self._stream_content(resp.text), "streamed body")
            self.assertEqual(self.sim.requests, [("model-a", True)])

    def test_e3_usage_kept_when_client_requested_it(self):
        with self._client() as client:
            self.sim.script("model-a", [ok(text="streamed", usage=(11, 4))])
            resp = self._post(client, "sim:model-a", "hello e3", stream=True,
                              stream_options={"include_usage": True})
            self.assertEqual(resp.status_code, 200)
            self.assertIn('"usage"', resp.text)
            self.assertIn("data: [DONE]", resp.text)

    def test_e2_usage_stripped_when_not_requested(self):
        """DESIGN §5.8: 클라이언트가 include_usage를 안 보냈으면 usage 청크 제거.

        회귀 테스트: litellm 1.91.x가 usage 청크에 비어있지 않은 choices(빈 delta)를
        합성해 붙여 choices 유무 기반 strip 조건이 무력화되던 버그 (수정됨 —
        _chunk_has_payload로 실제 payload 판별). FakeProvider는 가리고 실 stack만 드러냄.
        (소스 버그: 아래 BUG #2 주석 참조.)
        """
        with self._client() as client:
            self.sim.script("model-a", [ok(text="streamed")])
            resp = self._post(client, "sim:model-a", "hello e2", stream=True)
            self.assertEqual(resp.status_code, 200)
            # 기대: usage 없음. 실제: usage 청크가 새어나감 → 실패.
            self.assertNotIn('"usage"', resp.text)


# ==========================================================================
# f. TTFT 타임아웃 → failover (§5.8 / §5.13)
# ==========================================================================


class TTFTTimeoutTests(SimulatorScenarioTest):
    def test_f_ttft_timeout_streams_failover(self):
        with self._client() as client:
            # ttft=2초 설정, 첫 청크를 5초 지연 → TTFT 초과 → model-b로 failover
            self.sim.script("model-a", [delay_ttft(5)])
            resp = self._post(client, "auto", "hello f", stream=True)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["x-forge-model"], "sim:model-b")
            self.assertIn("data: [DONE]", resp.text)
            self.assertEqual(self.sim.requests,
                             [("model-a", True), ("model-b", True)])


# ==========================================================================
# g. mid-stream 실패 → SSE error 이벤트, 재시도 없음 (§7)
# ==========================================================================


class MidStreamCutTests(SimulatorScenarioTest):
    def test_g_midstream_failure_yields_error_event_no_retry(self):
        with self._client() as client:
            self.sim.script("model-a", [cut_midstream()])
            resp = self._post(client, "sim:model-a", "hello g", stream=True)
            self.assertEqual(resp.status_code, 200)  # 첫 청크는 이미 나갔으므로 200
            # 첫 정상 청크 후 에러 이벤트가 스트림에 포함된다.
            self.assertIn("par", resp.text)          # 첫 청크 내용
            self.assertIn('"error"', resp.text)      # mid-stream error 이벤트
            # 재시도 없음: model-a만 호출되고 model-b는 시도하지 않는다.
            self.assertEqual(self.sim.requests, [("model-a", True)])


# ==========================================================================
# h. 선제 스로틀 — rpm=2, 요청 3개 연속 시 3번째가 503 throttled (§5.13)
# ==========================================================================


class ThrottleTests(SimulatorScenarioTest):
    def test_h_third_request_throttled_when_rpm_exhausted(self):
        with self._client(rpm=2) as client:
            r1 = self._post(client, "auto", "same prompt")
            r2 = self._post(client, "auto", "same prompt")
            r3 = self._post(client, "auto", "same prompt")

            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            # 후보 provider가 sim 하나뿐 → 버킷 소진 시 후보 없음 → 503 throttled
            self.assertEqual(r3.status_code, 503)
            self.assertIn("throttl", r3.json()["error"]["message"].lower())


# ==========================================================================
# 발견된 소스 버그 (수정 금지 — expectedFailure로 문서화)
# --------------------------------------------------------------------------
# BUG #1  forge_gateway/providers/litellm_provider.py:111-131 (_extract_retry_after)
#   litellm 1.91.1은 RateLimitError의 응답 헤더를 e.response.headers /
#   e.headers가 아니라 e.litellm_response_headers에 담는다. 현재 코드는 앞의
#   두 곳만 보므로 Retry-After가 항상 None → 429 쿨다운이 DESIGN §5.5의
#   "Retry-After 존중" 대신 언제나 기본 300초로 들어간다.
#   재현: test_b2_retry_after_value_is_honored (rate_limit(retry_after=7)).
#
# BUG #2  forge_gateway/api/openai.py:351 (_try_stream의 usage strip 조건)
#   litellm 1.91.1은 usage 전용 청크에도 비어있지 않은 choices 배열
#   (빈 delta choice 1개)을 합성해 붙인다. 그래서
#   `usage and not (chunk.get("choices") or client_wants_usage)` 가 항상
#   거짓이 되어, 클라이언트가 include_usage를 요청하지 않아도 강제 주입된
#   usage 청크(§5.8)가 응답으로 새어나간다. FakeProvider(choices=[])는 이를
#   가리지만 실제 litellm stack은 드러낸다.
#   재현: test_e2_usage_stripped_when_not_requested.
# ==========================================================================


if __name__ == "__main__":
    unittest.main()
