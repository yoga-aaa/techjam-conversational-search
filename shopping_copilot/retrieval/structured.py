from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
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


@dataclass(frozen=True)
class StructuredFusionSignals:
    rank_quality: float
    attribute_coverage: float
    matched_rank_attributes: tuple[str, ...] = ()


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


def _attribute_to_group(attribute_groups: Mapping[str, Sequence[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for group, attributes in attribute_groups.items():
        for attribute in attributes:
            if attribute in result:
                raise ValueError(f"Attribute {attribute!r} belongs to multiple RRF groups")
            result[str(attribute)] = str(group)
    return result


def grouped_rrf_signals(
    ranked_by_attribute: Mapping[str, Sequence[tuple[str, float]]],
    k: int,
    weights: Mapping[str, float],
    attribute_groups: Mapping[str, Sequence[str]],
    secondary_discount: float,
) -> dict[str, StructuredFusionSignals]:
    """Apply group saturation to RRF while exposing independent coverage."""
    smoothing = max(1, int(k))
    discount = max(0.0, min(1.0, float(secondary_discount)))
    attribute_to_group = _attribute_to_group(attribute_groups)
    active_weights: dict[str, float] = {}
    contributions: dict[str, dict[str, float]] = {}

    for attribute, rows in ranked_by_attribute.items():
        weight = max(0.0, float(weights.get(attribute, 1.0)))
        if weight <= 0.0 or not rows:
            continue
        active_weights[attribute] = weight
        seen: set[str] = set()
        rank = 0
        for parent_asin, _raw_score in rows:
            if parent_asin in seen:
                continue
            seen.add(parent_asin)
            rank += 1
            contributions.setdefault(parent_asin, {})[attribute] = weight / float(smoothing + rank)

    if not active_weights:
        return {}

    group_maxima: dict[str, list[float]] = {}
    for attribute, weight in active_weights.items():
        group = attribute_to_group.get(attribute, attribute)
        group_maxima.setdefault(group, []).append(weight / float(smoothing + 1))
    max_possible = sum(
        max(values) + discount * (sum(values) - max(values))
        for values in group_maxima.values()
    )
    total_weight = sum(active_weights.values())
    result: dict[str, StructuredFusionSignals] = {}
    for parent_asin, per_attribute in contributions.items():
        per_group: dict[str, list[float]] = {}
        for attribute, value in per_attribute.items():
            per_group.setdefault(attribute_to_group.get(attribute, attribute), []).append(value)
        grouped_score = sum(
            max(values) + discount * (sum(values) - max(values))
            for values in per_group.values()
        )
        result[parent_asin] = StructuredFusionSignals(
            rank_quality=min(1.0, grouped_score / max_possible) if max_possible else 0.0,
            attribute_coverage=min(1.0, sum(active_weights[a] for a in per_attribute) / total_weight),
            matched_rank_attributes=tuple(sorted(per_attribute)),
        )
    return result


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
        self.attribute_groups = dict(rrf_config.get("attribute_groups") or {
            "taxonomy": ["category"],
            "appearance": ["color", "style"],
            "physical": ["material", "size"],
            "intent": ["feature", "use_case", "other"],
            "commercial": ["brand", "budget"],
        })
        _attribute_to_group(self.attribute_groups)
        self.secondary_discount = max(0.0, min(1.0, float(rrf_config.get("group_secondary_discount", 0.5))))
        signal_mix = dict(rrf_config.get("signal_mix") or {"rank_quality": 0.5, "attribute_coverage": 0.5})
        rank_mix = max(0.0, float(signal_mix.get("rank_quality", 0.0)))
        coverage_mix = max(0.0, float(signal_mix.get("attribute_coverage", 0.0)))
        if rank_mix + coverage_mix <= 0.0:
            self.rank_mix, self.coverage_mix = 1.0, 0.0
        else:
            self.rank_mix = rank_mix / (rank_mix + coverage_mix)
            self.coverage_mix = coverage_mix / (rank_mix + coverage_mix)
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
        constraints = {attribute: values for attribute, values in plan.structured_constraints.items() if values}
        non_category = {attribute: values for attribute, values in constraints.items() if attribute != "category"}
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
        self._cached_key = cache_key
        self._cached_scores = self._filter_scores(plan, {item: score for item, score in scores.items() if score > 0.0})
        return self._cached_scores

    def _attribute_scores(self, constraints: Mapping[str, Sequence[str]]) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for attribute, values in constraints.items():
            combined: dict[str, float] = {}
            for value in values:
                for parent_asin, score in self._value_scores(attribute, value).items():
                    combined[parent_asin] = max(combined.get(parent_asin, 0.0), score)
            result[attribute] = combined
        return result

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

    def _rankings(self, plan: SearchPlan) -> dict[str, list[tuple[str, float]]]:
        constraints = {attribute: values for attribute, values in plan.structured_constraints.items() if values}
        return {
            attribute: sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: self.per_attribute_limit]
            for attribute, scores in self._attribute_scores(constraints).items()
            if scores
        }

    def _rrf_scores(self, plan: SearchPlan) -> dict[str, float]:
        return self._filter_scores(plan, reciprocal_rank_fusion(self._rankings(plan), self.rrf_k, self.rrf_weights))

    def _optimized_rrf_scores(self, plan: SearchPlan) -> dict[str, float]:
        rankings = self._rankings(plan)
        signals = grouped_rrf_signals(
            rankings,
            self.rrf_k,
            self.rrf_weights,
            self.attribute_groups,
            self.secondary_discount,
        )
        active_attributes = [
            attribute
            for attribute, rows in rankings.items()
            if rows and max(0.0, float(self.rrf_weights.get(attribute, 1.0))) > 0.0
        ]
        scores = {
            parent_asin: (
                signal.rank_quality
                if len(active_attributes) <= 1
                else self.rank_mix * signal.rank_quality + self.coverage_mix * signal.attribute_coverage
            )
            for parent_asin, signal in signals.items()
        }
        return self._filter_scores(plan, scores)

    def _active_scores(self, plan: SearchPlan) -> dict[str, float]:
        if self.fusion_mode in {"rrf", "standard_rrf"}:
            return self._rrf_scores(plan)
        if self.fusion_mode == "optimized_rrf":
            return self._optimized_rrf_scores(plan)
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
            Candidate(parent_asin=parent_asin, constraint_score=score, source_routes=("structured",))
            for parent_asin, score in ranked
        )
        diagnostics = RetrievalDiagnostics(
            candidate_count=len(scores),
            category_count=0,
            top_score_gap=(ranked[0][1] - ranked[1][1]) if len(ranked) > 1 else (ranked[0][1] if ranked else 0.0),
            route=plan.route,
        )
        return RetrievalResult(candidates, diagnostics)
