# Work Log — 작업 진행 내역

> 진행한 작업, 발생한 오류와 수정 내역을 세션 단위로 기록한다. 최신이 위.

---

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
