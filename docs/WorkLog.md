# Work Log — 작업 진행 내역

> 진행한 작업, 발생한 오류와 수정 내역을 세션 단위로 기록한다. 최신이 위.

---

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

### 남은 것

- [ ] **사용자 통합 검증**: `forge doctor` → `forge start` → Cline(모델 auto) + Claude Code(`ANTHROPIC_BASE_URL=http://127.0.0.1:4000`) 실사용, Ollama/OpenRouter 키 있으면 함께
- [ ] 검증 통과 후 M3 착수 / PyPI 등록(PUBLISHING.md — pyproject 준비 완료)

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
