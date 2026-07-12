# 섀도 평가 설계서 — AI Judge / A-B의 실용형 (2026-07-12)

> DESIGN.md §10-33("A/B 테스팅, AI Judge — 별도 설계 후 착수")의 그 설계.
> 구현 대상 아님 — Roadmap S5의 입력. 열린 결정(§9)은 사용자 확인 후 착수한다.

## 1. 문제 — 왜 tuner만으로 부족한가

현행 학습 루프(`core/tuner.py`)는 **실트래픽 관측**만 쓴다. 구조적 맹점 두 가지:

1. **반사실(counterfactual) 부재** — 스케줄러가 안 고르는 모델은 영원히 데이터가
   안 쌓인다. 시드가 낮게 잡힌 모델은 선택 안 됨 → 관측 없음 → 보정 없음의
   자기강화 루프에 갇힌다. 지금 이 간극을 사람 손 리서치(벤치마크 재조사)로
   메우고 있다 — 2026-07-11 tier 전면 재검토가 그 비용의 실례.
2. **실패율은 품질의 대리 지표가 아니다** — tuner의 신호(실패율·failover율·tools
   실패율)는 "동작하는가"를 재지 "잘하는가"를 재지 못한다. 코드가 컴파일되지만
   틀린 답을 주는 모델은 실패율 0%다.

섀도 평가는 이 두 맹점을 메운다: 실요청을 복제해 **선택되지 않은 후보 모델**에도
보내고, 두 응답을 judge 모델이 비교 채점해 capability 보정의 새 입력으로 쓴다.

## 2. 설계 원칙 (순서 = 우선순위)

1. **실경로 무간섭** — 섀도 실행이 실요청에 지연·실패·rate limit 경쟁을 1도
   추가하지 않는다. 위반하면 기능 전체가 무가치.
2. **기본 off + 비용 하드 캡** — opt-in(`shadow.enabled: false` 기본), 일일 예산
   소진 시 그날은 중단. "무료인 줄 알았는데 과금"은 최악의 실패 모드(§5.12와 동일 기준).
3. **프라이버시 불변식 유지** — 프롬프트/응답 본문은 저장하지 않는다(README 약속).
   채점은 인메모리 파이프라인에서 끝내고 점수·메타데이터만 영속화.
4. **판단 근거 설명 가능** — 어떤 비교에서 이겼/졌는지 대시보드에서 추적 가능
   (블랙박스 학습 라우터와의 차별점 유지).

## 3. 아키텍처

```text
실요청 완료 (성공 응답 A 확보, 실경로 종료 후)
   │  fire-and-forget (asyncio task, 실응답 반환과 무관)
   ▼
ShadowSampler — 이 요청을 평가에 쓸지 결정
   │  샘플링 확률 × task별 일일 쿼터 × 예산 잔여 × 스로틀 잔량 조건
   ▼
ShadowRunner — 도전자 모델 선정 + 복제 실행
   │  도전자: 같은 정책 후보군에서 "관측 부족" 순으로 (반사실 우선)
   │  논스트리밍, max_tokens 캡, 스로틀 버킷 잔량 > 50%일 때만 (실트래픽 우선)
   ▼
Judge — 응답 A(챔피언) vs B(도전자) pairwise 비교
   │  제3의 모델이 채점 (비교 대상과 다른 provider 강제)
   ▼
shadow_evals 테이블 (점수·메타만) ──▶ Tuner v2 입력 (win-rate → capability 보정)
                                └──▶ 대시보드 "Model Arena" 섹션
```

기존 코드와의 접점: `ChatPipeline._record` 직후 훅 1곳, `CapabilityTuner.run_once`
집계에 win-rate 항 추가, `MetricsRepository`에 shadow_evals 테이블. 실경로 코드는
훅 한 줄 외에 건드리지 않는다.

## 4. Judge 설계 — 편향을 구조로 방어

