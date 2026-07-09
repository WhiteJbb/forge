"""Policy Engine — 정책이 후보 집합과 제약을 결정한다 (DESIGN.md §5.4)

평가 모델:
- `when` 있는 정책은 위에서 아래로 first-match → 그 정책의 route가 후보 그룹을 결정
- `when` 없는 정책은 constraints 전용 — 매칭 여부와 무관하게 항상 누적 적용
- 매칭되는 정책이 없으면 기본 라우팅(tier1→2→3) — M1과 동일한 하위 호환

Scheduler는 여기서 나온 그룹 안에서만 스코어링한다. 가용성/쿨다운/features는
Scheduler의 책임으로 남긴다 (요청 시점마다 변하므로).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..settings import ForgeConfig, PolicyWhen
from .registry import ModelEntry, Registry
from .types import AnalysisResult

logger = logging.getLogger(__name__)

TIER_NAMES = ("tier1", "tier2", "tier3")

# 속성 셀렉터 값 파서: ">=128000", "<4000", "==8" 등
_OP_RE = re.compile(r"^\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)?)\s*$")
_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


@dataclass
class Constraints:
    """하드 제약의 누적 결과 — 그룹 해석 시 엔트리를 필터한다 (§5.4, §5.12)"""

    allow_paid: Optional[bool] = None
    max_cost_per_request: Optional[float] = None
    exclude_providers: set[str] = field(default_factory=set)

    def merge(self, allow_paid, max_cost, exclude) -> None:
        if allow_paid is not None:
            self.allow_paid = allow_paid
        if max_cost is not None:
            self.max_cost_per_request = max_cost
        self.exclude_providers |= set(exclude or [])

    def passes(self, entry: ModelEntry, est_prompt_tokens: int,
               max_tokens: Optional[int]) -> bool:
        if entry.provider in self.exclude_providers:
            return False
        if self.allow_paid is False:
            # 가격이 (0,0)으로 "확인된" 모델만 — unknown은 보수적으로 제외 (§5.12)
            if entry.price_per_mtok != (0.0, 0.0):
                return False
        if self.max_cost_per_request is not None:
            if entry.price_per_mtok is None:
                return False  # 가격 미상 — 상한 판정 불가, 보수적 제외
            pin, pout = entry.price_per_mtok
            est_out = max_tokens if max_tokens else 4096  # 미지정 시 보수적 추정
            est_cost = (est_prompt_tokens * pin + est_out * pout) / 1_000_000
            if est_cost > self.max_cost_per_request:
                return False
        return True


@dataclass
class RoutePlan:
    """PolicyEngine.plan()의 출력 — Scheduler의 입력"""

    groups: list[list[ModelEntry]]      # 순서 있는 후보 그룹 (제약 필터 적용 완료)
    constraints: Constraints
    policy_name: str                    # 매칭된 정책 이름 또는 "default"
    rejected_by_constraints: int = 0    # 제약으로 탈락한 모델 수 (explain/에러 메시지용)


class PolicyEngine:
    def __init__(self, config: ForgeConfig, registry: Registry):
        self._config = config
        self._registry = registry

    def plan(
        self,
        analysis: AnalysisResult,
        requested_model: str = "auto",
        user_agent: str = "",
        max_tokens: Optional[int] = None,
    ) -> RoutePlan:
        constraints = self._gather_constraints(analysis, requested_model, user_agent)

        matched_name = "default"
        route_items: list = []
        for rule in self._config.policies:
            if rule.when is None or rule.route is None:
                continue  # constraints 전용 정책은 위에서 이미 수집
            if self._matches(rule.when, analysis, requested_model, user_agent):
                matched_name = rule.name
                route_items = list(rule.route.prefer) + list(rule.route.fallback)
                break  # first-match

        if route_items:
            raw_groups = [self._resolve_item(item) for item in route_items]
        else:
            raw_groups = [self._registry.by_tier(t) for t in TIER_NAMES]

        groups: list[list[ModelEntry]] = []
        rejected = 0
        for raw in raw_groups:
            kept = [e for e in raw
                    if constraints.passes(e, analysis.est_prompt_tokens, max_tokens)]
            rejected += len(raw) - len(kept)
            if kept:
                groups.append(kept)

        return RoutePlan(
            groups=groups,
            constraints=constraints,
            policy_name=matched_name,
            rejected_by_constraints=rejected,
        )

    def entry_passes_constraints(
        self,
        entry: ModelEntry,
        analysis: AnalysisResult,
        requested_model: str = "auto",
        user_agent: str = "",
        max_tokens: Optional[int] = None,
    ) -> bool:
        """클라이언트가 모델을 직접 지정해도 constraints는 적용된다 (§5.4)"""
        constraints = self._gather_constraints(analysis, requested_model, user_agent)
        return constraints.passes(entry, analysis.est_prompt_tokens, max_tokens)

    # --- 내부 ---

    def _gather_constraints(self, analysis: AnalysisResult, requested_model: str,
                            user_agent: str) -> Constraints:
        acc = Constraints()
        for rule in self._config.policies:
            if rule.constraints is None:
                continue
            # when 없는 정책은 무조건, when 있는 정책은 매칭 시에만 constraints 반영
            if rule.when is None or self._matches(rule.when, analysis,
                                                  requested_model, user_agent):
                acc.merge(
                    rule.constraints.allow_paid,
                    rule.constraints.max_cost_per_request,
                    rule.constraints.exclude_providers,
                )
        return acc

    @staticmethod
    def _matches(when: PolicyWhen, analysis: AnalysisResult,
                 requested_model: str, user_agent: str) -> bool:
        if when.task and analysis.task not in when.task:
            return False
        if when.model is not None and when.model != requested_model:
            return False
        if when.client is not None and when.client.lower() not in user_agent.lower():
            return False
        if when.min_prompt_tokens is not None \
                and analysis.est_prompt_tokens < when.min_prompt_tokens:
            return False
        if when.max_prompt_tokens is not None \
                and analysis.est_prompt_tokens > when.max_prompt_tokens:
            return False
        return True

    def _resolve_item(self, item) -> list[ModelEntry]:
        """route 항목 하나 → 모델 그룹. tier명 / 모델 id / 속성 셀렉터 dict (§5.4)"""
        if isinstance(item, str):
            if item in TIER_NAMES:
                return self._registry.by_tier(item)
            entry = self._registry.get(item)
            if entry is not None:
                return [entry]
            # 접두어 없는 id 허용 (resolve_client_model과 동일 규칙)
            entry = self._registry.resolve_client_model(item)
            if entry is not None:
                return [entry]
            logger.warning("policy route item %r matches no tier/model — ignored", item)
            return []

        # 속성 셀렉터: {"context_window": ">=128000"} — 숫자 속성 비교
        matched = self._registry.all()
        for attr, cond in item.items():
            m = _OP_RE.match(str(cond))
            if not m:
                logger.warning("invalid selector %r: %r — ignored", attr, cond)
                return []
            op, threshold = _OPS[m.group(1)], float(m.group(2))
            matched = [
                e for e in matched
                if getattr(e, attr, None) is not None
                and isinstance(getattr(e, attr), (int, float))
                and op(float(getattr(e, attr)), threshold)
            ]
        return matched
