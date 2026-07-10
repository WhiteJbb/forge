# Plan — 현재 구현 계획

> 구현 착수 전 반드시 이 문서를 갱신하고, 이 계획에 따라 구현한다.
> 마일스톤 전체와 설계 근거는 [DESIGN.md](../DESIGN.md) §10이 단일 소스다. 이 문서는 "지금 하는 작업"의 실행 계획만 담는다.
> M1 기록: [WorkLog.md](WorkLog.md) 2026-07-09 항목 (13/13 완료, main `6e99236`).

## 현재 마일스톤: M3 — 플랫폼화 (진행중)

목표: 학습 루프 + 관측 가능성. 범위 결정은 DecisionLog(M3 범위 결정) 참조.
진행 방식: 브랜치 `feat/m3-platform` → 로컬 squash merge.

| # | 작업 | DESIGN.md | 담당 | 상태 |
| --- | --- | --- | --- | --- |
| 27 | **Capability 학습 루프** — 텔레메트리 점수 보정(±2 클램프) + tools feature 자동 강등 | §5.11-3 | 직접 | 완료 |
| 28 | **내장 정적 대시보드** — 단일 HTML SPA, `/dashboard/ui` (Next.js 대체 — 결정 ①) | §5.10 | Opus 위임 | 완료 |
| 29 | **Prometheus `/metrics`** — prometheus-client, JSON은 `/v1/stats` 유지 | §5.7 | Sonnet 위임 | 완료 |
| 30 | PostgreSQL Repository | §5.7 | — | 보류 (결정 ③) |
| 31 | Redis StateStore | §5.2 | — | 보류 (설계: 멀티 인스턴스 시) |
| 32 | 멀티 API 키 로테이션 | §10 | — | 후속 |
| 33 | A/B 테스팅, AI Judge | §10 | — | 후속 (별도 설계) |

완료 기준: 학습 루프가 request_metrics 집계로 유효 점수를 보정하고 임계 초과 시 tools
강등 + 로그, 대시보드가 브라우저에서 상태/추이 시각화, `/metrics`가 Prometheus 포맷 반환.
전체 테스트 통과.

---

## 완료: M2.5 — 정리 스프린트 (main `3138061`)

진행 방식: 브랜치 `feat/m2.5-cleanup` → 로컬 squash merge. 이후 사용자 통합 검증 → M3.

| # | 작업 | 근거 | 담당 | 상태 |
| --- | --- | --- | --- | --- |
| A | 패키지 rename `src` → `forge_gateway` + pyproject.toml (PyPI 배포 가능 상태) | §8.1 | 직접 | 완료 |
| B | capability 벤치마크 시드 — 웹 리서치 → forge.yaml 점수 갱신 (출처 주석) | §5.11-1 | 직접 (웹 검색) | 완료 (MiniMax M3 tier2 승격 포함) |
| C | Provider Simulator — 429/5xx/지연/절단 주입 mock 서버 + 시나리오 테스트 | §9-1 | Opus 위임 | 완료 — **실 litellm 스택 버그 2건 발견·수정** (Retry-After 미반영, usage 청크 누출) |

### M3 후속: UX 스프린트 (진행중 — 사용자 워크스루 피드백)

| # | 작업 | 담당 | 상태 |
| --- | --- | --- | --- |
| U1 | `forge reload` CLI | Sonnet 위임 | 완료 |
| U2 | `forge start` 시작 배너 (대시보드/클라이언트 연결 안내) | 직접 | 완료 |
| U3 | 지출 가드 온보딩 — init 템플릿 주석 + 유료 자동등록 경고 로그 | 직접+Sonnet | 완료 |
| U4 | 최근 요청 피드 — `/v1/stats/recent` + 대시보드 섹션 + explain 실행기 | 직접+Sonnet 위임 | 완료 |
| U5 | **정책 간편 수정** — `forge.local.yaml` 오버레이 + `forge guard` CLI (자동 reload) | 직접+Sonnet | 완료 |

> UX 스프린트 완료 (2026-07-09): 테스트 210건 + 스모크 통과.

### M3 후속: 무료 프로바이더 카탈로그 확장 (진행중)

배경: 사용자 요청 — "무료로 쓸 수 있는 API들 싹 긁어서 넣을 수 있게" (2026-07-09 대화).
방향 결정: 자동 발굴/다중계정 가입(ToS 위반 소지)이 아니라, 공식 문서로 확인된 무료
프로바이더만 큐레이션해서 카탈로그에 추가 — 리서치 근거는 [Research.md](Research.md) 참조.
진행 방식: 브랜치 `feat/free-provider-catalog` → PR → squash merge.