LLM judge의 알려진 편향과 구조적 대책 (프롬프트 지시만으로 방어하지 않는다):

| 편향 | 대책 |
| --- | --- |
| 위치 편향 (먼저 본 응답 선호) | A/B 제시 순서를 요청마다 랜덤화, 절반씩 스왑 |
| 자기 선호 (자기 계열 응답 선호) | judge는 비교 대상 둘과 **다른 provider** 모델 강제 |
| 길이 선호 (긴 응답 선호) | 채점 기준에 "요구 충족 최소성" 명시 + 두 응답 토큰 수를 judge에게 비노출 |
| 스타일 vs 정확성 혼동 | task별 루브릭 분리 — code/debug는 "정확·동작" 축만, docs는 명료성 축 포함 |
| 채점 비결정성 | 같은 (model_a, model_b, task) 쌍에 `min_samples`(기본 5) 누적 전에는 보정 미반영 — tuner의 기존 관례 재사용 |

**출력 계약**: judge는 `{"winner": "A"|"B"|"tie", "confidence": 0~1}` JSON만 반환
(rationale 요구 안 함 — 토큰 절약 + 본문 미저장 원칙). 파싱 실패는 채점 폐기(재시도 1회).

**judge 모델 선정**: 별도 지정(`shadow.judge_model`) 또는 미지정 시 "무료 + tier1
+ 비교 대상과 다른 provider" 중 스코어 최상위를 자동 선택. 무료 후보가 없으면
채점 스킵(과금 금지 원칙).

## 5. 반사실 우선 샘플링 — 무엇을 비교하나

도전자 선정 우선순위 (같은 정책 후보군 내에서):

1. **관측 부족 모델** — 최근 `window_days` 내 (model, task) 실트래픽 표본 <
   `min_samples`인 모델. §1-1의 자기강화 루프를 깨는 게 1차 목적.
2. **시드-실측 괴리 후보** — capability_adjustments가 클램프 상한(±2)에 붙어 있는
   모델 (시드가 실측과 계속 싸우는 신호 — 시드 자체가 틀렸을 가능성).
3. 그 외 라운드로빈.

챔피언(실선택 모델)의 응답은 이미 있으므로 **추가 비용은 도전자 1회 + judge 1회**.

## 6. 비용 통제 (하드 가드 3중)

```yaml
shadow:
  enabled: false            # opt-in
  sample_rate: 0.05         # 성공 요청의 5%만 후보
  daily_budget_usd: 0.0     # 0 = 무료 모델만 (유료 도전자/judge 금지)
  max_completion_tokens: 512
  min_bucket_headroom: 0.5  # 스로틀 버킷 잔량 50% 미만이면 스킵 (실트래픽 우선)
```

- `daily_budget_usd: 0`(기본)이면 도전자·judge 모두 **가격 (0,0) 확정 모델만** —
  unknown 가격은 보수적으로 제외(§5.12와 동일 규칙).
- 예산 소진 판정은 §5.12 비용 계산 재사용. 소진 시 당일 자정(UTC)까지 중단 + 로그 1회.
- rpm 소모는 실트래픽과 같은 키 버킷을 쓰되 `min_bucket_headroom` 조건으로 양보.

## 7. 데이터 모델

```sql
CREATE TABLE shadow_evals (
    id           INTEGER PRIMARY KEY,
    timestamp    TEXT NOT NULL,          -- UTC ISO8601
    task_type    TEXT NOT NULL,
    champion     TEXT NOT NULL,          -- 실선택 모델 id
    challenger   TEXT NOT NULL,
    winner       TEXT NOT NULL,          -- 'champion' | 'challenger' | 'tie'
    confidence   REAL,
    judge_model  TEXT NOT NULL,
    challenger_cost REAL DEFAULT 0.0,    -- judge 비용 포함 합산
    prompt_tokens INTEGER DEFAULT 0      -- 크기 분포 분석용 (본문은 저장 안 함)
);
CREATE INDEX idx_se_pair ON shadow_evals(champion, challenger, task_type);
```

