# Decision Log

## 2026-07-12 — 섀도 평가 열린 결정 확정 + 보안 2건 즉시 착수

**결정** (사용자 확정, [ShadowEvaluation.md](ShadowEvaluation.md) §9):
- 섀도 평가는 **무료 기본, 유료 opt-in** — `daily_budget_usd > 0` 설정 시 유료
  도전자/judge 허용. **대시보드에서 토글·예산·judge 모델 선택이 가능해야 한다**
  (구현 계약 §9.1 — `forge.local.yaml` 오버레이 + `/admin/shadow`, guard/U5 전례 재사용).
- judge는 자동 선정 기본 + 대시보드에서 명시 선택 가능.
- 진짜 A/B(실트래픽 직접 라우팅) 2단계는 보류 유지.
- 전면 분석에서 발견한 보안 2건(시크릿 마스킹 신규 키 형식 미커버, CORS
  `*`+credentials)은 로드맵 S1을 기다리지 않고 즉시 구현 (fix/security-hardening).

**이유**: 유료 사용자도 섀도 평가의 수혜자여야 하지만 과금은 명시적 동의
뒤에만 — 예산 하드 컷과 `allow_paid` guard 상속이 이중 안전장치. 대시보드 제어는
"forge.yaml을 손으로 안 고친다"는 U5 방향의 연장.

## 2026-07-12 — 스코어링 v2: 레이턴시 로그 스케일 + speed 시드를 콜드 스타트 prior로

**결정**: `_score()`의 레이턴시 부분 점수를 선형(100ms→10 ~ 2000ms→0)에서 **로그
스케일**(200ms 이하 10점, 30초 이상 0점, 사이 log10 선형)로 교체하고, 실측이 없는
모델(콜드 스타트)은 `speed` capability를 prior로 환산한다(speed 7=중립 5.0 앵커,
±1당 ±5/3점, 0~10 클램프). 가중치(0.15)는 유지. 기대 순위는 골든 하네스
(`tests/test_scheduler_golden.py`)로 고정해 이후 공식 변경의 회귀 게이트로 쓴다.

**이유**: (a) 2026-07-11 실측에서 같은 모델이 호스팅에 따라 TTFT 1.5초~18초로
10배 이상 차이났는데 기존 공식은 2초 이상을 전부 0점으로 포화시켜 이 차이를
구분하지 못했다 — 정책 `prefer` 순서로 우회했던 것의 근본 해소. (b) `speed`
시드는 그동안 스코어링이 읽지 않는 dead data였다(DecisionLog 2026-07-11 부수
발견) — 실측이 쌓이기 전 구간에만 prior로 쓰면 시드가 실측과 싸우지 않으면서
신규 모델의 초기 라우팅 품질을 올린다. (c) 앵커를 "speed 7(기본값)=중립 5.0"으로
잡아 시드 없는 모델의 동작이 v1과 완전히 동일 — 하위 호환이 골든 테스트로 증명됨.
속도가 실력을 뒤집지 않는다는 기존 원칙(tier/capability 지배, 2026-07-11 결정)도
골든 테스트로 고정.

## 2026-07-12 — 멀티 인스턴스(StateStore/Redis) 로드맵 제외, F1–F4 착수

**결정**: 전면 분석([Roadmap.md](Roadmap.md)) 후 사용자 결정 — 멀티 인스턴스 대비
StateStore 추상화·Redis 공유 상태는 F1과 6개월 로드맵에서 제외한다. F1은 멀티 키
로테이션 스키마 + Deps 스냅샷(reload 원자성) + Bedrock/Azure auth 확장 계약으로 축소.
F1–F4는 즉시 착수하고, 단순 구현은 Sonnet·복잡 구현은 Opus에 위임한다.

**이유**: Forge는 단일 로컬 사용자용 게이트웨이로 쓰이고 있고(2026-07-09 M3 범위
결정과 동일 근거), 멀티 인스턴스는 수요 신호가 없는 상태에서 상태 계층 전체를
추상화하는 비용만 크다. 반면 멀티 키 로테이션은 무료 티어 한도 확장이라는 핵심
소구점에 직결되며 StateStore 없이 프로세스 내 구현으로 충분하다.

## 2026-07-12 — 멀티 키 로테이션: 429는 "키" 귀책, 모델 쿨다운은 전 키 소진 시에만

**결정**: provider에 API 키가 여러 개면(`api_key_envs` / 카탈로그 `KEY_2..KEY_9` 관례)
rpm 토큰 버킷과 429 쿨다운을 **키 단위**로 관리한다. 429 발생 시 사용한 키만
쿨다운(Retry-After 존중)하고, 남은 가용 키가 있으면 모델은 쿨다운·exclude하지 않고
같은 후보를 다른 키로 재시도한다. 가용 키가 하나도 없을 때만 기존과 동일하게 모델
즉시 쿨다운. 단일 키 provider는 기존 동작과 완전 동일(하위 호환).

