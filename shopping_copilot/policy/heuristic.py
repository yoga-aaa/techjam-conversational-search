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
        if state.last_turn_explicit_override:
            return None
        if state.turn >= 10:
            return None

        over_general = (
            not plan.lexical_terms
            or diagnostics.candidate_count > self.search_config.probe_overload_threshold
            or (plan.route == "browsing" and len(state.active_slots) <= 1)
        )
        if not over_general:
            return None

        attribute = self._select_other_first(state) or self._select_fixed_attribute(state, plan.route)
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
            decision = PolicyDecision(
                should_ask=True,
                ask_attribute=attribute,
                message=QUESTION_TEMPLATES[attribute],
                recommendation_count=early_decision.recommendation_count,
            )
            return self._apply_slate_gate(state, ranked, decision)

        other_first = self._select_other_first(state)
        if other_first is not None:
            state.question_scores = {"other": 1.0}
            decision = PolicyDecision(
                should_ask=True,
                ask_attribute=other_first,
                message=QUESTION_TEMPLATES[other_first],
                recommendation_count=self.policy_config.uncertain_recommendation_count,
            )
            return self._apply_slate_gate(state, ranked, decision)

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
        decision = PolicyDecision(
            should_ask=True,
            ask_attribute=attribute,
            message=QUESTION_TEMPLATES[attribute],
            recommendation_count=self.policy_config.uncertain_recommendation_count,
        )
        return self._apply_slate_gate(state, ranked, decision)

    def _apply_slate_gate(
        self,
        state: SessionState,
        ranked: Sequence[RankedCandidate],
        decision: PolicyDecision,
    ) -> PolicyDecision:
        """Keep the visible slate compact until enough evidence supports expansion."""

        if not self.policy_config.slate_gate_enabled or not decision.should_ask:
            return decision
        leader_matches = (
            int(ranked[0].component_scores.get("matched_product_constraint_count", 0.0))
            if ranked
            else 0
        )
        requirements_drained = "other" in state.no_preference_attributes
        enough_evidence = (
            state.turn >= self.policy_config.slate_expand_min_turn
            and leader_matches >= self.policy_config.slate_expand_min_matches
        )
        forced_expansion = state.turn >= self.policy_config.slate_expand_turn
        recommendation_count = (
            decision.recommendation_count
            if requirements_drained or enough_evidence or forced_expansion
            else min(decision.recommendation_count, self.policy_config.slate_compact_count)
        )
        return PolicyDecision(
            should_ask=decision.should_ask,
            ask_attribute=decision.ask_attribute,
            message=decision.message,
            recommendation_count=recommendation_count,
        )

    def _select_attribute(
        self,
        state: SessionState,
        route: str,
        ranked: Sequence[RankedCandidate],
    ) -> str | None:
        other_first = self._select_other_first(state)
        if other_first is not None:
            state.question_scores = {"other": 1.0}
            return other_first
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

    def _select_other_first(self, state: SessionState) -> str | None:
        if not self.policy_config.other_first_enabled:
            return None
        if "other" in state.no_preference_attributes:
            return None
        asked = state.asked_attribute_counts.get("other", 0)
        if asked >= self.policy_config.other_first_max_questions:
            return None
        return "other"

    @staticmethod
    def _select_fixed_attribute(state: SessionState, route: str) -> str | None:
        unavailable = (
            set(state.active_slots)
            | set(state.asked_attributes)
            | set(state.no_preference_attributes)
        )
        # Repeated "no preference" answers are evidence that continuing the
        # fixed attribute checklist is unlikely to add useful constraints.
        # Ask one broad fallback question instead so the user can disclose a
        # requirement whose wording does not fit our small slot taxonomy.
        if len(state.no_preference_attributes) >= 2 and "other" not in unavailable:
            return "other"

        if route == "browsing":
            priority = ("use_case", "feature", "material", "budget", "style", "color", "size", "other")
        else:
            priority = ("feature", "material", "color", "budget", "size", "style", "use_case", "other")

        return next((attribute for attribute in priority if attribute not in unavailable), None)
