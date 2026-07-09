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


def load_config(path: str | Path = "forge.yaml") -> ForgeConfig:
    """forge.yaml을 읽고 검증한다. 실패 시 명확한 메시지와 함께 ConfigError."""
    path = Path(path)
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
