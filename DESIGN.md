# Forge 설계서

> Intelligent AI Gateway for Coding Agents — 아키텍처 & 상세 설계
>
> 기준 문서: [prd.md](prd.md) / 기준 코드: v0.1 (`src/`)

---

## 1. 설계 목표

PRD의 핵심 차별점은 **"사용자가 모델을 선택하지 않는다"**이다. 이를 실현하는 축은 세 가지다.

1. **OpenAI 호환 단일 진입점** — 모든 코딩 에이전트(Cline, Claude Code, Continue, Aider…)가 `localhost:4000` 하나만 바라본다.
2. **Policy Engine 중심 라우팅** — "어떤 모델"이 아니라 "어떤 정책"을 관리한다. LiteLLM/Portkey와의 차별점.
3. **자가 치유 스케줄링** — 헬스/레이턴시/쿨다운/실패율을 실시간 반영해 최적 모델로 자동 이동.

이 설계서는 (a) 현재 코드의 진단, (b) 목표 아키텍처, (c) 컴포넌트별 상세 설계, (d) 마일스톤 계획을 담는다.

---

## 2. 현재 코드베이스 진단 (v0.1)

### 구현된 것

| 컴포넌트 | 파일 | 상태 |
| --- | --- | --- |
| OpenAI 호환 API (`/v1/chat/completions`, `/v1/models`) | [server.py](src/server.py) | 동작 |
| Request Analyzer (키워드 기반 task 분류) | [analyzer.py](src/analyzer.py) | 동작 |
| Scheduler (스코어링 + tier failover) | [scheduler.py](src/scheduler.py) | 동작 |
| Health Monitor (30초 주기 ping) | [health_monitor.py](src/health_monitor.py) | 동작 |
| Metrics (SQLite 요청 로그 + 일일 집계) | [metrics.py](src/metrics.py) | 동작 |
| Tier/Capability 정의 | [config.py](src/config.py) | 하드코딩 |

### 구조적 한계 (이번 설계가 해결할 것)

