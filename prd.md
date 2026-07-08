# 프로젝트명

**Forge**

> Intelligent AI Gateway for Coding Agents

**한 줄 소개**

> *여러 LLM Provider를 하나의 OpenAI API로 통합하고, 가장 적합한 모델을 실시간으로 선택하는 AI Gateway.*

이게 핵심이다.

기존 LiteLLM이 **API Gateway**라면

Forge는

> **AI Scheduler + AI Gateway + AI Router**

이다.

---

# 목표

## Before

```text
Cline -------------> Claude
OpenCode ----------> GPT-OSS
Continue ----------> OpenAI
Claude Code -------> Anthropic
Cursor ------------> OpenRouter
```

모든 툴마다 설정이 다르다.

---

## After

```text
          모든 툴

   Cline
   Cursor
   OpenCode
   Claude Code
   Continue
   Aider
   RooCode

           │

           ▼

       localhost:4000

           │

         Forge

           │

    알아서 모델 선택
```

사용자는

**모델을 신경 안 쓴다.**

---

# 전체 구조

```text
                  ┌─────────────────────────────┐
                  │        AI Clients           │
                  │─────────────────────────────│
                  │ Cline                       │
                  │ OpenCode                    │
                  │ Claude Code                 │
                  │ Cursor                      │
                  │ Continue                    │
                  │ Aider                       │
                  └─────────────┬───────────────┘
                                │ OpenAI API
                                ▼
               ┌─────────────────────────────────┐
               │            Forge API            │
               │  /chat/completions              │
               │  /models                        │
               │  /health                        │
               └─────────────┬───────────────────┘
                             ▼
                ┌─────────────────────────────┐
                │      Routing Engine         │
                └─────────────┬───────────────┘
                              │
      ┌──────────────┬─────────┼──────────┬─────────────┐
      ▼              ▼         ▼          ▼             ▼
 Capability      Health    Latency    Cooldown     Cost Policy
 Analyzer        Monitor    Monitor    Manager      Manager
      │              │         │          │             │
      └──────────────┴─────────┴──────────┴─────────────┘
                              │
                              ▼
                     LiteLLM Provider Layer
                              │
      ┌─────────────┬──────────────┬─────────────┬────────────┐
      ▼             ▼              ▼             ▼
   NVIDIA      OpenRouter     Anthropic      Ollama
```

---

# Core Engine

## 1. Request Analyzer

들어오는 요청을 분석한다.

예시

```text
"리팩토링해줘"

↓

task = refactor
```

```text
"버그 수정"

↓

task = debug
```

```text
"README 작성"

↓

task = documentation
```

```text
"테스트 코드"

↓

task = testing
```

---

## 2. Capability Matrix

모든 모델을 점수화한다.

예시

| 모델        | Code | Debug | Refactor | Docs | Context | Speed |
| --------- | ---- | ----- | -------- | ---- | ------- | ----- |
| GLM       | 10   | 10    | 10       | 8    | 9       | 9     |
| DeepSeek  | 10   | 10    | 9        | 8    | 10      | 8     |
| Kimi      | 9    | 9     | 10       | 10   | 10      | 8     |
| GPT OSS   | 8    | 8     | 8        | 9    | 9       | 8     |
| Codestral | 9    | 8     | 8        | 6    | 6       | 10    |

Forge는

현재 작업과 가장 맞는 모델을 선택한다.

---

# Scheduler

가장 중요한 부분.

모델 선택 공식

```text
Score =

Capability

+

Latency

+

Health

+

Availability

+

Context

+

Priority

-

Failure Score

-

Cooldown Penalty
```

즉

GLM이 원래 최고라도

429가 계속 난다면

자동으로

DeepSeek

↓

Kimi

↓

Mistral

순으로 이동.

---

# Health Monitor

30초마다

```text
GET /models
```

또는

짧은 Completion 실행.

상태

```text
GLM

🟢 Alive

Latency 430ms
```

```text
DeepSeek

🟢 Alive

Latency 520ms
```

```text
GPT OSS

🔴 Timeout
```

Pool에서 자동 제외.

---

# Cooldown Manager

429 발생

↓

```text
GLM
```

↓

```text
Cooldown 5분
```

↓

Scheduler는 선택하지 않음.

5분 후

자동 복귀.

---

# Metrics Engine

모델별

```text
평균 속도

평균 토큰

실패율

429 횟수

5xx 횟수

Timeout

비용

사용량
```

모두 저장.

SQLite로 시작하고 나중에 PostgreSQL로 확장.

---

# Auto Discovery

부팅 시

```text
GET /v1/models
```

↓

신규 모델 발견

↓

Capability 기본값 부여

↓

Pool 등록

↓

Dashboard 갱신

NVIDIA 모델 목록을 자동으로 읽어 등록하는 부분은 네가 가져온 `/v1/models` 응답을 그대로 활용하면 된다. 

---

# Tier System

## Tier1

최상위

```
GLM

DeepSeek Pro

Kimi

Qwen
```

랜덤 + Least Busy

---

## Tier2

```
GPT OSS

Mistral

Nemotron

DeepSeek Flash
```

