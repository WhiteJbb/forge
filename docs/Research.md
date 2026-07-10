# Research — 기술 조사 기록

> 조사 일자와 출처를 남긴다. 오래된 조사는 재검증 후 사용.

---

## 2026-07-09 — 오픈소스 게이트웨이 경쟁 분석

> 지식 기준: 2026년 초. 상세 논의는 DESIGN.md에 반영 완료.

| 프로젝트 | 성격 | Forge와 겹침 | 없는 것 |
| --- | --- | --- | --- |
| LiteLLM | 사실상 표준 게이트웨이 | failover, 쿨다운, latency/cost 라우팅, 예산, Prometheus | 요청 내용 기반 라우팅 |
| Portkey Gateway | 오픈소스 게이트웨이 | Config 기반 fallback/LB, 가드레일 | 내용 기반 라우팅 |
| RouteLLM (LMSYS) | 학습형 라우터 | 내용 기반 (강/약 모델 선택) | 게이트웨이 기능, 투명성 |
| Arch Gateway | Envoy + 소형 라우터 LLM | 정책 기반 선호 라우팅 (가장 유사) | 코딩 에이전트 특화, 경량성 |
| one-api / new-api | 채널 집계 프록시 | 멀티 프로바이더, 쿼터, 멀티 키 | 내용 기반 라우팅 |
| TensorZero | Rust 게이트웨이 + 실험 | A/B, 피드백 루프 | 코딩 특화 |

**결론**: "게이트웨이 기능 넓이"는 레드오션. **코딩 에이전트 특화 × 내용 인지 × 정책**의 교집합이 빈 공간. 넓이 경쟁 회피, litellm SDK에 프로바이더 커버리지 위임.

## 2026-07-09 — PyPI 패키지명 가용성 조사

방법: `https://pypi.org/pypi/<name>/json` HTTP 상태 코드 (404 = 미등록).

- **선점(200)**: `forge`, `forge-ai`, `llmforge`, `modelforge`, `agentforge`, `codeforge`
- **가용(404)**: `forge-gateway` ✅(권장), `forge-llm`, `forge-router`, `forge-proxy`, `llm-forge`, `ai-forge`, `forgeai`, `aiforge`, `forgegateway`, `forge-gw`
- 주의: PyPI 이름 정규화(`-`/`_`/대소문자 동일 취급). `llm-forge`는 선점된 `llmforge`와 한 글자 차이라 혼동 위험 → 비추천.
- CLI 충돌: Foundry(Ethereum)의 `forge` 바이너리와 PATH 충돌 가능 → `forge-gw` alias 병행.
- 등록 절차: [PUBLISHING.md](../PUBLISHING.md)

## 2026-07-09 — 프로바이더 한도 실측

- NVIDIA 무료 티어(integrate.api.nvidia.com): **40 RPM** (사용자 실측 확인) → forge.yaml `rpm: 40` 기본값 근거

## 2026-07-09 — capability 벤치마크 시드 (DESIGN.md §5.11-1)

forge.yaml의 수기 점수를 공개 벤치마크 기반으로 교체. 주 신호: **SWE-bench Verified/Pro**(에이전트 코딩 — Forge 트래픽과 가장 유사), **LiveCodeBench**(오염 저항). Aider Polyglot 공개 리더보드는 당사 모델 대부분이 미등재라 보조 신호로만.

