# Work Log — 작업 진행 내역

> 진행한 작업, 발생한 오류와 수정 내역을 세션 단위로 기록한다. 최신이 위.

---

## 2026-07-12 — 전면 분석 + F1–F4 스프린트 (feat/contracts-v2, feat/scoring-v2)

### 배경

사용자 요청으로 코드베이스 전면 분석 + 6개월 로드맵을 작성([Roadmap.md](Roadmap.md))
하고, 최고난이도 4개 작업(F1–F4)을 즉시 실행. 멀티 인스턴스(StateStore/Redis)는
사용자 결정으로 로드맵에서 제외(DecisionLog). 계약·감사·설계는 직접, 명세가 명확한
구현은 Opus/Sonnet 서브에이전트에 파일 소유권을 나눠 병렬 위임.

### 한 일

1. **전면 분석** (병렬 탐색 3트랙 + 직접 검증): 보안 2건 발견 — 시크릿 마스킹
   정규식이 신규 8개 프로바이더 키 형식 미커버(`providers/base.py` `_SECRET_RE`),
   CORS `*`+credentials 조합. 둘 다 로드맵 S1(Opus)에 배정 — 이번 스프린트 범위 아님.
2. **F1a — Deps 불변 스냅샷** (`api/deps.py` 신설): reload가 `deps.*` 필드를 순차
   대입해 요청이 신/구 컴포넌트를 섞어 볼 수 있던 창을 제거 — 컴포넌트 전체를 새로
   조립한 뒤 `DepsRef.current` 참조 하나만 대입. API 모듈들의 openai.py 허브 결합도
   해소(deps.py 분리).
3. **F3 — 동시성 감사에서 실버그 2건 발견·수정**: reload(`forge guard` 실행 포함)
   때마다 ① 세션 고정 맵 전멸(프롬프트 캐시 미스 유발) ② 스로틀 버킷 가득 리셋
   (rpm 일시 초과 → 429 유발). `Scheduler.adopt_affinity`/`ProviderThrottle.adopt`로
   이관. 그 외 감사 항목(throttle peek/consume race, 스트림 슬롯 누수, watcher 취소,
   `_delayed_close`와 장수 스트림)은 기존 코드가 안전함을 확인 — 수정 불필요.
4. **F1b — 멀티 API 키 로테이션** (DecisionLog "429는 키 귀책"): 스키마
   (`api_key_envs`, 카탈로그 `{KEY_ENV}_2~_9` 관례 스캔)는 직접, throttle 키 단위
   버킷/쿨다운 + 파이프라인 429 분기는 Opus, 카탈로그 스캔/doctor `keys: N`/문서는
   Sonnet에 위임. 가용 키가 남으면 모델 무귀책으로 같은 후보를 다른 키로 재시도.
5. **F1c — auth 확장 계약**: Azure `api_version` + AWS `aws:{...}` SigV4 env 블록
   스키마와 litellm kwargs 스레딩까지 (카탈로그 등재는 S6).
6. **F2 — 스코어링 v2**: 레이턴시 점수를 로그 스케일(200ms→10점 ~ 30s→0점)로 교체해
   2초 포화 해소, `speed` 시드(기존 dead data)를 콜드 스타트 prior로 — speed 7=중립
   5.0 앵커라 미시드 모델은 v1과 동작 동일. 골든 라우팅 하네스 9건으로 기대 순위 고정.
7. **F4 — 섀도 평가 설계서** ([ShadowEvaluation.md](ShadowEvaluation.md)): tuner의
   반사실 부재를 메우는 섀도 라우팅 + pairwise judge 설계. 편향 방어를 구조로
   (순서 랜덤화, 타 provider judge 강제), 비용 3중 가드(기본 무료-only). 열린 결정
   4건은 사용자 확인 대기 — S5 구현의 입력.

### 산출물 / 검증

- PR #4 (feat/contracts-v2): F1a/b/c + F3 — 전체 266건 테스트 통과
- PR #5 (feat/scoring-v2, #4에 스택): F2 — 전체 275건 통과 (골든 9건 포함)
- **PR 머지는 권한 정책상(사람 리뷰 없는 자가 머지 차단) 사용자 대기** — #4 먼저
  squash 머지 후 #5를 main으로 리베이스해 머지해야 함
- main 직접 커밋(문서): Roadmap.md, Plan/DecisionLog 갱신, ShadowEvaluation.md, 본 기록

### 남은 문제 및 다음 할 일

- [ ] 사용자: PR #4 → #5 순서로 리뷰·머지 (#5는 #4 머지 후 리베이스 필요할 수 있음)
- [ ] 보안 2건(마스킹 정규식 카탈로그 연동, CORS 잠금)은 로드맵 S1 첫 작업 — 미착수
- [ ] 멀티 키 실검증: 실제 2키 환경에서 `forge doctor` + 429 유도 확인 (사용자 환경)
- [ ] mid-stream 429는 멀티 키여도 모델 귀책 (설계상 한계 — openai.py 주석 참조)
- [ ] ShadowEvaluation.md 열린 결정 4건 사용자 확인 → S5 착수 조건

### Learning Recovery

- reload 상태 소실 버그 2건은 코드 정독 중 발견한 것 — "reload가 상태를 이관하는가"를
  상태 종류별로 체크리스트화하면(health/핀/버킷/카운터) 같은 유형 누락을 구조적으로 방지.
- 스코어 공식 변경은 골든 하네스를 먼저 만들고 바꾸는 순서가 옳았다 — 앵커
  (speed 7=중립 5.0)를 하위 호환 증명에 썼다.
- 다음에 직접 설명해볼 질문: ① 429 키 귀책 시 세션 핀이 같은 모델 재선택을
  보장하는 경로 ② adopt()가 세마포어 "객체"를 공유해야 하는 이유.

## 2026-07-11 — 속도 기반 라우팅 정책 확장 (feat/paid-provider-catalog, 계속)

### 배경

전날 유료 프로바이더 카탈로그 작업에 이어, 사용자가 "지금 새로 들어온 모델들도
벤치마킹 기준으로 티어 분류 해주고 일단 기준을 속도로 좀 잡을까 무료 모델들은
너무 느려서 거의 못써먹겠던데"라고 요청. 바로 tier를 다시 매기기 전에 스케줄러
코드를 먼저 읽었다 — `scheduler.py::_score()`가 실제로 무엇을 읽는지 확인하지
않고 tier를 재작업하면 헛수고가 될 수 있어서.

### 한 일

1. **스코어링 코드 확인 — `speed` 필드가 dead data임을 발견**: `capability_seed`에
   정성껏 넣어둔 `speed` 점수(0~10)가 `_score()`의 `TASK_TO_CAPABILITY` 매핑에
   전혀 등장하지 않음(code/debug/refactor/docs만 참조). 실제 속도 영향 경로는
   `tier`(가중치 10%, 기본 그룹 순서도 결정)와 실측 EWMA latency(가중치 15%,
   2000ms 이상은 전부 0점이라 2초와 180초를 구분 못함) 둘뿐.
2. **기존 전례 발견**: `forge.yaml`을 다시 읽어보니 이미 2026-07-09에 이 문제를
   한 번 겪었었다 — `default` 정책의 `prefer` 순서가 NVIDIA 무료 모델 중 빠른
   것만 골라 우선시키고, tier1(glm-5.2/qwen3.5, TTFT 180초+)는 일부러 제외해둔
   상태였음. `tier`는 실력 순위, 속도는 정책으로 분리하는 기존 설계를 발견하고
   사용자에게 확인 — "tier 재정의"가 아니라 "정책 prefer 확장"이 맞는 방향임을
   합의.
3. **범위 확장 확인**: 신규 유료 4개뿐 아니라 기존 무료 프로바이더(nvidia는 이미
   측정됐으니 cerebras/gemini/sambanova)까지 같이 재측정하기로 사용자가 범위를
   넓힘.
4. **실측**:
   - Fireworks(deepseek-v4-pro/kimi-k2p6/qwen3p7-plus/glm-5p2), Cohere(command-a-03-2025/
     command-r7b-12-2024), Cerebras(zai-glm-4.7/gpt-oss-120b), Gemini(gemini-3-flash-preview/
     gemini-3.5-flash), SambaNova(DeepSeek-V3.1/gpt-oss-120b/MiniMax-M2.7)를 `litellm.
     acompletion(stream=True)`로 직접 호출해 TTFT + `content`/`reasoning_content` 글자
     수를 따로 집계. reasoning 모델(cerebras/sambanova의 gpt-oss-120b, fireworks 3종)은
     TTFT는 빠른데 짧은 max_tokens 예산 안에서 보이는 답이 0자인 경우가 많았음 —
     숨은 reasoning 토큰이 예산을 다 먹어서. 이걸 놓치면 "TTFT 빠름 = 빨리 씀"으로
     오판할 뻔했다.
   - **Gemini "Flash" 계열이 실측 TTFT 16~19초**로 나와서 놀랐다 — 이름과 정반대.
   - SambaNova `MiniMax-M2.7`은 결제수단 없이는 호출 자체가 막힘(에러로 확인).
   - x.ai(grok-4.5/grok-build-0.1), Together(deepseek-v4-pro)는 실키/크레딧이 없어
     서브에이전트로 Artificial Analysis(3rd-party) 조사 — `deepseek-v4-pro`가
     NVIDIA(무료) 18초 대비 Together/Fireworks(유료)에서 1~1.5초로 10배 이상
     빠르다는 걸 확인.
