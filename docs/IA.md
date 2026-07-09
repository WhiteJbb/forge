# IA — 정보 구조

> Forge는 헤드리스 API 게이트웨이가 본체이고, 화면(UI)은 FastAPI가 서빙하는 **내장 정적 SPA 대시보드**(`/dashboard/ui`)다 — PRD의 Next.js 계획은 pip 설치만으로 동작해야 하는 도구 특성상 Node 툴체인 요구가 채택 장벽이라 폐기됨(§5.10 결정, 2026-07-09).
> 따라서 이 문서는 ① API 표면 구조, ② Dashboard 화면 구조 두 축으로 관리한다.

## 1. API 표면 (개발자가 만나는 구조)

```text
localhost:4000
├── OpenAI 호환 (클라이언트가 붙는 곳)
│   ├── POST /v1/chat/completions      # 메인 진입점, 스트리밍 포함
│   ├── POST /v1/embeddings
│   └── GET  /v1/models                # auto, auto:debug 등 별칭 포함
├── Anthropic 호환
│   └── POST /v1/messages              # Claude Code용 (ANTHROPIC_BASE_URL)
├── 관측/진단
│   ├── GET  /health
│   ├── GET  /v1/stats                 # JSON 메트릭
│   ├── GET  /v1/stats/recent          # 최근 요청 피드 (대시보드 소비)
│   ├── GET  /metrics                  # Prometheus 포맷
│   ├── GET  /dashboard                # Dashboard용 데이터 JSON
│   ├── GET  /dashboard/ui             # 내장 정적 대시보드 SPA (브라우저 화면)
│   └── POST /v1/route/explain         # 라우팅 드라이런
└── 관리 (loopback 전용)
    ├── POST /admin/reload
    ├── POST /admin/provider           # 501 — 미구현. forge.yaml 수정 후 POST /admin/reload로 대체
    └── POST /admin/cooldown/{model}/clear
```

상세 명세: [DESIGN.md](../DESIGN.md) §5.8

## 2. CLI 구조

```text
forge
├── start      # 서버 기동
├── init       # 대화형 forge.yaml 생성 (감지된 환경변수 API 키 기반 provider 제안)
├── doctor     # 키/연결/discovery 진단
├── models     # Registry 상태 출력 (offline 뷰)
├── reload     # 실행 중인 서버에 POST /admin/reload
├── guard      # forge.local.yaml 지출 가드 관리 (--no-paid/--allow-paid/--max-cost/--off)
└── policies   # 유효 정책을 평가 순서대로 출력
```

## 3. Dashboard 화면 구조 (구현됨 — `/dashboard/ui`)

단일 화면(SPA) 안에 아래 섹션들이 순서대로 배치된다. Next.js처럼 별도 라우트로 나뉘지 않는다.

| 섹션 | 내용 | 데이터 소스 |
| --- | --- | --- |
| Today | 스탯 타일 — 오늘 요청 수 / 성공률 / 평균 레이턴시 / 비용 | `/dashboard` (today) |
| 7-Day Trend | 요청량·평균 레이턴시 추이 차트 (7일) | `/v1/stats?days=7` |
| Throttle | 프로바이더별 선제 스로틀(token bucket) 상태 | `/dashboard` (throttle) |
| Model Status | tier별 모델 상태 보드, 쿨다운 타이머 | `/dashboard` (tiers/cooldown) |
| Policies | 현재 forge.yaml 유효 정책 목록 | `/dashboard` (policies) |
| Recent Requests | 최근 30건 요청 피드 (실시간) | `/v1/stats/recent?limit=30` |
| Route Explain | 임의 요청을 넣어 라우팅 결과를 미리 보는 드라이런 실행기 | `POST /v1/route/explain` |

폴링 주기: `/dashboard` 3초(연결 상태 배너 소유), `/v1/stats` 30초, `/v1/stats/recent`는 독립 피드로 별도 폴링.

화면 흐름: Today/Model Status에서 이상 감지 → Recent Requests에서 원인 확인 → Route Explain으로 정책 매칭 검증 → forge.yaml 또는 `forge guard`로 정책 조정 → `forge reload`(또는 대시보드가 서버 API로 트리거).

## 변경 규칙

API 엔드포인트 추가/제거, Dashboard 화면 구조 변경은 Approval Gate 대상 — 사용자 승인 후 이 문서와 DESIGN.md를 함께 갱신한다.
