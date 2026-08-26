from __future__ import annotations

from collections.abc import Sequence

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import ModelUsage, PolicyDecision, RankedCandidate


ALLOWED_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other",
}


class OfficialResponseBuilder:
    """Converts rich internal objects into the strict official response schema."""

    def __init__(self, store: CatalogStore) -> None:
        self.store = store

    def build(
        self,
        ranked: Sequence[RankedCandidate],
        decision: PolicyDecision,
        top_k: int,
        usage: ModelUsage | None = None,
    ) -> dict:
        requested_count = max(0, min(int(top_k), decision.recommendation_count, 10))
        seen: set[str] = set()
        recommendations: list[dict[str, str]] = []
        for item in ranked:
            if item.parent_asin in seen or not self.store.contains(item.parent_asin):
                continue
            seen.add(item.parent_asin)
            recommendations.append({"parent_asin": item.parent_asin})
            if len(recommendations) >= requested_count:
                break

        ask_attribute = decision.ask_attribute
        if ask_attribute not in ALLOWED_ATTRIBUTES:
            ask_attribute = None
        actual_usage = usage or ModelUsage()
        return {
            "message": str(decision.message),
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            "usage": {
                "prompt_tokens": max(0, int(actual_usage.prompt_tokens)),
                "completion_tokens": max(0, int(actual_usage.completion_tokens)),
            },
        }