5. **`forge.yaml` 정책 갱신**: `default`/`heavy-work`/`hard-tasks` 세 정책의
   `prefer` 순서를 실측으로 재작성. 사용자 결정("무료 먼저 다 쓰고, 안 되거나
   느리면 유료 빠른 모델로")에 따라 `default`는 무료 빠른 것(mistral-small-4 →
   sambanova:DeepSeek-V3.1 → nemotron-3-super → deepseek-v4-flash) 다음에만 유료
   빠른 것(cohere:command-a-03-2025 → xai:grok-build-0.1 → fireworks:deepseek-v4-pro)
   순서. `heavy-work`/`hard-tasks`는 무료 v4-pro를 그대로 우선하고 유료 고속
   호스팅은 쿨다운 시 대체용으로만 추가.
   - `together:deepseek-ai/DeepSeek-V4-Pro`를 처음엔 넣었다가, `PolicyEngine.plan()`을
     직접 호출해 검증하던 중 "policy route item matches no tier/model — ignored"
     경고를 발견 — 사용자 환경에 `TOGETHER_API_KEY`가 없어서 매 요청마다 헛경고만
     반복될 상황이었음. 빼고 문서에 "키 생기면 추가" 메모만 남김.
6. **검증**: `PolicyEngine.plan()`을 세 정책 전부(coding/debug/heavy-work 트리거)에
   대해 직접 호출해 전 prefer 항목이 경고 없이 실제 등록 모델로 resolve됨을
   확인. 전체 유닛테스트 235건 재실행, 통과 유지.
7. **문서**: `docs/Research.md`(2026-07-11 "속도 전면 실측" 섹션, 표+교훈 3개),
   `docs/DecisionLog.md`(tier 재정의 대신 정책 확장을 택한 이유 + speed dead-data
   발견 기록), `docs/Plan.md`(S1-S5 작업표), 본 항목.

### 설계 결정

- **`tier`를 속도로 재정의하지 않았다** — `hard-tasks`/`heavy-work` 정책이 "느려도
  강한 모델을 쓴다"는 의도로 tier1을 명시적으로 prefer하고 있는데, tier의 의미
  자체를 속도로 바꾸면 그 정책의 의도가 깨진다. 정책 `prefer` 확장이 기존 설계
  의도를 보존하면서 새 데이터만 반영하는 최소 변경이었다.
- **Together를 알고도 정책에 안 넣었다** — 데이터는 최상위권(TTFT ~1초, tok/s
  ~208)이었지만 키가 없는 상태에서 참조하면 매 요청마다 로그만 더러워진다.
  "확인된 것만, 지금 쓸 수 있는 것만" 원칙을 지켰다.

### 남은 문제 및 다음 할 일

- `speed` capability 필드는 여전히 dead data — 스코어링에 실제로 반영하거나
  latency 스코어 구간을 세분화(지금은 2초 이상 전부 0점)하는 건 별도 개선 과제.
- Together AI 키가 생기면 세 정책 모두에 `together:deepseek-ai/DeepSeek-V4-Pro`
  추가할 것 (실측 최상위권).
- SambaNova `MiniMax-M2.7`은 결제수단 추가 전까지 실사용 불가 — capability_seed는
  유지하되 참고 정보로만 취급 중.
- 이번 실측(TTFT/tok/s)은 각 모델당 1회 샘플이라 변동 가능성 있음(AA 벤치마크도
  ±10~15% 변동 명시) — 장기적으로는 forge 자체의 텔레메트리 학습 루프(tuner.py)가
  실제 트래픽 기반으로 보정하겠지만, 지금은 정책 `prefer` 순서가 이 1회 샘플에
  의존하고 있다는 한계는 남아있음.

### 후속: "정책상 사용하면서 tier가 올라가나?" 질문에 답하려고 tuner.py 확인

사용자가 "그럼 정책상 사용하면서 점점 위로 올라오나?"라고 질문. `tuner.py`를
읽고 정확히 답함: `prefer` 목록 순서는 정적 YAML이라 실사용으로 재배열되지
않는다. 별도로 있는 학습 루프(`CapabilityTuner`)는 `tier`가 아니라 task별
`capability_adjustments`만 ±2 클램프(상향은 +1로 더 보수적)로 건드리고, 그마저도
표본 5개 이상(`min_samples`) 쌓여야 작동 — tier 자체가 승격되는 경로는 코드에
없음을 확인.

### 후속: "성능·속도 좋은 유료 모델은 tier1에 있어야 하지 않냐" → 전체 tier 재검토

사용자 지적에 답하다가 실제 불일치를 하나 발견(`fireworks:glm-5p2`가
`nvidia:z-ai/glm-5.2`와 같은 모델인데 tier1 누락 — 별도 커밋으로 즉시 수정,
위 커밋 로그 참조) 후, 사용자가 "모델들 다 검토해서 tier 수정해줘"라고 요청해
전체 재검토 진행.

- **내가 새로 넣은 항목 안에서 발견한 것**: `xai:grok-4.5`(자체 발표만, 제3자
  미검증)가 형제 모델 `grok-build-0.1`(동일 근거 수준으로 tier2)과 다르게
  tier1을 받고 있던 걸 발견 — tier2로 정정. `together`/`fireworks`의
  `deepseek-v4-pro` capabilities `context` 값이 8/9로 미세하게 어긋나 있던 것도
  9로 통일.
- **기존 NVIDIA 데이터(2026-07-09 세션) 재검토**: 이 세션의 전체 판단 맥락을
  다 알 수 없어서 함부로 건드리지 않기로 하고, 사용자에게 먼저 후보를 보여줌.
  같은 지표(SWE-bench Verified/Pro)로 직접 비교 가능하고 tier1 범위와 겹치는
  세 개(`mistral-medium-3.5`, `deepseek-v4-flash`, `gemini-3.5-flash`)를 사용자
  확인 후 tier1로 승격. `minimax-m3`(자체 발표만)는 grok-4.5와 같은 기준으로
  후보에서 제외, tier2 유지.
- **건드리지 않은 것**: `sambanova:MiniMax-M2.7`/`DeepSeek-V3.1`은 Research.md에
  이미 "2차 집계만으로 tier1 승격 보류 중"이라고 명시돼 있던 기존 결정이라
  그대로 유지 — 누락이 아니라 확인된 보류임을 재확인.
- 평가 기준을 명문화(DecisionLog.md 참조): "제조사 자체 발표뿐, 제3자 미검증"인
  벤치마크는 tier1 불충분 — 이전엔 이 기준을 모델마다 다르게 적용했던 게
  일관성 문제의 원인이었음.
- `tests/test_settings.py`의 grok-4.5 tier 기대값을 tier1→tier2로 갱신. 전체
  테스트 235건 재실행 통과. `PolicyEngine.plan()`으로 tier 풀 재확인(승격된
  3개가 tier1 그룹에, grok-4.5가 tier2 그룹에 정확히 반영됨).

**부수 리스크 인지**: grok-4.5가 tier2로 내려가면서 `default` 정책의 일반
fallback(`[tier2, tier1, tier3]`, tier2가 먼저 시도됨) 풀에 들어갔다 — prefer
목록 7개가 전부 막혔을 때만 도달하는 드문 경로지만, 신규 등록이라 실측
latency가 없어 중립값(5.0)으로 시작해 한동안 capability 점수만으로 뽑힐 수
있음. 실트래픽이 쌓이면 EWMA가 자연히 보정할 것으로 보고 별도 조치는 안 함.

---

## 2026-07-10 — 유료 프로바이더 카탈로그 확장 (feat/paid-provider-catalog)

### 배경

사용자가 "다른 유료 API 프로바이더들도 다 인식 가능하도록 하려는데 뭐뭐 추가해야될까?
x.ai라던지"라고 질문. 바로 목록을 나열하지 않고 먼저 방향을 확인했다 — 지금까지
카탈로그 확장은 "공식 문서로 확인된 **무료** 프로바이더만 큐레이션"이라는 방향(Plan.md
M3 후속)이었고, 유료 프로바이더를 폭넓게 받자는 결정은 없었기 때문. 질문 3개로 정리:
① 목적(페일오버 다양성/프리미엄 모델 사용/포괄적 카탈로그) → **포괄적 카탈로그**,
② 우선 대상(x.ai/Cohere/Together AI·Fireworks/AWS Bedrock·Azure OpenAI 중 복수 선택)
→ **전부**, ③ 가격 정확도(공식 페이지 직접 시딩/litellm 내장 신뢰/추적 안 함) →
**공식 pricing 페이지 근거로 직접 시딩**.

