from __future__ import annotations

from collections.abc import Sequence

from shopping_copilot.catalog.constraints import normalized_value
from shopping_copilot.core.config import PolicyConfig
from shopping_copilot.core.contracts import RankedCandidate, SessionState


class CandidateCoverageManager:
    """Detects retrieval stagnation and schedules unseen recommendation slates."""

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def observe(
        self,
        state: SessionState,
        ranked: Sequence[RankedCandidate],
    ) -> None:
        if not self.config.coverage_enabled:
            state.coverage_mode = False
            return

        current_ids = tuple(
            item.parent_asin
            for item in ranked[: self.config.coverage_candidate_limit]
        )
        current_set = set(current_ids)
        previous_set = set(state.previous_candidate_ids)
        union = current_set | previous_set
        overlap = len(current_set & previous_set) / len(union) if union else 0.0
        signature_entries: list[tuple[str, tuple[str, ...]]] = []
        for name, values in state.active_slots.items():
            normalized = tuple(sorted({normalized_value(value) for value in values if normalized_value(value)}))
            if normalized:
                signature_entries.append((f"+{name}", normalized))
        for name, values in state.negative_slots.items():
            normalized = tuple(sorted({normalized_value(value) for value in values if normalized_value(value)}))
            if normalized:
                signature_entries.append((f"-{name}", normalized))
        for name in state.no_preference_attributes:
            signature_entries.append((f"~{name}", ()))
        slot_signature = tuple(sorted(signature_entries))
        effective_signature = tuple(
            entry for entry in slot_signature if not entry[0].startswith("~")
        )
        previous_effective_signature = tuple(
            entry for entry in state.previous_slot_signature if not entry[0].startswith("~")
        )
        no_new_constraints = (
            bool(state.previous_slot_signature)
            and effective_signature == previous_effective_signature
        )
        stable_candidates = (
            bool(previous_set)
            and overlap >= self.config.coverage_overlap_threshold
        )

        # Stagnation means both the requirements and the leading candidate set
        # stayed stable. A repeated question alone is not enough to activate
        # coverage rotation.
        if no_new_constraints and stable_candidates:
            state.stagnant_candidate_turns += 1
        else:
            state.stagnant_candidate_turns = 0

        state.candidate_overlap = overlap
        state.coverage_mode = (
            state.turn >= self.config.coverage_min_turn
            and state.stagnant_candidate_turns >= self.config.coverage_stagnant_turns
        )
        state.previous_candidate_ids = current_ids
        state.previous_slot_signature = slot_signature

    def order_for_response(
        self,
        state: SessionState,
        ranked: Sequence[RankedCandidate],
    ) -> list[RankedCandidate]:
        if not state.coverage_mode:
            return list(ranked)
        unseen = [item for item in ranked if item.parent_asin not in state.shown_asins]
        previously_shown = [item for item in ranked if item.parent_asin in state.shown_asins]
        quota = self.config.coverage_override_zero_consensus_quota
        if (
            quota > 0
            and state.explicit_override_count > 0
            and state.stagnant_candidate_turns >= self.config.coverage_override_balance_min_stagnant
        ):
            # After an override, reserve a small tail for candidates found by
            # a different route. This is a bounded rescue mechanism, not a
            # replacement for the evidence ranking order.
            width = min(self.config.coverage_slate_width, len(unseen))
            quota = min(quota, width)
            head = unseen[: width - quota]
            head_ids = {item.parent_asin for item in head}
            zero_consensus = [
                item
                for item in unseen
                if item.parent_asin not in head_ids
                and float(item.component_scores.get("route_consensus", 0.0)) <= 0.0
            ][:quota]
            selected_ids = head_ids | {item.parent_asin for item in zero_consensus}
            remainder = [item for item in unseen if item.parent_asin not in selected_ids]
            return [*head, *zero_consensus, *remainder, *previously_shown]
        if self.config.coverage_stratified_enabled:
            width = min(self.config.coverage_slate_width, len(unseen))
            head_count = min(self.config.coverage_head_count, width)
            head = unseen[:head_count]
            tail = unseen[head_count:]
            sample_count = width - head_count
            if sample_count > 0 and len(tail) > sample_count:
                indices = [
                    min(len(tail) - 1, ((index + 1) * len(tail) // (sample_count + 1)))
                    for index in range(sample_count)
                ]
                selected = [tail[index] for index in dict.fromkeys(indices)]
                selected_ids = {item.parent_asin for item in selected}
                remainder = [item for item in tail if item.parent_asin not in selected_ids]
                return [*head, *selected, *remainder, *previously_shown]
        return unseen + previously_shown

    @staticmethod
    def record_response(state: SessionState, recommendations: object) -> None:
        if not isinstance(recommendations, list):
            return
        state.shown_asins.update(
            str(item.get("parent_asin"))
            for item in recommendations
            if isinstance(item, dict) and item.get("parent_asin")
        )
