# Research — 기술 조사 기록

> 조사 일자와 출처를 남긴다. 오래된 조사는 재검증 후 사용.

---

## 2026-07-09 — 오픈소스 게이트웨이 경쟁 분석

> 지식 기준: 2026년 초. 상세 논의는 DESIGN.md에 반영 완료.

| 프로젝트 | 성격 | Forge와 겹침 | 없는 것 |
| --- | --- | --- | --- |
| LiteLLM | 사실상 표준 게이트웨이 | failover, 쿨다운, latency/cost 라우팅, 예산, Prometheus | 요청 내용 기반 라우팅 |
| Portkey Gateway | 오픈소스 게이트웨이 | Config 기반 fallback/LB, 가드레일 | 내용 기반 라우팅 |
| RouteLLM (LMSYS) | 학습형 라우터 | 내용 기반 (강/약 모델 선택) | 게이트웨이 기능, 투명성 |
| Arch Gateway | Envoy + 소형 라우터 LLM | 정책 기반 선호 라우팅 (가장 유사) | 코딩 에이전트 특화, 경량성 |
| one-api / new-api | 채널 집계 프록시 | 멀티 프로바이더, 쿼터, 멀티 키 | 내용 기반 라우팅 |
| TensorZero | Rust 게이트웨이 + 실험 | A/B, 피드백 루프 | 코딩 특화 |

**결론**: "게이트웨이 기능 넓이"는 레드오션. **코딩 에이전트 특화 × 내용 인지 × 정책**의 교집합이 빈 공간. 넓이 경쟁 회피, litellm SDK에 프로바이더 커버리지 위임.

## 2026-07-09 — PyPI 패키지명 가용성 조사

방법: `https://pypi.org/pypi/<name>/json` HTTP 상태 코드 (404 = 미등록).

- **선점(200)**: `forge`, `forge-ai`, `llmforge`, `modelforge`, `agentforge`, `codeforge`
- **가용(404)**: `forge-gateway` ✅(권장), `forge-llm`, `forge-router`, `forge-proxy`, `llm-forge`, `ai-forge`, `forgeai`, `aiforge`, `forgegateway`, `forge-gw`
- 주의: PyPI 이름 정규화(`-`/`_`/대소문자 동일 취급). `llm-forge`는 선점된 `llmforge`와 한 글자 차이라 혼동 위험 → 비추천.
- CLI 충돌: Foundry(Ethereum)의 `forge` 바이너리와 PATH 충돌 가능 → `forge-gw` alias 병행.
- 등록 절차: [PUBLISHING.md](../PUBLISHING.md)

## 2026-07-09 — 프로바이더 한도 실측

- NVIDIA 무료 티어(integrate.api.nvidia.com): **40 RPM** (사용자 실측 확인) → forge.yaml `rpm: 40` 기본값 근거

## 조사 예정

- [ ] litellm SDK의 `stream_options` / usage 청크 동작 방식 (M1-6 착수 전)
- [ ] Anthropic Messages ↔ OpenAI 포맷 변환 시 tool use 블록 매핑 (M2-16 착수 전)
- [ ] 공개 코딩 벤치마크 소스 확정 — Aider polyglot leaderboard, LiveCodeBench 최신 상태 (M2-22 착수 전)
- [ ] OpenRouter/Ollama 무료 한도 실측 (M2-18 착수 전)
