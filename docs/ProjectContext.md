# Project Context — Forge

> 프로젝트 배경과 목표. 방향 전환이 생기면 이 문서를 갱신한다.

## 무엇을 만드는가

**Forge** — Intelligent AI Gateway for Coding Agents.

여러 LLM Provider(NVIDIA, OpenRouter, Ollama, Anthropic)를 하나의 OpenAI 호환 API(`localhost:4000`)로 통합하고, 요청 내용을 분석해 가장 적합한 모델을 실시간으로 선택하는 게이트웨이. 사용자는 모델을 신경 쓰지 않는다.

- 상세 요구사항: [prd.md](../prd.md)
- 아키텍처/상세 설계: [DESIGN.md](../DESIGN.md)

## 왜 만드는가 (문제)

- 코딩 에이전트(Cline, Claude Code, Aider, Continue…)마다 LLM 설정이 제각각이고, 사용자가 매번 모델을 골라야 한다.
- 무료 티어(NVIDIA 40 RPM 등)는 rate limit이 빡빡해서 429가 나면 수동으로 모델을 바꿔야 한다.
- 기존 게이트웨이(LiteLLM 등)는 모델 그룹 단위 라우팅만 하고, **요청 내용(작업 유형)을 보지 않는다.**

## 포지셔닝과 차별점 (2026-07 논의 확정)

게이트웨이 기능의 **넓이**로는 LiteLLM과 경쟁하지 않는다 (반드시 진다). 코딩 에이전트 트래픽의 특수성에서 나오는 **깊이**에 집중한다:

1. **Task-aware 라우팅** — 요청 내용 → 작업 유형 → capability 기반 모델 선택 (LiteLLM에 없음)
2. **세션 고정** — 같은 대화는 같은 모델로 (프롬프트 캐시 적중 + 에이전트 일관성)
3. **Policy Engine** — "어떤 모델"이 아니라 "어떤 정책"을 관리
4. **무료 티어 조합 최적화** — 선제적 rate limiting + failover
5. **라우팅 투명성** — 스코어 공식 + `/v1/route/explain` (학습형 블랙박스 라우터와 반대)

상세 비교 분석: [Research.md](Research.md)

## 현재 상태 (2026-07-09)

- v0.1 코드 존재 (`src/`): NVIDIA 단일 프로바이더, tier failover, 키워드 분석기, SQLite 메트릭
- **설계 완료**: DESIGN.md에 목표 아키텍처 + M1~M3 마일스톤 확정
- 다음 단계: M1 구현 착수 (기반 재정렬)

## 공개 계획

오픈소스로 공개 예정. PyPI 패키지명 `forge-gateway`(가용 확인, 등록 대기 — [PUBLISHING.md](../PUBLISHING.md)), 라이선스 MIT 또는 Apache-2.0 (미결).
