from __future__ import annotations

from collections.abc import Sequence

from shopping_copilot.core.config import PolicyConfig
from shopping_copilot.core.contracts import RankedCandidate


class ProtectedRescueSelector:
    """Select a bounded response slate while keeping the primary head fixed."""

    def __init__(self, policy_config: PolicyConfig) -> None:
        self.config = policy_config

    @staticmethod
    def _first_unique_by_parent_asin(
        ranked: Sequence[RankedCandidate],
    ) -> list[RankedCandidate]:
        seen: set[str] = set()
        unique: list[RankedCandidate] = []
        for item in ranked:
            if item.parent_asin in seen:
                continue
            seen.add(item.parent_asin)
            unique.append(item)
        return unique

    def select(
        self,
        base_ranked: Sequence[RankedCandidate],
        expanded_ranked: Sequence[RankedCandidate],
        rescue_ids: set[str],
        recommendation_count: int,
    ) -> list[RankedCandidate]:
        limit = max(0, min(10, int(recommendation_count)))
        if limit == 0:
            return list(base_ranked)

        base_unique = self._first_unique_by_parent_asin(base_ranked)
        expanded_unique = self._first_unique_by_parent_asin(expanded_ranked)
        head_size = min(
            self.config.protected_rescue_head_size,
            limit,
            len(base_unique),
        )
        head = base_unique[:head_size]
        head_ids = {item.parent_asin for item in head}

        eligible_rescue_ids: list[str] = []
        rank_limit = max(0, min(60, int(self.config.protected_rescue_rank_limit)))
        quota = max(0, int(self.config.protected_rescue_quota))
        for item in expanded_unique[:rank_limit]:
            if item.parent_asin in rescue_ids and item.parent_asin not in head_ids:
                eligible_rescue_ids.append(item.parent_asin)
            if len(eligible_rescue_ids) >= min(quota, limit - head_size):
                break

        if not eligible_rescue_ids:
            return list(base_ranked)

        allowed_ids = {
            item.parent_asin for item in base_unique
        } | set(eligible_rescue_ids)
        selected = list(head)
        selected_ids = set(head_ids)

        for item in expanded_unique:
            if item.parent_asin not in allowed_ids:
                continue
            if item.parent_asin in rescue_ids and item.parent_asin not in eligible_rescue_ids:
                continue
            if item.parent_asin in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item.parent_asin)
            if len(selected) >= limit:
                break

        for item in base_unique:
            if len(selected) >= limit:
                break
            if item.parent_asin not in selected_ids:
                selected.append(item)
                selected_ids.add(item.parent_asin)

        remaining_base = [
            item
            for item in base_unique
            if item.parent_asin not in selected_ids
        ]
        return selected + remaining_base
