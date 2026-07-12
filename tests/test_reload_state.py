"""핫 리로드 상태 보존 회귀 테스트 (DESIGN.md §5.9, DecisionLog 2026-07-12)

reload는 Deps 스냅샷을 통째로 교체하되(원자성), 운영 상태 — 모델 health,
세션 고정, 스로틀 버킷 잔량 — 를 이관해야 한다. 이관이 깨지면 `forge guard`
한 번에 프롬프트 캐시 적중이 무너지고 rpm 버킷이 리셋돼 한도를 일시 초과한다.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from forge_gateway.server import create_app

CONFIG_TEXT = """version: 1
auto_providers: false
providers:
  - name: dummy
    litellm_prefix: openai
    api_base: http://127.0.0.1:1/v1
    api_key_env: DUMMY_KEY
    discovery: false
    free: true
    rpm: 10
models:
  - id: "dummy:model-a"
    tier: tier1
metrics:
  db_path: "{db_path}"
"""


class ReloadStatePreservationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("FORGE_API_KEY", None)
        os.environ.pop("DUMMY_KEY", None)

        db_path = (Path(self._tmpdir) / "m.db").as_posix()
        cfg_path = Path(self._tmpdir) / "forge.yaml"
        cfg_path.write_text(CONFIG_TEXT.format(db_path=db_path), encoding="utf-8")
        self.app = create_app(str(cfg_path))
        self.ref = self.app.state.forge_deps_ref

    def _reload(self):
        # lifespan 없이 라우트만 구동 — reload_fn은 start 전 stop을 허용한다
        client = TestClient(self.app)
        resp = client.post("/admin/reload")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_reload_swaps_snapshot_atomically(self):
        old = self.ref.current
        self._reload()
        new = self.ref.current
        self.assertIsNot(new, old)  # 스냅샷 통째 교체
        self.assertIs(new.metrics, old.metrics)  # 장수 컴포넌트는 재사용 (api/deps.py)
        self.assertIs(new.analyzer, old.analyzer)

    def test_reload_preserves_session_pins(self):
        old = self.ref.current
        old.scheduler._affinity.pin("sess-1", "dummy:model-a")
        self._reload()
        self.assertEqual(
            self.ref.current.scheduler._affinity.get("sess-1"), "dummy:model-a")

    def test_reload_preserves_throttle_bucket_tokens(self):
        old = self.ref.current
        for _ in range(3):
            self.assertIsNotNone(old.throttle.acquire("dummy"))
        self._reload()
        snap = self.ref.current.throttle.snapshot()["dummy"]
        self.assertEqual(snap["rpm"], 10)
        self.assertEqual(snap["tokens_remaining"], 7)  # 소모분 이관 (가득 리셋 금지)

    def test_reload_preserves_model_health(self):
        old = self.ref.current
        old.registry.get("dummy:model-a").health.record_success(123.0)
        self._reload()
        health = self.ref.current.registry.get("dummy:model-a").health
        self.assertEqual(health.status, "healthy")


if __name__ == "__main__":
    unittest.main()
