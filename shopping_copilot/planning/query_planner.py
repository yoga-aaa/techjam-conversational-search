from __future__ import annotations

import re

from shopping_copilot.core.config import SearchConfig
from shopping_copilot.core.contracts import SearchPlan, SessionState


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
    "those", "options", "quite", "right", "yet", "ask", "about", "one", "specific",
    "attribute", "actually", "ignore", "earlier", "preference", "matters", "need",
}


class RuleQueryPlanner:
    """Converts conversation state into a route-specific, implementation-neutral plan."""

    def __init__(self, config: SearchConfig) -> None:
        self.config = config

    def build(self, state: SessionState) -> SearchPlan:
        terms: list[str] = []
        for text in state.active_context:
            terms.extend(
                token.lower()
                for token in TOKEN_RE.findall(text)
                if len(token) > 1 and token.lower() not in STOPWORDS
            )
        for values in state.active_slots.values():
            for value in values:
                terms.extend(token.lower() for token in TOKEN_RE.findall(value))

        excluded = {term.lower() for term in state.excluded_terms}
        terms = [term for term in dict.fromkeys(terms) if term not in excluded][:40]
        route = state.intent_mode if state.intent_mode in {"buying", "browsing"} else "browsing"

        hard_filters: dict[str, object] = {}
        if state.active_slots.get("budget"):
            try:
                hard_filters["price_max"] = float(state.active_slots["budget"][-1])
            except ValueError:
                pass

        if route == "buying":
            bm25_weight, dense_weight, constraint_weight, profile_weight = 0.55, 0.20, 0.20, 0.05
            diversity_enabled = False
        else:
            bm25_weight, dense_weight, constraint_weight, profile_weight = 0.30, 0.45, 0.10, 0.15
            diversity_enabled = True

        return SearchPlan(
            route=route,
            lexical_terms=tuple(terms),
            semantic_query=" ".join(state.active_context),
            hard_filters=hard_filters,
            excluded_terms=tuple(sorted(excluded)),
            bm25_weight=bm25_weight,
            dense_weight=dense_weight,
            constraint_weight=constraint_weight,
            profile_weight=profile_weight,
            candidate_k=self.config.candidate_k,
            use_dense=self.config.dense_enabled,
            diversity_enabled=diversity_enabled,
        )
