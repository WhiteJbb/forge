"""forge.yaml 로더 — 스키마 검증과 기본값 (DESIGN.md §5.9)

코드에는 스키마와 기본값만 두고, 모든 설정 값은 forge.yaml이 단일 소스다.
API 키는 환경변수로만 받는다 (§8.3).
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

VALID_TIERS = ("tier1", "tier2", "tier3")
VALID_FEATURES = ("tools", "parallel_tools", "json_mode", "vision", "streaming")


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4000
    debug: bool = False
    max_body_mb: int = 20
    # CORS 허용 오리진 (§8.3). 기본은 빈 목록 = CORS 미들웨어 비활성(브라우저
    # cross-origin 접근 차단). 브라우저 앱을 붙일 때만 오리진을 명시적으로 나열한다.
    cors_origins: list[str] = Field(default_factory=list)


class AuthConfig(BaseModel):
    api_key_env: str = "FORGE_API_KEY"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")


def _reject_literal_key(v: str, field: str) -> str:
    # 환경변수 "이름"이어야 한다 — 키 문자열이 직접 들어오면 거부 (§8.3)
    if v and (v.startswith("sk-") or v.startswith("nvapi-") or len(v) > 64):
        raise ValueError(
            f"{field} must be an environment variable NAME, not the key itself"
        )
    return v


class AwsAuthConfig(BaseModel):
    """AWS SigV4 자격 계약 (Bedrock, §5.1 확장) — 값은 전부 환경변수 이름으로만.

    스키마·kwargs 스레딩까지가 이 계약의 범위다. 카탈로그 등재·실검증은
    별도 작업(Roadmap S6, 기존 P6 보류의 해제 조건).
    """

    access_key_env: str
    secret_key_env: str
    session_token_env: Optional[str] = None
    region: str

    @field_validator("access_key_env", "secret_key_env", "session_token_env")
    @classmethod
    def _no_literal(cls, v: Optional[str]) -> Optional[str]:
        return _reject_literal_key(v, "aws.*_env") if v else v


class ProviderConfig(BaseModel):
    name: str
    litellm_prefix: str = "openai"
    api_base: Optional[str] = None
    api_key_env: Optional[str] = None
    # 멀티 API 키 로테이션 (DecisionLog 2026-07-12): 같은 provider에 키 여러 개를
    # 등록해 무료 티어 한도를 곱한다. rpm 버킷·429 쿨다운은 "키 단위"(core/throttle.py),
    # max_concurrent 세마포어는 인프라 동시성이라 provider 단위 유지.
    # api_key_envs가 있으면 api_key_env보다 우선한다 (겸용 시 목록만 사용).
    api_key_envs: list[str] = Field(default_factory=list)
    # Azure OpenAI 계약 (§5.1 확장): litellm kwargs로 스레딩만, 카탈로그 등재는 S6
    api_version: Optional[str] = None
    aws: Optional[AwsAuthConfig] = None
    discovery: bool = True
    free: bool = False
    rpm: Optional[int] = None  # 키 "하나당" 분당 요청 한도 (§5.13)
    max_concurrent: Optional[int] = None
    pass_reasoning: bool = False
    auto_registered: bool = False  # 카탈로그 자동 등록 여부 (로그/doctor 표시용)

    @field_validator("api_key_env")
    @classmethod
    def _no_literal_keys(cls, v: Optional[str]) -> Optional[str]:
        return _reject_literal_key(v, "api_key_env") if v else v

    @field_validator("api_key_envs")
    @classmethod
    def _no_literal_key_list(cls, v: list[str]) -> list[str]:
        seen = set()
        for name in v:
            _reject_literal_key(name, "api_key_envs")
            if name in seen:
                raise ValueError(f"api_key_envs has duplicate entry {name!r}")
            seen.add(name)
        return v

    @property
    def api_key_env_names(self) -> list[str]:
        """유효 키 환경변수 이름 목록 — api_key_envs 우선, 없으면 api_key_env 단수."""
        if self.api_key_envs:
            return list(self.api_key_envs)
        return [self.api_key_env] if self.api_key_env else []

    @property
    def api_keys(self) -> list[str]:
        """해석된 키 값 목록 (빈 값 제외, 선언 순서 유지) — 인덱스가 곧 key_index."""
        values = (os.environ.get(n, "").strip() for n in self.api_key_env_names)
        return [v for v in values if v]

    @property
    def api_key(self) -> str:
        """대표 키(첫 번째) — probe/list_models 등 단일 키 경로용 (하위 호환)."""
        keys = self.api_keys
        return keys[0] if keys else ""


class ModelOverride(BaseModel):
    id: str  # "provider:provider_model_id"
    tier: Optional[str] = None
    capabilities: dict[str, int] = Field(default_factory=dict)
    features: Optional[list[str]] = None
    context_window: Optional[int] = None
    price_per_mtok: Optional[tuple[float, float]] = None  # (input, output) USD/1M tok

    @field_validator("id")
    @classmethod
    def _id_has_provider(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError(f"model id must be 'provider:model_id', got {v!r}")
        return v

    @field_validator("tier")
    @classmethod
    def _valid_tier(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_TIERS:
            raise ValueError(f"tier must be one of {VALID_TIERS}, got {v!r}")
        return v

    @field_validator("features")
    @classmethod
    def _valid_features(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v:
            unknown = set(v) - set(VALID_FEATURES)
            if unknown:
                raise ValueError(f"unknown features {unknown}, valid: {VALID_FEATURES}")
        return v


class DefaultsConfig(BaseModel):
    capability: int = 7
    tier: str = "tier3"
    features: list[str] = Field(default_factory=lambda: ["tools", "streaming"])

    @field_validator("tier")
    @classmethod
    def _valid_default_tier(cls, v: str) -> str:
        if v not in VALID_TIERS:
            raise ValueError(f"defaults.tier must be one of {VALID_TIERS}, got {v!r}")
        return v

    @field_validator("features")
    @classmethod
    def _valid_default_features(cls, v: "list[str]") -> "list[str]":
        unknown = set(v) - set(VALID_FEATURES)
        if unknown:
            raise ValueError(f"unknown features {unknown}, valid: {VALID_FEATURES}")
        return v


class SchedulerConfig(BaseModel):
    cooldown_seconds: int = 300
    max_failures_before_cooldown: int = 3
    max_attempts: int = 4
    latency_ewma_alpha: float = 0.3
    session_affinity: bool = True
    session_ttl_minutes: int = 30


class TimeoutsConfig(BaseModel):
    connect: float = 5.0
    ttft: float = 30.0
    total_deadline: float = 600.0


class MetricsConfig(BaseModel):
    db_path: str = "forge.db"
    retention_days: int = 30


class HealthConfig(BaseModel):
    probe_idle_minutes: int = 5
    probe_timeout: float = 10.0


class AnalyzerConfig(BaseModel):
    llm_fallback: bool = False


class TunerConfig(BaseModel):
    """Capability 학습 루프 (§5.11-3)"""

    enabled: bool = True
    interval_minutes: int = 30      # 보정 주기
    window_days: int = 3            # 집계 윈도
    min_samples: int = 5            # (model, task)별 최소 표본 — 미만이면 보정 안 함
    demote_failure_rate: float = 0.5  # tools 요청 실패율이 이 이상이면 feature 강등


class PolicyWhen(BaseModel):
    """정책 매칭 조건 — 지정된 필드만 검사한다 (전부 만족 시 매칭, §5.4)"""

    task: list[str] = Field(default_factory=list)   # 빈 리스트 = task 무관
    model: Optional[str] = None                     # 클라이언트가 보낸 model 값과 일치
    client: Optional[str] = None                    # User-Agent 부분 문자열 (대소문자 무시)
    min_prompt_tokens: Optional[int] = None
    max_prompt_tokens: Optional[int] = None

    @field_validator("task")
    @classmethod
    def _valid_tasks(cls, v: list[str]) -> list[str]:
        valid = {"coding", "debug", "refactor", "documentation", "testing"}
        unknown = set(v) - valid
        if unknown:
            raise ValueError(f"unknown tasks {unknown}, valid: {sorted(valid)}")
        return v


class PolicyConstraints(BaseModel):
    """하드 제약 — 매칭 여부와 무관하게 항상 누적 적용 (§5.4)"""

    allow_paid: Optional[bool] = None
    max_cost_per_request: Optional[float] = None
    exclude_providers: list[str] = Field(default_factory=list)


class PolicyRule(BaseModel):
    """`when` 있는 정책은 first-match로 route를 결정하고,
    `when` 없는 정책은 constraints 전용으로 항상 적용된다."""

    name: str
    when: Optional[PolicyWhen] = None
    route: Optional["PolicyRoute"] = None
    constraints: Optional[PolicyConstraints] = None

    @model_validator(mode="after")
    def _has_effect(self) -> "PolicyRule":
        if self.route is None and self.constraints is None:
            raise ValueError(f"policy {self.name!r} has neither route nor constraints")
        return self


class PolicyRoute(BaseModel):
    """후보 그룹의 순서 목록 — 항목은 tier 이름 / 모델 id / 속성 셀렉터 dict (§5.4)"""

    prefer: list = Field(default_factory=list)
    fallback: list = Field(default_factory=list)

    @field_validator("prefer", "fallback")
    @classmethod
    def _valid_items(cls, v: list) -> list:
        for item in v:
            if not isinstance(item, (str, dict)):
                raise ValueError(
                    f"route item must be tier name, model id, or attribute selector dict, "
                    f"got {type(item).__name__}: {item!r}"
                )
        return v


PolicyRule.model_rebuild()  # "PolicyRoute" 전방 참조 해석


class ForgeConfig(BaseModel):
    version: int = 1
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    providers: list[ProviderConfig] = Field(default_factory=list)
    models: list[ModelOverride] = Field(default_factory=list)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    analyzer: AnalyzerConfig = Field(default_factory=AnalyzerConfig)
    tuner: TunerConfig = Field(default_factory=TunerConfig)
    policies: list[PolicyRule] = Field(default_factory=list)
    auto_providers: bool = True  # 카탈로그 자동 등록 (§8.1) — false로 비활성화

    @model_validator(mode="after")
    def _model_providers_exist(self) -> "ForgeConfig":
        provider_names = {p.name for p in self.providers}
        for m in self.models:
            prov = m.id.split(":", 1)[0]
            if prov not in provider_names:
                raise ValueError(
                    f"model {m.id!r} references unknown provider {prov!r} "
                    f"(defined: {sorted(provider_names)})"
                )
        return self

    def provider(self, name: str) -> Optional[ProviderConfig]:
        for p in self.providers:
            if p.name == name:
                return p
        return None


class ConfigError(Exception):
    """forge.yaml 로드/검증 실패 — 부팅을 중단해야 하는 에러"""


# --- 프로바이더 카탈로그 (§8.1 "설치 5분") ---------------------------------
#
# 환경변수에 키가 있고 forge.yaml에 같은 이름의 provider가 없으면 자동 등록한다.
# 명시적 선언이 항상 우선. `auto_providers: false`로 전체 비활성화.
# discovery가 안 되는 프로바이더(anthropic)는 default_models로 모델을 공급한다.
PROVIDER_CATALOG: "list[dict]" = [
    {"name": "nvidia", "key_env": "NVIDIA_API_KEY",
     "api_base": "https://integrate.api.nvidia.com/v1",
     "free": True, "rpm": 40, "max_concurrent": 8},
    {"name": "openrouter", "key_env": "OPENROUTER_API_KEY",
     "api_base": "https://openrouter.ai/api/v1"},
    {"name": "groq", "key_env": "GROQ_API_KEY",
     "api_base": "https://api.groq.com/openai/v1"},
    {"name": "mistral", "key_env": "MISTRAL_API_KEY",
     "api_base": "https://api.mistral.ai/v1"},
    {"name": "deepseek", "key_env": "DEEPSEEK_API_KEY",
     "api_base": "https://api.deepseek.com/v1"},
    {"name": "openai", "key_env": "OPENAI_API_KEY",
     "api_base": "https://api.openai.com/v1"},
    # 아래 3개는 "결제수단 미연결 시 기본 경로"가 recurring 무료 티어임을 공식 문서로
    # 확인함 (docs/Research.md 2026-07-09 조사). 카드 연결 시 실제로는 유료로 전환될 수
    # 있으니 free 플래그를 NVIDIA와 같은 관례로 사용 — "확인된 기본 경로"이지 "영구 보장"은
    # 아니다.
    # capability_seed: discovery id별 tier/capabilities 시드 — nvidia의 models: 오버라이드와
    # 같은 값이지만, 해당 provider가 실제로 auto-register될 때(키가 있을 때)만 적용된다
    # (forge.yaml에 provider를 미리 선언할 필요 없음). 근거·출처: docs/Research.md
    # 2026-07-09 "신규 무료 provider 벤치마크 시드". source="config" 취급이라 §5.6
    # 능동 헬스 probe 대상에도 포함된다(discovery 전용 모델은 제외되는 것과 대비).
    {"name": "cerebras", "key_env": "CEREBRAS_API_KEY",
     "api_base": "https://api.cerebras.ai/v1",
     "free": True, "rpm": 5,  # 공식: RPM 5 / TPM 30K / TPD 1M (Free Trial 티어)
     "capability_seed": {
         # SWE-bench Verified 73.8% / LiveCodeBench V6 84.9 (z.ai 공식) — Cerebras
         # Preview 상태라 예고 없이 제거될 수 있음(공식 문서 명시), self-healing failover가
         # 흡수
         "zai-glm-4.7": {"tier": "tier1",
                         "capabilities": {"code": 9, "debug": 9, "refactor": 9,
                                          "docs": 8, "context": 8, "speed": 8}},
         # nvidia:gpt-oss-120b과 동일 모델(OpenAI 공식 모델카드 SWE-V 52.6%) — 동일 시드 재사용
         "gpt-oss-120b": {"tier": "tier2",
                          "capabilities": {"code": 8, "debug": 7, "refactor": 7,
                                           "docs": 8, "context": 8, "speed": 9}},
     }},
    # SambaNova는 free: True로 잘못 표시했던 걸 재검증 후 정정함(2026-07-09) — 실제로는
    # $5 1회성 트라이얼 크레딧(카드 불필요, ~30일)뿐이고, 소진되면 카드 없이는 402
    # CREDITS_EXHAUSTED로 완전히 막힌다(SambaNova 직원이 커뮤니티에서 "free tier를 별도로
    # 유지할 계획 없다"고 직접 확인). "결제수단 미연결 시 RPM 20" 문서는 rate-limit
    # *등급* 설명일 뿐 소진 후 동작과는 무관 — 다른 provider들처럼 paid 취급.
    # capability_seed는 여전히 유효(모델 품질 순위는 과금 여부와 무관) — allow_paid:false면
    # 자동 제외됨.
    {"name": "sambanova", "key_env": "SAMBANOVA_API_KEY",
     "api_base": "https://api.sambanova.ai/v1",
     "rpm": 20,
     "capability_seed": {
         # SWE-bench Verified 66.0%(2차 자료, 공식 테크리포트 미대조) — Production
         "DeepSeek-V3.1": {"tier": "tier2",
                          "capabilities": {"code": 7, "debug": 6, "refactor": 6,
                                           "docs": 6, "context": 7, "speed": 7}},
         "gpt-oss-120b": {"tier": "tier2",
                          "capabilities": {"code": 8, "debug": 7, "refactor": 7,
                                           "docs": 8, "context": 8, "speed": 9}},
         # SWE-V 78%/SWE-Pro 56.2% 주장 있으나 2차 집계만, 공식 대조 안 됨 — 보수적 배치
         "MiniMax-M2.7": {"tier": "tier2",
                          "capabilities": {"code": 8, "debug": 7, "refactor": 7,
                                           "docs": 7, "context": 7, "speed": 7}},
     }},
    {"name": "gemini", "key_env": "GEMINI_API_KEY",
     "api_base": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "free": True,  # 공식: RPD 매일 리셋(recurring) — 정확한 RPM/RPD 수치는 비공개
     "capability_seed": {
         # SWE-bench Verified 78% (Google 공식 블로그, "2.5 시리즈·Gemini 3 Pro 능가") —
         # Preview 상태
         "models/gemini-3-flash-preview": {
             "tier": "tier1",
             "capabilities": {"code": 9, "debug": 9, "refactor": 9,
                              "docs": 8, "context": 8, "speed": 9}},
         # SWE-bench Pro(Public) 55.1%, Terminal-bench 2.1 76.2% (DeepMind 공식
         # 모델카드) — Stable. tier2 -> tier1: 같은 지표(SWE-bench Pro)로 tier1인
         # deepseek-v4-pro(55.4%, 동일하게 공식 소스)와 사실상 동일 — 사용자 확인
         # (2026-07-11 전체 tier 재검토)
         "models/gemini-3.5-flash": {
             "tier": "tier1",
             "capabilities": {"code": 8, "debug": 8, "refactor": 8,
                              "docs": 8, "context": 8, "speed": 9}},
     }},
    # Zhipu/Z.ai는 무료·유료 모델이 같은 키/엔드포인트에 혼재 — 프로바이더 전체를
    # free로 표시하면 유료 모델까지 (0,0)으로 오분류되므로 free: false로 두고, 공식
    # 문서로 "무료 모델"이라 확인된 항목만 forge.yaml의 models: 오버라이드로 개별 지정.
    {"name": "zai", "key_env": "ZAI_API_KEY",
     "api_base": "https://api.z.ai/api/paas/v4/"},
    # --- 유료 프로바이더 확장 (2026-07-10 조사, docs/Research.md) ---
    # capability_seed의 price_per_mtok은 공식 pricing 페이지 근거로만 채운다(litellm 내장
    # 가격표를 신뢰하지 않고 직접 시딩하기로 결정 — 사용자 확인). 벤치마크/가격을 1차 소스로
    # 확인 못한 항목은 price_per_mtok/capabilities를 비워 tier3·litellm 폴백에 맡긴다
    # (§5.12 가격 우선순위, core/pricing.py).
    {"name": "xai", "key_env": "XAI_API_KEY",
     "api_base": "https://api.x.ai/v1",
     # 공식 REST 레퍼런스(docs.x.ai/docs/api-reference)에 GET /v1/models 미기재 —
     # discovery 지원 여부 미확인이라 보수적으로 off
     "discovery": False,
     "capability_seed": {
         # xAI 자체 발표 SWE-bench Pro 64.7%(제3자 미검증, 참고용) — 플래그십, context 500K.
         # tier1이었으나 같은 xai:grok-build-0.1이 "독립 벤치마크 없음"으로 tier2 받은
         # 것과 동일한 근거 수준(자체 발표뿐, 제3자 미검증)인데 여기만 tier1을 줬던 게
         # 일관성 오류 — tier2로 정정 (2026-07-11, 전체 tier 재검토)
         "grok-4.5": {"tier": "tier2",
                      "capabilities": {"code": 9, "debug": 8, "refactor": 8,
                                       "docs": 8, "context": 9, "speed": 7},
                      "price_per_mtok": [2.00, 6.00]},
         # 코딩 에이전트 전용 모델("agentic software engineering workflows") — 독립
         # 벤치마크 수치가 없어 보수적으로 tier2
         "grok-build-0.1": {"tier": "tier2",
                            "capabilities": {"code": 8, "debug": 7, "refactor": 7,
                                             "docs": 7, "context": 7, "speed": 8},
                            "price_per_mtok": [1.00, 2.00]},
     }},
    {"name": "cohere", "key_env": "COHERE_API_KEY",
     "api_base": "https://api.cohere.ai/compatibility/v1",
     # discovery는 실키로 동작 확인됨(200, OpenAI 포맷, 31개 모델 — 2026-07-10)이지만
     # 그 목록에 채팅 불가 모달(예: cohere-transcribe-03-2026 음성 전사)이 섞여 있음.
     # 4xx는 failover 안 하고 그대로 반환하는 정책(§7 UpstreamBadRequest)상, 스케줄러가
     # 우연히 그런 모델을 골라 라우팅하면 복구 없이 요청이 실패함 -> 의도적으로 off,
     # 채팅 모델만 수동 큐레이션 (사용자 결정 2026-07-10, Research.md 참조)
     "discovery": False,
     # 현재 플래그십(Command A) 가격을 공식 페이지에서 1차 확인 못함(레거시 모델만 나열),
     # 코딩 벤치마크 수치도 확인 실패 -> capability_seed 없이 등록, 가격은 litellm 폴백에 위임
     "default_models": ["command-a-03-2025", "command-r7b-12-2024"]},
    {"name": "together", "key_env": "TOGETHER_API_KEY",
     "api_base": "https://api.together.ai/v1",
     # discovery 기본값(True) 유지 — GET /v1/models가 OpenAI 포맷으로 동작함을 공식 문서로 확인
     "capability_seed": {
         # SWE-bench Verified 80.6% / SWE-bench Pro 55.4% / LiveCodeBench 93.5 (공식 HF 모델카드)
         "deepseek-ai/DeepSeek-V4-Pro": {"tier": "tier1",
                                         "capabilities": {"code": 10, "debug": 9, "refactor": 9,
                                                          "docs": 8, "context": 9, "speed": 7},
                                         "price_per_mtok": [1.74, 3.48]},
     }},
    {"name": "fireworks", "key_env": "FIREWORKS_API_KEY",
     "api_base": "https://api.fireworks.ai/inference/v1",
     # discovery는 실키로 동작 확인됨(OpenAI 포맷 7개 모델 반환 - 2026-07-10)이지만
     # 그 중 flux-1-schnell-fp8(이미지 생성)처럼 채팅 불가 모달이 7개 중 1개꼴로
     # 섞여 있어 Cohere보다 비율이 높음. 4xx는 failover 안 하는 정책(§7)상 스케줄러가
     # 그런 모델을 고르면 복구 없이 요청이 실패하므로 의도적으로 off, 채팅 모델만
     # 수동 큐레이션 (사용자 결정 2026-07-10, Research.md 참조)
     "discovery": False,
     "capability_seed": {
         # SWE-bench Verified 80.6% (공식 모델 페이지)
         "accounts/fireworks/models/deepseek-v4-pro": {
             "tier": "tier1",
             "capabilities": {"code": 10, "debug": 9, "refactor": 9,
                              "docs": 8, "context": 9, "speed": 7},
             "price_per_mtok": [1.74, 3.48]},
         # SWE-bench Verified 80.2% (공식 모델 페이지)
         "accounts/fireworks/models/kimi-k2p6": {
             "tier": "tier1",
             "capabilities": {"code": 10, "debug": 9, "refactor": 8,
                              "docs": 8, "context": 8, "speed": 7},
             "price_per_mtok": [0.95, 4.00]},
         # 가격은 공식 확인됨, 코딩 벤치마크는 미확인 -> tier/capabilities 없이 가격만 시딩
         "accounts/fireworks/models/qwen3p7-plus": {"price_per_mtok": [0.40, 1.60]},
         # GLM-5.2 — nvidia:z-ai/glm-5.2와 동일 모델(forge.yaml, SWE-Pro 62.1% 공식
         # z.ai 벤치마크). 모델 실력은 호스트가 바뀌어도 그대로다(속도만 다름) -
         # 이미 확보한 근거를 그대로 재사용, 새로 지어내지 않음 (2026-07-11 사용자
         # 지적으로 발견한 불일치 수정)
         "accounts/fireworks/models/glm-5p2": {
             "tier": "tier1",
             "capabilities": {"code": 10, "debug": 10, "refactor": 10,
                              "docs": 8, "context": 10, "speed": 8},
             "price_per_mtok": [1.40, 4.40]},
     }},
    {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY",
     "litellm_prefix": "anthropic", "discovery": False,
     # OpenAI 호환 /models가 없어 discovery 불가 — 대표 모델을 카탈로그가 공급
     "default_models": ["claude-opus-4-8", "claude-sonnet-5",
                        "claude-haiku-4-5-20251001"]},
    {"name": "ollama", "key_env": "OLLAMA_API_BASE",  # 값 자체가 base URL
     "litellm_prefix": "ollama", "free": True, "api_base_from_env": True},
]


def _scan_extra_key_envs(key_env: str) -> "list[str]":
    """`{key_env}_2`..`{key_env}_9`를 순서대로 스캔해 값이 있는 환경변수 이름만 모은다.

    중간 번호가 비어 있어도(_2 없고 _3 있음) 건너뛰고 계속 스캔한다 — 사용자가 키를
    회수/추가하며 번호가 듬성듬성해질 수 있으므로 연속성을 요구하지 않는다.
    """
    found = []
    for i in range(2, 10):
        candidate = f"{key_env}_{i}"
        if os.environ.get(candidate, "").strip():
            found.append(candidate)
    return found


def apply_auto_providers(config: "ForgeConfig") -> "list[str]":
    """카탈로그 기반 자동 등록. 추가된 provider 이름 목록을 반환한다."""
    if not config.auto_providers:
        return []
    declared = {p.name for p in config.providers}
    added: list[str] = []
    for item in PROVIDER_CATALOG:
        name = item["name"]
        if name in declared:
            continue  # 명시 선언 우선
        # 기본 키(key_env)가 없으면 provider 자체를 등록하지 않는다 — _2만 있고 본 키가
        # 없는 경우도 여기서 걸러진다(단순성 유지, 스펙 §1).
        key_value = os.environ.get(item["key_env"], "").strip()
        if not key_value:
            continue
        api_base = key_value if item.get("api_base_from_env") else item.get("api_base")

        # 멀티 키 로테이션 관례 (DecisionLog 2026-07-12): api_base_from_env 항목(ollama,
        # 값 자체가 base URL)은 "키"가 아니므로 스캔 대상에서 제외한다.
        extra_key_envs = (
            [] if item.get("api_base_from_env") else _scan_extra_key_envs(item["key_env"])
        )

        provider_kwargs: dict = dict(
            name=name,
            litellm_prefix=item.get("litellm_prefix", "openai"),
            api_base=api_base,
            discovery=item.get("discovery", True),
            free=item.get("free", False),
            rpm=item.get("rpm"),
            max_concurrent=item.get("max_concurrent"),
            auto_registered=True,
        )
        if extra_key_envs:
            provider_kwargs["api_key_envs"] = [item["key_env"]] + extra_key_envs
        else:
            provider_kwargs["api_key_env"] = (
                None if item.get("api_base_from_env") else item["key_env"]
            )
        config.providers.append(ProviderConfig(**provider_kwargs))
        for model_id in item.get("default_models", []):
            config.models.append(ModelOverride(id=f"{name}:{model_id}"))
        for model_id, seed in item.get("capability_seed", {}).items():
            price = seed.get("price_per_mtok")
            config.models.append(ModelOverride(
                id=f"{name}:{model_id}",
                tier=seed.get("tier"),
                capabilities=seed.get("capabilities", {}),
                price_per_mtok=tuple(price) if price else None,
            ))
        added.append(name)
    return added


def load_dotenv(path: str | Path = ".env") -> int:
    """.env의 KEY=VALUE를 os.environ에 주입한다 (이미 설정된 변수는 덮어쓰지 않음).

    표준 라이브러리만 사용하는 최소 구현 — 주석/빈 줄/`export ` 접두어/양끝 따옴표 지원.
    반환값: 주입한 변수 수. 파일이 없으면 0.
    """
    path = Path(path)
    if not path.exists():
        return 0
    loaded = 0
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    except OSError:
        return loaded
    return loaded


def _apply_local_overlay(config: "ForgeConfig", local_path: Path) -> None:
    """forge.local.yaml 오버레이 — `forge guard` 등 CLI가 관리하는 기계 전용 파일.

    손으로 쓴 forge.yaml의 주석을 보존하기 위해 CLI는 이 파일만 다시 쓴다.
    현재 지원: policies (config.policies 앞에 삽입 — 로컬 정책이 first-match에서
    먼저 평가되고, constraints는 어차피 누적 적용된다 §5.4).
    """
    if not local_path.exists():
        return
    try:
        raw = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {local_path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{local_path} must contain a mapping at top level")
    try:
        local_policies = [PolicyRule(**p) for p in (raw.get("policies") or [])]
    except Exception as e:
        raise ConfigError(f"invalid policies in {local_path}: {e}") from e
    config.policies = local_policies + config.policies


def load_config(path: str | Path = "forge.yaml") -> ForgeConfig:
    """forge.yaml을 읽고 검증한다. 실패 시 명확한 메시지와 함께 ConfigError.

    같은 디렉터리의 .env를 먼저 로드한다 — CLI/서버 어느 진입점이든
    run_forge.bat 없이 .env만으로 키가 잡히게 (§8.3).
    """
    path = Path(path)
    load_dotenv(path.resolve().parent / ".env")
    if not path.exists():
        raise ConfigError(f"config file not found: {path.resolve()}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at top level")

    try:
        config = ForgeConfig(**raw)
    except Exception as e:
        raise ConfigError(f"invalid config in {path}: {e}") from e

    _apply_local_overlay(config, path.resolve().parent / "forge.local.yaml")

    added = apply_auto_providers(config)
    if added:
        import logging
        logging.getLogger("forge").info(
            "auto-registered providers from environment keys: %s "
            "(disable with auto_providers: false)", ", ".join(added))
    return config
