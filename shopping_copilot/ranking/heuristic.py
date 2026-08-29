from __future__ import annotations

import math

from shopping_copilot.catalog.constraints import normalized_tokens, product_matches_value, violates_negative_slots
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import RankingConfig
from shopping_copilot.core.contracts import RankedCandidate, RetrievalResult, SearchPlan, SessionState


class HeuristicRanker:
    """Deterministic offline reranker over the candidate set."""

    def __init__(self, store: CatalogStore, config: RankingConfig) -> None:
        self.store = store
        self.config = config
        self._match_cache: dict[tuple[str, str, str], bool] = {}

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
        constraint_pairs = list(dict.fromkeys(
            (name, value.lower())
            for name, values in state.active_slots.items()
            if name != "budget"
            for value in values
            if value.strip()
        ))
        constraint_values = [value for _, value in constraint_pairs]
        candidate_products = {
            candidate.parent_asin: self.store.get(candidate.parent_asin)
            for candidate in result.candidates
        }
        constraint_weights = {pair: 1.0 for pair in constraint_pairs}
        if self.config.specificity_weighting:
            for slot, value in constraint_pairs:
                token_count = len(normalized_tokens(value))
                # Longer, concrete phrases carry more intent than a generic
                # one-word slot such as "material" or "black".
                constraint_weights[(slot, value)] = min(
                    2.5,
                    1.0 + self.config.specificity_weight_step * max(0, token_count - 1),
                )
        precise_matches = self.config.specificity_weighting or self.config.all_constraints_bonus > 0.0
        if self.config.rarity_weighting and constraint_values:
            candidate_count = len(result.candidates)
            for slot, value in constraint_pairs:
                document_frequency = sum(
                    self._matches(product, slot, value)
                    if self.config.specificity_weighting
                    else value in product.searchable_text.lower()
                    for product in candidate_products.values()
                    if product is not None
                )
                constraint_weights[(slot, value)] *= (
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
            if precise_matches:
                matched_pairs = {
                    pair
                    for pair in constraint_pairs
                    if self._matches(product, pair[0], pair[1])
                }
            else:
                matched_pairs = {
                    pair
                    for pair in constraint_pairs
                    if pair[1] in text
                }
            text_constraint_score = (
                sum(
                    constraint_weights[pair]
                    for pair in constraint_pairs
                    if pair in matched_pairs
                ) / total_constraint_weight
                if total_constraint_weight > 0.0
                else 0.0
            )
            constraint_score = max(text_constraint_score, candidate.constraint_score)
            matched_constraint_count = len(matched_pairs)
            all_constraints_match = bool(constraint_pairs) and matched_constraint_count == len(constraint_pairs)
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
            if self.config.all_constraints_bonus > 0.0 and all_constraints_match:
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
                        "matched_constraint_count": float(matched_constraint_count),
                        "active_constraint_count": float(len(constraint_pairs)),
                        "all_constraints_match": float(all_constraints_match),
                        "category_anchor": float("category_anchor" in candidate.source_routes),
                    },
                )
            )

        return sorted(ranked, key=lambda item: (-item.final_score, item.parent_asin))

    def _matches(self, product: object, slot: str, value: str) -> bool:
        parent_asin = getattr(product, "parent_asin", "")
        key = (str(parent_asin), slot, value)
        if key not in self._match_cache:
            self._match_cache[key] = product_matches_value(product, slot, value)
        return self._match_cache[key]
