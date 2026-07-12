# Forge 전면 분석 & 6개월 실행 로드맵 (2026-07-12)

> 작성 기준: v0.3.0 (PyPI 정식 릴리스, 2026-07-11), main `9f1f164`, 테스트 235건 전체 통과 실측 확인.
> 워커 전제: **사람 1명 풀타임 + Opus** (구현 위임 주력). Fable 가용 24시간은 별도 §2로 분리.
> 분석 방법: 직접 정독(설계/기획 문서 전체 + 핵심 코드) + 병렬 심층 탐색 3트랙(아키텍처 / 테스트 / 부채·보안) 교차 검증.

---

## 0. 전제에 대한 교정 두 가지 (계획의 정확도를 좌우함)

1. **"0.1→0.3 진행 속도" 전제는 사실과 다르다.** CHANGELOG에 0.1/0.2 릴리스는 존재하지 않는다.
   실제로는 최초 커밋(2026-07-09) → v0.3.0(2026-07-11), **약 2일 / 34커밋**에 M1~M3 전체(4.7k LOC + 3.6k 테스트)를
   압축 개발했다. 함의: **Opus 처리량은 이미 실증됐고, 진짜 병목은 사람의 검증 대역폭이다**
   (실키 연동 검증, PR 리뷰, 방향 결정). 이 로드맵은 그 병목을 기준으로 잡았다 — 스프린트가
   못 끝나면 잘리는 건 Opus 작업이 아니라 "사람 검증이 필요한 항목"이다.
2. **공격적 계획의 실제 리스크는 코드 생산이 아니라 채택 검증이다.** 멀티 인스턴스/K8s 같은 항목은
   DecisionLog(2026-07-09)가 이미 "실수요 시"로 보류했다. 이 로드맵은 그 결정을 존중해
   **결정 게이트**(§3.4)로 명시했다 — 수요 신호 없이 관성으로 진행하지 않는다.

---

## 1. 코드베이스 전면 분석

### 1.1 현재 상태 스냅샷

| 항목 | 상태 |
| --- | --- |
| 규모 | 소스 ~4,700 LOC (25모듈) / 테스트 ~3,600 LOC (18파일, **235건 전체 통과** — 2026-07-12 실측 16.6s) |
| 스택 | Python 3.10+, FastAPI, LiteLLM SDK, SQLite, prometheus-client. 단일 프로세스 |
| 릴리스 | PyPI `forge-gateway` 0.3.0 정식 (2026-07-11). Docker/CI 없음 |
| 마일스톤 | M1(기반)·M2(지능)·M2.5(정리)·M3(플랫폼)·UX 스프린트·무료/유료 카탈로그·속도 라우팅 **전부 완료** |
| 잔여 백로그 | PostgreSQL·Redis StateStore·멀티 키 로테이션·AI Judge/A-B(보류/후속), Bedrock/Azure(P6), speed 스코어링(dead data), structlog(미착수) |

### 1.2 아키텍처 요약

- **파이프라인**: 진입([api/openai.py](../forge_gateway/api/openai.py) `ChatPipeline`) → 분석(analyzer, 3계층 신호) →
  정책(policy, first-match + 누적 constraints) → 스케줄링(scheduler: 하드 필터 → 세션 고정 → 7항목 가중 스코어)
  → 선제 스로틀(token bucket) → LiteLLM 호출 → 예외 타입별 failover → write-behind 메트릭.
  Anthropic `/v1/messages`는 변환 후 동일 파이프라인 합류.
- **계층 규율 준수**: 순환 의존 없음, 계약(types/protocol/settings) 공유만으로 모듈 분리 유지. 주석에
  결정 근거·리뷰 번호가 달린 자기설명적 코드베이스 — 위임 개발에 최적화된 상태.