②에 답하자마자 바로 구현하지 않고 실제 인증 어댑터(`litellm_provider.py`)와
`ProviderConfig` 스키마를 먼저 읽어 확인한 결과, AWS Bedrock(AWS SigV4 3종
자격증명)과 Azure OpenAI(리소스별 커스텀 deployment 이름 + `api_version`)는
"단일 API 키 + api_base"라는 기존 카탈로그 계약에 안 맞는다는 걸 발견 — 스키마
확장이 필요한 별도 결정이라 다시 질문했고, 사용자가 "이번엔 x.ai/Cohere/Together/
Fireworks만, Bedrock/Azure는 별도 작업으로 미룬다"를 선택.

### 한 일

1. **리서치 (일반 서브에이전트 4개 병렬, 프로바이더별 1개)** — x.ai/Cohere/Together AI/
   Fireworks AI 공식 문서를 웹에서 직접 fetch해서 엔드포인트/인증/모델ID/공식
   pricing/레이트리밋/코딩 벤치마크를 조사. 결과는 `docs/Research.md` 2026-07-10
   섹션에 출처 URL과 함께 기록.
   - **프롬프트 인젝션 의심 감지**: Together AI·Fireworks 담당 에이전트가 WebSearch
     결과 일부에서 전제를 반박하는 부가 텍스트 + 미검증 수치가 섞여 드는 비정상
     패턴을 발견하고 전량 폐기, 대신 `curl -L`로 공식 문서 원문(mintlify raw md,
     HF README raw, discourse json)을 직접 받아 재검증. 사용자에게도 이 사실을
     투명하게 알림.
   - Cohere는 OpenAI 호환 Compatibility API가 있다는 건 확인됐지만 현재 플래그십
     (Command A) 가격을 공식 페이지에서 1차 소스로 확인하지 못함(레거시 모델만
     명시) — 3rd-party 인용값은 채택하지 않고 "미확인"으로 남김.
2. **`forge_gateway/settings.py`**:
   - `PROVIDER_CATALOG`에 `xai`/`cohere`/`together`/`fireworks` 4개 항목 추가.
     x.ai/Fireworks/Cohere는 discovery 미확인이라 `discovery: false` +
     `capability_seed`로 모델을 직접 공급, Together AI는 공식 문서로 discovery가
     동작함을 확인해 기본값(true) 유지.
   - `capability_seed`에 `price_per_mtok` 선택 필드 신설, `apply_auto_providers`가
     `ModelOverride.price_per_mtok`까지 스레딩하도록 확장(기존 tier/capabilities
     스레딩과 동일 매커니즘) — 이게 없으면 가격을 공식 소스로 확인해도 꽂을 자리가
     없었음(§5.12 가격 우선순위 1번 경로는 forge.yaml 수동 오버라이드만 지원했음).
   - 벤치마크/가격을 1차 소스로 확인 못한 모델(Cohere 전체, Fireworks의
     Qwen3.7-Plus/GLM-5.2)은 `price_per_mtok`만 채우거나 아예 시드를 비워 tier3
     기본값·litellm 폴백에 위임 — 없는 근거를 만들어내지 않음.