**이유**: 429는 모델 품질이 아니라 키 할당량의 신호다 — 키 A의 429로 모델을
쿨다운하면 키 B의 멀쩡한 할당량이 낭비된다(오류 귀책 원칙, CLAUDE.md 검증 원칙과
동일 맥락). max_concurrent 세마포어는 키가 아니라 인프라 동시성이므로 provider
단위 유지.

## 2026-07-09 — M3 범위 결정 (Approval Gates)

**결정**:
- ① Dashboard는 PRD의 Next.js 대신 **FastAPI가 서빙하는 내장 정적 SPA**(단일 HTML) — pip 설치만으로 동작해야 하는 도구 특성상 Node 툴체인 요구는 채택 장벽. DESIGN.md §5.10 갱신
- ② Prometheus 구현에 **prometheus-client 의존성 승인**
- ③ **PostgreSQL 보류** — 단일 로컬 사용자는 SQLite로 충분, Repository 인터페이스는 준비돼 있어 실수요 시 구현
- 부수: Redis(설계상 "필요 시"), 멀티 키 로테이션·A/B·AI Judge는 후속으로 유지

주요 의사결정과 그 근거를 기록한다. (규칙: [CLAUDE.md](../CLAUDE.md) Working Rules 5)

---

## 2026-07-09 — Git 브랜치/PR 워크플로우 도입

**결정**: 큰 기능·변경은 main 직접 커밋 금지. 브랜치(`feat/`, `fix/`, `refactor/`, `docs/`) 작업 → PR 생성 → **squash merge** → 브랜치 삭제. 예외: **문서만 수정하는 변경(코드 미포함)은 main 직접 커밋/푸시 허용** (2026-07-09 사용자 확정).

**이유**: main 히스토리를 PR 단위 1커밋으로 유지해 변경 추적과 롤백을 단순화. 오픈소스 공개(DESIGN.md §8) 후 외부 기여 워크플로우와도 일치.

**출처**: 사용자 지시 (CLAUDE.md Working Rules 8로 성문화)

## 2026-07-09 — PyPI 패키지명 `forge-gateway` 확정 권장

**결정**: PyPI 패키지명 후보 중 `forge-gateway` 권장 (조기 등록 필요). `forge`, `forge-ai`, `llmforge` 등은 선점 확인(2026-07).

**이유**: 프로젝트 성격을 그대로 설명하고, 설계서에서 이미 가칭으로 사용 중. 등록 절차는 [PUBLISHING.md](../PUBLISHING.md).

