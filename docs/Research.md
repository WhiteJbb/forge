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

## 2026-07-09 — capability 벤치마크 시드 (DESIGN.md §5.11-1)

forge.yaml의 수기 점수를 공개 벤치마크 기반으로 교체. 주 신호: **SWE-bench Verified/Pro**(에이전트 코딩 — Forge 트래픽과 가장 유사), **LiveCodeBench**(오염 저항). Aider Polyglot 공개 리더보드는 당사 모델 대부분이 미등재라 보조 신호로만.

| 모델 (NVIDIA 서빙) | 확인된 수치 | code 시드 | 조치 |
| --- | --- | --- | --- |
| GLM-5.2 | SWE-Pro **62.1%** (오픈 1위, GPT-5.5 능가), 1M ctx | 10 | context 9→10 |
| DeepSeek V4 Pro | LiveCodeBench **93.5** (전 모델 선두급) | 10 | 유지 |
| Qwen3.5-397B-A17B | SWE-V **76.2%** / SWE-Pro **50.9%** | 9 | debug 9→8 (Pro 격차) |
| Mistral Medium 3.5 | SWE-V **77.6%** (Devstral 2·Qwen3.5 능가), 256K | 9 | code 7→9 상향 |
| DeepSeek V4 Flash | SWE-V **~76–78.4%** | 9 | code 8→9 |
| **MiniMax M3** | SWE-Pro **59.0%** (GPT-5.5·Gemini 3.1 Pro 능가 주장) | 9 | **tier3→tier2 승격**, code 6→9 — 기존 배치가 큰 저평가 |
| Nemotron 3 Ultra | BenchLM 종합 오픈 상위권 (Opus 4.8 뒤) | 9 | code 8→9 |
| Nemotron 3 Super 120B | LCB 81.2 / SWE-V 60.47% / SWE-Multi 45.78% | 7 | 유지 |
| gpt-oss-120b | LCB **88.0** vs SWE-Multi **30.8%** — 대비 극단 | 8 | debug/refactor 하향 (에이전트 작업 약함) |
| Mistral Large 3 / Small 4 / Llama-3.3-Nemotron-49B | 2026-07 자료 미확인 | — | 기존 추정 유지 + 주석 표기 |

**한계**: 벤치별 측정 조건이 달라 절대 비교 불가 — 시드는 상대 순위 근거로만 사용. NVIDIA 서빙판의 실효 컨텍스트/양자화는 미확인이라 context_window 하드 값은 미설정 (초과 시 상향 failover가 자가 보정). M3의 텔레메트리 보정 루프가 이 시드의 오차를 흡수하는 구조.

**출처**: [llm-stats Aider Polyglot](https://llm-stats.com/benchmarks/aider-polyglot), [morphllm SWE-bench Pro 리더보드](https://www.morphllm.com/swe-bench-pro), [BenchLM coding](https://benchlm.ai/blog/posts/best-llm-coding), [Nemotron 3 Super 실측](https://mpp-insights.com/blog/testing-nemotron-3-super-bi-platform), [Mistral Medium 3.5 가이드](https://www.aimadetools.com/blog/mistral-medium-3-5-complete-guide/), [2026-05 벤치 라운드업](https://codersera.com/blog/ai-agent-benchmarks-state-of-leaderboard-may-2026/)

## 2026-07-09 — NVIDIA 무료 티어 실측 속도 (스트리밍 TTFT, 모델당 1회)

| 모델 | TTFT | 총 시간 | 비고 |
| --- | --- | --- | --- |
| glm-5.2 | — | 180s 타임아웃 | 인터랙티브 사용 불가 |
| qwen3.5-397b | — | 180s 타임아웃 | 〃 |
| deepseek-v4-pro | 18.3s | 35.2s | 대형 중 유일한 생존자 |
| deepseek-v4-flash | 8.6s | 8.6s | |
| mistral-medium-3.5 | 19.9s | 24.2s | |
| nemotron-3-super | 2.8s | 2.8s | |
| mistral-small-4 | 0.4s | 0.8s | 최속 |

→ forge.yaml 정책을 신호 기반 계층화로 전환 (일상=고속 3종, 컨텍스트 3만↑/debug=v4-pro).
품질 벤치 순위와 무료 티어 응답성은 별개 축 — 정책이 이 간극을 메꾼다.

## 조사 예정

- [ ] litellm SDK의 `stream_options` / usage 청크 동작 방식 (M1-6 착수 전)
- [ ] Anthropic Messages ↔ OpenAI 포맷 변환 시 tool use 블록 매핑 (M2-16 착수 전)
- [x] 공개 코딩 벤치마크 소스 확정 → 2026-07-09 시드 완료 (위 섹션)
- [ ] OpenRouter/Ollama 무료 한도 실측 (M2-18 착수 전)
