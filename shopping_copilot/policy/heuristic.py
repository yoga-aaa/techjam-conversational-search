from __future__ import annotations

from collections.abc import Sequence

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import PolicyConfig, SearchConfig
from shopping_copilot.core.contracts import (
    PolicyDecision,
    RankedCandidate,
    RetrievalDiagnostics,
    SearchPlan,
    SessionState,
)
from shopping_copilot.policy.information_gain import InformationGainQuestionScorer


QUESTION_TEMPLATES = {
    "material": "Do you have a preferred material?",
    "color": "Do you have a preferred color?",
    "size": "Is there a size or fit requirement?",
    "style": "What style do you prefer?",
    "brand": "Do you have a preferred brand or store?",
    "budget": "What budget range should I use?",
    "feature": "Which feature matters most to you?",
    "use_case": "What activity or occasion is this for?",
    "other": "What other requirement matters most to you?",
}


def semantic_signature(
    item: RankedCandidate,
    mode: str = "count",
) -> tuple[int, ...]:
    """Return the conservative tri-state evidence counts used for ambiguity."""

    if mode == "exact" and item.semantic_signature:
        return item.semantic_signature
    scores = item.component_scores
    return (
        int(round(scores.get("semantic_conflict_count", 0.0))),
        int(round(scores.get("semantic_match_count", 0.0))),
        int(round(scores.get("semantic_unknown_count", 0.0))),
    )


def adaptive_semantic_slate_size(
    ranked: Sequence[RankedCandidate],
    mode: str = "count",
    anchor: str = "rank_one",
) -> int:
    """Show every candidate tied with the selected semantic anchor, API-capped."""

    if not ranked:
        return 0
    top_signature = semantic_anchor_signature(ranked, mode, anchor)
    group_size = sum(
        semantic_signature(item, mode) == top_signature
        for item in ranked
    )
    return max(1, min(10, group_size))


def semantic_anchor_signature(
    ranked: Sequence[RankedCandidate],
    mode: str = "count",
    anchor: str = "rank_one",
) -> tuple[int, ...]:
    """Choose the slate group from rank one or the strongest semantic tier."""

    if not ranked:
        return ()
    if anchor == "strongest_semantic_tier":
        anchor_item = max(
            ranked,
            key=lambda item: (
                item.component_scores.get("semantic_match_count", 0.0),
                item.component_scores.get("semantic_global_idf_coverage", 0.0),
                item.final_score,
            ),
        )
    else:
        anchor_item = ranked[0]
    return semantic_signature(anchor_item, mode)


class HeuristicPolicy:
    """Deterministic clarification and recommendation policy."""

    def __init__(
        self,
        search_config: SearchConfig,
        policy_config: PolicyConfig,
        store: CatalogStore,
    ) -> None:
        self.search_config = search_config
        self.policy_config = policy_config
        self.question_scorer = InformationGainQuestionScorer(
            store=store,
            candidate_limit=policy_config.question_candidate_limit,
            minimum_coverage=policy_config.question_min_coverage,
            minimum_score=policy_config.question_min_score,
            attribute_priors=policy_config.question_attribute_priors,
        )

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

        attribute = self._select_fixed_attribute(state, plan.route)
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
        early_decision: PolicyDecision | None = None,
    ) -> PolicyDecision:
        if state.turn >= 10:
            state.question_scores = {}
            return PolicyDecision(False, None, "Here are my best matches.", 10)

        if early_decision is not None:
            attribute = self._select_attribute(state, plan.route, ranked)
            if attribute is None:
                return PolicyDecision(False, None, "Here are my best matches.", 10)
            return PolicyDecision(
                should_ask=True,
                ask_attribute=attribute,
                message=QUESTION_TEMPLATES[attribute],
                recommendation_count=self._clarification_recommendation_count(
                    ranked,
                    early_decision.recommendation_count,
                ),
            )

        if len(ranked) >= 2:
            score_gap = ranked[0].final_score - ranked[1].final_score
        elif ranked:
            score_gap = ranked[0].final_score
        else:
            score_gap = 0.0

        enough_context = len(state.active_slots) >= 2
        if ranked and enough_context and score_gap >= self.policy_config.confident_score_gap:
            state.question_scores = {}
            return PolicyDecision(False, None, "Here are my best matches.", 10)

        attribute = self._select_attribute(state, plan.route, ranked)
        if attribute is None:
            return PolicyDecision(False, None, "Here are my best matches.", 10)
        return PolicyDecision(
            should_ask=True,
            ask_attribute=attribute,
            message=QUESTION_TEMPLATES[attribute],
            recommendation_count=self._clarification_recommendation_count(
                ranked,
                self.policy_config.uncertain_recommendation_count,
            ),
        )

    def _clarification_recommendation_count(
        self,
        ranked: Sequence[RankedCandidate],
        fallback: int,
    ) -> int:
        if not self.policy_config.adaptive_semantic_slate:
            return fallback
        return adaptive_semantic_slate_size(
            ranked,
            self.policy_config.adaptive_semantic_slate_mode,
            self.policy_config.adaptive_semantic_slate_anchor,
        )

    def _select_attribute(
        self,
        state: SessionState,
        route: str,
        ranked: Sequence[RankedCandidate],
    ) -> str | None:
        explicit_exploration = any(
            marker in message.lower()
            for message in state.messages
            for marker in ("still exploring", "just browsing", "not sure", "open to", "show me some")
        )
        unavailable = (
            set(state.active_slots)
            | set(state.asked_attributes)
            | set(state.no_preference_attributes)
        )
        if (
            self.policy_config.question_strategy == "information_gain"
            and route == "browsing"
            and explicit_exploration
            and len(state.no_preference_attributes) >= 2
            and "other" not in unavailable
        ):
            state.question_scores = {"other": 0.0}
            return "other"
        if (
            self.policy_config.question_strategy == "information_gain"
            and route == "browsing"
            and explicit_exploration
        ):
            selected = self.question_scorer.choose(state, ranked)
            if selected is not None:
                return selected
        else:
            state.question_scores = {}
        return self._select_fixed_attribute(state, route)

    @staticmethod
    def _select_fixed_attribute(state: SessionState, route: str) -> str | None:
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
