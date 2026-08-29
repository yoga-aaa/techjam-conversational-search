from __future__ import annotations

from collections import defaultdict

from shopping_copilot.catalog.constraints import (
    matches_any_excluded_term,
    normalized_tokens,
    normalized_value,
    product_matches_value,
)
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan


GENERIC_CATEGORY_NAMES = {
    "clothing",
    "clothing shoes jewelry",
    "clothing shoes & jewelry",
    "clothing, shoes & jewelry",
}


def category_anchor(categories: tuple[str, ...]) -> str:
    """Build the evaluator-compatible normalized category tail."""

    parts: list[str] = []
    for category in categories:
        for piece in category.split(","):
            value = normalized_value(piece)
            if value and value not in GENERIC_CATEGORY_NAMES:
                parts.append(value)
    return " ".join(parts[-2:]) if parts else "clothing item"


class CategoryAnchorRetriever:
    """A catalog-derived category route that preserves the full anchor pool.

    The route uses a separate, larger anchor budget. This keeps category recall
    wider than the normal lexical slate without making every product in a very
    broad category participate in the reranker.
    """

    def __init__(self, store: CatalogStore, candidate_k: int = 200) -> None:
        self.store = store
        self.candidate_k = max(10, candidate_k)
        self._postings: dict[str, tuple[str, ...]] = {
            key: tuple(values)
            for key, values in self._build_postings(store).items()
        }
        self._keys = tuple(sorted(self._postings, key=lambda key: (-len(key.split()), key)))
        self._match_cache: dict[tuple[str, str, str], bool] = {}
        self._rank_cache: dict[tuple[object, ...], tuple[tuple[str, float, int, float], ...]] = {}

    @staticmethod
    def _build_postings(store: CatalogStore) -> dict[str, list[str]]:
        postings: dict[str, list[str]] = defaultdict(list)
        for product in store.products:
            postings[category_anchor(product.categories)].append(product.parent_asin)
        return postings

    def _anchor_ids(self, plan: SearchPlan) -> tuple[str, ...]:
        values = plan.structured_constraints.get("category", ())
        for raw in values:
            value = normalized_value(raw)
            if not value:
                continue
            exact = self._postings.get(value)
            if exact:
                return exact
            # The state parser can retain a shorter category phrase. Prefer the
            # longest catalog key ending in that phrase as a safe fallback.
            matches = [key for key in self._keys if key.endswith(value) or value.endswith(key)]
            if matches:
                return self._postings[matches[0]]
        return ()

    def _ranked(self, plan: SearchPlan) -> list[tuple[str, float, int, float]]:
        cache_key = (
            tuple(sorted((key, tuple(values)) for key, values in plan.structured_constraints.items())),
            tuple(plan.excluded_terms),
            plan.hard_filters.get("price_max"),
            tuple(plan.lexical_terms),
        )
        cached = self._rank_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        anchor_ids = self._anchor_ids(plan)
        if not anchor_ids:
            return []
        constraints = [
            (attribute, value)
            for attribute, values in plan.structured_constraints.items()
            if attribute != "category"
            for value in values
            if normalized_value(value)
        ]
        price_max = plan.hard_filters.get("price_max")
        ranked: list[tuple[str, float, int, float]] = []
        for parent_asin in anchor_ids:
            product = self.store.get(parent_asin)
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            if matches_any_excluded_term(product, plan.excluded_terms):
                continue
            matched = sum(self._matches(product, attribute, value) for attribute, value in constraints)
            score = matched / len(constraints) if constraints else 0.0
            tokens = set(normalized_tokens(product.searchable_text))
            lexical_score = (
                sum(term in tokens for term in plan.lexical_terms) / len(plan.lexical_terms)
                if plan.lexical_terms else 0.0
            )
            ranked.append((parent_asin, score, product.rating_number, lexical_score))
        ranked.sort(key=lambda item: (-item[1], -item[3], -item[2], item[0]))
        ranked = ranked[: self.candidate_k]
        self._rank_cache[cache_key] = tuple(ranked)
        return ranked

    def _matches(self, product: object, attribute: str, value: str) -> bool:
        parent_asin = getattr(product, "parent_asin", "")
        key = (str(parent_asin), attribute, normalized_value(value))
        if key not in self._match_cache:
            self._match_cache[key] = product_matches_value(product, attribute, value)
        return self._match_cache[key]

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        ranked = self._ranked(plan)
        gap = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else (ranked[0][1] if ranked else 0.0)
        return RetrievalDiagnostics(
            candidate_count=len(ranked),
            category_count=1 if ranked else 0,
            top_score_gap=max(0.0, gap),
            route=plan.route,
        )

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        ranked = self._ranked(plan)
        return RetrievalResult(
            candidates=tuple(
                Candidate(
                    parent_asin=parent_asin,
                    lexical_score=lexical_score,
                    constraint_score=score,
                    source_routes=("category_anchor",),
                )
                for parent_asin, score, _, lexical_score in ranked
            ),
            diagnostics=self._diagnostics(plan, ranked),
        )

    @staticmethod
    def _diagnostics(plan: SearchPlan, ranked: list[tuple[str, float, int, float]]) -> RetrievalDiagnostics:
        gap = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else (ranked[0][1] if ranked else 0.0)
        return RetrievalDiagnostics(
            candidate_count=len(ranked),
            category_count=1 if ranked else 0,
            top_score_gap=max(0.0, gap),
            route=plan.route,
        )


class DisabledCategoryAnchorRetriever:
    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        return RetrievalDiagnostics(0, 0, 0.0, plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        return RetrievalResult((), self.probe(plan))
