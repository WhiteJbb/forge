"""
Forge Configuration
"""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ProviderConfig(BaseModel):
    """Provider configuration"""
    name: str
    api_base: str
    api_key_env: str
    enabled: bool = True


class TierConfig(BaseModel):
    """Tier configuration"""
    name: str
    models: list[str] = Field(default_factory=list)
    priority: int = 1


class PolicyRule(BaseModel):
    """Policy rule for routing"""
    when: dict = Field(default_factory=dict)
    prefer: list[str] = Field(default_factory=list)
    fallback: list[str] = Field(default_factory=list)


class ForgeSettings(BaseSettings):
    """Forge application settings"""

    # Server
    host: str = "0.0.0.0"
    port: int = 4000
    debug: bool = False

    # Database
    database_url: str = "sqlite:///forge.db"

    # Redis
    redis_url: Optional[str] = None

    # Health Check
    health_check_interval: int = 30  # seconds
    health_check_timeout: int = 10  # seconds

    # Cooldown
    cooldown_duration: int = 300  # 5 minutes
    max_failures_before_cooldown: int = 3

    # Routing
    default_tier: str = "tier1"
    max_fallbacks: int = 2
    request_timeout: int = 600

    # LiteLLM config path
    litellm_config_path: str = "config.yaml"

    # NVIDIA API
    nvidia_api_key: Optional[str] = None

    # Capability Matrix defaults
    default_capability_scores: dict = Field(default_factory=lambda: {
        "code": 7,
        "debug": 7,
        "refactor": 7,
        "docs": 7,
        "context": 7,
        "speed": 7,
    })

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "allow",
    }


# Global settings instance
settings = ForgeSettings()


# Tier definitions
TIERS = {
    "tier1": TierConfig(
        name="tier1",
        models=[
            "z-ai/glm-5.2",
            "deepseek-ai/deepseek-v4-pro",
            "qwen/qwen3.5-397b-a17b",
        ],
        priority=1,
    ),
    "tier2": TierConfig(
        name="tier2",
        models=[
            "mistralai/mistral-large-3-675b-instruct-2512",
            "nvidia/nemotron-3-ultra-550b-a55b",
            "mistralai/mistral-medium-3.5-128b",
            "deepseek-ai/deepseek-v4-flash",
            "nvidia/nemotron-3-super-120b-a12b",
            "gpt-oss-120b",
        ],
        priority=2,
    ),
    "tier3": TierConfig(
        name="tier3",
        models=[
            "mistralai/mistral-small-4-119b-2603",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "minimaxai/minimax-m3",
        ],
        priority=3,
    ),
}


# Capability Matrix (initial scores)
CAPABILITY_MATRIX = {
    "z-ai/glm-5.2": {"code": 10, "debug": 10, "refactor": 10, "docs": 8, "context": 9, "speed": 9},
    "deepseek-ai/deepseek-v4-pro": {"code": 10, "debug": 10, "refactor": 9, "docs": 8, "context": 10, "speed": 8},
    "qwen/qwen3.5-397b-a17b": {"code": 9, "debug": 9, "refactor": 10, "docs": 10, "context": 10, "speed": 8},
    "mistralai/mistral-large-3-675b-instruct-2512": {"code": 8, "debug": 8, "refactor": 8, "docs": 9, "context": 9, "speed": 8},
    "nvidia/nemotron-3-ultra-550b-a55b": {"code": 8, "debug": 7, "refactor": 8, "docs": 8, "context": 9, "speed": 7},
    "mistralai/mistral-medium-3.5-128b": {"code": 7, "debug": 7, "refactor": 7, "docs": 8, "context": 8, "speed": 9},
    "deepseek-ai/deepseek-v4-flash": {"code": 8, "debug": 7, "refactor": 7, "docs": 7, "context": 8, "speed": 10},
    "nvidia/nemotron-3-super-120b-a12b": {"code": 7, "debug": 7, "refactor": 7, "docs": 7, "context": 7, "speed": 9},
    "gpt-oss-120b": {"code": 8, "debug": 8, "refactor": 8, "docs": 9, "context": 9, "speed": 8},
    "mistralai/mistral-small-4-119b-2603": {"code": 6, "debug": 6, "refactor": 6, "docs": 7, "context": 6, "speed": 10},
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": {"code": 6, "debug": 6, "refactor": 6, "docs": 7, "context": 7, "speed": 9},
    "minimaxai/minimax-m3": {"code": 6, "debug": 6, "refactor": 6, "docs": 7, "context": 6, "speed": 9},
}


# Task keyword mapping for Request Analyzer
TASK_KEYWORDS = {
    "refactor": [
        "리팩토링", "refactor", "개선", "improve", "재구성", "restructure",
        "clean up", "정리", "optimize", "최적화",
    ],
    "debug": [
        "버그", "bug", "에러", "error", "수정", "fix", "debug",
        "디버그", "문제", "issue", "오류", "해결", "resolve",
    ],
    "documentation": [
        "README", "readme", "문서", "documentation", "docs", "작성",
        "write", "설명", "explain", "주석", "comment",
    ],
    "testing": [
        "테스트", "test", "테스트 코드", "test code", "unit test",
        "유닛 테스트", "spec", "스펙",
    ],
    "coding": [
        "구현", "implement", "코드", "code", "개발", "develop",
        "작성", "write", "생성", "generate", "만들", "create",
        "추가", "add", "기능", "feature",
    ],
}