- **결합 지점 1개**: `Deps` 공유 컨테이너가 [api/openai.py:47](../forge_gateway/api/openai.py#L47)에 있어
  anthropic/admin/observe가 전부 openai.py를 import — 허브 결합. `api/deps.py` 분리로 해소 가능(소규모).
- **async 위생 양호**: 요청 경로 블로킹 I/O 없음(SQLite는 전부 `to_thread`, 메트릭은 `put_nowait`),
  취소 전파·백그라운드 태스크 강참조 관리까지 의식적으로 처리돼 있음.

### 1.3 기술부채 (실측 근거 포함, 심각도순)

| # | 부채 | 근거 | 심각도 |
| --- | --- | --- | --- |
| D1 | **시크릿 마스킹이 신규 키 형식 미커버** — 정규식이 초기 4계열(nvapi-/sk-*/gsk_/AIza)만. Cerebras(`csk-`)/x.ai(`xai-`)/Fireworks(`fw_`)/Cohere/Together/Mistral/SambaNova/Zhipu 키는 업스트림 에러 에코 시 로그에 **평문 노출** 가능. CLAUDE.md 자체 규칙 위반 상태 | [providers/base.py:19](../forge_gateway/providers/base.py#L19) vs 카탈로그 12종([settings.py:283-431](../forge_gateway/settings.py#L283)) | **높음** |
| D2 | **CORS `allow_origins=["*"]` + `allow_credentials=True`** — 기본 무인증 로컬 모드에서 악성 웹페이지가 브라우저 경유로 `127.0.0.1:4000`에 요청·응답 탈취 가능(drive-by 크레딧 소모). loopback 바인딩으로는 못 막는 경로 | [server.py:131-137](../forge_gateway/server.py#L131) | **높음** |
| D3 | **CI/CD·Docker 전무** — DESIGN.md §8이 약속한 GitHub Actions/Docker가 없음. PyPI에 공개된 패키지인데 릴리스가 전부 수동, 회귀 게이트 없음 | `.github/` 부재 | **높음** |
| D4 | **상태가 전부 프로세스 로컬 + StateStore 추상화 부재** — health/cooldown/throttle/session/tuner 보정이 인메모리. 재시작 시 소실, 멀티 인스턴스 시 rpm이 N배로 뻥튀기(무료 티어 즉시 429). 인터페이스조차 없음 | [registry.py:4](../forge_gateway/core/registry.py#L4), [throttle.py:65-68](../forge_gateway/core/throttle.py#L65) | 높음(확장 전제 시) |
| D5 | **reload 논-아토믹 교체 창** — `deps.config/registry/scheduler/...`를 순차 대입, 교체 도중 요청이 신 registry+구 scheduler 혼합 참조 가능 | [server.py:204-209](../forge_gateway/server.py#L204) | 중간 |
| D6 | **PROVIDER_CATALOG 하드코딩 ~170줄** — 벤치마크/가격 시드가 코드에 박혀 모델 출시마다 코드 수정 강제. settings.py 559줄 비대 | [settings.py:260-431](../forge_gateway/settings.py#L260) | 중간 |
| D7 | **speed capability가 dead data** — 스코어링이 안 읽음. latency 점수는 2초 이상 전부 0점이라 "다소 느림"과 "치명적으로 느림" 구분 불가(실측: 같은 모델이 호스팅 따라 TTFT 10배 차) | DecisionLog 2026-07-11, [scheduler.py:273](../forge_gateway/core/scheduler.py#L273) | 중간 |
| D8 | 의존성 상한 미핀(`litellm>=1.50.0` 등) + requirements.txt 이중 관리 — litellm 업그레이드 회귀 전례 있음(Retry-After 헤더 위치) | pyproject.toml:20-29 | 중간 |
| D9 | 관측 게이팅 비일관 — `/v1/stats`는 키 게이팅, `/dashboard`(JSON)는 항상 공개(api_base·비용·정책명 노출) | [observe.py:42-82](../forge_gateway/api/observe.py#L42) | 중간 |
| D10 | 파일 로깅/로테이션·structlog·request_id 상관 부재(stderr뿐), 프로세스 관리 수단 없음 | [server.py:35-39](../forge_gateway/server.py#L35) | 낮음 |
| D11 | dashboard.html 단일 1,261줄 인라인 SPA — XSS 방어(`esc()`)는 일관 적용돼 안전하나, 규모 확장 시 유지보수 한계. 단, "Node 툴체인 없이 pip만"은 의도된 결정(DecisionLog ①)이라 **번들러 도입이 아니라 파일 분할**이 올바른 방향 | static/dashboard.html | 낮음 |
| D12 | 문서 표류 소소 4건 — README "v0.3-dev" 표기(정식 릴리스됨), README에 `pip install forge-gateway` 경로 부재, cli.py 도크스트링 낡음, test_simulator_scenarios.py 헤더가 "expectedFailure로 문서화"라 하나 실제는 수정 완료된 회귀 테스트(코드가 옳고 주석이 낡음) | 각 파일 | 낮음 |

### 1.4 테스트 커버리지

**강점**: 핵심 차별점(라우팅/failover/스트리밍/스로틀/쿨다운)이 **실스택 시뮬레이터 E2E**(uvicorn+litellm+httpx 실통과,
네트워크는 loopback 밀폐)로 결정론적으로 커버됨. "실패 경로가 제품" 원칙과 정합. 시간 의존은 주입 clock으로 제거.

**공백** (전용 테스트 0건):
- `api/admin.py` reload E2E / `api/auth.py` 401·403 강제 / `api/observe.py` stats·metrics·dashboard + 인증 게이팅
- **`server.py reload_config_fn`** — health 이관·원자 교체라는 최고위험 로직이 무테스트
- `litellm_provider.py` 헬퍼 단위 테스트(`_translate_error`/`_extract_retry_after` 등 — litellm 버전 특이성에 민감한데 E2E로만 간접 커버)
- 클라이언트 취소(499) 경로, 게이트웨이 레벨 동시 요청 경쟁, `/v1/embeddings`

### 1.5 문서화 수준

필수 산출물 10종 전부 존재·최신(WorkLog 56KB, Research 42KB — 결정마다 근거·출처·확인일 기록).
DESIGN.md가 코드와 거의 일치하는 드문 상태. 격차는 §1.3 D12의 표류 4건과 **"사용자용 문서 부재"**
(README 하나뿐 — 정책 레시피·운영 가이드·트러블슈팅 문서 없음. 채택 단계에서 병목이 될 항목).

### 1.6 "Opus 반복 가능" vs "Opus 단독 곤란" 구분

**Opus로 반복 가능** (명세가 명확·기존 패턴 존재·파일 소유권 분리 용이):
- 테스트 공백 메우기(§1.4 — 기존 하네스 패턴 모방), 보안 4종 수정(D1/D2/D9 + 기본 가드 온보딩)
- CI/CD·Dockerfile·릴리스 자동화(업계 표준 패턴), 문서 표류 수정·사용자 문서 초안
- 프로바이더 카탈로그 갱신(평가 기준이 DecisionLog에 명문화돼 있어 재현 가능), 카탈로그 데이터 파일 분리
- PostgreSQL Repository(인터페이스 기존), structlog 전환, pydantic 수치 도메인 검증 보강
- **계약이 먼저 주어지면**: 멀티 키 로테이션·Redis StateStore·Bedrock/Azure 구현

**Opus 단독 곤란** (교차 모듈 계약 판단·미묘한 동시성·개방형 설계 — 이 프로젝트의 실증 원칙
"아키텍처가 걸린 부분은 직접"에 해당):
1. **장기 하위호환을 내다본 설정·상태 계약 설계** — 멀티 키/멀티 인스턴스/Bedrock을 한 번에 수용하는 스키마 확장. 계약이 틀리면 이후 3개 스프린트 재작업
2. **스코어 공식 개정** — 제품 핵심 차별점. 다목적(품질/속도/무료 우선) 트레이드오프를 건드리면 조용한 라우팅 품질 회귀 — 회귀를 "정의"하는 하네스 설계 자체가 고난이도
3. **async 경쟁 조건의 재현·수정** — reload 창(D5), throttle peek/consume race, 세션 고정 이동 경쟁. "추측 금지" 원칙상 결정론적 재현을 만들어야 하는데 이게 구현보다 어려움
4. **평가 방법론 설계**(AI Judge/A-B) — 정답 없는 개방형 설계 + 비용/편향 판단

---

## 2. Fable 오늘 세션 (24시간) — 최고난이도 작업만

선별 기준: §1.6의 "Opus 단독 곤란" 4개 축과 정확히 일치 + **6개월 로드맵의 의존 뿌리**가 되는 것만.
아래 4건이 끝나면 이후 6개월의 절반 이상이 "계약 위 병렬 구현"으로 바뀐다 — Opus 처리량이 극대화되는 구조.

| 우선순위 | 작업 | 예상 소요 | 산출물 형태 |
| --- | --- | --- | --- |
| **F1** | **아키텍처 계약 패키지 v2** — ① StateStore 프로토콜(health/cooldown/throttle/session 상태 추상화 + 무손실 인메모리 기본 구현) ② 멀티 API 키 로테이션 설정 스키마(`api_key_env`→복수 키, 키 단위 버킷·쿨다운·귀책 규칙) ③ Deps 불변 스냅샷 계약(reload 원자성, D5 해소 설계) ④ Bedrock/Azure를 수용하는 ProviderConfig auth 모델 확장(P6 보류 해제 조건) | 6–8h | 계약 코드(Protocol/pydantic 스키마) + 기본 구현 + DESIGN.md 증보 + 마이그레이션 규칙. 전체 테스트 통과 유지 |
| **F2** | **스코어링 엔진 v2 + 골든 라우팅 회귀 하네스** — speed dead data 해소(처리속도 축 신설 또는 latency 로그 스케일 구간화, TTFT/tok\/s 분리), 대표 요청 프로파일 × 모델 풀 상태 → 기대 선택 순위를 고정하는 골든 테이블 테스트 | 5–6h | scheduler.py 개정 + 골든 테스트 스위트 + DecisionLog 항목(공식 변경 근거) |
| **F3** | **동시성·정합성 정밀 감사 + 수정** — reload 논-아토믹 창(F1 ③ 적용), throttle peek/consume 경쟁, 세션 고정 move_pin 경쟁, 취소 경로의 슬롯/버킷 누수, 메트릭 드롭 시맨틱 — 각각 결정론적 재현 후 수정 | 4–5h | 재현 테스트 + 수정 커밋 + WorkLog 기록 |
| **F4** | **AI Judge / A-B 섀도 라우팅 설계서** — 트래픽 섀도잉 프로토콜, 저비용 judge 방법론, 온라인 지표, 비용 상한, 실패 모드 분석. DESIGN.md §10-33 "별도 설계 후 착수"의 그 설계 | 3–4h | 설계 문서(스키마·실험 프로토콜 포함) — S5 구현의 직접 입력 |

합계 18–23h. **F1 > F2 > F3 > F4** 순서로, 시간 부족 시 F4부터 탈락(설계서는 S4에 사람+Opus로 대체 가능. F1은 대체 불가).

**왜 Opus가 아니라 Fable인가 (건별 근거)**:
- **F1**: 계약은 이 프로젝트에서 실증된 최대 리스크 지점이다("계약 없이 모듈부터 만들면 통합에서 깨진다" — 개발 지침).
  4개 계약이 서로 맞물리고(키 로테이션 버킷이 StateStore 위에, Bedrock auth가 같은 스키마 확장에), 6개월 뒤
  요구(멀티 인스턴스)까지 하위호환을 유지해야 한다. Opus는 국지 스키마는 잘 만들지만 교차 계약의 장기 정합
  판단에서 재작업을 유발한 전례가 업계 공통이고, 재작업 비용이 3개 스프린트 규모라 최고 모델 투입 대비 효율이 명확.
- **F2**: 스코어 공식은 "설명 가능한 라우팅"이라는 제품 정체성 그 자체다. 잘못 바꾸면 테스트는 다 통과하는데
  라우팅 품질만 조용히 나빠진다 — 실패가 비가시적인 유형. 회귀를 정의하는 골든 하네스를 먼저 설계할 수 있는
  판단력이 필요하고, 이후 Opus의 모든 스케줄러 작업이 이 하네스를 안전망으로 쓴다.
- **F3**: async 경쟁은 재현 자체가 구현보다 어렵다. 프로젝트 원칙(추측 금지·실증 우선)상 "아마 race일 것"으로
  수정할 수 없으므로, 최소 재현을 저작할 수 있는 모델이 해야 한다. Opus에게 주면 플레이키 테스트가 양산될 위험.
- **F4**: 평가 방법론은 정답이 없는 설계 판단(judge 편향, 섀도 비용, 지표 선택). 설계서가 나오면 구현은
  Opus가 충분히 가능 — 즉 Fable 1회 투입으로 S5 전체가 위임 가능해진다.

**의도적으로 Fable에 넣지 않은 것**: 보안 수정(D1/D2/D9)은 심각도는 높지만 난이도가 낮다 — S1 첫날 Opus 배정.
카탈로그·CI·문서도 마찬가지. Fable 시간은 난이도 기준으로만 배분했다.

---

## 3. 6개월 로드맵 (2026-07-13 ~ 2027-01-10, 13스프린트)

### 3.1 마일스톤 개요

```
S1        S2         S3–S4        S5         S6–S8            S9–S10        S11–S12     S13
v0.3.1    v0.4.0     v0.4.x       v0.5.0     v0.6.0           문서/벤치마크   v1.0-rc→GA   채택 대응
보안+CI    멀티 키     지능 v2 정착   Judge/A-B  엔터프라이즈+      +확장점        하드닝+릴리스
                                             멀티 인스턴스(게이트)
```

병렬 트랙: **A 라우팅 지능** / **B 플랫폼·프로바이더** / **C 신뢰성·릴리스** / **D 제품·커뮤니티**.
담당 표기: [사람] = 결정·실키 검증·리뷰·대외, [Opus] = 명세 기반 구현·테스트·문서. 난이도 ★(반복)~★★★(계약 필요).

### 3.2 스프린트 상세

**S1 (07/13–07/26) — v0.3.1: 보안·릴리스 엔지니어링** — 트랙 C
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 보안 4종: D1 마스킹을 카탈로그 연동 자동 생성으로(키 접두어를 PROVIDER_CATALOG에서 도출), D2 CORS 기본 잠금+설정화, D9 /dashboard 게이팅 일관화, `forge init` 시 spend guard 기본 제안 | Opus | ★ | 4건 각각 회귀 테스트 동반, 실키 로그로 마스킹 확인[사람] |
| GitHub Actions CI: ruff + 테스트 매트릭스(win/ubuntu × 3.10/3.12) + 커버리지 리포트 | Opus | ★ | PR마다 자동 실행, main 보호 |
| Dockerfile + compose + README `pip install forge-gateway` 경로 + D12 표류 4건 일소 | Opus | ★ | `docker run`으로 기동 확인 |
| 테스트 공백 1차: auth 401/403, admin reload E2E, observe 게이팅, embeddings, **reload_config_fn** | Opus | ★★ | 커버리지 공백 목록(§1.4) 소거 |
| F1~F3 산출물 리뷰·머지 + pydantic 수치 도메인 검증 보강 | 사람+Opus | ★★ | 전체 테스트 + CI 녹색 |
| **릴리스 v0.3.1** (PyPI+Docker, 보안 수정 고지) | 사람 | — | 배포 완료 |

**S2 (07/27–08/09) — v0.4.0: 멀티 키 로테이션 + 신뢰성** — 트랙 B+C, **의존: F1**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 멀티 API 키 로테이션 구현(F1-② 계약): 키 단위 버킷·쿨다운·귀책, doctor/dashboard 표시 | Opus | ★★ | 동일 프로바이더 2키 등록 시 실효 rpm 2배 E2E[시뮬레이터] + 실키 검증[사람] |
| Deps 스냅샷 reload(F1-③) 구현 적용 | Opus | ★★ | reload E2E + 경쟁 재현 테스트(F3 패턴) 통과 |
| litellm_provider 헬퍼 단위 테스트 + 의존성 상한 핀 + Renovate 봇 | Opus | ★ | litellm 버전 bump PR이 자동 생성·검증됨 |
| structlog + request_id 상관 + 파일 로깅/로테이션 옵션 | Opus | ★ | 로그 한 줄로 failover 체인 추적 가능 |
| 취소(499)·게이트웨이 동시성 테스트 하네스 확장 | Opus | ★★ | §1.4 잔여 공백 소거 |
| **릴리스 v0.4.0** ("무료 티어 한도 ×N" 헤드라인) | 사람 | — | 배포+발표 글 |

**S3 (08/10–08/23) — 지능 v2 정착 + 관측** — 트랙 A, **의존: F2**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 스코어링 v2 실트래픽 검증: 대시보드 라우팅 품질 패널(태스크별 선택 분포, failover율, TTFT p50/p95) | Opus | ★★ | 패널 배포, 골든 하네스 회귀 0건 |
| 골든 픽스처: 실제 Cline/Claude Code/Aider 트래픽 녹화 → analyzer·변환 회귀 스위트(DESIGN §9-2 미완 항목) | 사람(녹화)+Opus(테스트화) | ★★ | 10+ 실트래픽 시나리오 고정 |
| tuner v2: 속도 신호(tok/s) 학습 편입, 관측 감쇠(decay), 보정 내역 대시보드 노출 | Opus | ★★ | F2 설계 기반, 시뮬레이터 E2E |
| Windows 유니코드 크래시 근본 원인 확정(P8 잔여 — 현재 방어만 있고 원인 미확정) | Opus | ★ | 재현 or 원인 문서화 후 종결 |

**S4 (08/24–09/06) — 클라이언트 표면 확장** — 트랙 B, **결정 게이트 ①**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| `/v1/responses`(OpenAI Responses API) 지원 — Codex CLI 등 신형 에이전트 수용. **범위는 사용자 결정**(게이트 ①) | 계약: 사람 리뷰 / 구현: Opus | ★★★ | Codex CLI 실연동 스모크[사람] |
| analyzer LLM 폴백(opt-in, DESIGN §5.3 M3+ 항목) 구현 | Opus | ★★ | confidence<0.6 트래픽에서 분류 개선 측정 |
| Anthropic 변환 최신 정합: 최신 Claude Code 이벤트/기능 추적 스모크 자동화 | Opus+사람 | ★★ | 골든 픽스처에 편입 |
| Deps 허브 결합 해소(`api/deps.py` 분리) + dashboard.html 파일 분할(번들러 없이) | Opus | ★ | 구조 변경 후 전체 테스트 통과 |

**S5 (09/07–09/20) — v0.5.0: 평가 계층** — 트랙 A, **의존: F4 설계서**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 섀도 라우팅 파이프라인: 실요청 비동기 복제 → 후보 모델 비교 → judge 채점 → tuner 보정 입력 연결 | Opus | ★★★ | F4 설계서 기반. 비용 상한·opt-in 가드 필수 |
| 평가 리포트 화면 + `route/explain`에 "이 선택의 최근 평가 근거" 연동 | Opus | ★★ | 대시보드에서 모델별 실측 품질 확인 가능 |
| **릴리스 v0.5.0** ("자가 평가하는 게이트웨이") | 사람 | — | 배포+발표 |

**S6 (09/21–10/04) — 엔터프라이즈 프로바이더 + 카탈로그 지속 가능화** — 트랙 B, **의존: F1-④**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| AWS Bedrock / Azure OpenAI 프로바이더(F1-④ auth 확장 계약 기반, P6 보류 해제) | Opus | ★★★ | 둘 중 1개 이상 실계정 연동[사람] |
| PROVIDER_CATALOG 데이터 파일 분리(D6) + 모델/가격 자동 검증 스크립트(공식 페이지 대조 보조) | Opus | ★★ | 카탈로그 갱신이 코드 수정 없이 가능 |
| 카탈로그 정기 갱신 1회전(신규 모델 시드 — DecisionLog의 tier 증거 기준 적용) | Opus+사람 승인 | ★ | 갱신 근거 Research.md 기록 |

**S7 (10/05–10/18) — 멀티 인스턴스 1부** — 트랙 B, **결정 게이트 ② (채택 신호 없으면 트랙 D로 대체)**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| Redis StateStore 구현(F1-① 인터페이스): throttle/session/health 공유 | Opus | ★★ | 2-인스턴스 compose에서 rpm 총량 준수 E2E |
| PostgreSQL MetricsRepository(인터페이스 기존, DESIGN §5.7) | Opus | ★ | SQLite와 동일 테스트 스위트 통과 |
| SQLite 단일 락 경합 완화(읽기 커넥션 분리 or WAL) — D4 연관 | Opus | ★★ | 대시보드 조회가 flush와 비경합 확인 |

**S8 (10/19–11/01) — v0.6.0: 멀티 인스턴스 2부 + 배포 표면** — 트랙 B+C, **의존: S7**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| K8s manifests/Helm 차트 + graceful drain 검증 | Opus | ★★ | 2-replica 데모, 롤링 재시작 무중단 |
| 부하 테스트 기준선(locust): p95 지연·메트릭 드롭 임계 문서화 — D4 큐 드롭 시맨틱 검증 포함 | Opus | ★★ | 기준선 리포트 커밋 |
| Grafana 대시보드 템플릿 제공(기존 Prometheus 지표 소비) | Opus | ★ | 템플릿 json + 가이드 |
| **릴리스 v0.6.0** | 사람 | — | 배포 |

**S9 (11/02–11/15) — 문서·벤치마크 공개** — 트랙 D
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 문서 사이트(mkdocs-material): 시작 가이드/정책 레시피집/클라이언트별 연동/운영·트러블슈팅 | Opus 초안+사람 편집 | ★ | GitHub Pages 배포 |
| 공개 벤치마크: vs LiteLLM Router — failover 시나리오 재현 리포지토리 + 라우팅 품질 비교(골든 픽스처 재사용) | Opus+사람 검증 | ★★ | 재현 가능한 공개 리포트 |
| README 개편 + 쇼케이스 GIF/데모, 발표 글 | 사람 | — | 공개 |

**S10 (11/16–11/29) — 확장점** — 트랙 B+D, **결정 게이트 ③**
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 플러그인 훅(entry-point 기반 최소 SDK): 커스텀 analyzer/policy/provider 주입 — PRD v1.0 "Plugin SDK"의 최소형 | 계약: 사람 리뷰 / 구현: Opus | ★★★ | 서드파티 예제 플러그인 1개 동작 |
| MCP 서버 노출: forge 상태·explain·guard를 MCP 도구로(PRD v1.0 "MCP 연동") | Opus | ★★ | Claude Code에서 forge 상태 조회 데모 |
| 외부 노출 모드 가이드: 리버스 프록시(X-Forwarded-For 포함) 문서 + 관련 방어 옵션 | Opus | ★ | 문서+테스트 |

**S11 (11/30–12/13) — v1.0 하드닝** — 트랙 C
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 전면 보안 리뷰(외부 노출 모드 포함) + 72h soak 테스트 + 크로스 플랫폼 매트릭스(win/mac/linux) | Opus+사람 | ★★ | P1/P2 이슈 0건 |
| 설정 스키마 version 필드 실동작(마이그레이션 경로) + 하위호환 보증 문서 | Opus | ★★ | v0.x config가 v1에서 기동 |
| **v1.0.0-rc1** 배포 + RC 피드백 수집 창구 | 사람 | — | rc1 태그 |

**S12 (12/14–12/27) — v1.0 릴리스** — 트랙 D
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| RC 피드백 수정 일괄 + Trusted Publishing 전환(PUBLISHING.md 잔여) | Opus | ★ | 릴리스가 태그 push로 자동화 |
| **v1.0.0 GA**: 릴리스 노트, 발표(블로그/HN/레딧), CONTRIBUTING+good-first-issue 정비 | 사람 주도 | — | GA 배포+발표 |

**S13 (12/28–01/10) — 채택 대응 + 차기 계획** — 트랙 D
| 작업 | 담당 | 난이도 | DoD |
| --- | --- | --- | --- |
| 이슈/PR 대응 체계(Opus 1차 트리아지 → 사람 결정), 핫픽스 사이클 | 사람+Opus | ★ | 응답 SLA 확립 |
| v1.0 회고 + 실사용 텔레메트리 기반 v1.1 계획(이 문서 갱신) | 사람 | — | Roadmap.md v2 |

### 3.3 의존 관계 요약

```
F1(계약) ──▶ S2 멀티 키 ──▶ S6 Bedrock/Azure
   └───────▶ S7 StateStore ──▶ S8 K8s/멀티 인스턴스
F2(스코어링+골든) ──▶ S3 tuner v2·품질 패널 ──▶ S9 벤치마크
F3(동시성 감사) ──▶ S2 reload 스냅샷 검증
F4(평가 설계) ──▶ S5 섀도/Judge
S3 골든 픽스처 ──▶ S4 Anthropic 정합 · S9 벤치마크
S1 CI ──▶ 이후 전 스프린트의 회귀 게이트
```
병렬성: S3(트랙 A)과 S4(트랙 B)는 파일 소유권이 겹치지 않아 스프린트 내 Opus 병렬 위임 가능.
S9(문서)는 S6~S8과 완전 독립이라 지연 흡수 버퍼로도 기능.

### 3.4 결정 게이트 (사용자 결정 필요 — 임의 진행 금지 항목)

| 게이트 | 시점 | 질문 | 기본 권고 |
| --- | --- | --- | --- |
| ① Responses API | S4 착수 전 | Codex CLI류 수용을 지금 할 것인가? (호환 표면이 하나 늘어나는 장기 유지 비용) | 수요 확인 후 진행 — 이슈/요청 없으면 S10 이후로 밀기 |
| ② 멀티 인스턴스 | S7 착수 전 | 채택 신호(스타/이슈/실사용자)가 있는가? DecisionLog 보류 결정을 뒤집을 근거? | 신호 없으면 S7·S8을 트랙 D(커뮤니티·품질)로 대체 |
| ③ Plugin/MCP 범위 | S10 착수 전 | 확장점 공개 = API 동결 비용. v1.0 전에 열 것인가? | 최소 훅만 열고 SDK 문서는 experimental 표기 |
| ④ 외부 노출 모드 | S10 | localhost 도구 정체성을 유지할지, 팀 게이트웨이로 확장할지 | v1.0은 localhost 정체성 유지, 외부 노출은 가이드 문서까지만 |

### 3.5 리스크와 반대 시나리오 (계획이 틀리는 경우)

- **사람 검증 병목 초과**: 스프린트당 [사람] 항목이 실키 검증 2건 + 릴리스 1건을 넘으면 지연 시작.
  절삭 순서: 트랙 D → 트랙 B 신규 표면 → 트랙 A. 트랙 C(보안·CI·테스트)는 절삭 금지.
- **litellm 상류 회귀**: 전례 있음(Retry-After 위치, usage 청크). S2의 헬퍼 단위 테스트+상한 핀+Renovate가 방어선.
  그래도 깨지면: Provider Protocol 뒤에 격리돼 있으므로 직접 HTTP 구현체로의 부분 대체(§1.2)가 플랜 B —
  단 예외 분류 매트릭스 재구축 비용(~1스프린트)을 각오할 것.
- **무료 티어 정책 변동**(SambaNova 정정 전례): 카탈로그 신뢰가 제품 신뢰. S6의 자동 검증 스크립트 전까지는
  분기 1회 수동 재검증을 사람 일정에 고정.
- **채택 부진 시나리오**: S7~S8(멀티 인스턴스)이 무의미해지는 대신, S9(벤치마크·문서)를 앞당겨
  "왜 LiteLLM이 아니라 Forge인가"를 증명하는 것이 우선 — 게이트 ②가 이 전환을 강제한다.
- **모델 생태계 속도**: capability 시드는 아무리 갱신해도 낡는다. 구조적 해법은 S5의 자가 평가 루프가
  시드 의존도를 낮추는 것 — S5가 밀리면 이 리스크가 누적된다는 점을 우선순위 판단에 반영할 것.

---

## 부록: 이 문서의 근거 소스

- 병렬 심층 분석 3트랙(아키텍처/테스트/부채·보안, 2026-07-12) — 파일:라인 근거는 §1 각 표에 인라인
- 직접 검증: 테스트 235건 전체 통과 실행, CORS·마스킹 정규식 코드 확인, git 이력·PR·이슈 확인
- 기존 문서: DESIGN.md §10(마일스톤·보류 항목), DecisionLog.md(보류·기준 결정), Plan.md(M3 후속 이력),
  prd.md(v1.0 비전), PUBLISHING.md(릴리스 상태), WorkLog.md(잔여 이슈)
