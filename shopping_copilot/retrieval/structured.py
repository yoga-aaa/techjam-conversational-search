from __future__ import annotations

from collections import Counter, defaultdict

from shopping_copilot.catalog.constraints import matches_any_excluded_term, normalized_tokens
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "my", "of", "on", "or", "the", "this", "to",
    "with", "would", "you", "key", "need", "prefer", "preference", "requirement",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in normalized_tokens(value)
        if len(token) > 1 and token not in STOPWORDS
    }


class StructuredRetriever:
    """Attribute-aware candidate search derived entirely from the frozen catalog."""

    def __init__(self, store: CatalogStore) -> None:
        self.store = store
        self.general_postings: dict[str, set[str]] = defaultdict(set)
        self.category_postings: dict[str, set[str]] = defaultdict(set)
        self.brand_postings: dict[str, set[str]] = defaultdict(set)
        self._cached_key: tuple[object, ...] | None = None
        self._cached_scores: dict[str, float] = {}
        self._build_indexes()

    def _build_indexes(self) -> None:
        for product in self.store.products:
            for token in _tokens(product.searchable_text):
                self.general_postings[token].add(product.parent_asin)
            for token in _tokens(" ".join(product.categories)):
                self.category_postings[token].add(product.parent_asin)
            for token in _tokens(f"{product.store} {product.title}"):
                self.brand_postings[token].add(product.parent_asin)

    def _postings(self, attribute: str) -> dict[str, set[str]]:
        if attribute == "category":
            return self.category_postings
        if attribute == "brand":
            return self.brand_postings
        return self.general_postings

    def _value_scores(self, attribute: str, value: str) -> dict[str, float]:
        terms = _tokens(value) - {attribute, attribute.replace("_", "")}
        if not terms:
            return {}
        counts: Counter[str] = Counter()
        postings = self._postings(attribute)
        for term in terms:
            counts.update(postings.get(term, ()))
        denominator = float(len(terms))
        return {
            parent_asin: min(1.0, count / denominator)
            for parent_asin, count in counts.items()
        }

    def _scores(self, plan: SearchPlan) -> dict[str, float]:
        cache_key = (
            tuple(sorted((attribute, tuple(values)) for attribute, values in plan.structured_constraints.items())),
            tuple(sorted((name, str(value)) for name, value in plan.hard_filters.items())),
            tuple(plan.excluded_terms),
        )
        if cache_key == self._cached_key:
            return self._cached_scores

        constraints = {
            attribute: values
            for attribute, values in plan.structured_constraints.items()
            if values
        }
        non_category = {
            attribute: values
            for attribute, values in constraints.items()
            if attribute != "category"
        }
        if not non_category:
            self._cached_key = cache_key
            self._cached_scores = {}
            return {}

        attribute_scores: dict[str, dict[str, float]] = {}
        for attribute, values in constraints.items():
            combined: dict[str, float] = {}
            for value in values:
                for parent_asin, score in self._value_scores(attribute, value).items():
                    combined[parent_asin] = max(combined.get(parent_asin, 0.0), score)
            attribute_scores[attribute] = combined

        candidate_ids: set[str] = set()
        for attribute in non_category:
            candidate_ids.update(attribute_scores.get(attribute, {}))
        category_ids = set(attribute_scores.get("category", {}))
        narrowed_ids = candidate_ids & category_ids
        if narrowed_ids:
            candidate_ids = narrowed_ids

        price_max = plan.hard_filters.get("price_max")
        scores: dict[str, float] = {}
        denominator = float(len(constraints))
        for parent_asin in candidate_ids:
            product = self.store.get(parent_asin)
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            if matches_any_excluded_term(product, plan.excluded_terms):
                continue
            score = sum(values.get(parent_asin, 0.0) for values in attribute_scores.values()) / denominator
            if score > 0.0:
                scores[parent_asin] = score
        self._cached_key = cache_key
        self._cached_scores = scores
        return scores

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        scores = sorted(self._scores(plan).values(), reverse=True)
        score_gap = scores[0] - scores[1] if len(scores) > 1 else (scores[0] if scores else 0.0)
        return RetrievalDiagnostics(len(scores), 0, max(0.0, score_gap), plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        scores = self._scores(plan)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: plan.candidate_k]
        candidates = tuple(
            Candidate(
                parent_asin=parent_asin,
                constraint_score=score,
                source_routes=("structured",),
            )
            for parent_asin, score in ranked
        )
        diagnostics = RetrievalDiagnostics(
            candidate_count=len(scores),
            category_count=0,
            top_score_gap=(ranked[0][1] - ranked[1][1]) if len(ranked) > 1 else (ranked[0][1] if ranked else 0.0),
            route=plan.route,
        )
        return RetrievalResult(candidates, diagnostics)
