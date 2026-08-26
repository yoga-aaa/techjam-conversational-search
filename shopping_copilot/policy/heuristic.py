from __future__ import annotations

from collections.abc import Sequence

from shopping_copilot.core.config import PolicyConfig, SearchConfig
from shopping_copilot.core.contracts import (
    PolicyDecision,
    RankedCandidate,
    RetrievalDiagnostics,
    SearchPlan,
    SessionState,
)


QUESTION_TEMPLATES = {
    "material": "Do you have a preferred material?",
    "color": "Do you have a preferred color?",
    "size": "Is there a size or fit requirement?",
    "style": "What style do you prefer?",
    "budget": "What budget range should I use?",
    "feature": "Which feature matters most to you?",
    "use_case": "What activity or occasion is this for?",
    "other": "What other requirement matters most to you?",
}


class HeuristicPolicy:
    """Deterministic clarification and recommendation policy."""

    def __init__(self, search_config: SearchConfig, policy_config: PolicyConfig) -> None:
        self.search_config = search_config
        self.policy_config = policy_config

    def before_search(
        self,
        state: SessionState,
        plan: SearchPlan,
        diagnostics: RetrievalDiagnostics,
    ) -> PolicyDecision | None:
        if state.turn >= 10:
            return None

        over_general = (
            not plan.lexical_terms
            or diagnostics.candidate_count > self.search_config.probe_overload_threshold
            or (plan.route == "browsing" and len(state.active_slots) <= 1)
        )
        if not over_general:
            return None

        attribute = self._select_attribute(state, plan.route)
        if attribute is None:
            return None
        return PolicyDecision(
            should_ask=True,
            ask_attribute=attribute,
            message=QUESTION_TEMPLATES[attribute],
            recommendation_count=self.policy_config.broad_recommendation_count,
        )

    def after_ranking(
        self,
        state: SessionState,
        plan: SearchPlan,
        ranked: Sequence[RankedCandidate],
        diagnostics: RetrievalDiagnostics,
    ) -> PolicyDecision:
        if state.turn >= 10:
            return PolicyDecision(False, None, "Here are my best matches.", 10)

        if len(ranked) >= 2:
            score_gap = ranked[0].final_score - ranked[1].final_score
        elif ranked:
            score_gap = ranked[0].final_score
        else:
            score_gap = 0.0

        enough_context = len(state.active_slots) >= 2
        if ranked and enough_context and score_gap >= self.policy_config.confident_score_gap:
            return PolicyDecision(False, None, "Here are my best matches.", 10)

        attribute = self._select_attribute(state, plan.route)
        if attribute is None:
            return PolicyDecision(False, None, "Here are my best matches.", 10)
        return PolicyDecision(
            should_ask=True,
            ask_attribute=attribute,
            message=QUESTION_TEMPLATES[attribute],
            recommendation_count=self.policy_config.uncertain_recommendation_count,
        )

    @staticmethod
    def _select_attribute(state: SessionState, route: str) -> str | None:
        if route == "browsing":
            priority = ("use_case", "feature", "material", "budget", "style", "color", "size", "other")
        else:
            priority = ("feature", "material", "budget", "size", "color", "style", "use_case", "other")

        unavailable = (
            set(state.active_slots)
            | set(state.asked_attributes)
            | set(state.no_preference_attributes)
        )
        return next((attribute for attribute in priority if attribute not in unavailable), None)
