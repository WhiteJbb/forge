"""
Scheduler - Core routing engine that selects the best model based on
capability, health, latency, cooldown, and policy
"""

import time
import random
from typing import Optional
from .config import TIERS, CAPABILITY_MATRIX, settings


class ModelHealth:
    """Tracks health status of a single model"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.status = "unknown"  # healthy, unhealthy, unknown
        self.latency_ms = 0.0
        self.last_check = 0.0
        self.success_count = 0
        self.failure_count = 0
        self.consecutive_failures = 0
        self.last_error = None
        self.in_cooldown = False
        self.cooldown_until = 0.0
        self.total_requests = 0
        self.total_failures = 0
        self.total_429 = 0
        self.total_5xx = 0
        self.total_timeouts = 0

    def record_success(self, latency_ms: float):
        """Record a successful request"""
        self.status = "healthy"
        self.latency_ms = latency_ms
        self.last_check = time.time()
        self.success_count += 1
        self.consecutive_failures = 0
        self.total_requests += 1
        self.in_cooldown = False
        self.cooldown_until = 0.0

    def record_failure(self, error_type: str = "unknown"):
        """Record a failed request"""
        self.last_check = time.time()
        self.failure_count += 1
        self.consecutive_failures += 1
        self.total_requests += 1
        self.total_failures += 1
        self.last_error = error_type

        if error_type == "429":
            self.total_429 += 1
        elif error_type.startswith("5"):
            self.total_5xx += 1
        elif error_type == "timeout":
            self.total_timeouts += 1

        # Check if we should enter cooldown
        if self.consecutive_failures >= settings.max_failures_before_cooldown:
            self.enter_cooldown()

    def enter_cooldown(self):
        """Put model in cooldown"""
        self.in_cooldown = True
        self.cooldown_until = time.time() + settings.cooldown_duration
        self.status = "cooldown"

    def check_cooldown_expired(self) -> bool:
        """Check if cooldown has expired"""
        if self.in_cooldown and time.time() > self.cooldown_until:
            self.in_cooldown = False
            self.cooldown_until = 0.0
            self.consecutive_failures = 0
            self.status = "unknown"  # Will be updated on next health check
            return True
        return False

    def is_available(self) -> bool:
        """Check if model is available for routing"""
        self.check_cooldown_expired()
        return not self.in_cooldown and self.status != "unhealthy"

    def get_failure_rate(self) -> float:
        """Get failure rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.total_failures / self.total_requests) * 100

    def to_dict(self) -> dict:
        """Convert to dict for API response"""
        remaining_cooldown = 0
        if self.in_cooldown and self.cooldown_until > time.time():
            remaining_cooldown = int(self.cooldown_until - time.time())

        return {
            "model": self.model_name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "in_cooldown": self.in_cooldown,
            "cooldown_remaining": remaining_cooldown,
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "total_429": self.total_429,
            "total_5xx": self.total_5xx,
            "total_timeouts": self.total_timeouts,
            "failure_rate": round(self.get_failure_rate(), 2),
            "consecutive_failures": self.consecutive_failures,
        }


