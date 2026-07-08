"""
Health Monitor - Periodically checks model health and updates status
"""

import asyncio
import time
import logging
import httpx
from typing import Optional
from .config import TIERS, settings
from .scheduler import Scheduler

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Monitors model health by periodically checking availability"""

    def __init__(self, scheduler: Scheduler, api_base: str = "https://integrate.api.nvidia.com/v1"):
        self.scheduler = scheduler
        self.api_base = api_base
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.client: Optional[httpx.AsyncClient] = None

    async def start(self):
        """Start the health monitor"""
        if self.running:
            return

        self.running = True
        self.client = httpx.AsyncClient(timeout=settings.health_check_timeout)
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Health Monitor started")

    async def stop(self):
        """Stop the health monitor"""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self.client:
            await self.client.aclose()
            self.client = None

        logger.info("Health Monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                await self._check_all_models()
            except Exception as e:
                logger.error(f"Health monitor error: {e}")

            await asyncio.sleep(settings.health_check_interval)

    async def _check_all_models(self):
        """Check health of all models"""
        tasks = []
        for tier_name, tier_config in TIERS.items():
            for model in tier_config.models:
                tasks.append(self._check_model_health(model))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_model_health(self, model: str):
        """Check health of a single model"""
        if not self.client:
            return

        # Skip if in cooldown
        health = self.scheduler.model_health.get(model)
        if health and health.in_cooldown:
            return

        start_time = time.time()
        try:
            # Try a minimal completion request
            api_key = settings.nvidia_api_key or ""
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            }

            response = await self.client.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=settings.health_check_timeout,
            )

            latency = (time.time() - start_time) * 1000

            if response.status_code == 200:
                self.scheduler.update_health_status(model, "healthy", latency)
                logger.debug(f"Model {model}: healthy ({latency:.0f}ms)")
            elif response.status_code == 429:
                # Rate limited - don't mark as unhealthy, just note it
                logger.warning(f"Model {model}: rate limited (429)")
                # If already unknown, keep as unknown
                health = self.scheduler.model_health.get(model)
                if health and health.status != "healthy":
                    self.scheduler.update_health_status(model, "unknown", 0)
            else:
                self.scheduler.update_health_status(model, "unhealthy", 0)
                logger.warning(f"Model {model}: unhealthy (status {response.status_code})")

        except httpx.TimeoutException:
            self.scheduler.update_health_status(model, "unhealthy", 0)
            logger.warning(f"Model {model}: timeout")

        except httpx.ConnectError:
            self.scheduler.update_health_status(model, "unhealthy", 0)
            logger.warning(f"Model {model}: connection error")

        except Exception as e:
            self.scheduler.update_health_status(model, "unhealthy", 0)
            logger.error(f"Model {model}: error - {e}")

    async def check_model_once(self, model: str) -> dict:
        """Manually check a single model's health"""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=settings.health_check_timeout)

        start_time = time.time()
        try:
            api_key = settings.nvidia_api_key or ""
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            }

            response = await self.client.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=settings.health_check_timeout,
            )

            latency = (time.time() - start_time) * 1000

            if response.status_code == 200:
                self.scheduler.update_health_status(model, "healthy", latency)
                return {"model": model, "status": "healthy", "latency_ms": round(latency, 1)}
            else:
                return {
                    "model": model,
                    "status": "unhealthy",
                    "error": f"HTTP {response.status_code}",
                }

        except Exception as e:
            return {"model": model, "status": "unhealthy", "error": str(e)}

    async def discover_models(self) -> list[str]:
        """Discover available models from provider"""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=settings.health_check_timeout)

        try:
            api_key = settings.nvidia_api_key or ""
            headers = {"Authorization": f"Bearer {api_key}"}

            response = await self.client.get(
                f"{self.api_base}/models",
                headers=headers,
                timeout=settings.health_check_timeout,
            )

            if response.status_code == 200:
                data = response.json()
                models = []
                for item in data.get("data", []):
                    model_id = item.get("id", "")
                    if model_id:
                        models.append(model_id)

                logger.info(f"Discovered {len(models)} models from provider")
                return models
            else:
                logger.error(f"Failed to discover models: HTTP {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Model discovery error: {e}")
            return []