# Plan — 현재 구현 계획

> 구현 착수 전 반드시 이 문서를 갱신하고, 이 계획에 따라 구현한다.
> 마일스톤 전체와 설계 근거는 [DESIGN.md](../DESIGN.md) §10이 단일 소스다. 이 문서는 "지금 하는 작업"의 실행 계획만 담는다.

## 현재 마일스톤: M1 — 기반 재정렬 (착수 전)

목표: 코드 구조를 목표 아키텍처(DESIGN.md §3~§4)로 옮기고 알려진 결함을 잡는다. 기능 외형은 거의 동일.

### 작업 순서 (의존성 순)

| # | 작업 | DESIGN.md | 상태 |
| --- | --- | --- | --- |
| 1 | 디렉터리 재구성 + 데드 코드 제거 | §4 | 완료 |
| 2 | forge.yaml 로더 + 검증 (`version`, 기본 바인딩 127.0.0.1) | §5.9 | 완료 |
| 3 | Model Registry (`features` 포함) | §5.2 | 완료 |
| 4 | Provider Layer — LiteLLM SDK (drop_params, reasoning 정규화, 에러 정규화) | §5.1 | 완료 |
| 5 | 요구 기능 하드 필터 | §5.5-0 | 완료 |
| 6 | 스트리밍 failover + usage 강제 수집 | §5.8 | 완료 |
| 7 | 쿨다운 정합화 + context_length 상향 failover | §5.5, §7 | 완료 |
| 8 | 타임아웃 3단 예산 + 취소 전파 | §5.13 | 완료 |
| 9 | Metrics write-behind + 스키마 보강 + 격리 + 보존 + shutdown flush | §5.7 | 완료 |
| 10 | 인증 + 메타데이터 헤더 + 키 마스킹 | §5.8 | 완료 |
| 11 | /v1/embeddings | §5.8 | 완료 (명시 모델 지정 방식) |
| 12 | Health passive 전환 + 스태거링 + 워밍업 | §5.6 | 완료 |
| 13 | LICENSE + README 클라이언트 연동 문서 | §8.2 | 보류 — 라이선스 결정 대기 |

> M1 구현: unittest 67건 + 통합 스모크 13항목 통과. 실기동 검증(NVIDIA 키로 실제 라우팅)은
> 사용자 환경에서 확인 필요. 취소 전파는 코드 구현됨, 자동 테스트 미커버(수동 확인 항목).

### 진행 방식

- 작업 단위별로 브랜치(`feat/m1-<작업명>`) → PR → squash merge (CLAUDE.md 규칙 8)
- 각 작업 완료 시 이 표의 상태 갱신 + WorkLog 기록
- Approval Gates 해당 항목(의존성 추가, DB 스키마 변경 등)은 착수 전 사용자 확인

### M1 완료 기준

- 기존 기능(chat completions, failover, 대시보드 JSON) 동작 유지
- test_forge.py 통과 + 신규 단위 테스트 (스코어링, 하드 필터, 쿨다운 전이)
- `forge.yaml` 하나로 모든 설정 표현 (config.py 하드코딩 제거)

## 다음: M2 — 지능 계층

Policy Engine, 세션 고정, /v1/messages, 선제 스로틀 등 — DESIGN.md §10 M2 항목 14~26. M1 완료 후 이 문서를 M2 실행 계획으로 갱신한다.
