# Plan — 현재 구현 계획

> 구현 착수 전 반드시 이 문서를 갱신하고, 이 계획에 따라 구현한다.
> 마일스톤 전체와 설계 근거는 [DESIGN.md](../DESIGN.md) §10이 단일 소스다. 이 문서는 "지금 하는 작업"의 실행 계획만 담는다.
> M1 기록: [WorkLog.md](WorkLog.md) 2026-07-09 항목 (13/13 완료, main `6e99236`).

## 현재 마일스톤: M2 — 지능 계층 (진행중)

목표: Policy Engine을 중심으로 한 정책 기반 라우팅 + Anthropic 호환 + 선제 스로틀.
진행 방식: 브랜치 `feat/m2-intelligence` → 로컬 squash merge (PR 생략 — 임시, DecisionLog 참조).

| # | 작업 | DESIGN.md | 담당 | 상태 |
| --- | --- | --- | --- | --- |
| 14 | **Policy Engine** — 스키마·평가기·scheduler 연동·constraints | §5.4 | 직접 | 진행중 |
| 15 | 세션 고정 | §5.5-1 | — | M1에서 선행 구현됨 |
| 16 | **`/v1/messages` Anthropic 호환** — 포맷 변환(tool use·스트리밍 이벤트) | §5.8 | Opus 위임 | 진행중 |
| 17 | Analyzer 개선 (힌트 채널·구조 신호·가중) | §5.3 | — | M1에서 선행 구현됨 |
| 18 | **선제 rate limiting** — provider별 token bucket + 세마포어 | §5.13 | Opus 위임 | 진행중 |
| 19 | Auto Discovery 배선 | §5.2 | — | 부팅분 M1 완료, reload 연동은 #20 |
| 20 | `/admin/reload` 핫 리로드(원자적 교체·health 보존) + `/admin/provider` | §5.8 | 직접 | 대기 |
| 21 | Ollama/OpenRouter/Anthropic 프로바이더 검증 | §5.1 | 사용자 환경 | 구성 지원만 (실검증은 키/로컬 필요) |
| 22 | 벤치마크 기반 capability 시드 | §5.11-1 | 보류 | 별도 리서치 필요 |
| 23 | **가격표 배선** — litellm.model_cost 폴백 + 비용 계산 | §5.12 | Sonnet 위임 | 진행중 |
| 24 | **`/v1/route/explain`** 드라이런 | §5.8 | 직접 (Policy 이후) | 대기 |
| 25 | **CLI** — `forge start/init/doctor/models` | §8.1 | Sonnet 위임 | 진행중 (pyproject 패키징은 PyPI 등록 시점으로 분리) |
| 26 | 테스트 하네스 — Provider Simulator | §9 | Sonnet 위임 | 대기 (코어 통합 후) |

### 완료 기준

- 정책 YAML로 PRD의 4개 예시 정책이 전부 동작 (기본 정책 미정의 시 M1과 동일한 tier 라우팅 = 하위 호환)
- `/v1/messages`로 논스트리밍+스트리밍+tool use 왕복 변환 (Claude Code 실연동 스모크는 사용자 환경)
- 스로틀: rpm 소진 시 429 없이 다른 provider로 분산되는 통합 테스트
- 전체 unittest 통과 + 스모크 통과

### 리스크

- Anthropic 스트리밍 이벤트 변환 충실도 (§11) — 변환 모듈 단위 테스트로 방어, 실연동은 후속
- reload의 원자성 — in-flight 요청은 구 참조로 완주, 신규는 새 참조 (지연 close)
