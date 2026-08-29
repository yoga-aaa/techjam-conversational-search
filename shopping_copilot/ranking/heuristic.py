from __future__ import annotations

import math

from shopping_copilot.catalog.constraints import (
    normalized_tokens,
    normalized_value,
    product_matches_value,
    violates_negative_slots,
)
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
        constraints = list(dict.fromkeys(
            (name, normalized_value(value))
            for name, values in state.active_slots.items()
            if name != "budget"
            for value in values
            if normalized_value(value)
        ))
        candidate_products = {
            candidate.parent_asin: self.store.get(candidate.parent_asin)
            for candidate in result.candidates
        }
        constraint_weights = {constraint: 1.0 for constraint in constraints}
        if self.config.specificity_weighting:
            for constraint in constraints:
                token_count = len(normalized_tokens(constraint[1]))
                constraint_weights[constraint] *= 1.0 + self.config.specificity_weight_step * min(
                    max(0, token_count - 1),
                    8,
                )
        if self.config.rarity_weighting and constraints:
            candidate_count = len(result.candidates)
            for constraint in constraints:
                slot, value = constraint
                document_frequency = sum(
                    product_matches_value(product, slot, value)
                    for product in candidate_products.values()
                    if product is not None
                )
                rarity = (
                    0.0
                    if document_frequency == 0
                    else 1.0 + math.log(
                        (candidate_count + 1.0) / (document_frequency + 1.0)
                    )
                )
                constraint_weights[constraint] *= rarity

        effective_constraint_weight = plan.constraint_weight
        if self.config.dynamic_constraint_weight and constraints:
            effective_constraint_weight = max(
                plan.constraint_weight,
                min(
                    self.config.maximum_constraint_weight,
                    plan.constraint_weight
                    + self.config.constraint_weight_step * len(constraints),
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
            total_constraint_weight = sum(constraint_weights.values())
            matched_constraints = {
                constraint
                for constraint in constraints
                if product_matches_value(product, constraint[0], constraint[1])
            }
            text_constraint_score = (
                sum(
                    constraint_weights[constraint]
                    for constraint in matched_constraints
                ) / total_constraint_weight
                if total_constraint_weight > 0.0
                else 0.0
            )
            constraint_score = max(text_constraint_score, candidate.constraint_score)
            profile_score = (
                sum(term in product.searchable_text.lower() for term in state.profile_terms) / len(state.profile_terms)
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
            all_constraints_match = bool(constraints) and len(matched_constraints) == len(constraints)
            if all_constraints_match:
                final_score += self.config.all_constraints_bonus
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
                        "matched_constraint_count": float(len(matched_constraints)),
                        "active_constraint_count": float(len(constraints)),
                        "all_constraints_match": float(all_constraints_match),
                    },
                )
            )

        return sorted(ranked, key=lambda item: item.final_score, reverse=True)