| 모델 (NVIDIA 서빙) | 확인된 수치 | code 시드 | 조치 |
| --- | --- | --- | --- |
| GLM-5.2 | SWE-Pro **62.1%** (오픈 1위, GPT-5.5 능가), 1M ctx | 10 | context 9→10 |
| DeepSeek V4 Pro | LiveCodeBench **93.5** (전 모델 선두급) | 10 | 유지 |
| Qwen3.5-397B-A17B | SWE-V **76.2%** / SWE-Pro **50.9%** | 9 | debug 9→8 (Pro 격차) |
| Mistral Medium 3.5 | SWE-V **77.6%** (Devstral 2·Qwen3.5 능가), 256K | 9 | code 7→9 상향 |
| DeepSeek V4 Flash | SWE-V **~76–78.4%** | 9 | code 8→9 |
| **MiniMax M3** | SWE-Pro **59.0%** (GPT-5.5·Gemini 3.1 Pro 능가 주장) | 9 | **tier3→tier2 승격**, code 6→9 — 기존 배치가 큰 저평가 |
| Nemotron 3 Ultra | BenchLM 종합 오픈 상위권 (Opus 4.8 뒤) | 9 | code 8→9 |
| Nemotron 3 Super 120B | LCB 81.2 / SWE-V 60.47% / SWE-Multi 45.78% | 7 | 유지 |
| gpt-oss-120b | LCB **88.0** vs SWE-Multi **30.8%** — 대비 극단 | 8 | debug/refactor 하향 (에이전트 작업 약함) |
| Mistral Large 3 / Small 4 / Llama-3.3-Nemotron-49B | 2026-07 자료 미확인 | — | 기존 추정 유지 + 주석 표기 |

**한계**: 벤치별 측정 조건이 달라 절대 비교 불가 — 시드는 상대 순위 근거로만 사용. NVIDIA 서빙판의 실효 컨텍스트/양자화는 미확인이라 context_window 하드 값은 미설정 (초과 시 상향 failover가 자가 보정). M3의 텔레메트리 보정 루프가 이 시드의 오차를 흡수하는 구조.

