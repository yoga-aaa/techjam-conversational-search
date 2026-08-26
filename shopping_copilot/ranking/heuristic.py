from __future__ import annotations

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import RankedCandidate, RetrievalResult, SearchPlan, SessionState


class HeuristicRanker:
    """Deterministic offline reranker over the candidate set."""

    def __init__(self, store: CatalogStore) -> None:
        self.store = store

    def rank(
        self,
        state: SessionState,
        plan: SearchPlan,
        result: RetrievalResult,
    ) -> list[RankedCandidate]:
        if not result.candidates:
            return []

        max_lexical = max((item.lexical_score for item in result.candidates), default=1.0) or 1.0
        max_dense = max((item.dense_score for item in result.candidates), default=1.0) or 1.0
        constraint_values = [
            value.lower()
            for name, values in state.active_slots.items()
            if name != "budget"
            for value in values
        ]

        ranked: list[RankedCandidate] = []
        for candidate in result.candidates:
            product = self.store.get(candidate.parent_asin)
            if product is None:
                continue
            text = product.searchable_text.lower()
            text_constraint_score = (
                sum(value in text for value in constraint_values) / len(constraint_values)
                if constraint_values
                else 0.0
            )
            constraint_score = max(text_constraint_score, candidate.constraint_score)
            profile_score = (
                sum(term in text for term in state.profile_terms) / len(state.profile_terms)
                if state.profile_terms
                else 0.0
            )
            lexical_score = candidate.lexical_score / max_lexical
            dense_score = candidate.dense_score / max_dense if candidate.dense_score else 0.0
            final_score = (
                plan.bm25_weight * lexical_score
                + plan.dense_weight * dense_score
                + plan.constraint_weight * constraint_score
                + plan.profile_weight * profile_score
            )
            ranked.append(
                RankedCandidate(
                    parent_asin=candidate.parent_asin,
                    final_score=final_score,
                    component_scores={
                        "lexical": lexical_score,
                        "dense": dense_score,
                        "constraint": constraint_score,
                        "profile": profile_score,
                    },
                )
            )

        return sorted(ranked, key=lambda item: item.final_score, reverse=True)