3. **문서 동기화** — `.env.example`(신규 키 4개 안내), `README.md`("Adding a
   provider is just an API key" 목록 갱신 + 가격 소싱 정책 문단 + Bedrock/Azure
   미지원 사유 명시), `CHANGELOG.md`(Added), `docs/Research.md`(위 리서치 전체
   + 프롬프트 인젝션 메모), `docs/Plan.md`(M3 후속 "유료 프로바이더 카탈로그
   확장" 섹션, Bedrock/Azure는 P6으로 보류 표기).
4. **`tests/test_settings.py`**: 신규 프로바이더 자동등록, Cohere의 무시드
   등록, Together discovery 유지, `price_per_mtok` 스레딩(전체 시드/가격만
   시드 두 경우 모두) 검증 테스트 5건 추가. 전체 235건 통과.
5. **실키 검증 (사용자가 4개 중 3개에 가입해서 키 제공, Together는 $5 선불
   요구사항 때문에 스킵)** — `forge doctor`로 먼저 확인했더니 xai/cohere에서
   `list_models` 경고 로그가 `UnicodeEncodeError`로 깨져 실제 에러가 안 보였다.
   콘솔 출력 대신 `load_config` + `make_provider(...).list_models()`를 직접
   호출해 결과를 파일에 써서 우회 확인:
   - **Cohere discovery가 실제로 동작함**(`GET .../compatibility/v1/models` →
     200, 실제 31개 모델) — 조사 당시 "미확인"으로 보수적으로 꺼뒀던
     `discovery: false`가 틀렸음이 실증됨. `settings.py`에서 제거(기본값
     true로 전환), 이제 discovery로 전부 커버되므로 `default_models` 수동
     목록도 삭제. `tests/test_settings.py`의 관련 테스트도 갱신.
   - **x.ai**: 403 "신규 팀에 크레딧 없음" — 신규가입 무료크레딧이 없다는
     사용자 보고와 일치, 문서로는 못 찾았던 사실을 실증.
   - **Fireworks**: 실제로 $6 크레딧을 받았으나(문서로는 못 찾았던 사실 —
     사용자 실사용이 더 정확한 SambaNova 패턴 재현) 지금은 412 "계정 정지"
     상태 — 카탈로그 설정 문제가 아니라 계정 상태 문제.
   - 로깅 크래시 자체는 `.env` 정리 후 재현 실패 — 원인 확정은 못했지만,
     `server.py`의 로깅 초기화에 `sys.stdout`/`stderr` `errors="backslashreplace"`
     방어 코드를 추가(`cli.py` `main()`에 이미 있는 패턴을 서버 부팅 경로에도
     적용). 업스트림 에러는 임의의 유니코드를 담을 수 있다는 게 명시적으로
     드러난 사례라, 근본 원인을 못 잡았어도 방어는 해두는 게 맞다고 판단.
   - 전체 테스트 재실행, 235건 통과 유지.

### 설계 결정

- **가격은 forge.yaml 오버레이가 아니라 `capability_seed`에 얹었다** — 이미
  존재하는 자동등록 경로(사용자가 키만 넣으면 등록)를 그대로 쓰기 위해서다.
  별도 forge.yaml `models:` 섹션을 미리 채워두는 방식도 가능했지만, 그러면
  README의 "Adding a provider is just an API key" 원칙이 깨진다.
- **Bedrock/Azure는 스키마를 억지로 끼워 맞추지 않고 통째로 미뤘다** — 계약
  변경(§ "계약 우선" 원칙)이 걸린 결정이라, 조사 중간에 발견하자마자 구현을
  멈추고 사용자에게 다시 물었다. 어설프게 끼워 넣으면(예: `api_key_env`에
  AWS_ACCESS_KEY_ID를 넣고 Bearer 토큰처럼 취급) 실제로 동작하지 않거나
  Azure의 deployment 매핑을 사용자가 우회할 방법이 없어져 오히려 더 나쁘다.
- **Cohere 가격을 3rd-party 인용값으로 채우지 않았다** — "공식 페이지 근거로
  직접 시딩"이라는 이번 결정의 취지 자체가 litellm 내장 표(불확실한 출처)를
  신뢰하지 않겠다는 것이었으므로, 똑같이 출처가 약한 3rd-party 값으로 대체하면
  결정의 의미가 없어진다. 미확인인 채로 litellm 폴백에 맡기는 게 일관적이다.

6. **Fireworks 계정 정지 해제 후 재검증 + discovery 재검토** — 사용자가 결제 문제를
   해결("firework 풀었어")한 뒤 다시 확인: `list_models` 성공(7개 모델), 실제
   `probe`(채팅 completion, max_tokens=1)도 `deepseek-v4-pro`로 성공(레이턴시
   ~1.8초) — 연결·계정 상태·실요청 경로 전부 검증 완료.
   - `list_models`가 반환한 7개 중 `flux-1-schnell-fp8`(이미지 생성 모델)이
     섞여 있는 걸 발견 — discovery는 실제로 동작하지만(공식 문서에 관리 API만
     보고 "불가"라 판단했던 원래 조사가 틀렸음), 채팅 불가 모달까지 같은
     목록에 노출된다는 뜻. forge는 4xx를 failover 없이 그대로 반환하므로
     스케줄러가 이런 모델을 고르면 요청이 복구 없이 실패할 위험이 있음
     (Fireworks 7개 중 1개 ≈14%, 이미 discovery:true로 바꿔둔 Cohere도
     31개 중 1개 ≈3%로 같은 위험을 갖고 있었다는 걸 이때 같이 발견).
   - 사용자에게 물어 **Cohere/Fireworks 둘 다 discovery:false로 되돌리기**로
     결정 — discovery 자체는 동작이 확인됐지만 안전(failover 가능한 순수
     채팅 모델만 큐레이션)을 우선. `settings.py`의 cohere 항목을 원복
     (`default_models` 복원), 관련 테스트도 원복하고 근거 주석을 "미확인"이
     아니라 "확인됐지만 의도적으로 off"로 갱신.
   - 전체 테스트 재실행, 235건 통과 유지.

### 남은 문제 및 다음 할 일

- Together AI는 여전히 미검증(사용자가 $5 선불 부담으로 키를 안 만듦) —
  discovery/가격은 문서 근거만으로 유지 중.
- x.ai는 연결은 확인됐지만 계정 크레딧이 0(구매 필요) — 실제 채팅 요청까지는
  아직 미검증.
- Cohere/Fireworks 모두 discovery 자체는 동작이 확인됐으나 비채팅 모달 혼입
  위험으로 의도적으로 꺼둔 상태 — 나중에 "채팅 모델만 걸러내는" 필터링
  매커니즘을 만들면 다시 켤 수 있음(지금은 스코프 밖으로 미룸).
- `forge doctor`의 Unicode 로깅 크래시 근본 원인 미확정(재현 실패) — 재발하면
  `.env` 값에 비-ASCII 문자가 실제로 섞여 있는지부터 확인.
- AWS Bedrock/Azure OpenAI — `ProviderConfig` 스키마 확장(별도 작업, Plan.md P6).

### 블로그/포트폴리오 소재

- "무료 프로바이더 확장과 유료 프로바이더 확장이 왜 다른 문제인가" — 가격
  정확도가 실제 과금과 연결되는 순간부터 "1차 소스 인용 vs 지어내지 않기"가
  코드 설계(스키마에 선택 필드 하나 더 두는 것)로 이어지는 과정.
- "서브에이전트가 프롬프트 인젝션을 스스로 감지하고 폐기한 사례" — 웹 리서치
  위임 시 결과를 맹신하지 않고 1차 소스 직접 대조로 되돌아간 방어적 행동.

### Learning Recovery

- AI가 주도적으로 처리: 4개 프로바이더 리서치 위임, 카탈로그 스키마 확장 설계,
  문서 5종 동기화, 테스트 작성.
- 아직 완전히 이해 못했을 수 있는 부분: `core/pricing.py`의 가격 우선순위 3단계
  (forge.yaml > free 플래그 > litellm.model_cost)가 `capability_seed`를 통한
  자동등록 경로에서 정확히 어떻게 상호작용하는지, LiteLLM이 Bedrock/Azure의
  자격증명을 내부적으로 어떻게 읽어오는지(boto3 체인 vs 명시적 kwargs)는 다음에
  직접 설명해볼 가치가 있음.

---

## 2026-07-09 — 공개 배포 전 점검 (feat/free-provider-catalog, 마무리)

### 배경

사용자가 "이제 배포해도 될 정도인가?"라고 질문. "배포"의 의미(개인용 계속 사용/PyPI
공개/프로덕션 멀티유저)를 나눠서 답한 뒤, "PyPI 공개" 기준으로 남은 격차(PR #1 미머지,
CHANGELOG 미반영, 보안 체크리스트 미점검)를 짚었고 사용자가 진행을 승인("ㄱㄱ").

### 한 일

1. **CHANGELOG.md 갱신** — `[Unreleased]`에 무료 provider 카탈로그 확장(Cerebras/
   Gemini/SambaNova/Zhipu), `capability_seed` 매커니즘, SambaNova 정정을 Added/Fixed로
   반영. 이전까지 M3 후속 작업이 전혀 기록 안 돼 있었음(자체 ReviewChecklist "릴리스
   전 CHANGELOG 갱신" 항목 미이행 상태였음).
2. **보안/프라이버시 체크리스트 실제 코드 검증** (Explore 서브에이전트, 추측 금지 —
   파일:라인 근거로만 판정) — `docs/ReviewChecklist.md`의 4항목을 대조:
   - API 키 마스킹: 대부분 확인됐지만 `health.py`의 probe/list_models/discover 예외
     로그 3곳이 `mask_secrets()`를 안 거치고 raw 예외 문자열(`f"...{e}"`)을 그대로
     로그에 찍고 있었음 — CLAUDE.md 자체 규칙("API 키는 로그 어디에도 노출 금지")
     위반 소지.
   - 프롬프트/응답 본문 미저장: 확인됨(`RequestMetric`/`request_metrics` 스키마에
     텍스트 필드 없음, 숫자/코드만).
   - `/admin/*` loopback 강제: 확인됨(`admin.py`의 `require_loopback` 의존성 +
     `server.py`의 `require_key` 이중 적용, 문서 주장이 아니라 실제 코드 강제).
   - 안전한 기본값: `server.host` 기본 127.0.0.1, 타임아웃 전부 유한값은 확인됐지만,
     **host를 비-loopback으로 바꾸면서 `FORGE_API_KEY`를 안 설정해도 아무 경고가
     없었음** — README에는 "반드시 설정할 것"이라고만 적혀 있고 코드 강제가 없던 상태.
3. **발견한 2건 수정**:
   - `mask_secrets`(구 `_mask_secrets`)를 `litellm_provider.py`에서 `providers/base.py`
     로 이동해 공유 유틸로 만듦(두 모듈 다 이미 `base.py`를 import하고 있어 계층상
     자연스러움) — `litellm_provider.py`는 `from .base import mask_secrets as
     _mask_secrets`로 기존 호출부를 그대로 유지, `health.py`의 3곳(`_probe_one`,
     `_check_providers`, `discover`)에 `mask_secrets(str(e))` 적용.
   - `server.py`의 `create_app()`에 시작 시 경고 추가: `server.host`가 loopback이
     아니고 `config.auth.api_key`가 비어 있으면 경고 로그(차단 아님) — 기존 "유료
     프로바이더 자동 등록" 경고와 같은 패턴(경고만, 로컬 개발 편의는 유지).
4. **회귀 테스트 신설** (`tests/test_server_security.py`, 5건) — `mask_secrets` 자체
   동작(알려진 키 접두사 마스킹, 무관 텍스트는 그대로) + 비-loopback/무인증 조합일 때
   경고가 뜨는지·loopback이거나 키가 있으면 안 뜨는지(`assertNoLogs`, Python 3.10+).
5. 전체 테스트 228건 통과(224 + 신규 5건 — 정확히는 이전 223건 + 5건).
6. `docs/Plan.md`에 "공개 배포 전 점검" 섹션(D1-D5) 추가.

### 오류/수정

- **API 키 마스킹 불일치** (자체 발견, 보안 리뷰) — 증상: `list_models`/probe/discover
  실패 시 예외 문자열이 마스킹 없이 로그에 그대로 남음. 원인: `_mask_secrets`가
  `litellm_provider.py`에만 있고 `_extract_message` 경로에만 적용돼, 그 함수를
  안 거치는 다른 예외 로그 지점(특히 health.py)에는 마스킹 계약이 전파되지 않음.
  수정: 마스킹 함수를 공유 위치(`providers/base.py`)로 옮기고 누락된 3곳에 적용.
- **비-loopback 무인증 무경고** (자체 발견) — 증상: `server.host: 0.0.0.0` +
  `FORGE_API_KEY` 미설정 조합에서도 아무 경고 없이 부팅됨(README에만 "반드시 설정"이라
  적혀 있고 코드 강제 없음). 원인: 배포 관련 안전장치가 "유료 provider 자동등록" 경고
  하나만 있었고 인증 관련 안전장치는 만든 적이 없었음. 수정: 동일 패턴의 경고 추가.

### 설계 결정

- 두 수정 다 **차단이 아니라 경고**로 처리 — 로컬 개발 편의(무인증 기본값)를 깨지
  않으면서, 실수로 외부에 노출했을 때만 눈에 보이는 신호를 준다. 기존 "유료 provider
  자동등록" 경고와 일관된 철학.
- `mask_secrets`의 새 위치를 `core/`가 아니라 `providers/base.py`로 정한 이유:
  `health.py`가 이미 `providers.base`를 import하고 있어(Provider 프로토콜) 새 의존성
  추가 없이 재사용 가능했고, 이 함수의 존재 이유 자체가 "provider 어댑터가 만든 예외
  메시지에 provider 키가 에코될 수 있다"는 것이라 provider 계층에 두는 게 개념적으로도
  맞음.

### 남은 문제 및 다음 할 일

- [ ] PR #1을 main에 squash merge (이 세션 마지막 단계)
- [ ] README의 "pre-release, Expect breaking changes" 문구를 뗄지는 사용자가 별도로
      결정할 사항 — 이번 점검 범위에는 포함하지 않음
- [ ] `HealthMonitor`/`LiteLLMProvider`/`create_app` 자체를 위한 전용 단위 테스트
      파일이 지금까지 없었다는 것도 이번에 발견 — 이번엔 보안 관련 경로만 좁게
      커버(`test_server_security.py`), 전체 커버리지 보강은 별도 작업

### 블로그/포트폴리오 소재

- "마스킹 함수 하나 빼먹은 로그 라인 3개 — 보안 체크리스트를 문서로만 두지 않고
  코드로 대조했을 때 나온 것"

### Learning Recovery

- AI가 주도: 보안 체크리스트 4항목 코드 대조(Explore 서브에이전트), 마스킹 유틸
  리팩터링 위치 결정, 경고 로직 구현, 회귀 테스트 작성.
- 다음에 직접 설명해보면 좋을 질문: (1) `assertNoLogs`가 Python 3.10부터 생겼다는 것과
  그 전엔 이런 "로그가 안 떴는지" 검증을 어떻게 했을지, (2) `mask_secrets`를 왜 `core/`가
  아니라 `providers/base.py`에 둬야 순환 의존(circular import)을 피하는지.


## 2026-07-09 — 정정: SambaNova free 오판정 수정 (feat/free-provider-catalog, 계속)

### 배경

사용자가 "SambaNova는 $5 크레딧 주고 끝인거같던데"라고 반박. 앞서 커밋에서
SambaNova를 recurring 무료(카드 없이 계속 사용 가능)로 판단해 `free: true`로
등록했는데, 사용자의 실사용 경험과 충돌.

### 한 일

1. 웹 재검증(서브에이전트) — 결과: 사용자가 맞음. `cloud.sambanova.ai/plans` 공식
   페이지는 Free 플랜을 "$5 in free API credits, no credit card required, 30일
   만료"로만 설명하고 recurring 무료 등급 언급이 없음. 가격표에 $0 모델이 없고 전
   모델 유료. 커뮤니티 사례에서 크레딧 소진 시 `402 CREDITS_EXHAUSTED`로 완전히
   막힘 확인. SambaNova 직원이 2025-02 공식 커뮤니티에서 "free tier를 별도 유지할
   계획 없다"고 직접 확인.
2. **원인 분석**: 이전 조사가 "카드 없이 되는 rate-limit *등급* 문서가 있는가"만
   확인했고 "그 등급의 크레딧/기간이 소진되면 실제로 어떻게 되는가"는 확인하지
   않은 방법론적 결함. rate-limit 분류와 과금 여부는 서로 다른 축인데 혼동함.
3. `forge_gateway/settings.py`의 `PROVIDER_CATALOG`에서 sambanova의 `free: True`
   제거 → 다른 유료 provider와 동일하게 취급(discovery 모델은 price 미상, `allow_paid:
   false`에서 자동 제외). `capability_seed`(DeepSeek-V3.1/gpt-oss-120b/MiniMax-M2.7)는
   모델 품질 순위라 과금 여부와 무관하므로 유지.
4. `.env.example`/README에서 SambaNova를 "무료" 목록에서 제거하고 실제 상태(1회성
   트라이얼, paid 취급) 명시.
5. 테스트 수정 — 기존 `test_free_tier_providers_registered_when_key_present`에서
   sambanova 제거, `test_sambanova_not_marked_free` 신규 추가. 전체 223건 통과.
6. **같은 실수 재발 방지** — Cerebras는 자체 문서가 무료 등급을 "Free **Trial**"로
   명명하고 있어(SambaNova와 같은 함정 가능성), Gemini도 포함해 "소진/기간 후 실제
   동작"까지 재검증하는 별도 조사를 진행 중(다음 세션 기록에 결과 반영).

### 오류/수정

- **SambaNova `free: true` 오판정** (사용자 발견) — 증상: `allow_paid: false`
  정책을 켜도 실제로는 과금될 수 있는 provider가 "무료"로 분류돼 통과됨. 원인:
  "카드 불필요"와 "recurring 무료"를 혼동(위 원인 분석 참조). 수정: free 플래그
  제거, 문서 정정.

### 설계 결정 / 교훈

- **"카드 불필요"≠"recurring 무료"** — 무료 여부를 판정할 때는 반드시 "크레딧/기간
  소진 후 동작"까지 확인해야 한다. 앞으로 새 provider를 free로 등록하기 전에 이
  체크리스트를 표준으로 삼음: (1) rate-limit 등급 문서 존재 여부, (2) 총량/기간
  상한 존재 여부, (3) 상한 도달 시 완전 차단인지 저속 유지인지, (4) 커뮤니티 실사용
  사례로 교차검증.
- 사용자의 반박을 그대로 받아들이지 않고 재검증부터 했다 — 반박이 맞을 수도, 우리
  판단이 맞을 수도 있어 실증이 먼저(Development Guide "추측 금지, 실증 우선" 원칙).

### 남은 문제 및 다음 할 일

- [x] Cerebras/Gemini "소진 후 동작" 재검증 완료 — 둘 다 문제없음(총량 상한/만료 없음),
      `free: true` 유지. Research.md 정정 섹션에 근거 추가.
- [ ] SambaNova capability_seed(DeepSeek-V3.1/gpt-oss-120b/MiniMax-M2.7)는 유지했으나
      실사용자가 `allow_paid: false` 없이 쓰면 예상치 못한 과금 가능 — 문서 강조 유지
- [ ] Cerebras 이용약관 원문 직접 대조는 못함(문서 간 명칭 불일치 "Free Trial" vs
      "Free"만 확인) — 완전한 확정은 아님, 정책 변경 시 재확인 필요

### 블로그/포트폴리오 소재

- "무료 티어 판정에서 진짜 확인해야 하는 질문: '카드가 필요 없다'가 아니라 '크레딧이
  0이 되면 무슨 일이 생기나'"

### Learning Recovery

- AI가 주도: 재검증 리서치, 원인 분석(rate-limit 등급 vs 과금 여부 혼동), 코드/문서
  정정.
- 다음에 직접 설명해보면 좋을 질문: 왜 `capability_seed`는 유지하면서 `free` 플래그만
  제거하는 게 맞는 선택인지(모델 품질과 과금 여부가 독립적인 축이라는 것).


## 2026-07-09 — 신규 무료 provider 벤치마크 시드 + 대시보드 상태 정체 조사 (feat/free-provider-catalog, 계속)

### 배경

사용자가 이전 커밋으로 추가된 4개 무료 provider(Cerebras/SambaNova/Gemini/Zhipu)의
모델들이 "다 tier3/capability 7 균일이라 정책이 사실상 안 골라준다"는 걸 알게 된 뒤
두 가지를 요청: (1) nvidia처럼 벤치마크 기반 tier/capability 시드, (2) 대시보드에서
그 provider들 상태가 안 바뀌는 이유.

### 한 일

1. **Gemini 키 문제 실사례 디버깅** (이 작업 전 단계) — `forge doctor`에는 잡히는데
   모델 0개 → 로그의 `UnicodeEncodeError('ascii', ... '—' ...)`를 httpx Headers로
   재현(`httpx.Headers({'Authorization': 'Bearer ...— 텍스트'})`)해서 `.env`의
   `GEMINI_API_KEY` 값에 키 뒤로 em dash 포함 설명 텍스트가 같이 들어간 게 원인임을
   확정. 코드 버그 아님 — 사용자가 `.env` 정리 후 해결(Gemini 55개 모델 discovery 확인).
2. **대시보드 "상태 안 바뀜" 원인 확정** — `health.py`의 `_idle_targets()`가
   `source == "config"`인 모델만 능동 probe하도록 이미 의도적으로 설계돼 있음(§2-5
   재발 방지 — discovery된 수백 개를 순회 probe하면 무료 티어 rate limit을 자해).
   discovery 전용 모델은 실트래픽이 없으면 health가 초기값 `"unknown"`에서 절대
   안 바뀜 — 정책이 tier3를 거의 안 고르는 것과 같은 근본 원인. `/dashboard` 엔드포인트
   자체에는 provider 필터링 버그 없음(전부 순회) — 확인 후 표시 로직은 정상으로 결론.
3. **실제 discovery id 확보** — `forge models`(오프라인, config 12개만)로는 안 보여서
   `GET /v1/models`을 사용자가 직접 호출해 cerebras 3개/sambanova 6개/gemini 55개(대부분
   TTS/이미지/비디오/임베딩이라 코딩과 무관, 실제 텍스트 코딩 모델은 10여 개로 축소)/
   zai 0개(키 미설정) 확인.
4. **벤치마크 리서치** (서브에이전트, 공식 문서 우선) — Cerebras/SambaNova/Gemini 각
   상위 2~3개 모델의 SWE-bench Verified/Pro, LiveCodeBench, Terminal-bench 점수 조사.
   출처와 상세는 [Research.md](Research.md) "신규 무료 provider 벤치마크 시드" 참조.
5. **`PROVIDER_CATALOG`에 `capability_seed` 필드 신설** (`forge_gateway/settings.py`) —
   anthropic의 `default_models`(bare id만 추가)를 확장해 tier/capabilities까지 포함,
   `apply_auto_providers`가 provider 등록 시 `config.models`에 직접 채워 넣음. forge.yaml
   에 provider를 미리 선언할 필요가 없어(§F3에서 정한 원칙 유지) 6개 모델(cerebras
   2개, sambanova 3개, gemini 2개 — 실제로는 nvidia:gpt-oss-120b 중복 재사용 포함해
   서로 다른 시드 5종)에 반영.
6. 테스트 2건 추가(capability_seed가 config.models에 반영되는지, 키 없으면 시드 자체가
   안 붙는지) — 전체 222건 통과.

### 오류/수정

- 없음 (Gemini 키 이슈는 사용자 환경 문제로 판명, 코드 수정 대상 아님)

### 설계 결정

- **capability_seed는 반드시 provider가 실제로 auto-register될 때만 적용** — forge.yaml에
  cerebras/sambanova/gemini를 미리 선언하는 대안도 검토했으나, 그러면 키가 없는 대다수
  사용자에게도 `forge doctor`가 "키 없음" 경고를 띄우게 되어 README의 "Adding a provider
  is just an API key" 철학을 깨게 됨 — anthropic의 `default_models` 매커니즘을 그대로
  확장하는 쪽을 선택.
- **부수효과로 대시보드 문제도 같이 해소**: capability_seed로 들어간 모델은
  `source == "config"`가 되어 §5.6 능동 probe 대상에 포함됨 — 별도 코드 수정 없이
  Registry의 기존 규칙(`_build_from_config`가 만든 엔트리는 항상 source="config")을
  그대로 활용.
- Preview 상태 모델(cerebras:zai-glm-4.7, gemini:gemini-3-flash-preview)은 벤치마크가
  가장 강력하지만 예고 없이 제거될 위험이 있음 — 그래도 시드에 포함하고 self-healing
  failover(쿨다운→다음 후보)로 흡수하는 쪽을 선택. 리스크는 Research.md/Plan.md에 명시.

### 남은 문제 및 다음 할 일

- [ ] MiniMax-M2.7/DeepSeek-V3.1/V3.2 — 2차 집계 수치만 있어 공식 테크리포트 원문 대조 필요
- [ ] cerebras:zai-glm-4.7 / gemini:gemini-3-flash-preview가 Preview에서 제거되거나
      GA로 바뀌면 forge.yaml capability_seed 재확인
- [ ] zai(Zhipu)는 사용자가 ZAI_API_KEY를 아직 설정 안 해서 0개 discovery — 실제 연동
      검증은 사용자 환경에서

### 블로그/포트폴리오 소재

- "UnicodeEncodeError 디버깅기 — .env 복붙 실수가 httpx Headers까지 타고 올라간 사례"
- "capability 시드를 forge.yaml이 아니라 provider 카탈로그에 두면 생기는 부수효과 —
  능동 헬스체크 대상 편입"

### Learning Recovery

- AI가 주도: 로그 한 줄로 UnicodeEncodeError 원인을 httpx 소스 레벨까지 추적해 재현,
  health.py의 의도된 설계(§2-5)를 발견해 "버그 아님"으로 결론, capability_seed
  메커니즘 설계·구현.
- 다음에 직접 설명해보면 좋을 질문: (1) `merge_discovered`가 이미 config로 들어온
  forge_id는 건드리지 않는 이유(source="config" 보존), (2) Scheduler 스코어링에서
  tier_priority(가중치 0.10)가 capability(가중치 0.30)보다 작은데도 tier1/tier2/tier3
  풀 구분이 실질적으로 더 큰 영향을 주는 이유(정책의 fallback 순서 자체가 tier 그룹
  단위라 스코어링 전에 후보 풀이 먼저 갈린다).

## 2026-07-09 — 무료 프로바이더 카탈로그 확장 (feat/free-provider-catalog)

### 배경

사용자가 "무료로 쓸 수 있는 API들 싹 긁어서 넣을 수 있게 하는게 좋지 않을까"라고
제안. 자동 발굴/다중계정 가입(대부분 프로바이더 ToS의 "1인 1계정" 조항 위반 소지, 이미
Plan.md 로드맵의 "multi-API-key rotation" 항목도 같은 리스크)과 "공식 문서로 확인된
무료 프로바이더만 큐레이션해서 추가" 두 방향을 제시했고, 사용자가 후자를 선택.

### 한 일

1. **웹 리서치로 사실 검증** — Cerebras/SambaNova/Gemini/Zhipu(z.ai)/OpenRouter 5개를
   병렬 서브에이전트로 공식 문서 기준 조사. 판단 기준은 "가입 시 1회 지급되는 소진형
   크레딧"이 아니라 "사용량과 무관하게 반복 리셋되는 rate-limit 티어"인지 — 이 기준이
   없으면 NVIDIA처럼 실제로는 트라이얼 크레딧인 걸 "무료"로 잘못 표시하는 실수를
   반복하게 됨. 결과는 [Research.md](Research.md) 2026-07-09 항목에 출처와 함께 기록.
2. **PROVIDER_CATALOG 확장** (`forge_gateway/settings.py`) — Cerebras/SambaNova/Gemini는
   `free: true`(recurring 확인됨), Zhipu(`zai`)는 무료·유료 모델이 같은 키에 혼재하므로
   `free: false`로 등록(모델 단위 오버라이드는 사용자가 필요시 직접 추가하도록 안내).
3. **`registry.merge_discovered` 개선** — discovery로 찾은 모델 id가 OpenRouter
   컨벤션인 `:free` 접미사면, provider 전체의 free 플래그와 무관하게 price=(0,0) 처리.
   이전에는 provider가 free가 아니면 무조건 price 미상 취급이라, OpenRouter의 실제
   무료 모델이 `allow_paid: false` 정책에서 보수적으로 제외되는 문제가 있었음 — 하드코딩
   목록이 아니라 규칙으로 처리해 OpenRouter 카탈로그 변경에도 자동으로 맞음.
4. `.env.example`, README(자동 인식 프로바이더 목록, 무료 티어 설명) 갱신.
5. 회귀 테스트 4건 추가(`test_settings.py` 2건, `test_registry.py` 2건) — 신규
   카탈로그 항목 등록 확인, `:free` 접미사 가격 처리, 비-free 프로바이더의 일반 모델은
   여전히 가격 미상 처리되는지.

### 오류/수정

- 로컬 환경에 `prometheus_client`가 설치돼 있지 않아 `test_prom`/`test_simulator_scenarios`
  2개 모듈이 import 실패 — pyproject.toml에는 선언돼 있었으나 editable install 시점
  이후 추가된 의존성이 재설치 없이는 안 잡혀 있었던 것. `pip install -e .` 재실행으로
  해결(코드 변경 아님, 환경 문제). 전체 220건 테스트 통과(신규 4건 포함) 확인.

### 설계 결정

- **모델 단위 무료 판정이 필요한 프로바이더(OpenRouter)는 규칙(`:free` 접미사 감지)으로,
  프로바이더 단위로 충분한 경우(Cerebras/SambaNova/Gemini)는 카탈로그의 `free` 플래그로**
  — 두 메커니즘을 혼용하지 않고 프로바이더의 실제 과금 구조에 맞춰 선택. Zhipu처럼 혼재
  구조인데 공식적으로 구분 가능한 접미사가 없는 경우는 안전한 쪽(가격 미상 → 보수적 제외)을
  기본값으로 두고, forge.yaml에 기본 반영하지 않음(README 철학 "Adding a provider is just
  an API key" 유지 — 아무도 안 쓰는 provider 설정이 기본 파일에 끼어들지 않게).
- `free` 플래그의 의미를 "결제수단 미연결 시 기본 경로"로 명확히 함(기존 NVIDIA 관례와
  통일) — 사용자가 나중에 카드를 연결하면 과금 전환될 수 있다는 점을 README/주석에 명시.

### 남은 문제 및 다음 할 일

- [ ] 사용자가 실키(Cerebras/SambaNova/Gemini/Zhipu)로 `forge doctor`/`forge start` 실연동 검증
- [ ] SambaNova/Zhipu의 API 키 env var명(`SAMBANOVA_API_KEY`/`ZAI_API_KEY`)이 서드파티
      관례일 뿐 공식 문서에 미명시 — 추후 공식 확정되면 재확인
- [ ] Gemini 정확한 RPM/RPD 수치가 공식 문서에서 대시보드로 위임돼 실측 필요(M2-18과 동일 성격)

### 블로그/포트폴리오 소재

- "무료 티어 vs 트라이얼 크레딧 구분하기 — LLM 게이트웨이에 프로바이더를 추가할 때
  놓치기 쉬운 함정"과, `:free` 접미사 하드코딩 대신 규칙화한 이유.

### Learning Recovery

- AI가 주도: 5개 프로바이더 공식 문서 병렬 조사, 설계(모델 단위 vs 프로바이더 단위 free
  판정 분리), 테스트 작성.
- 다음에 직접 설명해보면 좋을 질문: (1) `registry.merge_discovered`에서 `:free` 접미사
  판정이 `pconf.free`보다 먼저/나중에 적용돼도 결과가 같은 이유, (2) SambaNova의
  "1회성 $5 크레딧"과 "결제수단 미연결 시 Free Tier"가 왜 서로 다른 개념인지.

## 2026-07-09 — M3 플랫폼화 (feat/m3-platform)

### 한 일

1. **Capability 학습 루프(직접, §5.11-3)**: request_metrics 집계(`capability_stats`, cancelled 제외) → 결정적 보정 규칙(실패율 50%↑=-2 / 25%↑=-1 / 2%↓&표본20↑=+1) → `ModelEntry.capability_adjustments`에 반영, Scheduler가 ±2 클램프로 적용. tools 포함 요청 실패율 50%↑ 시 **feature 자동 강등** + `demoted_features` 노출. 30분 주기, reload 연동
2. **내장 정적 대시보드(Opus 위임)**: 단일 HTML SPA `/dashboard/ui` — 스탯 타일/tier 보드(쿨다운 카운트다운)/스로틀 게이지/7일 SVG 차트/정책 뱃지, 외부 리소스 0, 3초 폴링
3. **Prometheus(Sonnet 위임)**: `/metrics` 전환(자체 CollectorRegistry, config 모델만 게이지로 카디널리티 제한), MetricsEngine `on_record` 훅으로 카운터/히스토그램, JSON은 `/v1/stats` 유지
4. 범위 결정(DecisionLog): Next.js→내장 SPA, prometheus-client 승인, PG/Redis/멀티키/AI Judge 보류·후속

### 검증

- 전체 185건 테스트 통과, 스모크 31항목 통과 (reload 후 exporter 생존 포함)

### 종합 검토 라운드 (3렌즈 병렬 리뷰 → 일괄 수정)

코드 정밀(Opus 14건) / 신규 사용자 워크스루(Sonnet 11건) / 문서-코드 일치성(Sonnet 10건) 리뷰 후 수정:

- **[HIGH] mid-stream 실패 오분류**: `cancelled`로 기록돼 health·학습 루프가 못 보고 세션이 고장 난 모델에 계속 고정되던 문제 → record_failure 반영으로 자가 치유 복원
- **컨텍스트 초과 상향 failover의 fail-closed**: 창 미상(기본 설정) 모델 전멸 → 미상은 배제하지 않도록 수정, min_ctx는 보정 전 창 기준
- **비모델 실패의 health 오귀책 2종**: 4xx(클라이언트 잘못)와 스로틀 슬롯 포화가 멀쩡한 모델을 쿨다운시키던 문제 → 귀책 제거, 슬롯 포화는 rpm 토큰 환불 + `throttled` 기록
- **인증 계약 구멍**: `/admin/*`·`/v1/stats*`에 API 키 검증 누락(§5.8 위반) → 이중 검증 적용
- 세션 핀의 스로틀 필터 우회, 직접 지정 모델의 rpm 게이트 오적용, provider 예외 분류 순서(429/5xx 문구 오탐), 업스트림 에러 키 에코 마스킹, reload 백그라운드 태스크 GC 유실, ewma_alpha 리로드 미반영, Content-Length 조작 500, `defaults.tier` 무검증, 버전 문자열 3곳 불일치(pyproject 단일화)
- **i18n**: 사용자 노출 로그 ~26건 영어화(cp949 콘솔 깨짐 해결), CLI stdout UTF-8 재구성, Prometheus HELP 영어화, guard 음수 검증, `forge models` discovery 안내줄
- **문서 동기화**: README(로드맵 자기모순·엔드포인트·CLI 7종), DESIGN(§4/§5.8/§10 현행화), IA 전면 갱신, UserScenarios S1/S4, CHANGELOG.md 신설, PUBLISHING 재배포 절차, run_forge.bat 삭제(레거시)
- 검증: 전체 216건 + 스모크 통과. 알려진 한계로 문서화: chunked 요청의 바디 제한 우회, reload 직후 스로틀 상태 리셋(과도기 2×max_concurrent)

### UX 스프린트 (사용자 워크스루 피드백, U1~U5)

- `forge reload`/`forge guard --no-paid|--max-cost|--off`/`forge policies` CLI (Sonnet — 실서버 라이브 검증, cp949 콘솔 크래시 예방까지)
- **forge.local.yaml 오버레이**: 기계 전용 파일로 지출 가드 관리 — 손으로 쓴 forge.yaml 주석 보존
- `forge start` 배너(대시보드/클라이언트 연결 안내), 유료 자동등록 경고 로그, init 템플릿 가드 주석
- `/v1/stats/recent` + 대시보드 최근 요청 피드(failover 강조, cancelled 회색) + Route Explain 실행기
- 테스트 210건 + 스모크 통과 (스모크의 docs 정책 기대값은 계층화 정책으로 갱신)

### 후속 (사용자 피드백 반영)

- **대시보드 밀도**: 차트를 상단으로, 유휴 discovered 모델 접기 토글 (main `61b6283`)
- **프로바이더 자동 등록** (auto_providers): 카탈로그 8종(nvidia/openrouter/groq/mistral/deepseek/openai/anthropic/ollama) — .env에 키만 넣으면 provider 자동 등록 + discovery. 명시 선언 우선, `auto_providers: false`로 opt-out, anthropic은 대표 모델 자동 공급
- 그 과정에서 **server.py의 import 부작용 발견·제거**: 모듈 레벨 `app = create_app()`이 테스트/도구 import 시 저장소 설정과 .env를 로드해 실키를 주입 → 시뮬레이터 테스트가 실제 API로 유출되던 문제. 테스트 config 밀폐화(auto_providers: false + 카탈로그 키 격리) 포함. 전체 190건 통과

## 2026-07-09 — M2.5 정리 스프린트 (feat/m2.5-cleanup)

### 한 일

1. **패키징(직접)**: `src` → `forge_gateway` rename(PyPI `forge` 패키지와 import 충돌 회피), pyproject.toml(엔트리포인트 `forge`/`forge-gw`), README 퀵스타트를 `pip install -e .` + CLI 흐름으로
2. **벤치마크 시드(직접, 웹 리서치)**: SWE-bench V/Pro·LiveCodeBench 기준으로 forge.yaml 점수 교체, 근거·출처는 Research.md. **MiniMax M3 tier3→tier2 승격**(SWE-Pro 59.0%), gpt-oss-120b 에이전트 작업 하향 등
3. **Provider Simulator(Opus 위임)**: localhost mock OpenAI 서버 + 실 litellm 스택 E2E 시나리오 12종 — FakeProvider가 가리던 **실스택 버그 2건 발견**:
   - Retry-After 미반영: litellm 1.91.x는 헤더를 `e.litellm_response_headers`에 담음 → 폴스루 조회로 수정 (빈 헤더 객체가 체인을 끊던 문제 포함)
   - usage 청크 누출: litellm이 usage 전용 청크에 빈 delta의 choices를 합성 → `_chunk_has_payload` 판별로 수정
4. 플레이키 수정: test_throttle 슬롯 테스트의 고정 sleep → 세마포어 동기화 (전체 스위트 부하에서 간헐 실패하던 원인)

### 검증

- 전체 153건 테스트 3회 연속 통과. editable install 후 `forge models` 동작

### 검증 중 발견·수정 (사용자 리포트)

- **.env 미인식**: `forge doctor`가 .env의 키를 못 읽음 — M1 재작성 때 .env 로드가 run_forge.bat에만 남고 CLI/서버 경로에서 빠짐 → `load_config`가 설정 파일 옆 .env를 자동 주입하도록 수정 (셸 변수 우선, stdlib 구현, 테스트 4건). main `b9736d7`
- **probe 캘리브레이션 3건** (`forge start` 실기동 로그): ① probe 10초 타임아웃이 tier1 reasoning 모델(느릴 뿐 정상)을 unhealthy로 오판 → 타임아웃은 상태 변경 없이 로그만 ② discovery 등록 110+개 모델까지 주기 probe 대상 → config 모델로 한정 (§2-5 자해 재발 방지) ③ 워밍업/모니터 첫 사이클 중복 probe → last_check 유휴 판정 + 기동 순서 변경. main `b01dbfa`
- **`/v1/messages` 스트리밍 크래시** (Claude Code 실연동): `OpenAIToAnthropicStream` 배선 시 `request_model` 인자 누락 — 스트리밍 응답이 200 헤더 후 본문 생성에서 TypeError. 원인: 라우터를 통과하는 anthropic 스트리밍 E2E 테스트 부재 → 수정 + 회귀 테스트 2건 추가. main `e3cc9d0`. **같은 세션에서 확인된 정상 동작**: 실전 failover(qwen 500→deepseek), 세션 고정, tool use 왕복(Write 승인 프롬프트)

### 통합 검증 결과 (2026-07-09, 실키)

- [x] `forge doctor` — 키 인식·연결 (env 로드 수정 후)
- [x] `forge start` — probe 캘리브레이션 수정 후 정상 부팅
- [x] OpenAI 호환 E2E — auto → task 분석 → 모델 선택 → 응답 + forge 헤더
- [x] **Claude Code 실연동** — `/v1/messages` 스트리밍·tool use(Write) 왕복으로 hello.py 실작성, 세션 고정 유지
- [x] 실전 failover 관측 (qwen 500 → deepseek attempt=2 → 200)
- [x] 대시보드 JSON 조회
- [ ] Cline 실연동 (OpenAI 경로 E2E로 사실상 커버 — 필요 시 확인)
- [ ] Ollama/OpenRouter (키/로컬 환경 필요 시)

### 남은 것

- [ ] M3 착수 (Dashboard UI, Prometheus, PostgreSQL) — 지시 대기
- [x] PyPI 등록 완료 (2026-07-09) — `forge-gateway 0.3.0.dev0`, 이름 확보

## 2026-07-09 — M2 구현 (feat/m2-intelligence)

### 한 일

1. **직접 구현**: Policy Engine(§5.4 — first-match 평가, tier/모델/속성 셀렉터, constraints 누적, 직접 지정 모델에도 적용), Scheduler 그룹 라우팅 + provider_filter, 스로틀 파이프라인 배선(peek→consume→slot), `/v1/messages` 엔드포인트(ChatPipeline dialect), `/v1/route/explain`(핀 비변경 드라이런), `/admin/reload`(원자적 교체·health 이관·discovered 보존·60초 지연 close)
2. **위임 구현**: Anthropic 변환 모듈(Opus, 24케이스 — 529 중단 후 재개), 선제 스로틀(Opus, 14케이스), 가격표 폴백(Sonnet, 12케이스 — 자체 프로브로 폴스루 버그 수정), CLI(Sonnet, 13케이스 — doctor는 실 NVIDIA 엔드포인트 검증)
3. forge.yaml 정책 예시(docs-prefer-writers + default), README M2 갱신(Claude Code 연동)
4. 검증: unittest 141건 통과(3회 반복으로 플레이키 확인 — 안정), 스모크 24항목 통과(reload 왕복 포함)

### 오류/수정

- Opus 에이전트 1개가 API 과부하(529)로 중단 → SendMessage로 재개해 완료
- 권한 분류기 장애로 셸 실행이 장시간 차단 → 비셸 작업(README, 배선 코드)으로 우회 후 재개
- 테스트 1회 일시 실패(재현 불가, 3회 연속 통과) — 재발 시 추적

### 남은 것 (M2 잔여)

- [ ] Ollama/OpenRouter/Anthropic 실검증 (사용자 키/로컬 필요)
- [ ] Claude Code 실연동 스모크 (`ANTHROPIC_BASE_URL=http://127.0.0.1:4000`)
- [ ] capability 벤치마크 시드 (리서치), Provider Simulator (후속)

## 2026-07-09 — M1 구현 (feat/m1-foundation)

### 한 일

1. Approval Gates 승인 처리 (PyYAML, DB 재생성) — 라이선스는 미결 유지
2. **직접 구현**: forge.yaml 스키마/로더(settings.py), 타입 계약(types.py), Provider 프로토콜+typed 예외(providers/base.py), Registry(EWMA·슬라이딩 윈도·429 즉시 쿨다운), Scheduler(하드 필터→세션 고정→스코어링), API 계층(failover 루프·스트리밍 first-chunk 커밋·usage 수집·취소 전파·총 데드라인), 서버 조립
3. **위임 구현** (병렬 서브에이전트): LiteLLM Provider(Opus), Metrics 저장 계층(Opus), Analyzer(Sonnet), Health Monitor(Sonnet), 테스트 스위트(Sonnet)
4. 구 파일 삭제: src/analyzer·config·scheduler·health_monitor·metrics.py, config.yaml, run_litellm.bat, test_forge.py. 커밋돼 있던 pyc/forge.db 추적 해제, .gitignore 보강
5. 검증: unittest 67건 통과, 통합 스모크 13항목 통과 (실기동에서 Auto Discovery가 NVIDIA 모델 110개 등록 확인)

### 오류/수정

- **Scheduler 세션 고정 버그** (테스트 에이전트 발견): 고정 확인이 tier 루프 내부에 있어 상위 tier 가용 시 하위 tier 핀이 무시됨 → 루프 진입 전 전역 확인으로 수정, 회귀 테스트 유지
- MetricsEngine.start() 동기/비동기 불일치 → server.py 수정
- Health 에이전트가 정리 중 .gitignore 미커밋 변경을 실수로 되돌림 → 복구

### 남은 것 (M1)

- [x] LICENSE + README — MIT 확정 후 작성 완료 (클라이언트별 연동 스니펫, 프라이버시 명시 포함)
- [ ] NVIDIA 키로 실제 라우팅 실기동 검증 (사용자 환경)

### 후속 기록

- M1 squash merge → main `6e99236`. gh CLI 부재로 git 네이티브 squash merge 사용 (gh 설치 권장 전달)
- 라이선스 MIT 확정 → LICENSE + README.md 작성 (M1 전 항목 종료)

## 2026-07-09 — 설계 확정 + 프로젝트 문서 체계 구축

### 한 일

1. **DESIGN.md 작성** — prd.md와 기존 v0.1 코드(`src/`)를 전수 분석해 설계서 작성
   - 현재 코드 진단 12건 (프로바이더 하드코딩, 스트리밍 failover 부재, 쿨다운 불일치, 헬스체크 rate limit 자해, 메트릭 블로킹 등)
   - 목표 아키텍처: Analyzer → Policy Engine → Scheduler(하드 필터→세션 고정→스코어링) → Provider Layer(LiteLLM SDK)
2. **오픈소스 경쟁 분석 반영** — LiteLLM/Portkey/RouteLLM/Arch 비교 → 차별점 확정, 개선점 6건 반영 (기능 플래그 필터, 세션 고정, /v1/messages, Analyzer 3계층, capability 수명주기, TTFT+비용)
3. **OSS 공개 관점 재검토 반영** — 13건 + 추가 발견 반영: 스트리밍 usage 강제 수집, 취소 전파, 선제 스로틀, 타임아웃 3단 예산, context_length 상향 failover, §8(배포/DX)·§9(테스트 전략) 신설, graceful shutdown, 보존 정책 등
4. **PyPI 패키지명 조사** — 16개 후보 조회: `forge-gateway` 가용 확인, `forge` 등 6개 선점. Foundry `forge` CLI 충돌 리스크 식별
5. **PUBLISHING.md 작성** — PyPI 등록 체크리스트 (계정/2FA/토큰, pyproject.toml, build/twine, PEP 541 주의)
6. **CLAUDE.md 규칙 추가** — 브랜치/PR/squash merge 워크플로우 (규칙 8), 예외: 문서만 수정 시 main 직접 커밋/푸시
7. **필수 산출물 스캐폴딩** — docs/ 11개 문서 생성 (본 문서 포함)
8. **PromptLog 폐지** — 사용자 결정으로 필수 산출물에서 제외, 파일 삭제 (DecisionLog 참고)

### 확인된 사실

- NVIDIA 무료 티어 RPM 한도 = 40 (사용자 실측)
- PyPI JSON API로 이름 가용성 확인 가능 (404 = 미등록)

### 오류/수정

- 없음 (이번 세션은 설계·문서 작업만, 코드 변경 없음)

### 다음 할 일

- [ ] M1 구현 착수 — [Plan.md](Plan.md)의 작업 순서대로, `feat/m1-*` 브랜치
- [ ] PyPI `forge-gateway` 등록 (사용자, [PUBLISHING.md](../PUBLISHING.md))
- [ ] 라이선스 확정 (MIT vs Apache-2.0)
