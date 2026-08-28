from __future__ import annotations

from shopping_copilot.catalog.constraints import normalized_tokens, normalized_value
from shopping_copilot.core.config import SearchConfig
from shopping_copilot.core.contracts import SearchPlan, SessionState


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
    "those", "options", "quite", "right", "yet", "ask", "about", "one", "specific",
    "attribute", "actually", "ignore", "earlier", "preference", "matters", "need",
    "prefer", "make", "switch", "what", "key", "requirement", "instead", "rather",
    "than", "after", "all", "avoid", "exclude", "without", "anything", "not", "no",
    "don", "t", "dont", "doesn", "doesnt", "do", "does", "longer", "drop", "remove", "forget", "fine", "okay",
    "include", "can", "sure", "necessarily", "too", "only", "also", "have", "use",
    "your", "judgment", "category", "color", "material", "size",
    "style", "brand", "budget", "feature", "case",
}


class RuleQueryPlanner:
    """Converts conversation state into a route-specific, implementation-neutral plan."""

    def __init__(self, config: SearchConfig) -> None:
        self.config = config

    def build(self, state: SessionState) -> SearchPlan:
        terms: list[str] = []
        for values in state.active_slots.values():
            for value in values:
                terms.extend(
                    token
                    for token in normalized_tokens(value)
                    if token not in STOPWORDS
                )
        for text in state.active_context:
            terms.extend(
                token.lower()
                for token in normalized_tokens(text)
                if len(token) > 1 and token not in STOPWORDS
            )

        excluded_values = {
            value
            for values in state.negative_slots.values()
            for value in values
            if normalized_value(value)
        }
        excluded_tokens = {
            token
            for value in excluded_values
            for token in normalized_tokens(value)
        }
        terms = [
            term
            for term in dict.fromkeys(terms)
            if term not in excluded_tokens and term not in STOPWORDS
        ][:40]
        route = state.intent_mode if state.intent_mode in {"buying", "browsing"} else "browsing"

        hard_filters: dict[str, object] = {}
        if state.active_slots.get("budget"):
            try:
                hard_filters["price_max"] = float(state.active_slots["budget"][-1])
            except ValueError:
                pass

        structured_constraints = {
            name: tuple(values)
            for name, values in state.active_slots.items()
            if name != "budget" and values
        }

        if route == "buying":
            bm25_weight, dense_weight, constraint_weight, profile_weight = 0.55, 0.20, 0.20, 0.05
            diversity_enabled = False
        else:
            bm25_weight, dense_weight, constraint_weight, profile_weight = 0.30, 0.45, 0.10, 0.15
            diversity_enabled = True

        return SearchPlan(
            route=route,
            lexical_terms=tuple(terms),
            semantic_query=" ".join(terms),
            structured_constraints=structured_constraints,
            hard_filters=hard_filters,
            excluded_terms=tuple(sorted(excluded_values)),
            bm25_weight=bm25_weight,
            dense_weight=dense_weight,
            constraint_weight=constraint_weight,
            profile_weight=profile_weight,
            candidate_k=self.config.candidate_k,
            use_dense=self.config.dense_enabled,
            use_structured=self.config.structured_enabled,
            diversity_enabled=diversity_enabled,
        )