## 8. Tuner 연동 — 점수화

- (model, task)별 **win-rate delta**: `wr = wins / (wins + losses)` (tie 제외,
  표본 `min_samples` 이상일 때만).
- 보정 기여: `judge_delta = (wr - 0.5) * 4` → ±2 범위. 기존 실패율 기반 delta와
  **가중 평균**(기본 judge 0.5 : telemetry 0.5)한 뒤 기존 ±2 클램프 통과 —
  기존 안전장치(일시 장애가 capability 판단을 뒤집지 않음)를 그대로 상속.
- tier는 건드리지 않는다 (2026-07-11 결정 유지: tier 승격은 사람 검토 사항.
  단, wr가 지속적으로 극단이면 대시보드에 "tier 재검토 후보" 배지로만 노출).

## 9. 열린 결정 (착수 전 사용자 확인)

| # | 질문 | 기본 권고 |
| --- | --- | --- |
| ① | 유료 모델을 도전자/judge로 허용할지 (`daily_budget_usd > 0`) | 1단계는 무료만 — 예산 기능은 스키마만 준비 |
| ② | judge 자동 선정 vs 명시 지정 강제 | 자동 선정 + 대시보드에 현재 judge 표시 |
| ③ | 진짜 A/B(실트래픽 1~5%를 도전자로 직접 라우팅, 사용자 재시도율 관측)로의 2단계 확장 | 보류 — 섀도 데이터가 쌓인 뒤 별도 결정. 실사용자 경험을 실험에 노출하는 것은 성격이 다른 결정 |
| ④ | Anthropic dialect 요청도 섀도 대상에 포함할지 | 포함 (변환 후 내부 포맷 동일 — 제외할 이유 없음) |

## 10. 구현 분해 (Roadmap S5, Opus 위임 단위)

| 모듈 | 내용 | 난이도 |
| --- | --- | --- |
| `core/shadow.py` | Sampler + Runner + Judge 오케스트레이션 (백그라운드 태스크, tuner._loop 패턴 재사용) | ★★ |
| settings 확장 | `shadow:` 블록 스키마 + 검증 | ★ |
| storage | shadow_evals 테이블 + repo 메서드 (기존 Repository 패턴) | ★ |
| tuner v2 | win-rate 집계 + 가중 결합 | ★★ |
| pipeline 훅 | `_record` 직후 fire-and-forget 1곳 | ★ |
| 대시보드 | Model Arena 섹션 (win-rate 매트릭스) | ★★ |
| 테스트 | 시뮬레이터에 judge 시나리오 추가 — 편향 방어(순서 랜덤화) 결정론 검증 포함 | ★★ |

**완료 기준(DoD)**: 섀도 평가가 켜진 상태에서 실요청 p95 지연 변화 0(±측정 오차),
무료-only 모드에서 비용 0 확인, win-rate가 tuner 보정에 반영되는 E2E, 프롬프트
본문이 어떤 테이블·로그에도 없음을 확인하는 테스트.

## 11. 왜 이 설계인가 (기각한 대안)

- **PRD의 "AI Judge 상시 평가"** (모든 요청 채점): 비용이 트래픽에 비례해 폭주.
  샘플링+반사실 우선이 같은 정보를 1/20 비용으로 얻는다.
- **벤치마크 자동 재수집**: 공개 벤치마크는 신모델 커버가 느리고 오염 논란이
  잦다. 자기 트래픽 기반 평가는 사용자 환경(주 언어·에이전트)에 자동 적응 —
  §5.11 학습 루프와 같은 논거의 연장.
- **Elo 레이팅**: 표본이 적은 초기엔 win-rate + min_samples가 더 단순하고 해석
  가능. 표본이 충분해지면 Elo 전환을 후속 검토(스키마는 pairwise 결과라 호환).
