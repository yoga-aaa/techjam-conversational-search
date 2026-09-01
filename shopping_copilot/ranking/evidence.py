from __future__ import annotations

import math

from shopping_copilot.catalog.constraints import normalized_tokens, normalized_value, violates_negative_slots
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import RankingConfig
from shopping_copilot.core.contracts import RankedCandidate, RetrievalResult, SearchPlan, SessionState


class EvidenceRanker:
    """Rank candidates by complete, accumulated user evidence.

    Retrieval scores remain a small tie-breaker. Product-bearing requirements
    decide the order once the conversation has disclosed them.
    """

    def __init__(self, store: CatalogStore, config: RankingConfig) -> None:
        self.store = store
        self.config = config
        self._normalized_text = {
            product.parent_asin: normalized_value(product.searchable_text)
            for product in store.products
        }
        self._normalized_fields = (
            {
                product.parent_asin: tuple(
                    normalized
                    for raw in (
                        product.title,
                        *product.categories,
                        *product.features,
                        *product.details,
                        product.store,
                        *product.description,
                    )
                    if (normalized := normalized_value(raw))
                )
                for product in store.products
            }
            if config.evidence_field_token_fallback
            else {}
        )

    @staticmethod
    def _constraints(state: SessionState) -> list[str]:
        return list(dict.fromkeys(
            normalized_value(value)
            for attribute, values in state.active_slots.items()
            if attribute not in {"category", "budget"}
            for value in values
            if normalized_value(value)
        ))

    @staticmethod
    def _contains_phrase(text: str, phrase: str) -> bool:
        if not text or not phrase:
            return False
        return f" {phrase} " in f" {text} "

    def _contains_evidence(self, parent_asin: str, text: str, phrase: str) -> bool:
        if self._contains_phrase(text, phrase):
            return True
        if not self.config.evidence_field_token_fallback:
            return False
        tokens = tuple(dict.fromkeys(normalized_tokens(phrase)))
        if len(tokens) < 2:
            return False
        return any(
            all(self._contains_phrase(field, token) for token in tokens)
            for field in self._normalized_fields.get(parent_asin, ())
        )

    @staticmethod
    def _budget_point(state: SessionState) -> float | None:
        values = state.active_slots.get("budget", ())
        if not values:
            return None
        try:
            return float(values[-1])
        except (TypeError, ValueError):
            return None

    def _budget_score(self, state: SessionState, price: float | None) -> float:
        point = self._budget_point(state)
        if point is None:
            return 0.0
        if self.config.soft_budget_as_cap:
            if price is None or price <= point:
                return 0.0
            relative_overage = (price - point) / max(point, 1.0)
            if relative_overage <= 0.10:
                return -0.2
            if relative_overage <= 0.25:
                return -0.8
            return -1.5
        if price is None:
            return -0.5
        if abs(price - point) <= max(0.01, 0.01 * point):
            return 6.0
        if 0.65 * point <= price <= 1.35 * point:
            return 1.2
        return -1.5

    def rank(
        self,
        state: SessionState,
        plan: SearchPlan,
        result: RetrievalResult,
    ) -> list[RankedCandidate]:
        if not result.candidates:
            return []
        constraints = self._constraints(state)
        weights = {
            constraint: self.config.exact_base_weight
            + self.config.exact_token_weight * min(len(normalized_tokens(constraint)), 12)
            for constraint in constraints
        }
        max_lexical = max((candidate.lexical_score for candidate in result.candidates), default=1.0) or 1.0
        ranked: list[RankedCandidate] = []
        for candidate in result.candidates:
            product = self.store.get(candidate.parent_asin)
            if product is None or violates_negative_slots(product, state.negative_slots):
                continue
            text = self._normalized_text.get(candidate.parent_asin, "")
            # Score each disclosed requirement independently. Longer phrases
            # carry more evidence, while missing requirements incur a penalty;
            # this makes a complete match outrank a merely similar title.
            matched = [
                constraint
                for constraint in constraints
                if self._contains_evidence(candidate.parent_asin, text, constraint)
            ]
            evidence_score = sum(weights[constraint] for constraint in matched)
            evidence_score -= self.config.exact_unmatched_penalty * (len(constraints) - len(matched))
            all_match = bool(constraints) and len(matched) == len(constraints)
            if all_match:
                # A product satisfying every active requirement receives a
                # bounded bonus, rather than relying on lexical score alone.
                evidence_score += self.config.exact_all_match_bonus
            lexical_score = candidate.lexical_score / max_lexical
            anchor_score = (
                self.config.exact_anchor_bonus
                if "category_anchor" in candidate.source_routes
                else 0.0
            )
            profile_score = (
                sum(term in text for term in state.profile_terms) / len(state.profile_terms)
                if state.profile_terms and not constraints
                else 0.0
            )
            popularity_score = (
                self.config.popularity_rating_weight * float(product.average_rating or 0.0)
                + self.config.popularity_count_weight * math.log1p(float(product.rating_number))
            )
            lexical_routes = {
                route
                for route in candidate.source_routes
                if route.startswith("bm25:")
            }
            route_consensus_score = max(0, len(lexical_routes) - 1) / 4.0
            final_score = (
                anchor_score
                + evidence_score
                + self._budget_score(state, product.price)
                + self.config.evidence_lexical_weight * lexical_score
                + self.config.evidence_candidate_constraint_weight * candidate.constraint_score
                + self.config.evidence_profile_weight * profile_score
                + self.config.evidence_route_consensus_weight * route_consensus_score
                + popularity_score
            )
            ranked.append(RankedCandidate(
                parent_asin=candidate.parent_asin,
                final_score=final_score,
                component_scores={
                    "lexical": lexical_score,
                    "constraint": evidence_score,
                    "profile": profile_score,
                    "anchor": anchor_score,
                    "popularity": popularity_score,
                    "route_consensus": route_consensus_score,
                    "budget": self._budget_score(state, product.price),
                    "matched_constraint_count": float(len(matched)),
                    "active_constraint_count": float(len(constraints)),
                    "matched_product_constraint_count": float(len(matched)),
                    "product_constraint_count": float(len(constraints)),
                    "all_constraints_match": float(all_match),
                },
            ))
        return sorted(ranked, key=lambda item: (-item.final_score, item.parent_asin))