**미결**: 최종 확정 및 PyPI 등록은 사용자 실행 대기. → **2026-07-09 등록 완료** (`forge-gateway 0.3.0.dev0`, https://pypi.org/project/forge-gateway/)

## 2026-07-09 — M1 착수 Approval Gates 승인

**결정**:
- ① PyYAML 의존성 추가 **승인** (forge.yaml 파싱)
- ② request_metrics 스키마 변경(DESIGN.md §6) **승인** — 마이그레이션 없이 forge.db 재생성 (로컬 개발 데이터)
- ③ 라이선스 **미결** — 상업적 이용 제한 여부 논의 중. 비상업 라이선스의 채택 비용에 대한 의견 전달 후 MIT / AGPL-3.0 중 재결정 예정. LICENSE 파일 생성(M1-13)은 결정 시까지 보류 → **아래 항목에서 MIT로 확정됨**

## 2026-07-09 — M2부터 당분간 PR 생략 (임시 프로세스)

**결정**: gh CLI 부재로 당분간 PR 없이 진행 (사용자 지시). 브랜치 작업 + 로컬 `git merge --squash`는 유지해 main 히스토리 1커밋 원칙은 지킨다. gh 설치 후 원 규칙(CLAUDE.md 7) 복귀.

## 2026-07-09 — 라이선스 MIT 확정

**결정**: MIT 라이선스 채택 (사용자 확정). 비상업 제한안과 AGPL-3.0을 검토 후 기각.

**이유**: ① 핵심 타깃(회사 업무에서 에이전트 쓰는 개발자)의 실사용을 막지 않음 — 기업 법무의 자동 승인 대상. ② 생태계 표준(litellm/FastAPI와 동일 계열)이라 기여·통합 장벽 없음. ③ localhost 무료 게이트웨이는 재판매 가치가 낮아 보호(카피레프트)의 실익이 작고, 채택이 프로젝트 성패 그 자체.

## 2026-07-09 — PromptLog 폐지

**결정**: `docs/PromptLog.md`(사용자 프롬프트 원문 기록)를 필수 산출물에서 제외하고 파일 삭제. CLAUDE.md의 프롬프트 원문 저장 규칙도 제거, 이후 규칙 번호 재정렬.

**이유**: 사용자 결정 — 프롬프트 원문 기록은 관리 비용 대비 효용이 낮음. 중요 논의는 ProjectContext/DecisionLog/WorkLog로 충분히 커버됨.

## 2026-07-09 — NVIDIA 무료 티어 RPM 한도 40 확인

**결정**: 선제 스로틀링(§5.13) 기본값 `rpm: 40` — 사용자 실측 확인값.

## 2026-07-11 — 속도 라우팅: `tier` 재정의 대신 정책 `prefer` 확장

**결정**: 무료 모델이 너무 느리다는 사용자 피드백에 `tier`(실력 순위) 자체를 속도
기준으로 재정의하지 않고, 기존에 이미 있던 방식(`forge.yaml`의 `default` 정책이
빠른 모델을 `prefer`로 따로 우선시키는 2026-07-09 전례)을 그대로 확장하기로
결정. `default`/`heavy-work`/`hard-tasks` 세 정책의 `prefer` 순서를 실측
TTFT(2026-07-11, docs/Research.md 참조)로 갱신 — cerebras/gemini/sambanova/
cohere/fireworks/x.ai를 포함해 전 프로바이더 실측. `default`는 "무료 먼저 다
쓰고 안 되거나 느리면 유료 빠른 모델로" 순서(사용자 확정), `heavy-work`/
`hard-tasks`는 여전히 무료 `nvidia:deepseek-v4-pro`를 우선하고 유료 고속 호스팅은
쿨다운 시 대체용으로만 추가.

**이유**: `tier`를 속도로 재정의하면 `hard-tasks`/`heavy-work` 정책이 명시적으로
"느려도 강한 모델을 쓴다"는 의도로 tier1(v4-pro 등)을 지정해둔 것과 의미가
충돌한다. 반면 정책 `prefer` 확장은 기존 설계 의도(실력=tier, 속도=정책)를
그대로 유지하면서 새 데이터만 반영하는 최소 변경이라 리스크가 작다.

**부수 발견**:
- `scheduler.py::_score()`가 `capability_seed`의 `speed` 필드를 전혀 읽지
  않는다는 걸 확인(dead data) — 실제 속도 반영 경로는 `tier` 가중치(10%)와
  실측 EWMA latency(15%, 2초 이상은 전부 0점이라 "다소 느림"과 "치명적으로
  느림"을 구분 못함)뿐. 이번엔 `prefer` 순서로 우회했지만, `speed`를 스코어링에
  실제로 반영하거나 latency 스코어 구간을 세분화하는 건 별도 개선 과제로 남음.
- Gemini "Flash" 계열(gemini-3-flash-preview/3.5-flash)이 실측 TTFT 16~19초로
  이름과 무관하게 느림 — 기존엔 몰랐던 사실.
- `deepseek-v4-pro`는 NVIDIA(무료) 18초 vs Fireworks/Together(유료) 1~1.5초 —
  같은 모델도 호스팅에 따라 속도가 10배 이상 차이 날 수 있음이 실측으로 확인됨.

## 2026-07-11 — tier 전면 재검토: "자체 발표만"은 tier1 불충분 기준 확정

**결정**: 사용자 요청("모델들 다 검토해서 tier 수정해줘")으로 capability_seed/
forge.yaml 전체를 재검토. **평가 근거 기준을 명시적으로 확정**: 제조사 자체
발표(공식 블로그/보도자료)뿐이고 제3자 검증이 없는 벤치마크 수치는 tier1의
충분조건이 아니다 — 최소한 공식 모델카드(방법론이 문서화된 형태)나 독립
리더보드 수치가 있어야 tier1. 이 기준으로 `xai:grok-4.5`(SWE-Pro 64.7%, 자체
발표뿐)를 tier1→tier2로, `nvidia:minimaxai/minimax-m3`(SWE-Pro 59.0%, 마찬가지로
자체 발표뿐)는 후보였으나 tier2 유지로 확정.

같은 지표(SWE-bench Verified 또는 Pro)로 직접 비교 가능하고 양쪽 다 공식
소스인 경우는 tier1로 승격: `mistral-medium-3.5`/`deepseek-v4-flash`(SWE-V,
기존 tier1 범위와 겹침), `gemini-3.5-flash`(SWE-Pro, tier1 deepseek-v4-pro와
사실상 동일) — 사용자 확인.

**이유**: 이전 세션들에서 같은 종류의 근거(자체 발표만)를 어떤 모델엔 관대하게
(grok-4.5 tier1), 어떤 모델엔 엄격하게(minimax-m3 tier2 유지) 적용해 일관성이
없었다. 기준을 명문화해두면 다음에 새 모델을 추가할 때마다 이 질문을 반복하지
않아도 된다.

**건드리지 않은 것**: `sambanova:MiniMax-M2.7`/`DeepSeek-V3.1`은 Research.md
"조사 예정"에 이미 "2차 집계만으로 tier1 승격 보류 중"이라고 명시된 기존
결정이라 그대로 유지(누락이 아니라 확인된 보류).

**부수 리스크**: `grok-4.5`가 tier2로 내려가면서 `default` 정책의 fallback
`[tier2, tier1, tier3]` 순서상 tier1보다 먼저 시도되는 풀에 들어갔다 — prefer
목록이 전부 막혔을 때만 도달하는 드문 경로지만, 신규 등록 모델은 실측 latency가
없어 중립값(5.0)으로 시작해 한동안 capability 점수만으로 경쟁할 수 있음.
실트래픽이 쌓이면 EWMA latency가 자연히 보정할 것으로 예상 — 별도 조치는
하지 않고 관찰만 하기로 함.