| # | 작업 | 근거 | 상태 |
| --- | --- | --- | --- |
| F1 | PROVIDER_CATALOG에 Cerebras/SambaNova/Gemini 추가 (공식 문서로 recurring 무료 확인) | Research.md 2026-07-09 | 완료 |
| F2 | `registry.merge_discovered`: discovery로 찾은 모델 id가 `:free` 접미사면 provider의 free 플래그와 무관하게 price=(0,0) (OpenRouter 컨벤션, 하드코딩 목록 대신 규칙화) | Research.md 2026-07-09 | 완료 |
| F3 | Zhipu(z.ai) 직접 API 프로바이더 추가 — 프로바이더 전체는 유료 혼재라 `free: false`(discovery 모델은 price 미상으로 안전 취급). forge.yaml에는 넣지 않음(README 철학 "Adding a provider is just an API key" 유지) — 특정 무료 모델을 확정 취급하고 싶으면 사용자가 직접 `models:` 오버라이드를 추가하도록 README에 예시만 안내 | Research.md 2026-07-09 (GLM-4.5-Flash는 이미 EOL 확인, 예시에서 GLM-4.7-Flash만 사용) | 완료 |
| F4 | .env.example / README "자동 인식" 목록 갱신, forge.yaml 주석에 출처+확인일 남기기 | — | 완료 |
| F5 | 카탈로그 확장 회귀 테스트 (`test_settings.py`, `test_registry.py`) | — | 완료 |
| F6 | 사용자 실환경 `forge models`/`GET /v1/models`로 실제 discovery id 확인 → Cerebras/SambaNova/Gemini 상위 모델 벤치마크 리서치 → `PROVIDER_CATALOG`에 `capability_seed` 필드 신설(provider 미선언 상태에서도 tier/capabilities 시드 적용, anthropic의 `default_models`와 같은 매커니즘 확장) | Research.md 2026-07-09 "신규 무료 provider 벤치마크 시드" | 완료 |
| F7 | **정정**: SambaNova `free: true`는 오판정이었음(사용자 반박으로 발견) — 재검증 결과 recurring 무료가 아니라 $5 1회성 트라이얼뿐, 소진 시 402로 완전히 막힘. `free` 플래그 제거(paid 취급, capability_seed는 유지), .env.example/README 정정. 같은 기준으로 Cerebras/Gemini도 재검증 → 둘 다 문제없어 `free: true` 유지 | Research.md 2026-07-09 "정정: SambaNova는 recurring 무료가 아니었음" | 완료 |

완료 기준: 신규 프로바이더가 `forge doctor`/`forge models`에 인식되고, `allow_paid: false`
정책에서 확인된 무료 모델만 통과, 전체 테스트 통과.

> **무료 프로바이더 확장 완료** (2026-07-09): 전체 222건 테스트 통과(신규 6건 포함).
> 부수효과(F6): capability_seed로 채워진 모델은 source="config" 취급되어 능동 헬스
> probe 대상에 포함됨 — 순수 discovery 모델은 실트래픽 없으면 대시보드 상태가
> "unknown"에 머무르는 문제(사용자 리포트)가 이 6개 모델에 대해서는 해소됨.
> 잔여: 사용자가 실키로 Cerebras/SambaNova/Gemini/Zhipu 연동 검증 (사용자 환경).

주의: `free` 플래그는 "결제수단 미연결 시 기본 경로"를 뜻함(기존 NVIDIA 항목과 동일 관례) —
사용자가 나중에 결제수단을 연결하면 실제로는 과금될 수 있다는 점은 README/주석에 명시.

### M3 후속: 공개 배포 전 점검 (완료 — 2026-07-09)

배경: 사용자 질문 "이제 배포해도 될 정도인가?" → PyPI 공개(이미 `forge-gateway` dev
등록됨)를 기준으로 남은 격차를 점검. `docs/ReviewChecklist.md` 보안/프라이버시
섹션을 실제로 코드 대조해 검증.

