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


class AuthConfig(BaseModel):
    api_key_env: str = "FORGE_API_KEY"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")


class ProviderConfig(BaseModel):
    name: str
    litellm_prefix: str = "openai"
    api_base: Optional[str] = None
    api_key_env: Optional[str] = None
    discovery: bool = True
    free: bool = False
    rpm: Optional[int] = None
    max_concurrent: Optional[int] = None
    pass_reasoning: bool = False

    @field_validator("api_key_env")
    @classmethod
    def _no_literal_keys(cls, v: Optional[str]) -> Optional[str]:
        # 환경변수 "이름"이어야 한다 — 키 문자열이 직접 들어오면 거부 (§8.3)
        if v and (v.startswith("sk-") or v.startswith("nvapi-") or len(v) > 64):
            raise ValueError(
                "api_key_env must be an environment variable NAME, not the key itself"
            )
        return v

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


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
        return ForgeConfig(**raw)
    except Exception as e:
        raise ConfigError(f"invalid config in {path}: {e}") from e
