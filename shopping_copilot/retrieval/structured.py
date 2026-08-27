from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Mapping, Sequence

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "my", "of", "on", "or", "the", "this", "to",
    "with", "would", "you", "key", "need", "prefer", "preference", "requirement",
}


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_RE.findall(value)
        if len(token) > 1 and token.lower() not in STOPWORDS
    }


def reciprocal_rank_fusion(
    ranked_by_attribute: Mapping[str, Sequence[tuple[str, float]]],
    k: int = 60,
    weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Fuse independent attribute rankings into normalized structured scores."""
    smoothing = max(1, int(k))
    configured_weights = weights or {}
    fused: dict[str, float] = {}
    max_possible = 0.0
    for attribute, rows in ranked_by_attribute.items():
        weight = max(0.0, float(configured_weights.get(attribute, 1.0)))
        if weight <= 0.0 or not rows:
            continue
        max_possible += weight / float(smoothing + 1)
        seen: set[str] = set()
        rank = 0
        for parent_asin, _raw_score in rows:
            if parent_asin in seen:
                continue
            seen.add(parent_asin)
            rank += 1
            fused[parent_asin] = fused.get(parent_asin, 0.0) + weight / float(smoothing + rank)
    if max_possible <= 0.0:
        return {}
    return {parent_asin: min(1.0, score / max_possible) for parent_asin, score in fused.items()}


class StructuredRetriever:
    """Attribute-aware candidate search derived entirely from the frozen catalog."""

    def __init__(self, store: CatalogStore, config: Mapping[str, object] | None = None) -> None:
        self.store = store
        self.general_postings: dict[str, set[str]] = defaultdict(set)
        self.category_postings: dict[str, set[str]] = defaultdict(set)
        self.brand_postings: dict[str, set[str]] = defaultdict(set)
        self._cached_key: tuple[object, ...] | None = None
        self._cached_scores: dict[str, float] = {}
        rrf_config = dict((config or {}).get("structured_rrf") or {})
        self.fusion_mode = str(rrf_config.get("mode", "max")).lower()
        self.rrf_k = max(1, int(rrf_config.get("k", 60)))
        self.per_attribute_limit = max(1, int(rrf_config.get("per_attribute_limit", 300)))
        self.rrf_weights = {
            str(name): float(value)
            for name, value in dict(rrf_config.get("weights") or {}).items()
        }
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

        attribute_scores = self._attribute_scores(constraints)
        candidate_ids: set[str] = set()
        for attribute in non_category:
            candidate_ids.update(attribute_scores.get(attribute, {}))
        category_ids = set(attribute_scores.get("category", {}))
        narrowed_ids = candidate_ids & category_ids
        if narrowed_ids:
            candidate_ids = narrowed_ids

        denominator = float(len(constraints))
        scores = {
            parent_asin: sum(values.get(parent_asin, 0.0) for values in attribute_scores.values()) / denominator
            for parent_asin in candidate_ids
        }
        scores = {parent_asin: score for parent_asin, score in scores.items() if score > 0.0}
        self._cached_key = cache_key
        self._cached_scores = self._filter_scores(plan, scores)
        return self._cached_scores

    def _attribute_scores(
        self,
        constraints: Mapping[str, Sequence[str]],
    ) -> dict[str, dict[str, float]]:
        attribute_scores: dict[str, dict[str, float]] = {}
        for attribute, values in constraints.items():
            combined: dict[str, float] = {}
            for value in values:
                for parent_asin, score in self._value_scores(attribute, value).items():
                    combined[parent_asin] = max(combined.get(parent_asin, 0.0), score)
            attribute_scores[attribute] = combined
        return attribute_scores

    def _filter_scores(self, plan: SearchPlan, scores: Mapping[str, float]) -> dict[str, float]:
        price_max = plan.hard_filters.get("price_max")
        excluded = tuple(term.lower() for term in plan.excluded_terms)
        filtered: dict[str, float] = {}
        for parent_asin, score in scores.items():
            product = self.store.get(parent_asin)
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            if excluded and any(term in product.searchable_text.lower() for term in excluded):
                continue
            filtered[parent_asin] = score
        return filtered

    def _rrf_scores(self, plan: SearchPlan) -> dict[str, float]:
        constraints = {
            attribute: values
            for attribute, values in plan.structured_constraints.items()
            if values
        }
        attribute_scores = self._attribute_scores(constraints)
        ranked_by_attribute = {
            attribute: sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: self.per_attribute_limit]
            for attribute, scores in attribute_scores.items()
            if scores
        }
        fused = reciprocal_rank_fusion(ranked_by_attribute, self.rrf_k, self.rrf_weights)
        return self._filter_scores(plan, fused)

    def _active_scores(self, plan: SearchPlan) -> dict[str, float]:
        if self.fusion_mode in {"rrf", "standard_rrf"}:
            return self._rrf_scores(plan)
        if self.fusion_mode == "max":
            return self._scores(plan)
        raise ValueError(f"Unsupported structured fusion mode: {self.fusion_mode}")

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        scores = sorted(self._active_scores(plan).values(), reverse=True)
        score_gap = scores[0] - scores[1] if len(scores) > 1 else (scores[0] if scores else 0.0)
        return RetrievalDiagnostics(len(scores), 0, max(0.0, score_gap), plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        scores = self._active_scores(plan)
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