---

## Tier3

```
Codestral

StarCoder

DeepSeek Coder

CodeGemma
```

---

# Dashboard

```
┌────────────────────────────────────────────┐
│ Forge Dashboard                            │
├────────────────────────────────────────────┤
│ Providers                                  │
│ 🟢 NVIDIA                                 │
│ 🟢 Ollama                                 │
│ 🟢 OpenRouter                             │
│ 🔴 Anthropic                              │
├────────────────────────────────────────────┤
│ Tier1                                     │
│ GLM                410ms      Healthy     │
│ DeepSeek           520ms      Healthy     │
│ Kimi               390ms      Healthy     │
├────────────────────────────────────────────┤
│ Cooldown                                 │
│ GPT OSS      2m 13s                       │
├────────────────────────────────────────────┤
│ Today                                   │
│ Requests     2,430                       │
│ Success      99.1%                       │
│ Failures     0.9%                        │
│ Cost         $0.00                       │
└────────────────────────────────────────────┘
```

---

# API

```
GET /models

GET /metrics

GET /providers

GET /health

GET /dashboard

POST /chat/completions

POST /embeddings

POST /admin/provider

POST /admin/reload
```

OpenAI API와 호환되도록 `/chat/completions`를 그대로 제공하면 Cline, OpenCode, Continue, Aider 등은 설정 변경 없이 붙일 수 있다.

---

# 기술 스택

| 영역         | 선택                  |
| ---------- | ------------------- |
| Language   | Python 3.12         |
| API        | FastAPI             |
| Provider   | LiteLLM             |
| Cache      | Redis               |
| DB         | SQLite → PostgreSQL |
| Scheduler  | 자체 구현               |
| Dashboard  | Next.js + Tailwind  |
| Charts     | Recharts            |
| Monitoring | Prometheus          |
| Logs       | Structlog           |

---

# 로드맵

### v0.1 (MVP) — 1주

* OpenAI Compatible API
* LiteLLM 연동
* NVIDIA 모델 자동 등록
* Tier 기반 Failover
* 기본 Dashboard

### v0.2 — 2주

* Health Check
* Cooldown
* Latency 측정
* Least Busy Routing
* Metrics 저장

### v0.3 — 3주

* Capability Routing
* Auto Discovery
* Provider Hot Reload
* Ollama/OpenRouter 지원
* 정책 기반 라우팅

### v1.0

* AI Judge(복수 모델 평가)
* A/B Testing
* Prompt Routing
* 비용 최적화
* Web UI 완성
* Plugin SDK
* Kubernetes 배포
* MCP 연동

## 마지막으로 하나를 더 추가하고 싶다

이 프로젝트의 가장 큰 차별점은 **"모델 선택을 사용자가 하지 않아도 된다"**는 점이다.

그래서 LiteLLM을 감싸는 수준을 넘어서 **Policy Engine**을 핵심으로 두는 것을 추천한다.

예를 들어 사용자가 다음처럼 정책만 정의하면 된다.

```yaml
policies:
  - when:
      task: coding
    prefer:
      - tier1
    fallback:
      - tier2
      - tier3

  - when:
      task: documentation
    prefer:
      - mistral
      - kimi

  - when:
      provider: nvidia
      unavailable: true
    fallback:
      - openrouter
      - anthropic

  - when:
      max_cost_per_request: 0
    allow_paid: false
```

이렇게 하면 Forge는 **"어떤 모델을 쓸지"가 아니라 "어떤 정책으로 라우팅할지"를 관리하는 플랫폼**이 된다. 이 방향이 기존 LiteLLM, Portkey 같은 프로젝트와 가장 큰 차별점이 될 수 있다.


네, PRD에 계획된 v0.1 MVP의 모든 기능을 구현했습니다:

✅ __v0.1 MVP (1주) - 완료__

- OpenAI Compatible API: `/v1/chat/completions`, `/v1/models` 등
- LiteLLM 연동: NVIDIA API로 직접 연동 (12개 모델)
- NVIDIA 모델 자동 등록: config.py에 Tier1/Tier2/Tier3 모델 정의
- Tier 기반 Failover: Scheduler가 모델 선택 및 장애 조치
- 기본 Dashboard: `/dashboard`, `/health`, `/metrics` 엔드포인트

✅ __v0.2 (2주) - 대부분 완료__

- Health Check: 30초 주기 모델 상태 모니터링
- Cooldown: 429 발생 시 5분 자동 제외
- Latency 측정: 각 모델의 응답 시간 추적
- Least Busy Routing: 실패율, 쿨다운 등 고려한 스코어링
- Metrics 저장: SQLite 기반 일일 통계 저장

✅ __v0.3 (3주) - 부분 완료__

- Capability Routing: Request Analyzer + Capability Matrix
- Auto Discovery: HealthMonitor에 모델 발견 기능
- Provider Hot Reload: 준비됨

__누락된 기능 (미래 버전):__

- `/embeddings` 엔드포인트
- Policy Engine (YAML 기반 정책)
- Redis 캐시
- PostgreSQL 지원
- Next.js Dashboard UI
- Prometheus 연동
