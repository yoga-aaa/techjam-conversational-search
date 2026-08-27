from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Product, RankedCandidate, SessionState


MATERIALS = {
    "acrylic", "cashmere", "cotton", "denim", "fabric", "lace", "leather",
    "linen", "modal", "nylon", "polyester", "rayon", "silk", "spandex",
    "suede", "synthetic", "wool",
}
COLORS = {
    "beige", "black", "blue", "brown", "gold", "gray", "green", "grey",
    "navy", "orange", "pink", "purple", "red", "silver", "teal", "white",
    "yellow",
}
SIZES = {
    "xxs", "xs", "small", "medium", "large", "xl", "xxl", "xxxl",
    "petite", "plus", "wide", "narrow", "regular",
}
STYLES = {"casual", "classic", "formal", "modern", "sporty", "vintage"}
USE_CASES = {
    "camping", "gym", "hiking", "office", "outdoor", "party", "rain",
    "running", "snow", "sports", "travel", "walking", "wedding", "winter",
    "work", "workout",
}
FEATURES = {
    "breathable", "comfortable", "durable", "insulated", "lightweight",
    "non-slip", "stretch", "warm", "waterproof", "windproof",
}
TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")

DEFAULT_PRIORS = {
    "material": 3.0,
    "feature": 2.0,
    "color": 1.1,
    "style": 0.30,
    "size": 0.30,
    "use_case": 0.30,
    "budget": 0.25,
    "brand": 0.15,
}
ATTRIBUTES = tuple(DEFAULT_PRIORS)


@dataclass(frozen=True)
class AttributeQuestionScore:
    attribute: str
    score: float
    coverage: float
    entropy: float
    expected_reduction: float
    distinct_values: int


def _matched_words(text: str, vocabulary: set[str]) -> tuple[str, ...]:
    tokens = set(TOKEN_RE.findall(text.lower()))
    return tuple(sorted(tokens & vocabulary))


def _feature_values(product: Product) -> tuple[str, ...]:
    known = _matched_words(product.searchable_text, FEATURES)
    if known:
        return known

    values: list[str] = []
    for raw in (*product.features, *product.details):
        normalized = SPACE_RE.sub(" ", raw.lower()).strip(" .,:;-")
        tokens = [token for token in TOKEN_RE.findall(normalized) if len(token) >= 3]
        if not tokens:
            continue
        values.append(" ".join(tokens[:4]))
        if len(values) >= 3:
            break
    return tuple(dict.fromkeys(values))


def _budget_bucket(price: float | None) -> tuple[str, ...]:
    if price is None:
        return ()
    if price < 25:
        return ("under_25",)
    if price < 50:
        return ("25_50",)
    if price < 100:
        return ("50_100",)
    return ("100_plus",)


def product_attribute_values(product: Product, attribute: str) -> tuple[str, ...]:
    text = product.searchable_text.lower()
    if attribute == "material":
        return _matched_words(text, MATERIALS)
    if attribute == "color":
        return _matched_words(text, COLORS)
    if attribute == "size":
        return _matched_words(text, SIZES)
    if attribute == "style":
        return _matched_words(text, STYLES)
    if attribute == "use_case":
        return _matched_words(text, USE_CASES)
    if attribute == "feature":
        return _feature_values(product)
    if attribute == "budget":
        return _budget_bucket(product.price)
    if attribute == "brand":
        brand = product.store.strip().lower()
        return (brand,) if brand else ()
    return ()


class InformationGainQuestionScorer:
    """Ranks clarification attributes by expected candidate-set reduction."""

    def __init__(
        self,
        store: CatalogStore,
        candidate_limit: int = 60,
        minimum_coverage: float = 0.12,
        minimum_score: float = 0.01,
        attribute_priors: dict[str, float] | None = None,
    ) -> None:
        self.store = store
        self.candidate_limit = max(10, candidate_limit)
        self.minimum_coverage = max(0.0, min(1.0, minimum_coverage))
        self.minimum_score = max(0.0, minimum_score)
        self.attribute_priors = {**DEFAULT_PRIORS, **(attribute_priors or {})}
        self._attribute_cache: dict[tuple[str, str], tuple[str, ...]] = {}

    def _values(self, product: Product, attribute: str) -> tuple[str, ...]:
        key = (product.parent_asin, attribute)
        if key not in self._attribute_cache:
            self._attribute_cache[key] = product_attribute_values(product, attribute)
        return self._attribute_cache[key]

    @staticmethod
    def _available(state: SessionState, attribute: str) -> bool:
        unavailable = (
            set(state.active_slots)
            | set(state.asked_attributes)
            | set(state.no_preference_attributes)
        )
        return attribute not in unavailable

    def score(
        self,
        state: SessionState,
        ranked: Sequence[RankedCandidate],
    ) -> list[AttributeQuestionScore]:
        weighted_candidates: list[tuple[Product, float]] = []
        for rank, candidate in enumerate(ranked[: self.candidate_limit], start=1):
            product = self.store.get(candidate.parent_asin)
            if product is None:
                continue
            weighted_candidates.append((product, 1.0 / math.log2(rank + 1.0)))

        total_weight = sum(weight for _, weight in weighted_candidates)
        if total_weight <= 0.0:
            return []

        results: list[AttributeQuestionScore] = []
        for attribute in ATTRIBUTES:
            if not self._available(state, attribute):
                continue

            value_weights: dict[str, float] = defaultdict(float)
            covered_weight = 0.0
            for product, weight in weighted_candidates:
                values = self._values(product, attribute)
                if not values:
                    continue
                covered_weight += weight
                divided_weight = weight / len(values)
                for value in values:
                    value_weights[value] += divided_weight

            coverage = covered_weight / total_weight
            if coverage < self.minimum_coverage or len(value_weights) <= 1:
                continue

            probabilities = [weight / covered_weight for weight in value_weights.values()]
            entropy_raw = -sum(probability * math.log(probability, 2) for probability in probabilities)
            entropy = entropy_raw / math.log(len(probabilities), 2)
            expected_reduction = 1.0 - sum(probability * probability for probability in probabilities)
            score = (
                coverage
                * entropy
                * expected_reduction
                * self.attribute_priors.get(attribute, 1.0)
            )
            if score < self.minimum_score:
                continue
            results.append(
                AttributeQuestionScore(
                    attribute=attribute,
                    score=score,
                    coverage=coverage,
                    entropy=entropy,
                    expected_reduction=expected_reduction,
                    distinct_values=len(value_weights),
                )
            )

        return sorted(results, key=lambda item: (-item.score, ATTRIBUTES.index(item.attribute)))

    def choose(
        self,
        state: SessionState,
        ranked: Sequence[RankedCandidate],
    ) -> str | None:
        scores = self.score(state, ranked)
        state.question_scores = {
            item.attribute: round(item.score, 6)
            for item in scores
        }
        return scores[0].attribute if scores else None