| # | 작업 | 상태 |
| --- | --- | --- |
| D1 | PR #1(무료 provider 카탈로그 확장) main 머지 | 완료 |
| D2 | CHANGELOG.md에 M3 후속 작업(무료 provider, capability_seed, SambaNova 정정) 반영 | 완료 |
| D3 | 보안/프라이버시 체크리스트 실제 코드 검증(Explore 서브에이전트) — API 키 마스킹, 프롬프트 본문 미저장, `/admin/*` loopback 강제, 안전한 기본값 | 완료 |
| D4 | D3에서 발견한 2건 수정: (a) `health.py`의 probe/list_models 예외 로그 3곳이 `mask_secrets`를 안 거치고 raw 예외 문자열을 찍던 것 — 마스킹 함수를 `providers/base.py`로 옮겨 공유(`litellm_provider.py`와 중복 정의 제거) 후 3곳 모두 적용. (b) `server.host`가 loopback이 아닌데 `FORGE_API_KEY` 미설정이면 아무 경고도 없던 것 — `create_app()`에 시작 시 경고 추가(차단은 안 함, 기존 유료 provider 경고와 같은 패턴) | 완료 |
| D5 | 회귀 테스트 신설(`tests/test_server_security.py`, 5건) | 완료 |

완료 기준: 전체 테스트 통과, 보안 체크리스트 4개 항목 모두 코드로 검증(문서 주장이
아니라), PR 머지.

> **배포 전 점검 완료** (2026-07-09): 전체 228건 테스트 통과(신규 5건 포함). 발견된
> 마스킹 누락 2곳 + 무경고 1건 수정. 상세 근거는 [WorkLog.md](WorkLog.md) 참조.
> PostgreSQL/Redis/멀티 API 키 로테이션은 여전히 DESIGN.md §10 기준 "보류"(단일
> 로컬 사용자 배포에는 불필요) — "공개 오픈소스로 내놓기" 기준 격차는 이번 점검으로 해소.

### M3 후속: 유료 프로바이더 카탈로그 확장 (진행중)

배경: 사용자 요청 — "다른 유료 API 프로바이더들도 다 인식 가능하도록, x.ai라던지" (2026-07-10
대화). 목적은 포괄적 카탈로그 구축(사용자 확인) — 무료 티어 확장과 달리 실제 과금이
걸리므로 가격은 **공식 pricing 페이지 근거로 직접 시딩**하기로 결정(litellm 내장
가격표 신뢰 대신, 사용자 확인) — 근거는 [Research.md](Research.md) 2026-07-10 참조.
진행 방식: 브랜치 `feat/paid-provider-catalog` → PR → squash merge.

AWS Bedrock/Azure OpenAI는 이번 라운드에서 **제외**(사용자 결정, 2026-07-10) — 둘 다
`ProviderConfig`의 "단일 API 키 + api_base" 계약에 안 맞음(Bedrock은 AWS SigV4 3종
자격증명, Azure는 리소스별 커스텀 deployment 이름 + `api_version`이 필요해 카탈로그
자동등록 자체가 구조적으로 어려움). 스키마 확장은 별도 작업으로 분리.

| # | 작업 | 근거 | 상태 |
| --- | --- | --- | --- |
| P1 | `PROVIDER_CATALOG`에 x.ai/Cohere/Together AI/Fireworks AI 추가 — 전부 OpenAI 호환 단일 API 키 패턴(기존 groq/mistral/deepseek와 동일 계약) | Research.md 2026-07-10 | 완료 |
| P2 | `capability_seed`에 `price_per_mtok` 필드 신설 — `apply_auto_providers`가 `ModelOverride.price_per_mtok`까지 스레딩하도록 확장(§5.12 가격 우선순위 1번 경로를 자동등록 모델에도 적용, 기존 tier/capabilities 스레딩과 같은 매커니즘) | — | 완료 |
| P3 | 가격/벤치마크를 공식 1차 소스로 확인 못한 항목(Cohere 전체, Fireworks의 Qwen3.7-Plus/GLM-5.2)은 `capability_seed`를 비우거나 가격만 채우고 tier/capabilities는 비워 litellm 폴백·tier3 기본값에 위임 — 없는 근거를 만들어내지 않음 | Research.md 2026-07-10 | 완료 |
| P4 | `.env.example` / README.md "Adding a provider is just an API key" 목록 / CHANGELOG.md 갱신 | — | 완료 |
| P5 | 카탈로그 확장 회귀 테스트 (`test_settings.py`) | — | 완료 |
| P6 | (별도 작업으로 분리) AWS Bedrock/Azure OpenAI — `ProviderConfig`에 `api_version`, AWS 자격증명 관련 필드 확장 필요. 스키마 계약 자체가 걸린 결정이라 이번 라운드에는 포함하지 않음 | 사용자 결정 2026-07-10 | 보류 |
| P7 | 사용자 실키로 x.ai/Cohere/Fireworks 검증. Cohere/Fireworks 둘 다 discovery가 실제로 동작함을 확인했으나, 반환 목록에 채팅 불가 모달(Cohere: 음성 전사, Fireworks: 이미지 생성)이 섞여 있어 4xx-no-failover 정책과 부딪히는 위험을 확인 → 사용자 결정으로 둘 다 `discovery: false` 유지, 채팅 모델만 수동 큐레이션. Fireworks는 계정 정지도 해결돼 실제 채팅 요청(probe)까지 성공 확인. x.ai는 계정에 크레딧 0(구매 필요)까지 확인. Together는 $5 선불 부담으로 미검증 | Research.md 2026-07-10 "실키 검증" | 완료(검증 가능한 한도까지) |
| P8 | `server.py` 로깅에 Windows 콘솔 유니코드 크래시 방어 추가(`cli.py`에 이미 있던 패턴을 서버 부팅 경로에도 적용) — 근본 원인은 재현 실패로 미확정 | WorkLog.md 2026-07-10 | 완료 |

