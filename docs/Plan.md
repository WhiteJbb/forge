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
