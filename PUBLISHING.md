# PyPI 패키지명 등록 가이드

> ✅ **등록 완료 (2026-07-09)** — `forge-gateway 0.3.0.dev0` 업로드됨: https://pypi.org/project/forge-gateway/
> 이 문서는 이후 릴리스 업로드 절차의 참고용으로 유지한다.
> 배경: [DESIGN.md](DESIGN.md) §8.1 — PyPI 조회 결과(2026-07) `forge-gateway` 가용 확인, 조기 등록 권장.

## 확정 정보

- **패키지명**: `forge-gateway` (예비 후보: `forge-llm`, `forge-router`)
- PyPI는 이름만 예약하는 기능이 없다 — **최소 패키지를 한 번 업로드하는 것이 곧 등록**이다.
- 업로드 성공 시점부터 이름은 계정 소유가 되며, 같은 이름으로 타인 등록 불가.

## 체크리스트

### 1. 계정 준비 (1회)

- [ ] https://pypi.org/account/register/ 계정 생성 + 이메일 인증
- [ ] **2FA 설정** — 업로드 계정에 필수
- [ ] Account settings → **API tokens** → "Add API token" 발급
  - 처음에는 scope를 전체 계정으로 생성 (프로젝트가 없으면 프로젝트 scope 선택 불가)
  - 첫 업로드 후 `forge-gateway` 전용 토큰으로 재발급해 좁히기
  - 토큰은 `pypi-` 로 시작 — **발급 화면에서만 보이므로 즉시 안전한 곳에 저장**

### 2. 최소 패키지 준비

프로젝트 루트에 `pyproject.toml` 생성:

```toml
[project]
name = "forge-gateway"
version = "0.0.1"
description = "Intelligent AI Gateway for Coding Agents (in development)"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] `pyproject.toml` 작성 (위 내용)
- [ ] `README.md` 없으면 프로젝트 한 줄 소개라도 작성 (빈 껍데기 방지 — 아래 주의사항)
- [ ] LICENSE 파일 (MIT) — DESIGN.md §8.2에서도 최우선 항목

### 3. 빌드 & 업로드

```powershell
pip install build twine
python -m build              # dist/ 에 .whl + .tar.gz 생성
twine upload dist/*
# username: __token__       (문자 그대로 __token__)
# password: pypi-로 시작하는 API 토큰
```

- [ ] 빌드 성공 확인 (`dist/` 에 파일 2개)
- [ ] 업로드 성공 → https://pypi.org/project/forge-gateway/ 페이지 확인
- [ ] (선택) `forge-gateway` 전용 scope 토큰으로 재발급, 기존 전체 토큰 폐기

## 주의사항

1. **완전히 빈 껍데기 업로드는 피할 것.** PyPI 정책(PEP 541)상 내용 없는 이름 선점(squatting)은 신고로 회수될 수 있다. "개발 중인 실제 프로젝트의 0.0.1"이면 문제없음 — description과 README에 프로젝트 소개를 넣어서 올릴 것. 현재 `src/` 코드를 그대로 0.0.1로 올려도 된다.
2. **연습이 필요하면 TestPyPI 먼저.** https://test.pypi.org (별도 계정)는 실 등록과 무관한 샌드박스다. `twine upload --repository testpypi dist/*`
3. 패키지명은 대소문자·`-`/`_` 구분 없이 정규화된다 (`forge-gateway` == `forge_gateway` == `Forge-Gateway`).

## 이후

- ~~pyproject.toml 작성~~ → **완료 (M2.5)**: 패키지 `forge_gateway`, 엔트리포인트 `forge`/`forge-gw`, 의존성 이관까지 반영됨. 위의 "최소 패키지 준비" 단계는 건너뛰고 §3 빌드 & 업로드만 실행하면 된다.
- CI 릴리스 자동화 시 API 토큰 대신 **Trusted Publishing**(GitHub Actions ↔ PyPI 연동, 토큰 불필요)으로 전환 (DESIGN.md §8.2)
