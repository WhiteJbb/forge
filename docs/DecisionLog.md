# Decision Log

주요 의사결정과 그 근거를 기록한다. (규칙: [CLAUDE.md](../CLAUDE.md) Working Rules 5)

---

## 2026-07-09 — Git 브랜치/PR 워크플로우 도입

**결정**: 큰 기능·변경은 main 직접 커밋 금지. 브랜치(`feat/`, `fix/`, `refactor/`, `docs/`) 작업 → PR 생성 → **squash merge** → 브랜치 삭제. 예외: **문서만 수정하는 변경(코드 미포함)은 main 직접 커밋/푸시 허용** (2026-07-09 사용자 확정).

**이유**: main 히스토리를 PR 단위 1커밋으로 유지해 변경 추적과 롤백을 단순화. 오픈소스 공개(DESIGN.md §8) 후 외부 기여 워크플로우와도 일치.

**출처**: 사용자 지시 (CLAUDE.md Working Rules 8로 성문화)

## 2026-07-09 — PyPI 패키지명 `forge-gateway` 확정 권장

**결정**: PyPI 패키지명 후보 중 `forge-gateway` 권장 (조기 등록 필요). `forge`, `forge-ai`, `llmforge` 등은 선점 확인(2026-07).

**이유**: 프로젝트 성격을 그대로 설명하고, 설계서에서 이미 가칭으로 사용 중. 등록 절차는 [PUBLISHING.md](../PUBLISHING.md).

**미결**: 최종 확정 및 PyPI 등록은 사용자 실행 대기.

## 2026-07-09 — Git 산출물에 AI 흔적 금지

**결정**: 커밋 메시지·PR 본문·브랜치명 등에 `Co-Authored-By: Claude`, "Generated with Claude Code" 같은 AI 서명/흔적을 남기지 않는다 (CLAUDE.md 규칙 9). 이미 푸시된 문서 커밋은 amend + force push로 정리.

**이유**: 사용자 지시 — 저장소 히스토리를 도구 흔적 없이 유지.

## 2026-07-09 — NVIDIA 무료 티어 RPM 한도 40 확인

**결정**: 선제 스로틀링(§5.13) 기본값 `rpm: 40` — 사용자 실측 확인값.
