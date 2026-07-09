# User Scenarios — 사용자 시나리오

> 페르소나: [Personas.md](Personas.md). 각 시나리오는 수용 기준을 겸한다.

## S1. 최초 설치와 연결 (P1)

1. `git clone` → `python -m venv .venv` → `pip install -e .` → `forge init` 실행[^1]
2. init이 환경변수에서 `NVIDIA_API_KEY`를 감지하고 forge.yaml 생성 제안
3. `forge start` → `forge doctor`로 연결 확인
4. Cline 설정에서 베이스 URL `http://localhost:4000/v1`, 모델 `auto` 지정
5. **성공 기준**: 여기까지 5분 이내, 문서는 README 하나로 충분

[^1]: PyPI에 `forge-gateway 0.3.0.dev0`가 dev 릴리스로 올라가 있지만(PUBLISHING.md), `pipx install forge-gateway` 한 줄 설치는 **PyPI 정식(비-dev) 릴리스 이후**의 권장 경로다. 현재 기준 흐름은 저장소를 clone해 editable(`-e`) 설치하는 것.

## S2. 일상 사용 — 모델 무감지 (P1)

1. 사용자가 Cline에서 "이 함수 리팩토링해줘" 요청
2. Forge: task=refactor 판정 → refactor 점수 높은 tier1 모델 선택 → 응답
3. 이어서 "README 써줘" → task=documentation → docs 특화 모델로 전환 (단, 같은 대화면 세션 고정 우선)
4. **성공 기준**: 사용자는 모델 이름을 한 번도 보지 않는다. 응답 헤더(`X-Forge-Model`)로만 확인 가능

## S3. 429 폭주 — 끊김 없는 failover (P1)

1. NVIDIA 무료 티어 한도 도달 직전 — 선제 스로틀이 트래픽을 OpenRouter로 분산
2. 그래도 429 발생 → 해당 모델 즉시 쿨다운 → 다음 후보로 투명 재시도
3. 에이전트는 정상 응답만 받는다
4. **성공 기준**: 사용자가 429를 인지하는 유일한 경로는 대시보드 쿨다운 목록

## S4. 정책 작성 — 무료만 쓰기 (P1, P2)

1. `forge guard --no-paid` 한 줄 실행 — forge.local.yaml에 지출 가드가 기록되고 실행 중인 서버에 자동 reload까지 적용된다. (직접 편집을 원하면 forge.yaml에 `constraints: { allow_paid: false }` 추가 후 `forge reload` / `POST /admin/reload`도 동일하게 동작)
2. 가격 미확인(unknown) 모델까지 후보에서 제외됨
3. **성공 기준**: 이후 `daily_summary` 비용이 항상 $0.00

## S5. 팀 공용 게이트웨이 (P2)

1. 서버에 Forge 배포, `server.host` 외부 바인딩 + `FORGE_API_KEY` 설정
2. 팀원들은 같은 엔드포인트 + 키로 접속
3. 리드는 대시보드에서 사용량/비용/모델 상태 확인
4. **성공 기준**: 팀원 온보딩 = 베이스 URL과 키 전달, 그것뿐

## S6. 라우팅 디버깅 (P3)

1. "왜 이 요청이 tier2로 갔지?" → `POST /v1/route/explain`에 동일 요청 전달
2. task 판정, 매칭 정책, 하드 필터 탈락 모델과 사유, 후보 스코어표 확인
3. capability 오버라이드 또는 정책 수정 → reload → 재확인
4. **성공 기준**: 라우팅 결정의 모든 단계가 설명 가능

## S7. 새 모델 등장 (P3)

1. 프로바이더에 신규 모델 추가됨 → 부팅/reload 시 Auto Discovery가 tier3 기본값으로 등록
2. 실트래픽이 쌓이면 텔레메트리가 점수 보정 (M3)
3. **성공 기준**: 사용자가 아무것도 안 해도 신규 모델이 후보에 들어온다