완료 기준: 4개 신규 프로바이더가 `forge doctor`/`forge models`에 인식되고, 공식 소스로
확인된 가격이 `/v1/models`·비용 계산에 정확히 반영되며, 전체 테스트 통과.

> **진행 상태** (2026-07-10): P1-P8 완료. Fireworks는 실제 채팅 요청까지 성공 검증(연결 +
> 계정 상태 + 실요청 전부 확인). x.ai는 연결은 되지만 계정 크레딧 0(구매 필요). Together
> AI는 $5 선불 요구사항으로 사용자가 키를 만들지 않아 미검증. Cohere/Fireworks 모두
> discovery는 동작하지만 비채팅 모달 혼입 위험 때문에 의도적으로 꺼둔 상태 유지.

### M3 후속: 속도 기반 라우팅 정책 확장 (완료 — 2026-07-11)

배경: 사용자 피드백 — "무료 모델들은 너무 느려서 거의 못써먹겠던데, 신규 모델도
tier 분류할 때 기준을 속도로 잡을까?" `scheduler.py::_score()`를 확인해보니
`capability_seed`의 `speed` 필드가 dead data임을 발견 — tier(10%)와 실측
latency(15%, 2초 이상은 전부 0점)만 실제로 속도에 영향. `tier`를 속도로 재정의하는
대신, 기존 전례(`default` 정책의 `prefer`로 속도를 별도 처리)를 확장하기로 사용자와
결정(DecisionLog.md 2026-07-11 참조). 범위는 신규 유료 4개뿐 아니라 기존 무료
프로바이더(nvidia/cerebras/gemini/sambanova)까지 포함하기로 사용자가 확장.

| # | 작업 | 근거 | 상태 |
| --- | --- | --- | --- |
| S1 | Cerebras/Gemini/SambaNova의 capability_seed 모델을 실키로 직접 스트리밍 호출해 TTFT + content/reasoning 글자 수 측정(reasoning 토큰과 혼동 방지) | Research.md 2026-07-11 | 완료 |
| S2 | x.ai(grok-4.5/grok-build-0.1), Together(deepseek-v4-pro) 속도는 실키가 없어 Artificial Analysis 3rd-party 벤치마크로 조사 | Research.md 2026-07-11 | 완료 |
| S3 | `forge.yaml`의 `default`/`heavy-work`/`hard-tasks` 정책 `prefer` 순서를 실측 데이터로 재작성. `default`는 "무료 먼저, 안 되거나 느리면 유료 빠른 모델로"(사용자 확정), `heavy-work`/`hard-tasks`는 무료 v4-pro 우선 유지 + 유료 고속 호스팅을 쿨다운 대체용으로 추가 | DecisionLog.md 2026-07-11 | 완료 |
| S4 | Together는 사용자 환경에 키가 없어 정책에 넣으면 매 요청마다 경고만 반복 — 데이터는 문서화하되 실제 prefer 목록에는 미포함(키 생기면 추가) | Research.md 2026-07-11 | 완료(의도적 보류) |
| S5 | `PolicyEngine.plan()`을 직접 호출해 새 prefer 목록이 경고 없이 전부 실제 등록 모델로 resolve되는지 확인(`default`/`heavy-work`/`hard-tasks` 전부) | — | 완료 |

