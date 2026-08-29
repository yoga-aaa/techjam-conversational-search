from __future__ import annotations

from shopping_copilot.catalog.constraints import matches_any_excluded_term, product_matches_value
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import SearchConfig
from shopping_copilot.core.contracts import Candidate, Product, RetrievalResult, SearchPlan
from shopping_copilot.retrieval.bm25 import BM25Retriever


class StrictAndCandidateRescuer:
    """Find high-confidence Strict-only candidates for response-only rescue."""

    def __init__(
        self,
        lexical: BM25Retriever,
        store: CatalogStore,
        config: SearchConfig,
    ) -> None:
        self.lexical = lexical
        self.store = store
        self.config = config

    @staticmethod
    def _matched_slot_count(product: Product, plan: SearchPlan) -> int:
        return sum(
            int(any(
                product_matches_value(product, slot, value)
                for value in values
            ))
            for slot, values in plan.structured_constraints.items()
            if slot != "budget" and values
        )

    def retrieve(
        self,
        plan: SearchPlan,
        primary: RetrievalResult,
    ) -> tuple[Candidate, ...]:
        strict = self.lexical.strict_candidates(
            plan,
            self.config.protected_rescue_fetch_k,
            deterministic_ties=True,
        )
        if not strict:
            return ()

        primary_ids = {candidate.parent_asin for candidate in primary.candidates}
        strict_order = sorted(
            strict,
            key=lambda candidate: (-candidate.lexical_score, candidate.parent_asin),
        )
        strict_entries = tuple(
            (strict_rank, candidate)
            for strict_rank, candidate in enumerate(strict_order, start=1)
        )
        qualified: list[tuple[str, int, int]] = []
        seen_ids: set[str] = set()
        price_max = plan.hard_filters.get("price_max")
        for strict_rank, candidate in strict_entries:
            parent_asin = candidate.parent_asin
            if parent_asin in primary_ids or parent_asin in seen_ids:
                continue
            product = self.store.get(parent_asin)
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            if matches_any_excluded_term(product, plan.excluded_terms):
                continue
            matched_slots = self._matched_slot_count(product, plan)
            if matched_slots < self.config.protected_rescue_min_slot_matches:
                continue
            seen_ids.add(parent_asin)
            qualified.append((parent_asin, matched_slots, strict_rank))

        if not qualified:
            return ()

        broad_scores = self.lexical.broad_scores_for_ids(
            plan,
            tuple(parent_asin for parent_asin, _, _ in qualified),
        )
        scored = [
            (parent_asin, matched_slots, strict_rank, broad_scores[parent_asin])
            for parent_asin, matched_slots, strict_rank in qualified
            if parent_asin in broad_scores
        ]
        scored.sort(
            key=lambda item: (
                -item[1],
                item[2],
                -item[3],
                item[0],
            )
        )
        return tuple(
            Candidate(
                parent_asin=parent_asin,
                lexical_score=broad_score,
                source_routes=("bm25:protected_strict",),
            )
            for parent_asin, _, _, broad_score in scored[: self.config.protected_rescue_pool_size]
        )
