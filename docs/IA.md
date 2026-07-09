# IA — 정보 구조

> Forge는 헤드리스 API 게이트웨이가 본체이고, 화면(UI)은 M3의 Next.js Dashboard다.
> 따라서 이 문서는 ① API 표면 구조, ② Dashboard 화면 구조(계획) 두 축으로 관리한다.

## 1. API 표면 (개발자가 만나는 구조)

```text
localhost:4000
├── OpenAI 호환 (클라이언트가 붙는 곳)
│   ├── POST /v1/chat/completions      # 메인 진입점, 스트리밍 포함
│   ├── POST /v1/embeddings
│   └── GET  /v1/models                # auto, auto:debug 등 별칭 포함
├── Anthropic 호환 (M2)
│   └── POST /v1/messages              # Claude Code용
├── 관측/진단
│   ├── GET  /health
│   ├── GET  /v1/stats                 # JSON 메트릭
│   ├── GET  /metrics                  # Prometheus (M3)
│   ├── GET  /dashboard                # Dashboard용 데이터 JSON
│   └── POST /v1/route/explain         # 라우팅 드라이런 (M2)
└── 관리 (loopback 전용)
    ├── POST /admin/reload
    ├── POST /admin/provider
    └── POST /admin/cooldown/{model}/clear
```

상세 명세: [DESIGN.md](../DESIGN.md) §5.8

## 2. CLI 구조 (M2)

```text
forge
├── start      # 서버 기동
├── init       # 대화형 forge.yaml 생성
├── doctor     # 키/연결/discovery 진단
└── models     # Registry 상태 출력
```

## 3. Dashboard 화면 구조 (M3 계획)

| 화면 | 내용 | 소스 |
| --- | --- | --- |
| ① 상태 보드 (홈) | Provider 생사, tier별 모델 상태, 쿨다운 타이머 | `/dashboard` |
| ② 메트릭 | 요청 추이, 성공률, 레이턴시(TTFT)/비용 차트 | `/v1/stats` |
| ③ 정책 뷰어 | forge.yaml 정책 시각화, route/explain 실행기 | `/v1/route/explain` |
| ④ 이벤트 로그 | 최근 failover/쿨다운/스로틀 이벤트 | `/v1/stats` |

화면 흐름: ① 홈에서 이상 감지 → ④ 이벤트에서 원인 확인 → ③ 정책 조정 → `/admin/reload`.

## 변경 규칙

API 엔드포인트 추가/제거, Dashboard 화면 구조 변경은 Approval Gate 대상 — 사용자 승인 후 이 문서와 DESIGN.md를 함께 갱신한다.