완료 기준: 세 정책의 `prefer` 목록이 실측 속도 순서를 반영하고, 경고 없이 전부
resolve되며, 전체 테스트 통과.

> **완료** (2026-07-11): Gemini "Flash" 계열이 실측 TTFT 16~19초로 이름과 무관하게
> 느리다는 것과, `deepseek-v4-pro`가 NVIDIA(무료·18초) 대비 Fireworks/Together(유료)
> 에서 10배 이상 빠르다는 게 이번에 새로 확인됨. `speed` 필드를 실제 스코어링에
> 반영하거나 latency 스코어 구간을 세분화하는 건 별도 개선 과제로 남김(DecisionLog 참조).

> **M2.5 완료** (2026-07-09): 전체 153건 테스트 3회 연속 통과, editable install + `forge` CLI 동작.
> **다음: 사용자 통합 검증** — 실키(NVIDIA)로 `forge doctor`/`forge start` + Cline(OpenAI) +
> Claude Code(`ANTHROPIC_BASE_URL`) 실연동. 검증 후 M3(Dashboard, Prometheus, PostgreSQL) 착수.

---

## 완료: M2 — 지능 계층 (main `8ec11aa`)

진행 방식: 브랜치 `feat/m2-intelligence` → 로컬 squash merge (PR 생략 — 임시, DecisionLog 참조).

| # | 작업 | DESIGN.md | 담당 | 상태 |
| --- | --- | --- | --- | --- |
| 14 | **Policy Engine** — 스키마·평가기·scheduler 연동·constraints | §5.4 | 직접 | 완료 |
| 15 | 세션 고정 | §5.5-1 | — | M1에서 선행 구현됨 |
| 16 | **`/v1/messages` Anthropic 호환** — 포맷 변환(tool use·스트리밍 이벤트) | §5.8 | Opus 위임 | 완료 (Claude Code 실연동은 사용자 환경) |
| 17 | Analyzer 개선 (힌트 채널·구조 신호·가중) | §5.3 | — | M1에서 선행 구현됨 |
| 18 | **선제 rate limiting** — provider별 token bucket + 세마포어 | §5.13 | Opus 위임 | 완료 |
| 19 | Auto Discovery 배선 (부팅 + reload) | §5.2 | — | 완료 |
| 20 | `/admin/reload` 핫 리로드(원자적 교체·health 보존) | §5.8 | 직접 | 완료 (/admin/provider는 reload로 대체 안내) |
| 21 | Ollama/OpenRouter/Anthropic 프로바이더 검증 | §5.1 | 사용자 환경 | 구성 지원만 (실검증은 키/로컬 필요) |
| 22 | 벤치마크 기반 capability 시드 | §5.11-1 | 보류 | 별도 리서치 필요 |
| 23 | **가격표 배선** — litellm.model_cost 폴백 + 비용 계산 | §5.12 | Sonnet 위임 | 완료 |
| 24 | **`/v1/route/explain`** 드라이런 | §5.8 | 직접 | 완료 |
| 25 | **CLI** — `forge start/init/doctor/models` | §8.1 | Sonnet 위임 | 완료 (pyproject 패키징은 PyPI 등록 시점으로 분리) |
| 26 | 테스트 하네스 — Provider Simulator | §9 | 후속 | 보류 — FakeProvider 통합 테스트가 실패 경로를 커버 중, 시뮬레이터는 별도 작업으로 |

> **M2 핵심 완료** (2026-07-09): unittest 141건 + 스모크 24항목 통과. 잔여: #21(사용자 환경),
> #22(리서치), #26(후속). PRD 정책 예시 중 `provider unavailable → fallback`은 별도 when 조건
> 없이 Scheduler 가용성 필터가 동일 효과를 냄 — 명시적 조건이 필요해지면 후속 추가.

### 완료 기준

- 정책 YAML로 PRD의 4개 예시 정책이 전부 동작 (기본 정책 미정의 시 M1과 동일한 tier 라우팅 = 하위 호환)
- `/v1/messages`로 논스트리밍+스트리밍+tool use 왕복 변환 (Claude Code 실연동 스모크는 사용자 환경)
- 스로틀: rpm 소진 시 429 없이 다른 provider로 분산되는 통합 테스트
- 전체 unittest 통과 + 스모크 통과

### 리스크

- Anthropic 스트리밍 이벤트 변환 충실도 (§11) — 변환 모듈 단위 테스트로 방어, 실연동은 후속
- reload의 원자성 — in-flight 요청은 구 참조로 완주, 신규는 새 참조 (지연 close)