**출처**: [llm-stats Aider Polyglot](https://llm-stats.com/benchmarks/aider-polyglot), [morphllm SWE-bench Pro 리더보드](https://www.morphllm.com/swe-bench-pro), [BenchLM coding](https://benchlm.ai/blog/posts/best-llm-coding), [Nemotron 3 Super 실측](https://mpp-insights.com/blog/testing-nemotron-3-super-bi-platform), [Mistral Medium 3.5 가이드](https://www.aimadetools.com/blog/mistral-medium-3-5-complete-guide/), [2026-05 벤치 라운드업](https://codersera.com/blog/ai-agent-benchmarks-state-of-leaderboard-may-2026/)

## 2026-07-09 — NVIDIA 무료 티어 실측 속도 (스트리밍 TTFT, 모델당 1회)

| 모델 | TTFT | 총 시간 | 비고 |
| --- | --- | --- | --- |
| glm-5.2 | — | 180s 타임아웃 | 인터랙티브 사용 불가 |
| qwen3.5-397b | — | 180s 타임아웃 | 〃 |
| deepseek-v4-pro | 18.3s | 35.2s | 대형 중 유일한 생존자 |
| deepseek-v4-flash | 8.6s | 8.6s | |
| mistral-medium-3.5 | 19.9s | 24.2s | |
| nemotron-3-super | 2.8s | 2.8s | |
| mistral-small-4 | 0.4s | 0.8s | 최속 |

→ forge.yaml 정책을 신호 기반 계층화로 전환 (일상=고속 3종, 컨텍스트 3만↑/debug=v4-pro).
품질 벤치 순위와 무료 티어 응답성은 별개 축 — 정책이 이 간극을 메꾼다.

## 2026-07-09 — 무료 티어 프로바이더 확장 조사 (Plan.md F1-F3)

사용자 요청("무료로 쓸 수 있는 API들 싹 긁어서") 대응 — 자동 발굴/다중계정이 아니라
공식 문서로 recurring 여부를 확인한 프로바이더만 큐레이션. 판단 기준: "가입 시 1회
지급되는 소진형 크레딧"이 아니라 "사용량과 무관하게 반복 리셋되는 rate-limit 티어"만
`free: true`로 표시.

- **Cerebras**: 공식 확인됨(recurring) — rate-limit 문서가 "capacity replenishes
  continuously"라고 명시. Free Trial 티어 RPM 5 / TPM 30K / TPH 1M / TPD 1M.
  `CEREBRAS_API_KEY`, `https://api.cerebras.ai/v1`.
  출처: [Cerebras rate limits](https://inference-docs.cerebras.ai/support/rate-limits), [quickstart](https://inference-docs.cerebras.ai/quickstart)
- **SambaNova**: 공식 확인됨 — "Free Tier: Applied when there is no payment method
  linked"(만료 없음, 결제수단 미연결 상태가 곧 무료 티어). RPM 20 / RPD 20 / TPD 200K.
  별도로 가입 시 1회성 $5 크레딧(30일 만료)도 있으나 이건 free 플래그 근거로 안 씀.
  `SAMBANOVA_API_KEY`는 공식 문서 미명시, LiteLLM 등 서드파티 관례.
  출처: [rate-limits](https://docs.sambanova.ai/docs/en/models/rate-limits), [api-keys-urls](https://docs.sambanova.ai/docs/en/get-started/api-keys-urls)
- **Gemini (AI Studio)**: 공식 확인됨(recurring) — "RPD quotas reset at midnight Pacific
  time." 정확한 RPM/RPD 수치는 더 이상 문서에 게시되지 않고 AI Studio 대시보드로 위임됨
  (forge.yaml에 rpm 미설정, 기존 groq/mistral 패턴과 동일하게 반응형 쿨다운에 의존).
  OpenAI 호환 base_url `https://generativelanguage.googleapis.com/v1beta/openai/`,
  `GEMINI_API_KEY`(공식, `GOOGLE_API_KEY`와 동시 설정 시 후자 우선).
  출처: [rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits), [openai 호환](https://ai.google.dev/gemini-api/docs/openai), [api-key](https://ai.google.dev/gemini-api/docs/api-key)
- **OpenRouter `:free` 모델**: 명명 규칙 `provider/model:free` 공식 확인. 무크레딧
  계정 하루 50회 / RPM 20, $10 이상 충전 시 하루 1,000회 — "영구 보장" 문구는 없으나
  반복 사용 가능한 구조. 프로바이더 전체를 free로 표시하는 대신
  `registry.merge_discovered`가 discovered 모델 id의 `:free` 접미사를 감지해
  price=(0,0) 처리(하드코딩 목록 대신 규칙화, 카탈로그 변경에 자동 추종).
  출처: [model-variants/free](https://openrouter.ai/docs/guides/routing/model-variants/free), [limits](https://openrouter.ai/docs/api/reference/limits)
- **Z.ai(Zhipu) GLM 직접 API**: 모델 단위로 "무료 모델" 공식 라벨 확인
  (GLM-4.7-Flash, GLM-4-Flash-250414 등, `docs.z.ai`/`docs.bigmodel.cn`). 단
  GLM-4.5-Flash는 EOL(2026-01-30, GLM-4.7-Flash로 자동 라우팅) 확인됨 — "영구 무료"
  보장 문구는 없고 rate limit도 대시보드 전용(JS 렌더링)이라 수치 미확인. 프로바이더
  전체는 유료 모델과 혼재하므로 `free: false`로 등록(discovery 모델은 price 미상 →
  `allow_paid: false`에서 보수적으로 제외). `ZAI_API_KEY`는 공식 문서 미명시, LiteLLM
  관례. base_url `https://api.z.ai/api/paas/v4/`.
  출처: [docs.z.ai glm-4.5](https://docs.z.ai/guides/llm/glm-4.5), [bigmodel model-overview](https://docs.bigmodel.cn/cn/guide/start/model-overview)

## 2026-07-09 — 신규 무료 provider 벤치마크 시드 (사용자 요청, `forge models`/`v1/models`로 실제 discovery id 확인 후 진행)

nvidia에 했던 것과 같은 방식(SWE-bench Verified/Pro, LiveCodeBench 우선, 없으면
Aider Polyglot/Terminal-bench로 대체) — 실제로 discovery된 모델 중 상위 2~3개씩만
큐레이션. 전부 "상대 순위 근거"로만 쓸 것 — 소스마다 reasoning level/harness/pass@k
조건이 달라 절대 비교는 위험(기존 원칙 유지).

- **cerebras:zai-glm-4.7** (GLM-4.7 풀사이즈 355B) — SWE-bench Verified **73.8%**,
  LiveCodeBench V6 **84.9** — [z.ai 공식 문서](https://docs.z.ai/guides/llm/glm-4.7).
  z.ai 자체 무료 모델인 GLM-4.7-Flash(SWE-V 59.2, 아래 참조)보다 훨씬 강한 모델을
  Cerebras가 무료로 서빙 — tier1 최우선 후보. **주의**: Cerebras Preview 상태, 예고
  없이 제거될 수 있음(공식 문서 명시) → self-healing failover가 흡수하지만 리스크로
  기록.
- **cerebras:gpt-oss-120b** / **sambanova:gpt-oss-120b** — nvidia:gpt-oss-120b과
  완전히 동일한 모델(OpenAI 공식 모델카드 SWE-V 52.6%, medium reasoning) — 같은
  capability 시드 재사용. 호스팅사별 속도차는 실측하지 않았으므로 capabilities.speed는
  동일하게 두고, 실제 latency는 §5.5 EWMA(실트래픽 기반)가 알아서 반영.
- **sambanova:DeepSeek-V3.1** (Production) — SWE-bench Verified **66.0%**(2차 자료,
  공식 테크리포트 미대조) — [MarkTechPost](https://www.marktechpost.com/2026/04/12/minimax-just-open-sourced-minimax-m2-7-a-self-evolving-agent-model-that-scores-56-22-on-swe-pro-and-57-0-on-terminal-bench-2/) 등 2차 집계. 보수적으로 tier2 하단 배치.
- **sambanova:MiniMax-M2.7** — SWE-V 78%/SWE-bench Pro 56.2%/Terminal-bench 2.0
  57.0 주장 있으나 전부 2차 집계, 공식 대조 안 됨 — 수치가 매력적이어도 tier2로
  보수적 배치. 공식 테크리포트 원문 대조 전까지 tier1 승격 보류.
- **gemini:models/gemini-3-flash-preview** — SWE-bench Verified **78%**,
  "2.5 시리즈·Gemini 3 Pro 능가"까지 공식 확인 — [Google 공식 블로그](https://blog.google/products/gemini/gemini-3-flash/). LiveCodeBench 90.8%/Aider Polyglot
  75.8%는 2차 집계라 **미확인** 처리, 시드에는 반영 안 함. **주의**: Preview 상태.
- **gemini:models/gemini-3.5-flash** (Stable) — SWE-bench Pro(Public) **55.1%**,
  Terminal-bench 2.1 **76.2%** — [Google DeepMind 공식 모델카드](https://deepmind.google/models/model-cards/gemini-3-5-flash/). SWE-bench Pro는 Verified보다 훨씬
  어려운 벤치라 위 78%(Verified)와 직접 비교 불가 — tier2로 배치.
- **미시드(자료 부족/약함, defaults 유지)**: cerebras:gemma-4-31b(LCB 80%뿐, 2차
  자료만·SWE-V 미확인), sambanova:DeepSeek-V3.2/gemma-4-31B-it(수치 출처마다 상이,
  교차검증 실패), sambanova:Meta-Llama-3.3-70B-Instruct(코딩 벤치마크 미확인),
  gemini의 2.5/3.1 계열(공식 수치 미확보), `-latest` 별칭 전체(가리키는 모델이 시점에
  따라 바뀔 수 있어 시드 대상에서 항상 제외).
- **설계**: forge.yaml에 provider를 미리 선언하지 않고도 시드를 적용하기 위해
  `PROVIDER_CATALOG`에 `capability_seed` 필드를 추가하고 `apply_auto_providers`가
  provider 등록 시 `config.models`에 직접 채워 넣게 함(anthropic의 `default_models`와
  같은 매커니즘, tier/capabilities까지 포함하는 버전). 부수효과: 이렇게 채워진 모델은
  `source == "config"`가 되어 §5.6 능동 헬스 probe 대상에도 포함됨 — 순수 discovery
  모델(probe 제외)과 달리 실트래픽 없이도 대시보드 상태가 주기적으로 갱신된다.

## 2026-07-09 — 정정: SambaNova는 recurring 무료가 아니었음 (사용자 반박으로 발견)

**이전 결론(위 섹션)이 틀렸다.** "결제수단 미연결 시 RPM 20/RPD 20/TPD 200K Free Tier"
문서(docs.sambanova.ai/docs/en/models/rate-limits)를 근거로 `free: true`를 매겼는데,
사용자가 실사용 경험("$5 크레딧 주고 끝인 것 같다")으로 반박해서 재검증했다.

**재검증 결과**: 실제로는 카드 없이 계속 쓸 수 있는 등급이 없다.
- `cloud.sambanova.ai/plans` 공식 페이지: Free 플랜의 유일한 내용은 "$5 in free API
  credits, no credit card required, 30일 만료" — 그 외 recurring 무료 등급 언급 없음.
- `cloud.sambanova.ai/plans/pricing`: $0.00 모델이 하나도 없음, 전 모델 토큰당 유료.
- 커뮤니티 실사용 사례: 크레딧이 0이 되면 `HTTP 402 CREDITS_EXHAUSTED` — 카드 연결
  여부와 무관하게 막힘. ([사례1](https://community.sambanova.ai/t/add-credits-flow-creates-invoices-that-immediately-auto-void-card-never-charged/1665), [사례2](https://community.sambanova.ai/t/i-had-been-using-this-platform-from-many-months-and-paying-every-month-through-cc-now-i-am-getting-error-as-out-of-credits/1629))
- SambaNova 직원이 2025-02 공식 커뮤니티에서 "기존 free tier는 developer tier로
  통합되고 별도로 유지할 계획 없다"고 직접 확인 ([스레드](https://community.sambanova.ai/t/is-free-tier-going-away/847), [블로그](https://sambanova.ai/blog/sambanova-cloud-developer-tier-is-live)).

**틀린 이유(방법론 결함)**: "카드 없이 되는 rate-limit 등급 문서가 존재하는가"만
확인했지, **"그 등급의 크레딧/기간이 소진되면 실제로 어떻게 되는가"를 확인하지
않았다.** rate-limit *등급* 설명과 *과금 여부*는 서로 다른 축인데 이를 혼동함 —
SambaNova의 문서는 RPM/RPD 분류만 설명하고 크레딧 소진 후 동작은 언급하지 않았다.

**후속 조치**: `PROVIDER_CATALOG`에서 sambanova의 `free: True` 제거(다른 유료
provider와 동일하게 취급, `capability_seed`는 모델 품질 순위라서 그대로 유지 —
`allow_paid: false`에서 자동 제외됨). `.env.example`/README 정정.

**교훈**: "카드 불필요"와 "recurring 무료"는 다른 주장이다 — 무료 여부 판정 시
반드시 "소진/만료 후 동작"까지 확인해야 한다.

**Cerebras/Gemini 재검증 결과 (같은 기준으로 즉시 재확인) — 둘 다 문제없음, `free: true` 유지**:
- **Cerebras**: 문서상 명칭은 "Free Trial"이지만 실제 구조는 RPM/TPM/TPH/TPD처럼
  매분·매시간·매일 리셋되는 rate limit뿐 — 총 누적 한도나 만료일이 없음(명칭과 구조가
  불일치하는 경우, SambaNova는 반대로 "Free Tier"라는 이름인데 실제로는 소진형이었음).
  PayGo FAQ의 402 에러는 Team 조직이 구매한 유료 크레딧(1년 만료) 소진 시에만 발생하며
  개인 무료 티어와는 무관. 다만 Cerebras 이용약관 원문 직접 대조는 못해 완전한 확정은
  아님(미확인 여지 남김). 출처: [rate-limits](https://inference-docs.cerebras.ai/support/rate-limits), [PayGo FAQ](https://support.cerebras.net/articles/5041581099-cerebras-self-serve-paygo-faq)
- **Gemini**: 공식 billing 문서가 "신규 계정은 Free Tier로 시작, billing 연결은
  선택적 업그레이드이며 언제든 비활성화해 Free Tier로 복귀 가능"이라고 명시 — 강제
  카드 등록/자동 만료 없음. 2026-04-01부터 Pro 계열이 무료 티어에서 빠지고
  Flash/Flash-Lite만 남은 건 "모델 가용성 변경"이고 티어 자체 소멸은 아님(우리가
  seed한 `gemini-3-flash-preview`/`gemini-3.5-flash`는 Flash 계열이라 영향 없음).
  출처: [billing](https://ai.google.dev/gemini-api/docs/billing), [pricing](https://ai.google.dev/gemini-api/docs/pricing)

**정정 후 상태**: SambaNova만 `free` 플래그 제거, Cerebras/Gemini는 변경 없음.

## 2026-07-10 — 유료 프로바이더 확장 조사 (x.ai / Cohere / Together AI / Fireworks AI)

사용자 요청("다른 유료 API 프로바이더들도 다 인식 가능하도록") 대응. 무료 티어 확장과
달리 실제 과금이 걸리므로, 가격은 각 프로바이더 **공식 pricing 페이지를 1차 소스로
직접 시딩**했다(사용자 결정) — litellm 내장 가격표(3순위 폴백, `core/pricing.py`)를
그냥 신뢰하지 않았다. 확인 못한 항목은 "미확인"으로 남기고 지어내지 않았다.

**조사 방법에 대한 메모(투명성)**: Together AI/Fireworks AI 조사 중 WebSearch 결과
일부가 정상적인 문서 검색 결과가 아니라 전제를 반박하는 듯한 부가 텍스트와 미검증
수치를 끼워 넣는 패턴을 보였다(프롬프트 인젝션 의심). 해당 결과는 전량 폐기하고
공식 문서 원문(`docs.together.ai`, `docs.fireworks.ai`, `huggingface.co` 모델카드
등)을 직접 fetch해서 재검증한 값만 아래에 반영했다.

### x.ai (Grok)
- `api_base`: `https://api.x.ai/v1`, `/v1/chat/completions` OpenAI 호환 확인. 공식 REST
  레퍼런스에 `GET /v1/models`가 없어(chat/completions·responses·deferred-completion
  3개만 문서화) discovery 지원 여부는 **미확인** → 보수적으로 `discovery: false`.
  출처: [API reference](https://docs.x.ai/docs/api-reference), [quickstart](https://docs.x.ai/developers/quickstart)
- 인증: `Authorization: Bearer $XAI_API_KEY`. 출처: 위 quickstart
- 가격(USD/1M tok, input/output): `grok-4.5` $2.00/$6.00(context 500K), `grok-4.3`
  $1.25/$2.50(context 1M), `grok-build-0.1`(에이전틱 코딩 전용) $1.00/$2.00(context 256K).
  출처: [grok-4.5](https://docs.x.ai/developers/models/grok-4.5), [grok-4.3](https://docs.x.ai/developers/models/grok-4.3), [grok-build-0.1](https://docs.x.ai/developers/models/grok-build-0.1)
- 레이트리밋(Tier 0 기본): grok-4.3/grok-build-0.1 계열 RPS 37/TPM 10M, grok-4.5 RPS
  150/TPM 50M — 카탈로그의 `rpm`에는 반영하지 않음(다른 유료 provider와 동일 관례,
  과금 등급에 따라 크게 달라짐). 출처: [rate-limits](https://docs.x.ai/developers/rate-limits)
- 벤치마크: grok-4.5 SWE-bench Pro 64.7% — **xAI 자사 발표, 제3자 미검증**이라
  `capability_seed`에서 tier1로는 반영했지만 참고용 caveat으로 남김. grok-build-0.1은
  독립 벤치마크 수치가 없어 tier2로 보수적 배치.
- 무료 크레딧: 공식 문서에 언급 없음, **미확인**(3차 매체 주장은 채택하지 않음).

### Cohere
- Cohere는 OpenAI SDK를 그대로 쓸 수 있는 **Compatibility API**를 공식 제공한다:
  `https://api.cohere.ai/compatibility/v1`, `/chat/completions`가 streaming/tool
  use/structured output까지 지원. 출처: [compatibility-api](https://docs.cohere.com/docs/compatibility-api)
- 이 경로의 `GET /models`가 OpenAI 포맷으로 동작하는지는 문서에 명시 없음 —
  **미확인** → `discovery: false`, `default_models`로 수동 공급.
- 대표 모델: `command-a-03-2025`(256K ctx), `command-r7b-12-2024`(경량, 128K ctx).
  최신 플래그십 `command-a-plus-05-2026`/`North Mini Code 1.0`은 엔터프라이즈
  문의 전용(`sales@cohere.com`)이라 카탈로그 기본 목록에서 제외.
  출처: [models](https://docs.cohere.com/docs/models), [command-a](https://docs.cohere.com/docs/command-a), [rate-limits](https://docs.cohere.com/docs/rate-limits)
- **가격 미확인**: `cohere.com/pricing` 공식 FAQ 페이지에는 레거시 모델(Command,
  Command R/R+)만 명시돼 있고 현재 플래그십(Command A) 단가는 페이지 구조상 직접
  확인 실패 — 3rd-party(OpenRouter 등)가 일관되게 인용하는 $2.50/$10.00은 1차
  소스 대조가 안 돼 **카탈로그에 반영하지 않음**(litellm 폴백에 위임). 출처:
  [pricing](https://cohere.com/pricing)
- 코딩 벤치마크: 공식 테크리포트(arXiv 2504.00698)가 SWE-Bench 등을 사용했다고
  명시하나 정확한 수치는 PDF 파싱 실패로 **미확인** → `capability_seed` 미부여.
- 레이트리밋: Trial 20 req/min(전 모델 공통), Production 500 req/min(Command
  A/R+/R/R7B). 무료 트라이얼은 크레딧이 아니라 요청 속도 제한 형태.

### Together AI
- `api_base`: `https://api.together.ai/v1`. `/v1/chat/completions`,
  `GET /v1/models` 모두 OpenAI 포맷으로 공식 지원 확인 → discovery 기본값(true) 유지.
  출처: [openai-api-compatibility](https://docs.together.ai/docs/openai-api-compatibility)
- 인증: `Authorization: Bearer $TOGETHER_API_KEY`. 출처: [api-keys](https://docs.together.ai/docs/api-keys-authentication)
- 가격(USD/1M tok): `deepseek-ai/DeepSeek-V4-Pro` $1.74(캐시 $0.20)/$3.48,
  `moonshotai/Kimi-K2.7-Code` $0.95(캐시 $0.19)/$4.00, `Qwen/Qwen3.7-Plus`
  $0.32/$1.28, `meta-llama/Llama-3.3-70B-Instruct-Turbo` $1.04/$1.04. 출처:
  [serverless/models](https://docs.together.ai/docs/serverless/models)
- `capability_seed`는 DeepSeek-V4-Pro만 시딩(SWE-bench Verified 80.6% / SWE-bench
  Pro 55.4% / LiveCodeBench 93.5, 공식 HF 모델카드 대조 — NVIDIA 서빙판과 동일
  모델, 위 2026-07-09 시드와 일관). 출처: [HF model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)
  나머지 모델은 discovery로 자동 등록되므로 별도 시드 불필요.
- 무료 크레딧 없음 — 최소 $5 선불 필요(만료 없음). 고정 RPM 미공개(조직별 동적
  한도). 출처: [billing](https://docs.together.ai/docs/billing-credits), [rate-limits](https://docs.together.ai/docs/rate-limits)

### Fireworks AI
- `api_base`: `https://api.fireworks.ai/inference/v1`, `/chat/completions` OpenAI
  호환 확인. 모델 목록은 계정 스코프 관리 API(`GET /v1/accounts/{id}/models`,
  스키마가 OpenAI `/models`와 다름)뿐이라 discovery **불가** 확정 →
  `discovery: false`. 출처: [openai-compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility), [list-models](https://docs.fireworks.ai/api-reference/list-models)
- 인증: `Authorization: Bearer $FIREWORKS_API_KEY`. 출처: [quickstart](https://docs.fireworks.ai/getting-started/quickstart)
- 가격/모델ID(USD/1M tok, 공식 모델 페이지):
  `accounts/fireworks/models/deepseek-v4-pro` $1.74(캐시 $0.14)/$3.48, SWE-bench
  Verified 80.6%; `accounts/fireworks/models/kimi-k2p6` $0.95(캐시 $0.16)/$4.00,
  SWE-bench Verified 80.2%; `accounts/fireworks/models/qwen3p7-plus`
  $0.40(캐시 $0.08)/$1.60, 코딩 벤치마크 미공개; `accounts/fireworks/models/glm-5p2`
  $1.40(캐시 $0.26)/$4.40, GPQA-Diamond 91.4%(코딩 특화 지표 아님, "강력한
  오픈소스 코딩 모델"은 Fireworks 자체 발표뿐). 출처: [deepseek-v4-pro](https://fireworks.ai/models/fireworks/deepseek-v4-pro), [kimi-k2p6](https://fireworks.ai/models/fireworks/kimi-k2p6), [qwen3p7-plus](https://fireworks.ai/models/fireworks/qwen3p7-plus), [glm-5p2](https://fireworks.ai/blog/glm-5p2)
- SWE-bench 확인된 두 모델(deepseek-v4-pro, kimi-k2p6)만 tier1 + 전체
  capabilities 시딩, qwen3p7-plus/glm-5p2는 가격만 시딩하고 tier/capabilities는
  비워 tier3 기본값에 위임(벤치마크 근거 없이 지어내지 않음).
- 레이트리밋: 결제수단 미등록 10 RPM, 등록+크레딧 보유 시 계정 전체 6,000 RPM(계정
  단위 상한, serverless 전용 별도 한도 아님). 출처: [rate-limits](https://docs.fireworks.ai/guides/quotas_usage/rate-limits)
- 무료 크레딧: 공식 문서에서 확인 못함, **미확인**(3차 매체의 "$1 크레딧" 주장은
  채택하지 않음).

### 카탈로그 반영 요약
`PROVIDER_CATALOG`에 4개 항목 추가 + `capability_seed`에 `price_per_mtok` 선택
필드 신설(`apply_auto_providers`가 `ModelOverride.price_per_mtok`까지 스레딩하도록
확장 — 기존 tier/capabilities 스레딩과 동일 매커니즘). Bedrock/Azure OpenAI는
`ProviderConfig` 계약이 안 맞아(AWS SigV4 자격증명 / api_version + 리소스별
deployment 이름) 이번 라운드에서 제외 — 별도 스키마 확장 작업으로 분리(사용자
결정 2026-07-10, [Plan.md](Plan.md) 참조).

## 조사 예정

- [ ] litellm SDK의 `stream_options` / usage 청크 동작 방식 (M1-6 착수 전)
- [ ] Anthropic Messages ↔ OpenAI 포맷 변환 시 tool use 블록 매핑 (M2-16 착수 전)
- [x] 공개 코딩 벤치마크 소스 확정 → 2026-07-09 시드 완료 (위 섹션)
- [x] OpenRouter/Ollama 무료 한도 실측 → 2026-07-09 공식 문서 확인 완료 (위 섹션, Ollama는 로컬이라 한도 없음)
- [ ] SambaNova/ZAI_API_KEY 등 서드파티 관례 env var명의 공식 확정 여부 재확인 (공식 문서에 명시되면 갱신)
- [ ] MiniMax-M2.7/DeepSeek-V3.1/V3.2 공식 테크리포트 원문 대조 (2차 집계만으로 tier1 승격은 보류 중)
- [ ] cerebras:zai-glm-4.7 / gemini:gemini-3-flash-preview — Preview 제거/변경 시 forge.yaml 시드 정리 필요