1. **프로바이더가 NVIDIA 하드코딩** — [server.py:117](src/server.py#L117)의 `get_api_base_for_model()`이 상수를 반환. `requirements.txt`에 litellm이 있지만 `src/`에서는 전혀 사용하지 않고, [config.yaml](config.yaml)은 별도 LiteLLM 프록시(`run_litellm.bat`)용으로 이원화되어 있다. → **Provider 추상화 계층** 도입.
2. **정책이 코드에 박혀 있음** — tier 순서, capability 매트릭스가 [config.py](src/config.py)에 하드코딩. PRD의 YAML Policy Engine이 없다. → **forge.yaml 단일 설정 + Policy Engine**.
3. **스트리밍 failover 부재** — [server.py:352](src/server.py#L352) `_stream_response`는 첫 모델이 429/5xx를 반환해도 에러 바디를 그대로 스트림으로 흘려보낸다. 논스트리밍만 failover가 된다. → **first-byte 이전 failover**.
4. **쿨다운 의미가 PRD와 다름** — PRD는 "429 발생 → 즉시 5분 쿨다운"인데 현재는 연속 실패 3회에서만 진입([scheduler.py:60](src/scheduler.py#L60)). 헬스체크 429는 쿨다운에 반영조차 안 됨.
5. **헬스체크 비용** — 모델 12개 × 30초마다 실제 completion 호출. 무료 티어의 rate limit을 헬스체크가 소모하고, 429를 유발하는 원인이 된다.
6. **메트릭 기록이 요청 경로를 블로킹** — async 핸들러 안에서 동기 sqlite `INSERT` + `COMMIT` ([metrics.py:84](src/metrics.py#L84)). 부하 시 이벤트 루프가 멈춘다.
7. **게이트웨이 자체 인증 없음** — `0.0.0.0:4000`에 무인증 노출. 프로바이더 API 키를 얹어 쓰는 프록시이므로 최소한의 키 검증 필요.
8. **잔재 코드** — [server.py:123](src/server.py#L123) `forward_to_provider`는 미사용 데드 코드.
9. **tool calling 인지 부재** — 요청의 `tools`/`response_format`/vision 요구를 보지 않고 라우팅한다. function calling이 안 되는 모델로 에이전트 요청이 가면 조용히 망가진다 — 코딩 에이전트 게이트웨이에서 가장 치명적인 실패 모드. → **기능 플래그 하드 필터** (§5.5).
10. **세션 무시 라우팅** — 매 요청 독립 선택이라 같은 대화가 모델 사이를 오간다. 프로바이더 프롬프트 캐시가 매번 미스나고 에이전트 행동 일관성이 깨진다. → **세션 고정** (§5.5).
11. **Anthropic 포맷 미지원** — Claude Code는 OpenAI 포맷이 아니라 Anthropic Messages 포맷으로 통신하므로 현재 구조로는 붙일 수 없다 (PRD의 클라이언트 목록과 모순). → **`/v1/messages`** (§5.8).
12. **커넥션 풀 미활용** — 요청마다 `httpx.AsyncClient`를 새로 생성해 TLS 핸드셰이크를 반복한다. → litellm SDK의 공유 클라이언트로 해소 확인 (§5.1).

---

## 3. 목표 아키텍처

```text
                        ┌──────────────────────────────┐
                        │         AI Clients           │
                        │ Cline / Claude Code / Aider… │
                        └──────────────┬───────────────┘
                                       │ OpenAI API (+ Forge API Key)
                                       ▼
┌───────────────────────────────────────────────────────────────────┐
│                          API Layer (FastAPI)                      │
│  /v1/chat/completions  /v1/messages(Anthropic)  /v1/embeddings    │
│  /v1/models  /health  /metrics  /v1/stats  /admin/*  /dashboard   │
└──────────────┬────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────┐     ┌───────────────────────────────┐
│     Request Analyzer     │     │        Policy Engine          │
│  task / 토큰 추정 / 언어  │────▶│  forge.yaml 정책 평가          │
└──────────────────────────┘     │  → 후보 모델 집합 + 제약        │
                                 └──────────────┬────────────────┘
                                                ▼
                                 ┌───────────────────────────────┐
                                 │          Scheduler            │
                                 │  후보 스코어링 → 모델 선택      │
                                 │  failover 루프 관리            │
                                 └──────────────┬────────────────┘
          상태 조회/기록                          ▼
┌────────────────────────┐       ┌───────────────────────────────┐
│     Model Registry     │◀─────▶│      Provider Layer           │
│  모델 메타데이터 SoT     │       │  LiteLLM SDK 어댑터            │
│  health/cooldown 상태   │       │  NVIDIA/OpenRouter/Ollama/    │
└───────┬────────────────┘       │  Anthropic (설정 기반)         │
        │                        └───────────────────────────────┘
        │ 갱신
┌───────┴────────────────┐       ┌───────────────────────────────┐
│  Health Monitor        │       │  Metrics Engine               │
│  passive 우선 + 보조 probe│      │  write-behind 큐 → SQLite/PG  │
│  Auto Discovery        │       │  Prometheus exporter          │
└────────────────────────┘       └───────────────────────────────┘
```

**요청 처리 흐름 (chat completion):**

```text
1. 인증 (FORGE_API_KEY, 설정 시)
2. 포맷 정규화: Anthropic 요청(/v1/messages)이면 내부 표준 포맷으로 변환
3. Analyzer: messages → { task, 토큰 추정, required_features, session_key }
   (힌트 우선순위: X-Forge-Task 헤더 / auto:task 별칭 > 구조 신호 > 키워드)
4. Policy Engine: (task, 요청 model, 클라이언트, 제약) → 후보 모델 순서 목록
5. 하드 필터: required_features·컨텍스트 창을 못 맞추는 모델을 후보에서 제거
6. 세션 고정: session_key에 고정된 모델이 후보에 있고 가용하면 즉시 선택
7. Scheduler 루프 (최대 max_attempts):
   a. 후보 스코어링 → 최고점 선택 (동률권 내 랜덤)
   b. Provider Layer로 전달 (litellm.acompletion)
   c. 성공 → 8로  |  429/5xx/timeout → 실패 기록, 쿨다운 판정, 고정 이동, 다음 후보
8. Metrics 큐에 기록 (비동기), forge 메타데이터 헤더 첨부,
   Anthropic 요청이면 응답 역변환 후 반환
```

---

## 4. 목표 디렉터리 구조

```text
forge/
├── forge.yaml               # 단일 설정 파일 (providers/models/policies/settings)
├── requirements.txt
├── src/
│   ├── server.py            # FastAPI 앱 조립 + lifespan만 (얇게)
│   ├── api/
│   │   ├── openai.py        # /v1/chat/completions, /v1/embeddings, /v1/models
│   │   ├── anthropic.py     # /v1/messages (Anthropic 포맷 ↔ 내부 표준 변환)
│   │   ├── admin.py         # /admin/reload, /admin/provider, /admin/cooldown
│   │   ├── observe.py       # /health, /metrics(prom), /v1/stats, /dashboard
│   │   └── auth.py          # Forge API Key 검증 미들웨어
│   ├── core/
│   │   ├── analyzer.py      # Request Analyzer
│   │   ├── policy.py        # Policy Engine
│   │   ├── scheduler.py     # 스코어링 + 선택 (failover 루프는 api/openai.py)
│   │   ├── registry.py      # Model Registry (모델 메타 + 상태의 SoT)
│   │   └── health.py        # Health Monitor + Auto Discovery
│   ├── providers/
│   │   ├── base.py          # Provider 프로토콜
│   │   └── litellm_provider.py
│   ├── storage/
│   │   ├── base.py          # MetricsRepository 프로토콜
│   │   ├── sqlite_repo.py
│   │   └── postgres_repo.py # M3
│   └── settings.py          # forge.yaml 로더 + pydantic 검증 + hot reload
├── dashboard/               # Next.js (M3)
└── tests/
```

원칙: **`config.py`의 하드코딩(TIERS, CAPABILITY_MATRIX, TASK_KEYWORDS)은 전부 `forge.yaml`로 이동**하고, 코드는 스키마 검증과 기본값만 가진다.

---

## 5. 컴포넌트 상세 설계

### 5.1 Provider Layer — LiteLLM SDK 어댑터

**결정: 별도 LiteLLM 프록시 프로세스를 버리고, Forge 프로세스 안에서 litellm Python SDK(`litellm.acompletion`)를 호출한다.**

- 이유: 프록시 이원화(`run_litellm.bat` + `config.yaml`)는 설정 중복과 홉 추가만 만든다. SDK를 쓰면 NVIDIA/OpenRouter/Anthropic/Ollama의 인증·요청 변환·스트리밍을 한 번에 얻고, Forge가 라우팅 주도권을 유지한다.
- LiteLLM의 자체 라우터/fallback 기능은 **사용하지 않는다** (`num_retries=0`). failover는 Forge Scheduler의 책임 — 이중 재시도로 인한 지연 폭주를 막는다.

```python
class Provider(Protocol):
    name: str                                   # "nvidia", "openrouter", ...
    async def chat(self, model: str, payload: dict, stream: bool): ...
    async def embeddings(self, model: str, payload: dict): ...
    async def list_models(self) -> list[str]:   # Auto Discovery용
    async def probe(self, model: str) -> ProbeResult: ...
```

- 구현체는 `LiteLLMProvider` 하나로 충분: `forge.yaml`의 provider 항목(`api_base`, `api_key_env`, `litellm_prefix`)을 받아 인스턴스화.
- **모델 ID 네임스페이스**: 클라이언트에 보이는 ID는 `provider/model` 그대로 유지하되, Registry가 `forge_id → (provider, provider_model_id)` 매핑을 소유한다. 같은 모델이 두 프로바이더에 있으면 서로 다른 엔트리(예: `nvidia:deepseek-v4-pro`, `openrouter:deepseek-v4-pro`)로 등록해 프로바이더 단위 failover가 자연스럽게 된다.
- **파라미터 호환성**: `drop_params=True` 기본 — 프로바이더가 지원하지 않는 OpenAI 파라미터(`logit_bias`, `logprobs` 등)는 드롭하고 경고 로그를 남긴다. 미지원 파라미터로 400을 그대로 돌려주면 사용자에게는 "Forge가 고장"으로 보인다.
- **reasoning 필드 정규화**: thinking 모델의 `reasoning_content` 같은 비표준 필드가 응답/스트림에 섞이면 일부 클라이언트 파서가 깨진다. 기본은 제거(strip), `providers[].pass_reasoning: true`로 통과 허용.
- **업스트림 에러 충실도**: 프로바이더 에러를 OpenAI 에러 포맷(`{"error": {"message", "type", "code"}}`)으로 정규화해 반환 — 클라이언트가 사용자에게 의미 있는 메시지를 보여줄 수 있어야 한다.
- **커넥션 풀 재사용**: 현재 코드처럼 요청마다 클라이언트를 만들지 않는다 — litellm SDK의 공유 클라이언트 사용을 M1에서 확인.

### 5.2 Model Registry — 상태의 단일 소스

현재 `TIERS` 전역 + `Scheduler.model_health` dict로 분산된 상태를 하나로 합친다.

```python
@dataclass
class ModelEntry:
    id: str                    # "nvidia:z-ai/glm-5.2"
    provider: str
    provider_model_id: str
    tier: str                  # tier1/2/3
    capabilities: dict[str, int]   # code/debug/refactor/docs/context/speed
    features: set[str]             # {"tools","parallel_tools","json_mode","vision","streaming"}
    context_window: int | None
    price_per_mtok: tuple[float, float] | None   # (input, output) USD/1M tok, None=unknown (§5.12)
    source: Literal["config", "discovered"]
    health: ModelHealth        # 기존 scheduler.ModelHealth 이관
```

- Scheduler, Health Monitor, Dashboard, Policy Engine 모두 Registry만 바라본다.
- **Auto Discovery**: 부팅 시 + `/admin/reload` 시 각 provider의 `list_models()` 호출 → 미등록 모델은 `capabilities=기본값(전부 7)`, `features=defaults.features`, `tier=tier3`, `source="discovered"`로 등록. discovery로는 tool calling 지원 여부를 알 수 없으므로 설정 기본값으로 시작하고, 실트래픽에서 tool 실패가 반복되면 자동 강등한다(§5.11). 설정에 명시된 모델이 discovery에 없으면 `unavailable` 마킹(제거하지 않음 — 일시적 목록 누락 대비).
- 상태 저장은 **인메모리가 기본**. 멀티 인스턴스는 M3에서 Redis `StateStore` 인터페이스로 확장(§5.8).

### 5.3 Request Analyzer

Analyzer는 **힌트 생산자**이고 결정은 Policy/Scheduler가 한다. 실제 에이전트 트래픽(거대 시스템 프롬프트 + 짧은 유저 메시지 + tool 결과 덩어리)에서 키워드 매칭 단독으로는 오분류가 잦으므로, 신호를 3계층으로 쌓는다.

- 출력: `{ task, confidence, est_prompt_tokens, required_features, session_key, language }`

**task 판정 — 신호 우선순위 (위가 이김):**

1. **클라이언트 명시 힌트** — `X-Forge-Task: refactor` 헤더 또는 model 이름 규약(`auto:refactor`, `auto:docs`). 에이전트는 자기 작업 유형을 이미 알고 있는 경우가 많다(Aider의 architect/code/ask 모드 등) — 추측보다 선언 채널이 정확하다. `/v1/models`에 `auto`, `auto:debug`, `auto:docs` 등 별칭을 노출해 클라이언트 설정 UI에서 선택할 수 있게 한다.
2. **구조적 신호** — 키워드보다 신뢰도 높음: diff/patch 블록 존재(→ refactor/debug 계열), 코드 블록 비율, `response_format` 지정, 메시지 수(긴 세션 = 진행 중인 작업 연속).
3. **키워드 매칭** — **마지막 user 메시지에 가중치** (전체 join은 긴 대화에서 과거 task에 끌려감). 시스템 프롬프트는 분석 대상에서 제외.

**required_features 추출** (§5.5 하드 필터의 입력):

- `tools` 필드 존재 → `tools` 요구 (+ `parallel_tool_calls` 시 `parallel_tools`)
- `response_format: json_*` → `json_mode`
- 이미지 콘텐츠 파트 → `vision`
- `est_prompt_tokens` (`len(content) / 3.5` 근사) → 컨텍스트 창 요구

**session_key 추출** (§5.5 세션 고정의 입력): 요청의 `user` 필드가 있으면 그 값, 없으면 시스템 프롬프트 + 첫 user 메시지의 해시. 코딩 에이전트는 같은 세션에서 이 프리픽스가 불변이므로 안정적인 키가 된다.

**LLM 분류 폴백** (M3+, 기본 off): confidence < 0.6일 때 tier3 소형 모델로 1-shot 분류 (`analyzer.llm_fallback: true`). 레이턴시/비용이 추가되므로 최후 수단.

### 5.4 Policy Engine — 핵심 차별점

**평가 모델: 위에서 아래로 first-match. 매칭된 정책이 후보 집합과 제약을 결정하고, Scheduler는 그 안에서만 스코어링한다.**

`forge.yaml`의 정책 스키마:

```yaml
policies:
  - name: docs-prefer-writer
    when:
      task: [documentation]
    route:
      prefer: [ "nvidia:qwen/qwen3.5-397b-a17b", tier2 ]   # 모델ID 또는 tier 혼용
      fallback: [ tier3 ]

  - name: long-context
    when:
      min_prompt_tokens: 60000
    route:
      prefer: [ context_window: ">=128000" ]                # 속성 셀렉터

  - name: free-only            # 전역 제약 (when 없음 = 항상 적용, constraints만)
    constraints:
      allow_paid: false

  - name: default              # 마지막 정책 = 기본 라우팅
    route:
      prefer: [ tier1 ]
      fallback: [ tier2, tier3 ]
```

- `when` 조건 필드: `task`, `model`(클라이언트가 보낸 model 파라미터), `client`(User-Agent 패턴), `min/max_prompt_tokens`, `provider_unavailable`.
- `route.prefer / fallback`: 모델 ID, tier 이름, 속성 셀렉터를 섞을 수 있는 **후보 그룹의 순서 목록**. Scheduler는 그룹 순서대로 시도한다 (그룹 내에서는 스코어 경쟁).
- `constraints`: `allow_paid`, `max_cost_per_request`, `exclude_providers` — **매칭 여부와 무관하게 항상 누적 적용**되는 하드 필터. (`when` 없는 정책은 제약 전용.)
- 클라이언트가 실제 모델 ID를 지정하면(`"model": "nvidia:z-ai/glm-5.2"`) 정책보다 우선하되 constraints는 여전히 적용. `"coder"`/`"auto"` 같은 별칭은 정책 라우팅으로 위임하고, `"auto:refactor"`처럼 task가 붙은 별칭은 Analyzer의 task 판정을 강제한 뒤 정책 라우팅한다(§5.3).

이 구조로 PRD의 4개 예시 정책이 전부 표현 가능하다.

### 5.5 Scheduler

선택 파이프라인은 3단계: **하드 필터 → 세션 고정 → 스코어링**.

**0) 하드 필터 — 스코어링 이전에 부적합 모델 제거.**

- `required_features ⊄ model.features` → 제외. tool 스키마가 포함된 요청이 function calling이 안 되는 모델로 가면 에이전트가 조용히 망가진다 — 코딩 에이전트 게이트웨이에서는 429보다 치명적인 실패 모드다.
- `est_prompt_tokens > 0.9 × context_window` → 제외 (아래 ContextFit 점수와 별개의 하드 컷).
- 필터로 후보 그룹이 비면 정책의 다음 fallback 그룹으로 진행하고, 전 그룹 소진 시 사유를 명시해 반환한다 (예: `400 "no candidate model supports: tools"`).

**1) 세션 고정 (sticky routing).**

코딩 에이전트는 같은 거대 프리픽스(시스템 프롬프트 + 파일 컨텍스트)를 매 턴 재전송한다. 요청마다 모델이 바뀌면 프로바이더 프롬프트 캐시가 매번 미스나고(비용·레이턴시 손해), 대화 도중 모델이 바뀌어 에이전트 행동 일관성이 깨진다.

- `session_key → model` 매핑을 인메모리 LRU로 유지 (TTL 기본 30분, 사용 시 갱신).
- 고정 모델이 (a) 이번 정책 후보에 포함, (b) 가용, (c) 하드 필터 통과 조건을 모두 만족하면 **스코어링 없이 그대로 선택**.
- failover 발생 시 고정을 새 모델로 이동 — 세션 도중 모델 교체는 failover에서만 일어난다.
- 설정: `scheduler.session_affinity: true`(기본), `session_ttl_minutes: 30`.

**2) 스코어링.**

스코어 공식은 현재 구현을 다듬어 PRD 공식의 누락 항(Context, Priority)을 채운다. 모든 부분 점수는 0~10 정규화.

```text
Score = 0.30·Capability(task)      # capability matrix
      + 0.15·Health               # healthy=10 / unknown=5 / cooldown·unhealthy=0
      + 0.15·Latency              # EWMA 기반, 100ms→10점 ~ 2000ms→0점
      + 0.10·Availability         # 최근 성공률 (슬라이딩 윈도)
      + 0.10·ContextFit           # est_tokens ≤ 0.5·ctx → 10, ≥ 0.9·ctx → 0
      + 0.10·TierPriority         # tier1=10, tier2=6, tier3=3
      - 0.10·FailurePenalty       # 연속 실패 ×2 (상한 10)
      (cooldown 모델은 스코어링 대상에서 제외 — 별도 패널티 항 불필요)
```

- **레이턴시는 EWMA** (`ewma = 0.3·new + 0.7·old`)로 전환 — 현재는 마지막 값 하나라 스파이크에 취약. **스트리밍 요청은 TTFT(첫 토큰까지 시간)를 레이턴시 신호로 사용**한다 — 총 소요 시간은 출력 길이에 좌우되어 라우팅 신호로 부적합하고, 코딩 에이전트의 체감 속도는 TTFT가 결정한다. 논스트리밍은 총 레이턴시 유지.
- **Availability는 슬라이딩 윈도(최근 50건)** — 현재의 누적 전체 실패율은 과거 장애가 영구 낙인이 됨.
- 동률권(최고점의 90% 이상) 내 랜덤 선택은 유지 — 사실상의 least-busy 분산 효과.
- **Cooldown 규칙 (PRD 정합화)**:
  - `429` → **즉시 쿨다운**. 기간 = `Retry-After` 헤더가 있으면 그 값, 없으면 기본 300초.
  - `5xx`/`timeout` → 연속 3회에 쿨다운 (현행 유지).
  - 쿨다운 만료 → `unknown` 복귀, 다음 probe나 실요청 성공으로 `healthy` 복귀.
- failover 루프의 `max_attempts`는 3 고정이 아니라 `min(설정값, 후보 수)`로.

### 5.6 Health Monitor — passive 우선으로 경량화

현재의 "12모델 × 30초 completion ping"은 rate limit 자해 행위다. 전략을 뒤집는다.

1. **Passive가 1차 신호**: 실제 트래픽의 성공/실패/레이턴시가 헬스의 기본 소스 (이미 `record_success/failure`로 구현됨).
2. **Active probe는 보조**: "최근 N분(기본 5분)간 트래픽이 없는 모델"만 probe. 전체 모델을 한 번에 치지 않고 **스태거링**(주기 내 분산)한다. probe는 `max_tokens=1` completion 유지 (NVIDIA는 모델별 `/models` 필터가 없어 실호출이 유일한 확인 수단).
3. **Provider 레벨 체크 분리**: `GET /v1/models`는 프로바이더 생사 확인용(모델별이 아님). 프로바이더가 죽으면 소속 모델 전체를 `unavailable`로.
4. probe 429는 **쿨다운 진입으로 처리하지 않고** `rate_limited` 마킹만 — probe가 실트래픽 기회를 뺏으면 안 된다.

### 5.7 Metrics & Storage

- **write-behind 큐**: 요청 경로에서는 `asyncio.Queue.put_nowait()`만 하고, 백그라운드 태스크가 배치로 flush (100건 또는 1초). 이벤트 루프 블로킹 제거.
- **Repository 패턴**: `MetricsRepository` 프로토콜에 `record_batch / today_summary / range_summary`. SQLite 구현이 기본, PG는 M3에서 구현체 추가만.
- `daily_summary` 테이블의 read-modify-write 패턴은 `INSERT ... ON CONFLICT ... DO UPDATE` (UPSERT)로 교체 — 배치 flush와 결합 시 경쟁 조건 제거.
- 스키마 보강: `request_metrics`에 `request_id`, `provider`, `status_code`, `attempt`(failover 몇 번째였는지), `ttft_ms`(스트리밍 첫 청크까지 시간) 컬럼 추가. failover 체인 분석과 체감 속도 분석이 가능해진다.
- `datetime.utcnow()` → `datetime.now(timezone.utc)` (3.12 deprecated).
- **Prometheus (M3)**: `/metrics`는 표준 관례대로 Prometheus 텍스트 포맷으로 전환하고, 현재의 JSON 응답은 `/v1/stats`로 이동. 카운터(`forge_requests_total{model,provider,status}`), 히스토그램(`forge_request_latency_seconds`, `forge_request_ttft_seconds`), 게이지(`forge_model_health`)면 대시보드 요구 충족.

### 5.8 API Layer

| 엔드포인트 | 설명 | 마일스톤 |
| --- | --- | --- |
| `POST /v1/chat/completions` | OpenAI 호환, 스트리밍 포함 failover | M1 |
| `POST /v1/messages` | Anthropic Messages 호환 — Claude Code용 (`ANTHROPIC_BASE_URL`) | M2 |
| `POST /v1/embeddings` | litellm 경유, 라우팅은 embedding 가능 모델로 한정 | M1 |
| `GET /v1/models` | Registry 기반 (별칭 `coder`, `auto` 포함) | M1 |
| `POST /v1/route/explain` | 드라이런 — 실제 호출 없이 task 판정·정책 매칭·필터 탈락 사유·스코어표 반환. 정책 디버깅의 유일한 수단 | M2 |
| `GET /health` | 게이트웨이+모델 요약 | 유지 |
| `GET /v1/stats` | 현 `/metrics` JSON 이동 | M1 |
| `GET /metrics` | Prometheus 포맷 | M3 |
| `GET /dashboard` | 대시보드 데이터 JSON (Next.js가 소비) | 유지 |
| `POST /admin/reload` | forge.yaml 핫 리로드 + re-discovery | M2 |
| `POST /admin/provider` | 런타임 프로바이더 추가 | M2 |
| `POST /admin/cooldown/{model}/clear` | 수동 쿨다운 해제 (운영 편의) | M2 |

**인증**: `FORGE_API_KEY` 환경변수가 설정되면 `/v1/*`와 `/admin/*`에 Bearer 검증. 미설정 시 무인증(로컬 개발 모드) — 단, `/admin/*`은 loopback 접속만 허용. `/v1/messages`는 Anthropic 관례인 `x-api-key` 헤더도 수용한다.

**Anthropic Messages API**: Claude Code는 OpenAI 포맷이 아니라 Anthropic 포맷으로 통신하므로(`ANTHROPIC_BASE_URL=http://localhost:4000`), OpenAI 호환 엔드포인트만으로는 PRD의 클라이언트 목록이 성립하지 않는다. `/v1/messages`로 받은 요청을 내부 표준 포맷으로 변환해 동일한 Analyzer→Policy→Scheduler 파이프라인에 태우고, 응답을 Anthropic 포맷(스트리밍은 `message_start`/`content_block_delta` 등 이벤트 시퀀스)으로 역변환한다. 프로바이더별 변환은 litellm이 담당하므로 Forge는 입구/출구 양 끝단만 책임진다. tool use 블록과 스트리밍 이벤트 매핑이 가장 까다로우므로, **실제 Claude Code를 붙인 스모크 테스트를 M2 완료 조건**으로 한다.

**스트리밍 failover (중요)**:

```text
후보 루프:
  litellm.acompletion(stream=True) 호출
  → 첫 청크(또는 200 확정) 수신 전 에러/429/5xx/TTFT 타임아웃(§5.13) ⇒ 실패 기록, 다음 후보로 (클라이언트는 모름)
  → 첫 정상 청크 수신 후 ⇒ 이 모델로 확정, 이후 에러는 SSE error 이벤트로 전달 (mid-stream 재시도 불가)
```

**스트리밍 usage 강제 수집**: OpenAI 스트리밍은 기본적으로 `usage`를 반환하지 않는다. 코딩 에이전트 트래픽은 거의 전부 스트리밍이므로, 이대로면 비용 계산(§5.12)과 학습 루프(§5.11)의 입력 데이터 대부분이 빈다. Forge는 업스트림 요청에 `stream_options: {include_usage: true}`를 항상 주입하고, 클라이언트가 원래 요청하지 않았다면 마지막 usage 청크를 클라이언트 응답에서 제거한다.

**응답 메타데이터**: 현재 응답 body에 `forge` 필드를 주입하는데, 엄격한 OpenAI 클라이언트 파서가 깨질 수 있다. **HTTP 헤더로 이동**: `X-Forge-Model`, `X-Forge-Tier`, `X-Forge-Task`, `X-Forge-Attempt`. (스트리밍에서도 헤더는 전달 가능.)

### 5.9 설정 체계 — forge.yaml 단일화

```yaml
version: 1                           # 설정 스키마 버전 — 향후 breaking change 마이그레이션용
server: { host: 127.0.0.1, port: 4000 }   # 외부 바인딩은 명시 설정 + API 키 필수 (§8.3)
auth: { api_key_env: FORGE_API_KEY }

providers:
  - name: nvidia
    litellm_prefix: openai          # litellm 호출 형식
    api_base: https://integrate.api.nvidia.com/v1
    api_key_env: NVIDIA_API_KEY
    discovery: true
    free: true                      # 무료 티어 — 소속 전 모델 가격 (0,0) (§5.12)
    rpm: 40                         # NVIDIA 무료 티어 실측 한도 40 RPM (확인됨) — 선제 스로틀 (§5.13)
    max_concurrent: 8
  - name: ollama
    litellm_prefix: ollama
    api_base: http://localhost:11434
    discovery: true

models:                              # capability/tier 오버라이드 (discovery 결과에 병합)
  - id: "nvidia:z-ai/glm-5.2"
    tier: tier1
    capabilities: { code: 10, debug: 10, refactor: 10, docs: 8, context: 9, speed: 9 }
    features: [tools, parallel_tools, json_mode, streaming]
    context_window: 200000

defaults:
  capability: 7
  tier: tier3
  features: [tools, streaming]       # discovery 모델 기본값 — tool 실패 반복 시 자동 강등 (§5.11)

scheduler:
  cooldown_seconds: 300
  max_attempts: 4
  latency_ewma_alpha: 0.3
  session_affinity: true
  session_ttl_minutes: 30

timeouts:                            # §5.13 타임아웃 3단 예산
  connect: 5
  ttft: 30
  total_deadline: 600

metrics:
  retention_days: 30                 # 원본 로그 보존 기간 — 초과분은 일일 집계만 유지

analyzer:
  llm_fallback: false                # confidence < 0.6일 때 소형 모델 분류 (기본 off)

health:
  probe_idle_minutes: 5
  probe_timeout: 10

policies:
  # §5.4 스키마
```

- pydantic으로 스키마 검증, 실패 시 부팅 중단(명확한 에러 메시지).
- `/admin/reload`: 파일 재파싱 → 검증 통과 시에만 원자적 교체(실패 시 기존 설정 유지). Registry는 diff 적용 — 기존 모델의 health 상태는 보존.

### 5.10 Dashboard (M3)

- ~~Next.js + Tailwind + Recharts 별도 앱~~ → **FastAPI가 서빙하는 내장 정적 SPA(단일 HTML)로 변경** (2026-07-09 결정, DecisionLog) — pip 설치만으로 동작해야 하는 도구에 Node 툴체인 요구는 채택 장벽. `/dashboard/ui`에서 서빙, `/dashboard` + `/v1/stats` JSON만 소비 (백엔드 결합 없음).
- 화면: ① Provider/모델 상태 보드 (tier별, 쿨다운 타이머) ② 요청 추이/성공률/레이턴시 차트 ③ 정책 뷰어(현재 forge.yaml 정책 시각화) ④ 최근 failover 이벤트 로그.
- M1~M2 동안은 기존 JSON 엔드포인트로 충분하므로 착수하지 않는다.

### 5.11 Capability 점수의 수명주기 — 시드에서 학습으로

손으로 적은 capability 점수는 신모델이 나올 때마다 낡는다 (현재 [config.py](src/config.py)의 점수도 근거 없는 수기 값이다). 점수를 3단계 수명주기로 관리한다.

1. **시드 (M2)** — 공개 코딩 벤치마크(Aider polyglot leaderboard, LiveCodeBench, SWE-bench 계열)에서 초기값을 산정해 forge.yaml에 기록. 각 점수에 출처·산정일 주석을 남겨 갱신 시점을 추적 가능하게 한다.
2. **관측 (M1~)** — `request_metrics`에 (model, task_type)별 실패율, failover 유발률(`attempt > 1` 비율), tool 포함 요청의 실패율(`had_tools`)을 축적. 별도 테이블 없이 기존 테이블 집계로 계산한다.
3. **보정 (M3)** — 유효 점수 = `base + clamp(telemetry_delta, ±2)`. 보정 폭을 ±2로 제한해 일시적 장애가 capability 판단 자체를 뒤집지 않게 한다. 같은 신호로 **feature 자동 강등**도 수행한다: tool 포함 요청의 실패율이 임계치를 넘는 모델은 `tools` feature를 제거하고 대시보드에 알린다 (미검증 discovery 모델의 안전장치).

이 루프가 PRD의 "AI Judge"보다 싸고 현실적인 대체재다 — 별도 평가 호출 없이 쓸수록 라우팅이 좋아지고, 판단 근거가 전부 자기 트래픽이므로 사용자 환경(주 언어, 에이전트 종류)에 자동 적응한다.

### 5.12 가격표와 비용 계산

현재 코드의 `cost` 컬럼은 계산 로직이 없어 항상 0이다 — 가격표 없이는 `max_cost_per_request`/`allow_paid` 정책도 공수표다. 비용을 실제 데이터로 만든다.

**가격 소스 (우선순위 순):**

1. forge.yaml 모델 항목의 `price_per_mtok: [input, output]` (USD, 100만 토큰당)
2. provider 레벨 `free: true` — 소속 전 모델을 (0, 0)으로 (NVIDIA 무료 티어, Ollama 로컬)
3. litellm 내장 가격표(`litellm.model_cost`) 조회
4. 전부 실패 → **unknown**

**정책과의 상호작용:**

- `allow_paid: false` → 가격이 (0, 0)으로 **확인된** 모델만 허용. unknown은 보수적으로 제외한다 — "무료인 줄 알았는데 과금"이 최악의 실패 모드이므로.
- `max_cost_per_request` → 하드 필터 단계(§5.5-0)에서 **사전 추정치**로 판정: `est_prompt_tokens × input단가 + max_tokens × output단가`.

**기록:** 응답의 `usage` 토큰 수로 사후 실비를 계산해 `request_metrics.cost`에 저장하고 `daily_summary`로 합산한다. 대시보드의 "Today Cost"가 비로소 실제 값이 된다. (스트리밍 usage 수집은 §5.8 — 이것 없이는 이 절 전체가 공수표다.)

### 5.13 트래픽 신뢰성 — 타임아웃·취소·선제 스로틀링

**타임아웃 3단 예산.** 단일 `request_timeout: 600` 하나로는 부족하다.

| 타임아웃 | 기본값 | 역할 |
| --- | --- | --- |
| `connect` | 5s | 죽은 프로바이더를 빠르게 포기하고 failover — 600초를 기다리지 않는다 |
| `ttft` | 30s | 스트리밍에서 첫 토큰 전이면 클라이언트에 아무것도 안 보냈으므로 failover 가능 (§5.8의 짝) |
| `total_deadline` | 600s | failover 전부를 합친 요청 전체 상한 — 후보를 4번 돌아도 클라이언트가 무한정 기다리지 않게 |

**클라이언트 취소 전파.** 에이전트 사용자는 생성 중 취소를 빈번히 한다(Cline 태스크 중단 등). 클라이언트 연결이 끊기면(FastAPI disconnect 감지) 진행 중인 업스트림 요청을 즉시 취소한다 — 전파하지 않으면 유료 프로바이더에서는 토큰이 계속 과금되고 무료 티어에서는 rate limit 슬롯을 낭비한다. 취소된 요청은 `cancelled`로 기록하되 모델 실패로 집계하지 않는다.

**선제적 rate limiting.** 429 후 쿨다운은 사후약방문이다 — 무료 티어의 RPM 한도는 대체로 알려져 있으므로 한도 직전에서 스스로 조절한다.

- provider 설정의 `rpm`, `max_concurrent` → token bucket + 세마포어.
- 버킷이 비면 해당 프로바이더의 모델을 일시적으로 후보에서 제외(쿨다운이 아니라 스로틀) — 429를 맞기 전에 트래픽이 다른 프로바이더로 분산된다.
- 429 리액티브 쿨다운(§5.5)은 안전망으로 유지. 무료 티어 조합 최적화가 Forge의 소구점인 만큼 이 이중화가 차별점이 된다.

**graceful shutdown.** SIGTERM/Ctrl-C 시: 신규 요청 거부(503) → 진행 중 요청 drain(상한 30s) → metrics 큐 flush → 종료. write-behind 큐(§5.7)가 유실되지 않기 위한 조건.

**기타 방어선.** 요청 바디 크기 상한(기본 20MB — 에이전트의 대형 컨텍스트 감안). 부팅 직후 전 모델이 unknown인 콜드 스타트는 tier1 한정 워밍업 probe로 해소.

---

## 6. 데이터 모델 (SQLite → PG 공용 스키마)

```sql
CREATE TABLE request_metrics (
    id            INTEGER PRIMARY KEY,          -- PG: BIGSERIAL
    request_id    TEXT NOT NULL,                -- failover 체인 묶음 키
    timestamp     TEXT NOT NULL,                -- UTC ISO8601
    model         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    tier          TEXT,
    task_type     TEXT,
    attempt       INTEGER DEFAULT 1,
    latency_ms    REAL,
    ttft_ms       REAL,                          -- 스트리밍 첫 청크까지 시간 (논스트리밍은 NULL)
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    had_tools     INTEGER DEFAULT 0,             -- tool 포함 요청 여부 (§5.11 학습 루프 입력)
    success       INTEGER NOT NULL,
    status_code   INTEGER,
    error_type    TEXT,
    cost          REAL DEFAULT 0.0
);
CREATE INDEX idx_rm_ts ON request_metrics(timestamp);
CREATE INDEX idx_rm_model_ts ON request_metrics(model, timestamp);

-- daily_summary는 유지하되 UPSERT로 갱신 (§5.7)
```

**보존 정책**: `request_metrics` 원본 행은 `metrics.retention_days`(기본 30일) 경과 시 백그라운드 태스크가 삭제하고, `daily_summary` 집계만 영구 보존한다 — 로컬 SQLite의 무한 성장 방지.

`model_overrides` 테이블(M2, 선택): 대시보드에서 capability를 수정하면 yaml 대신 DB에 저장하는 용도. M2까지는 forge.yaml이 유일한 오버라이드 소스.

---

## 7. 에러 처리 · Failover 정책 요약

| 상황 | 즉시 조치 | 상태 반영 | 클라이언트 |
| --- | --- | --- | --- |
| 429 | 다음 후보로 | **즉시 쿨다운** (Retry-After 존중) | 투명 |
| 5xx | 다음 후보로 | 연속 3회 시 쿨다운 | 투명 |
| timeout / connect error | 다음 후보로 | 연속 3회 시 쿨다운 | 투명 |
| 400 `context_length_exceeded` | **상향 failover** — 더 큰 컨텍스트 창 후보로 | 해당 모델의 실효 컨텍스트 추정 하향 보정 | 투명 |
| 그 외 4xx (429 제외) | **failover 안 함** | 실패 기록만 | 에러 그대로 반환 (요청 자체 문제) |
| 스트리밍 mid-stream 에러 | 재시도 불가 | 실패 기록 | SSE error 이벤트 |
| 후보 소진 | — | — | 503 + 시도 내역 |
| 클라이언트 연결 끊김 | **업스트림 즉시 취소** (§5.13) | `cancelled` 기록 — 실패로 집계 안 함 (모델 잘못이 아님) | — |

4xx를 failover하지 않는 이유: 잘못된 요청은 어느 모델로 가도 실패하며, 후보 전체를 태우는 낭비가 된다. (현행 코드도 동일 — 유지.) 유일한 예외가 컨텍스트 초과다 — 토큰 추정(§5.3)은 근사치라 하드 필터를 통과하고도 실제로는 초과할 수 있고, 이 실패만은 더 큰 컨텍스트 창의 모델에서 성공한다. 에러 바디의 코드를 파싱해 이 경우만 상향 failover하고, 그 모델의 실효 컨텍스트 추정치를 하향 보정한다(§5.11 관측과 연계).

---

## 8. 배포와 개발자 경험 (OSS)

"설치 5분"이 안 되면 라우팅 엔진이 아무리 좋아도 채택되지 않는다.

### 8.1 패키징과 CLI

- 배포 채널: PyPI 패키지(`pipx install <패키지명>` 한 줄 설치) + Docker 이미지/compose. `run_forge.bat`/`run_litellm.bat`은 폐기 (Windows 전용 + LiteLLM 프록시 이원화의 잔재).
  - PyPI 확인 결과(2026-07): `forge`, `forge-ai`, `llmforge`, `modelforge`, `agentforge`, `codeforge`는 **선점**. `forge-gateway`, `forge-llm`, `forge-router`, `llm-forge`는 **가용** — `forge-gateway` 확정 권장, 공개 전 조기 등록(스쿼팅 방지). 등록 절차는 [PUBLISHING.md](PUBLISHING.md) 체크리스트 참고. 브랜드는 Forge 유지.
  - CLI 명령 `forge`는 Foundry(Ethereum 툴킷)의 `forge` 바이너리와 PATH 충돌 가능 — README에 명시하고 충돌 시 대안 alias(`forge-gw`) 제공.
- CLI 서브커맨드:
  - `forge start` — 서버 기동
  - `forge init` — 대화형 forge.yaml 생성 (감지된 환경변수 API 키 기반으로 프로바이더 제안)
  - `forge doctor` — 키 유효성·프로바이더 연결·discovery 진단 (이슈 리포트의 절반을 예방하는 도구)
  - `forge models` — 현재 Registry와 모델 상태 출력

### 8.2 문서 = 제품

- README에 **클라이언트별 연동 스니펫**: Cline / Aider / Continue / Claude Code 각각의 베이스 URL·모델명(`auto`) 설정법. 게이트웨이 OSS 이슈의 절반은 "X 클라이언트에서 어떻게 붙여요"다.
- **LICENSE 파일 (MIT 또는 Apache-2.0)** — 라이선스 없는 저장소는 법적으로 사용·포크 불가라 OSS로서 존재하지 않는 것과 같다. 최우선 항목.
- CHANGELOG + semver, GitHub Actions CI (lint / test / PyPI·Docker release).

### 8.3 프라이버시와 안전 기본값

- **기본 바인딩 `127.0.0.1`** — 무인증 + LAN 노출(`0.0.0.0`)이 기본값이면 공개 즉시 보안 이슈감이다. 외부 바인딩은 명시 설정 + `FORGE_API_KEY` 설정을 필수로 강제.
- **텔레메트리 없음 + 프롬프트 미저장을 README에 명시** — 메트릭은 수치만 저장하며 프롬프트/응답 본문은 저장하지 않는다. 디버그용 본문 로깅은 opt-in(`logging.capture_bodies: true`) + 로컬 파일 한정. 개발자 도구에서 이 한 줄이 신뢰를 만든다.
- API 키는 환경변수로만 — forge.yaml에 키 문자열이 직접 들어 있으면 부팅 시 경고. 로그·대시보드·에러 응답에서 키 마스킹.
- 구조화 로깅: PRD 스택대로 structlog로 통일(현재는 stdlib logging), 모든 로그에 `request_id` 상관관계.

---

## 9. 테스트 전략

게이트웨이는 실패 경로가 제품이다 — 실패를 재현할 수 없으면 회귀를 잡을 수 없다.

1. **Provider Simulator** — 설정으로 429(Retry-After 포함)/5xx/지연/TTFT 지연/mid-stream 절단을 주입하는 mock OpenAI 서버. failover·쿨다운·스로틀·스트리밍 시나리오를 CI에서 결정론적으로 검증하는 핵심 도구.
2. **클라이언트 golden fixture** — 실제 Cline/Aider/Claude Code가 보내는 요청(tool 스키마, 거대 시스템 프롬프트, Anthropic 포맷)을 녹화해 Analyzer·포맷 변환·하드 필터의 회귀 테스트로 사용.
3. **단위 테스트 우선순위**: Policy Engine 평가기(표 기반), Scheduler 스코어링·하드 필터, 쿨다운 상태 전이, Anthropic ↔ 내부 포맷 변환.
4. **메트릭 격리 검증**: DB 잠김/디스크 풀에서도 요청 경로가 죽지 않는지 — 메트릭 기록 실패는 로그만 남기고 삼킨다.

---

## 10. 마일스톤 계획

### M1 — 기반 재정렬 (PRD v0.2 완성, ~1주)

코드 구조를 목표 아키텍처로 옮기고 알려진 결함을 잡는다. 기능 외형은 거의 동일.

1. 디렉터리 재구성 (§4) + `forward_to_provider` 등 데드 코드 제거
2. Provider Layer: LiteLLM SDK 어댑터 도입 — `drop_params`, reasoning 정규화, 에러 포맷 정규화, 공유 커넥션 풀 (§5.1). NVIDIA를 설정 기반으로 전환 (`run_litellm.bat`/`config.yaml` 폐기)
3. Model Registry 도입 — `features` 필드 포함 (Scheduler/HealthMonitor가 Registry 소비)
4. forge.yaml 로더 + 검증 (TIERS/CAPABILITY_MATRIX 이동, `version` 필드, 기본 바인딩 `127.0.0.1`)
5. **요구 기능 하드 필터** (§5.5-0) — tools/json_mode/vision/컨텍스트 창 부적합 모델 제거
6. 스트리밍 failover (first-byte 이전) + **usage 강제 수집** (`stream_options`, §5.8)
7. 쿨다운 규칙 PRD 정합화 (429 즉시 + Retry-After) + `context_length_exceeded` 상향 failover (§7)
8. **타임아웃 3단 예산 + 클라이언트 취소 전파** (§5.13)
9. Metrics write-behind 큐 + UPSERT + 스키마 보강 (`had_tools`, `ttft_ms`) + 기록 실패 격리 + 보존 정책 + graceful shutdown flush — 스트리밍은 TTFT를 레이턴시 신호로 (§5.5)
10. `FORGE_API_KEY` 인증, forge 메타데이터 헤더 이동, 로그·응답 키 마스킹
11. `/v1/embeddings`
12. Health Monitor passive-우선 전환 + 스태거링 + 콜드 스타트 tier1 워밍업
13. **LICENSE + README 클라이언트 연동 문서** (§8.2) — 공개 가능 조건

### M2 — 지능 계층 (PRD v0.3, ~2주)

14. **Policy Engine** (§5.4) — 스키마, 평가기, 테스트
15. **세션 고정 (sticky routing)** (§5.5-1) — 프롬프트 캐시 적중 + 대화 내 모델 일관성
16. **`/v1/messages` Anthropic 호환** (§5.8) — Claude Code 실연동 스모크 테스트가 완료 조건
17. Analyzer 개선 (§5.3) — 구조적 신호, `X-Forge-Task`/`auto:task` 힌트 채널, 마지막 user 메시지 가중
18. **선제적 rate limiting** (§5.13) — provider별 `rpm`/`max_concurrent` token bucket
19. Auto Discovery 배선 (부팅 + reload 시, features 기본값 부여)
20. `/admin/reload` 핫 리로드 (원자적 교체), `/admin/provider`, 쿨다운 수동 해제
21. Ollama / OpenRouter / Anthropic 프로바이더 검증
22. 벤치마크 기반 capability 초기값 시드 (§5.11-1)
23. 가격표 배선 + 비용 계산 (§5.12) — `max_cost_per_request`/`allow_paid` 정책의 전제 조건
24. **`/v1/route/explain`** 드라이런 (§5.8) — 정책 디버깅 도구
25. **CLI + 패키징** (§8.1) — `forge init/doctor/models`, PyPI/Docker
26. **테스트 하네스** (§9) — Provider Simulator + 클라이언트 golden fixture

### M3 — 플랫폼화 (PRD v1.0 일부)

27. **Capability 학습 루프** (§5.11-3) — 텔레메트리 점수 보정 + feature 자동 강등 (AI Judge의 실용 대체)
28. Next.js Dashboard
29. Prometheus exporter (`/metrics` 전환, JSON → `/v1/stats`)
30. PostgreSQL Repository 구현체
31. Redis StateStore (멀티 인스턴스 필요 시에만)
32. 멀티 API 키 로테이션 — 같은 프로바이더에 복수 키를 등록해 무료 티어 한도를 곱함 (스로틀 버킷을 키 단위로 확장, §5.13)
33. A/B 테스팅, AI Judge — 별도 설계 후 착수

### 미룬 것과 이유

- **Redis**: 단일 인스턴스 로컬 게이트웨이에서는 인메모리로 충분. 인터페이스만 뚫어두고 구현은 멀티 인스턴스 요구가 생길 때.
- **AI Judge / A/B**: 라우팅 품질 데이터(M1~M2의 메트릭)가 쌓여야 평가 기준을 세울 수 있음. 그 전 단계로 §5.11 학습 루프가 같은 목적을 더 싸게 달성한다.
- **Kubernetes / Plugin SDK**: v1.0 범위, 본 설계서 범위 밖.

---

## 11. 리스크 및 열린 결정

| 항목 | 내용 | 현재 입장 |
| --- | --- | --- |
| litellm SDK 무게 | import 비용·의존성이 큼. httpx 직접 구현 대비 트레이드오프 | 멀티 프로바이더 요구가 명확하므로 채택. 문제 시 Provider 프로토콜 뒤라 교체 가능 |
| task 분류 정확도 | 키워드 방식은 에이전트의 장문 프롬프트에서 오분류 가능 | 1차 방어는 클라이언트 힌트 채널(§5.3) — 명시 힌트 > 구조 신호 > 키워드. 오분류 시에도 default 정책으로 수렴 |
| Anthropic 변환 충실도 | tool use 블록·스트리밍 이벤트 매핑이 까다로움 | Claude Code 실연동 스모크 테스트를 M2 완료 조건으로 (§5.8) |
| capability 점수 노후화 | 수기 점수는 신모델이 나올 때마다 낡음 | 벤치마크 시드 → 텔레메트리 보정 수명주기로 관리 (§5.11) |
| 선제 스로틀 한도값 | 무료 티어 RPM 한도는 문서화가 부실하고 유동적 | 보수적 기본값 + 429 리액티브 쿨다운을 안전망으로 이중화 (§5.13) |
| PyPI 패키지명 | `forge`는 선점 확인 (2026-07) | `forge-gateway` 가용 확인 — 조기 등록 권장 (§8.1) |
| NVIDIA 무료 티어 rate limit | 헬스 probe조차 부담 | passive-우선 + 스태거링으로 완화, probe 주기 설정화 |
| 응답 메타데이터 위치 | body 주입은 호환성 리스크 | 헤더로 이동 확정, body 주입은 `debug` 설정에서만 |
| 별칭 모델명 | 클라이언트가 `gpt-4o` 등 임의 모델명을 보낼 수 있음 | 알 수 없는 모델명은 `auto`로 간주하고 정책 라우팅 (거부하지 않음) |
