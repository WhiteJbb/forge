"""가격표 조회 — litellm 내장 가격표(3순위) 조회 (DESIGN.md §5.12)

가격 소스 우선순위 1,2순위(forge.yaml price_per_mtok, provider free)는
Registry._build_from_config()가 이미 처리한다. 이 모듈은 3순위,
litellm.model_cost 조회만 담당한다. 그래도 못 찾으면 None(unknown) —
allow_paid=false 정책에서 unknown은 보수적으로 제외되므로 0으로 지어내지 않는다.
"""

import logging

from ..settings import ForgeConfig
from .registry import Registry

logger = logging.getLogger(__name__)

_warned_no_litellm = False


def resolve_price(provider_model_id: str, litellm_prefix: str) -> "tuple[float, float] | None":
    """litellm.model_cost에서 (input, output) USD/1M tok 가격을 조회한다.

    조회 키 후보 순서: ① provider_model_id 그대로 ② f"{litellm_prefix}/{provider_model_id}"
    ③ provider_model_id의 "/" 뒤 마지막 세그먼트.
    못 찾거나 필드가 없거나/숫자가 아니면 None.
    """
    global _warned_no_litellm
    try:
        import litellm
    except ImportError:
        if not _warned_no_litellm:
            logger.warning("litellm import 실패 — 가격표 조회(3순위)를 건너뜁니다")
            _warned_no_litellm = True
        return None

    model_cost = litellm.model_cost

    candidates = [provider_model_id, f"{litellm_prefix}/{provider_model_id}"]
    if "/" in provider_model_id:
        candidates.append(provider_model_id.rsplit("/", 1)[-1])

    for key in candidates:
        if key not in model_cost:
            continue
        # 첫 매칭 키에서 결정한다 — 필드가 없거나 숫자가 아니어도 다음 후보로 넘어가지 않는다
        spec = model_cost[key]
        if not isinstance(spec, dict):
            return None
        input_cost = spec.get("input_cost_per_token")
        output_cost = spec.get("output_cost_per_token")
        if not isinstance(input_cost, (int, float)) or isinstance(input_cost, bool):
            return None
        if not isinstance(output_cost, (int, float)) or isinstance(output_cost, bool):
            return None
        return (input_cost * 1_000_000, output_cost * 1_000_000)

    return None


def fill_registry_prices(registry: Registry, config: ForgeConfig) -> int:
    """price_per_mtok이 None인 엔트리에 litellm 가격표 조회 결과를 채운다.

    반환값: 채운 엔트리 개수.
    """
    filled = 0
    for entry in registry.all():
        if entry.price_per_mtok is not None:
            continue
        pconf = config.provider(entry.provider)
        litellm_prefix = pconf.litellm_prefix if pconf else "openai"
        price = resolve_price(entry.provider_model_id, litellm_prefix)
        if price is not None:
            entry.price_per_mtok = price
            filled += 1
    return filled
