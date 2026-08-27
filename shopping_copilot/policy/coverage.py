from __future__ import annotations

from collections.abc import Sequence

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
        slot_signature = tuple(
            sorted((name, tuple(values)) for name, values in state.active_slots.items())
        )
        no_new_constraints = (
            bool(state.previous_slot_signature)
            and slot_signature == state.previous_slot_signature
        )
        stable_candidates = (
            bool(previous_set)
            and overlap >= self.config.coverage_overlap_threshold
        )

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

    @staticmethod
    def order_for_response(
        state: SessionState,
        ranked: Sequence[RankedCandidate],
    ) -> list[RankedCandidate]:
        if not state.coverage_mode:
            return list(ranked)
        unseen = [item for item in ranked if item.parent_asin not in state.shown_asins]
        previously_shown = [item for item in ranked if item.parent_asin in state.shown_asins]
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
