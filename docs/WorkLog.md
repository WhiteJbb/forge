# Work Log — 작업 진행 내역

> 진행한 작업, 발생한 오류와 수정 내역을 세션 단위로 기록한다. 최신이 위.

---

## 2026-07-09 — M3 플랫폼화 (feat/m3-platform)

### 한 일

1. **Capability 학습 루프(직접, §5.11-3)**: request_metrics 집계(`capability_stats`, cancelled 제외) → 결정적 보정 규칙(실패율 50%↑=-2 / 25%↑=-1 / 2%↓&표본20↑=+1) → `ModelEntry.capability_adjustments`에 반영, Scheduler가 ±2 클램프로 적용. tools 포함 요청 실패율 50%↑ 시 **feature 자동 강등** + `demoted_features` 노출. 30분 주기, reload 연동
2. **내장 정적 대시보드(Opus 위임)**: 단일 HTML SPA `/dashboard/ui` — 스탯 타일/tier 보드(쿨다운 카운트다운)/스로틀 게이지/7일 SVG 차트/정책 뱃지, 외부 리소스 0, 3초 폴링
3. **Prometheus(Sonnet 위임)**: `/metrics` 전환(자체 CollectorRegistry, config 모델만 게이지로 카디널리티 제한), MetricsEngine `on_record` 훅으로 카운터/히스토그램, JSON은 `/v1/stats` 유지
4. 범위 결정(DecisionLog): Next.js→내장 SPA, prometheus-client 승인, PG/Redis/멀티키/AI Judge 보류·후속

### 검증

- 전체 185건 테스트 통과, 스모크 31항목 통과 (reload 후 exporter 생존 포함)

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
