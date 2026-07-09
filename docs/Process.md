# Process — 작업 방식과 절차

> 이 프로젝트에서 일하는 방법. 규칙의 원본은 [CLAUDE.md](../CLAUDE.md), 이 문서는 실행 절차로 풀어쓴 것.

## 작업 흐름 (마일스톤 단위)

```text
DESIGN.md (설계 확정)
   ↓
docs/Plan.md 갱신 (이번 마일스톤의 작업 순서·완료 기준)
   ↓
작업 단위별 반복:
   브랜치 생성 (feat/..., fix/..., refactor/..., docs/...)
   → 구현 + 테스트
   → PR 생성 (gh pr create — 변경 요약 + 검증 결과)
   → squash merge (gh pr merge --squash)
   → 브랜치 삭제
   → WorkLog 기록, Plan.md 상태 갱신
   ↓
마일스톤 완료 → ReviewChecklist.md 점검 → 다음 마일스톤 Plan 갱신
```

## Git 규칙 요약

| 변경 유형 | 방법 |
| --- | --- |
| 큰 기능/변경 (코드, 여러 파일, 구조·스키마·설정 변경) | 브랜치 → PR → squash merge |
| 문서만 수정 (코드 미포함) | main 직접 커밋/푸시 허용 |

## 문서 갱신 주기

| 문서 | 갱신 시점 |
| --- | --- |
| WorkLog.md | 매 작업 세션 종료 시 |
| DecisionLog.md | 의사결정 발생 즉시 (Approval Gate 승인 결과 포함) |
| PromptLog.md | 사용자의 주요 프롬프트 수신 시 |
| Plan.md | 마일스톤 시작 전 + 작업 상태 변화 시 |
| ProjectContext.md | 배경/방향 전환 시 |
| DESIGN.md | 설계 변경 시 (구현과 어긋나면 설계서를 먼저 고친다) |
| IA.md / UserScenarios.md / Personas.md | 해당 구조·시나리오 변경 시 (IA 변경은 Approval Gate) |
| Research.md | 기술 조사 수행 시 |

## Approval Gates

CLAUDE.md의 Approval Gates 목록에 해당하면 **즉시 멈추고 사용자 확인** → 결과를 DecisionLog에 기록. 구현 중 발견해도 예외 없음.

## 구현 원칙 (DESIGN.md에서 파생)

- 설계서와 코드가 어긋나면 어느 쪽이 맞는지 먼저 결정하고 문서부터 정합화
- 실패 경로가 제품이다 — 새 기능에는 실패 시나리오 테스트를 함께 (Provider Simulator, §9)
- 메트릭/로깅은 요청 경로를 절대 블로킹하지 않는다
- 기본값은 안전하게 (127.0.0.1, 텔레메트리 없음, 프롬프트 미저장)
