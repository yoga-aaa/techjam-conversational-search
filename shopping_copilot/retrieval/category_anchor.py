from __future__ import annotations

from collections import defaultdict

from shopping_copilot.catalog.constraints import (
    matches_any_excluded_term,
    normalized_value,
    product_matches_value,
)
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan


GENERIC_CATEGORY_NAMES = {
    "clothing",
    "clothing shoes jewelry",
}


def category_anchor(categories: tuple[str, ...]) -> str:
    """Return a stable, useful tail of the catalog's category path."""

    parts: list[str] = []
    for category in categories:
        for piece in category.split(","):
            normalized = normalized_value(piece)
            if normalized and normalized not in GENERIC_CATEGORY_NAMES:
                parts.append(normalized)
    return " ".join(parts[-2:]) if parts else ""


class CategoryAnchorRetriever:
    """Restrict broad lexical search to the category named by the user."""

    def __init__(self, store: CatalogStore, full_pool: bool = False) -> None:
        self.store = store
        self.full_pool = full_pool
        self._postings: dict[str, list[str]] = defaultdict(list)
        self._last_rank_key: tuple[object, ...] | None = None
        self._last_ranked: list[tuple[str, float]] | None = None
        for product in store.products:
            anchor = category_anchor(product.categories)
            if anchor:
                self._postings[anchor].append(product.parent_asin)

    def _anchor_ids(self, plan: SearchPlan) -> list[str]:
        for value in plan.structured_constraints.get("category", ()):
            normalized = normalized_value(value)
            if normalized in self._postings:
                return self._postings[normalized]
        return []

    def _ranked(self, plan: SearchPlan) -> list[tuple[str, float]]:
        rank_key = (
            tuple((name, tuple(values)) for name, values in sorted(plan.structured_constraints.items())),
            tuple(sorted((name, str(value)) for name, value in plan.hard_filters.items())),
            tuple(plan.excluded_terms),
        )
        if rank_key == self._last_rank_key and self._last_ranked is not None:
            return self._last_ranked
        candidate_ids = self._anchor_ids(plan)
        if not candidate_ids:
            return []
        constraints = [
            (attribute, value)
            for attribute, values in plan.structured_constraints.items()
            if attribute != "category"
            for value in values
            if normalized_value(value)
        ]
        # A category alone can contain hundreds of interchangeable products.
        # Injecting popularity-ranked category items at that point dilutes a
        # stronger lexical list. The route becomes useful only after at least
        # one product-bearing constraint can rank the category subset.
        if not constraints:
            return []
        price_max = plan.hard_filters.get("price_max")
        ranked: list[tuple[str, float, int]] = []
        for parent_asin in candidate_ids:
            product = self.store.get(parent_asin)
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            if matches_any_excluded_term(product, plan.excluded_terms):
                continue
            matched = sum(
                product_matches_value(product, attribute, value)
                for attribute, value in constraints
            )
            constraint_score = matched / len(constraints) if constraints else 0.0
            ranked.append((parent_asin, constraint_score, product.rating_number))
        ranked.sort(key=lambda item: (-item[1], -item[2], item[0]))
        result = [(parent_asin, score) for parent_asin, score, _ in ranked]
        self._last_rank_key = rank_key
        self._last_ranked = result
        return result

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        ranked = self._ranked(plan)
        score_gap = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else (ranked[0][1] if ranked else 0.0)
        return RetrievalDiagnostics(len(ranked), 1 if ranked else 0, max(0.0, score_gap), plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        ranked = self._ranked(plan)
        selected = ranked if self.full_pool else ranked[: plan.candidate_k]
        return RetrievalResult(
            candidates=tuple(
                Candidate(
                    parent_asin=parent_asin,
                    constraint_score=score,
                    source_routes=("category_anchor",),
                )
                for parent_asin, score in selected
            ),
            diagnostics=RetrievalDiagnostics(
                candidate_count=len(ranked),
                category_count=1 if ranked else 0,
                top_score_gap=(selected[0][1] - selected[1][1])
                if len(selected) > 1
                else (selected[0][1] if selected else 0.0),
                route=plan.route,
            ),
        )


class DisabledCategoryAnchorRetriever:
    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        return RetrievalDiagnostics(0, 0, 0.0, plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        return RetrievalResult((), self.probe(plan))
