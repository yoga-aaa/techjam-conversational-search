from __future__ import annotations

import math

from shopping_copilot.catalog.constraints import violates_negative_slots
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import RankingConfig
from shopping_copilot.core.contracts import RankedCandidate, RetrievalResult, SearchPlan, SessionState


class HeuristicRanker:
    """Deterministic offline reranker over the candidate set."""

    def __init__(self, store: CatalogStore, config: RankingConfig) -> None:
        self.store = store
        self.config = config

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
        constraint_values = list(dict.fromkeys(
            value.lower()
            for name, values in state.active_slots.items()
            if name != "budget"
            for value in values
        ))
        candidate_products = {
            candidate.parent_asin: self.store.get(candidate.parent_asin)
            for candidate in result.candidates
        }
        constraint_weights = {value: 1.0 for value in constraint_values}
        if self.config.rarity_weighting and constraint_values:
            candidate_count = len(result.candidates)
            for value in constraint_values:
                document_frequency = sum(
                    value in product.searchable_text.lower()
                    for product in candidate_products.values()
                    if product is not None
                )
                constraint_weights[value] = (
                    0.0
                    if document_frequency == 0
                    else 1.0 + math.log(
                        (candidate_count + 1.0) / (document_frequency + 1.0)
                    )
                )

        effective_constraint_weight = plan.constraint_weight
        if self.config.dynamic_constraint_weight and constraint_values:
            effective_constraint_weight = max(
                plan.constraint_weight,
                min(
                    self.config.maximum_constraint_weight,
                    plan.constraint_weight
                    + self.config.constraint_weight_step * len(constraint_values),
                ),
            )
        non_constraint_total = plan.bm25_weight + plan.dense_weight + plan.profile_weight
        non_constraint_scale = (
            (1.0 - effective_constraint_weight) / non_constraint_total
            if non_constraint_total > 0.0
            else 0.0
        )

        ranked: list[RankedCandidate] = []
        for candidate in result.candidates:
            product = candidate_products[candidate.parent_asin]
            if product is None:
                continue
            if violates_negative_slots(product, state.negative_slots):
                continue
            text = product.searchable_text.lower()
            total_constraint_weight = sum(constraint_weights.values())
            text_constraint_score = (
                sum(
                    constraint_weights[value]
                    for value in constraint_values
                    if value in text
                ) / total_constraint_weight
                if total_constraint_weight > 0.0
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
                plan.bm25_weight * non_constraint_scale * lexical_score
                + plan.dense_weight * non_constraint_scale * dense_score
                + effective_constraint_weight * constraint_score
                + plan.profile_weight * non_constraint_scale * profile_score
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
                        "constraint_weight": effective_constraint_weight,
                    },
                )
            )

        return sorted(ranked, key=lambda item: item.final_score, reverse=True)