class Scheduler:
    """
    Core routing engine that selects the best model based on:
    - Capability scores
    - Health status
    - Latency
    - Cooldown status
    - Tier priority
    - Policy rules
    """

    def __init__(self):
        self.model_health: dict[str, ModelHealth] = {}
        self.capability_matrix = CAPABILITY_MATRIX.copy()
        self.tiers = TIERS
        self._init_model_health()

    def _init_model_health(self):
        """Initialize health tracking for all models"""
        for tier_name, tier_config in self.tiers.items():
            for model in tier_config.models:
                if model not in self.model_health:
                    self.model_health[model] = ModelHealth(model)

    def select_model(
        self,
        task_type: str = "coding",
        preferred_tier: Optional[str] = None,
        exclude_models: Optional[list[str]] = None,
    ) -> tuple[Optional[str], dict]:
        """
        Select the best model for the given task.

        Args:
            task_type: Type of task (coding, debug, refactor, etc.)
            preferred_tier: Preferred tier to use
            exclude_models: Models to exclude (already tried)

        Returns:
            Tuple of (model_name, selection_info)
        """
        exclude_models = exclude_models or []

        # Determine tier order
        tier_order = self._get_tier_order(preferred_tier)

        # Try each tier in order
        for tier_name in tier_order:
            tier = self.tiers.get(tier_name)
            if not tier:
                continue

            # Get available models in this tier
            available_models = self._get_available_models(tier.models, exclude_models)

            if not available_models:
                continue

            # Score each available model
            scored_models = []
            for model in available_models:
                score = self._calculate_score(model, task_type)
                scored_models.append((model, score))

            if not scored_models:
                continue

            # Sort by score (descending)
            scored_models.sort(key=lambda x: x[1], reverse=True)

            # Get top candidates (within 10% of best score)
            best_score = scored_models[0][1]
            threshold = best_score * 0.9
            top_candidates = [m for m, s in scored_models if s >= threshold]

            # Random selection among top candidates (for load balancing)
            selected = random.choice(top_candidates)

            selection_info = {
                "selected_model": selected,
                "tier": tier_name,
                "task_type": task_type,
                "score": best_score,
                "candidates": [
                    {"model": m, "score": s} for m, s in scored_models[:5]
                ],
            }

            return selected, selection_info

        # No model available
        return None, {
            "selected_model": None,
            "tier": None,
            "task_type": task_type,
            "score": 0,
            "error": "No available models",
        }

    def _get_tier_order(self, preferred_tier: Optional[str] = None) -> list[str]:
        """Get tier order based on preference"""
        all_tiers = ["tier1", "tier2", "tier3"]

        if preferred_tier and preferred_tier in all_tiers:
            # Start with preferred, then try others in order
            remaining = [t for t in all_tiers if t != preferred_tier]
            return [preferred_tier] + remaining

        return all_tiers

    def _get_available_models(
        self,
        models: list[str],
        exclude_models: list[str],
    ) -> list[str]:
        """Get list of available models"""
        available = []
        for model in models:
            if model in exclude_models:
                continue

            health = self.model_health.get(model)
            if health and health.is_available():
                available.append(model)

        return available

    def _calculate_score(self, model: str, task_type: str) -> float:
        """
        Calculate model score based on:
        Score = Capability + Latency + Health + Availability + Context + Priority
                - Failure Score - Cooldown Penalty
        """
        # Capability score (0-10)
        capability = self._get_capability_score(model, task_type)

        # Health score (0-10)
        health = self._get_health_score(model)

        # Latency score (0-10) - lower latency = higher score
        latency = self._get_latency_score(model)

        # Availability score (0-10)
        availability = self._get_availability_score(model)

        # Failure penalty (0-10)
        failure_penalty = self._get_failure_penalty(model)

        # Cooldown penalty (0-10)
        cooldown_penalty = self._get_cooldown_penalty(model)

        # Calculate total score
        score = (
            capability * 0.35
            + health * 0.20
            + latency * 0.15
            + availability * 0.15
            - failure_penalty * 0.15
            - cooldown_penalty * 0.10
        )

        return max(score, 0.0)

    def _get_capability_score(self, model: str, task_type: str) -> float:
        """Get capability score for model and task"""
        caps = self.capability_matrix.get(model, settings.default_capability_scores)

        # Map task type to capability key
        task_to_cap = {
            "coding": "code",
            "debug": "debug",
            "refactor": "refactor",
            "documentation": "docs",
            "testing": "code",  # Use code capability for testing
        }

        cap_key = task_to_cap.get(task_type, "code")
        return float(caps.get(cap_key, 7))

    def _get_health_score(self, model: str) -> float:
        """Get health score (0-10)"""
        health = self.model_health.get(model)
        if not health:
            return 5.0  # Unknown health

        if health.status == "healthy":
            return 10.0
        elif health.status == "unknown":
            return 5.0
        elif health.status == "cooldown":
            return 0.0
        else:  # unhealthy
            return 0.0

    def _get_latency_score(self, model: str) -> float:
        """Get latency score (0-10, lower latency = higher score)"""
        health = self.model_health.get(model)
        if not health or health.latency_ms == 0:
            return 5.0  # Unknown latency

        # Score based on latency (100ms = 10, 2000ms = 0)
        latency = health.latency_ms
        if latency <= 100:
            return 10.0
        elif latency >= 2000:
            return 0.0
        else:
            return 10.0 - ((latency - 100) / 1900) * 10

    def _get_availability_score(self, model: str) -> float:
        """Get availability score based on success rate"""
        health = self.model_health.get(model)
        if not health or health.total_requests == 0:
            return 8.0  # Default for new models

        success_rate = 1 - (health.total_failures / health.total_requests)
        return success_rate * 10

    def _get_failure_penalty(self, model: str) -> float:
        """Get failure penalty score"""
        health = self.model_health.get(model)
        if not health:
            return 0.0

        # Penalty based on recent failures
        penalty = min(health.consecutive_failures * 2, 10)
        return penalty

    def _get_cooldown_penalty(self, model: str) -> float:
        """Get cooldown penalty score"""
        health = self.model_health.get(model)
        if not health:
            return 0.0

        if health.in_cooldown:
            return 10.0
        return 0.0

    def record_success(self, model: str, latency_ms: float):
        """Record a successful request"""
        health = self.model_health.get(model)
        if health:
            health.record_success(latency_ms)

    def record_failure(self, model: str, error_type: str = "unknown"):
        """Record a failed request"""
        health = self.model_health.get(model)
        if health:
            health.record_failure(error_type)

    def update_health_status(self, model: str, status: str, latency_ms: float = 0):
        """Update health status from health monitor"""
        health = self.model_health.get(model)
        if health:
            health.status = status
            if latency_ms > 0:
                health.latency_ms = latency_ms
            health.last_check = time.time()

    def get_all_models_status(self) -> list[dict]:
        """Get status of all models"""
        return [health.to_dict() for health in self.model_health.values()]

    def get_models_by_tier(self, tier: str) -> list[dict]:
        """Get status of models in a specific tier"""
        tier_config = self.tiers.get(tier)
        if not tier_config:
            return []

        result = []
        for model in tier_config.models:
            health = self.model_health.get(model)
            if health:
                result.append(health.to_dict())
        return result

    def get_cooldown_models(self) -> list[dict]:
        """Get list of models in cooldown"""
        result = []
        for health in self.model_health.values():
            if health.in_cooldown:
                result.append(health.to_dict())
        return result