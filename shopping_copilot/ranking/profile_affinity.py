from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Product


# Public profiles describe aspects that mattered in prior purchases, not exact
# preferred values.  Each aspect therefore maps to catalog language that gives
# evidence that a product addresses that aspect.
PROFILE_ASPECT_TERMS: dict[str, tuple[str, ...]] = {
    "fit": (
        "fit", "fits", "fitting", "size", "sizing", "sized", "adjustable",
        "stretch", "stretchy", "elastic", "width", "wide", "narrow", "slim",
        "regular fit",
    ),
    "material": (
        "material", "fabric", "cotton", "polyester", "leather", "wool",
        "nylon", "spandex", "silk", "rayon", "linen", "denim", "suede", "mesh",
    ),
    "comfort": (
        "comfort", "comfortable", "comfortably", "cushion", "cushioned",
        "cushioning", "soft", "breathable", "ergonomic", "lightweight",
        "padded", "padding",
    ),
    "style": (
        "style", "stylish", "fashion", "fashionable", "design", "classic",
        "casual", "formal", "modern", "vintage", "sporty", "elegant",
    ),
    "durability": (
        "durable", "durability", "sturdy", "rugged", "reinforced", "strong",
        "long lasting", "heavy duty",
    ),
    "performance": (
        "performance", "moisture wicking", "quick dry", "support", "supportive",
        "traction", "waterproof", "non slip", "breathable",
    ),
    "warmth": (
        "warm", "warmth", "thermal", "insulated", "insulation", "fleece", "winter",
    ),
    "weather": (
        "weather", "waterproof", "water resistant", "windproof", "rain", "snow",
        "uv", "sun protection",
    ),
}


@lru_cache(maxsize=128)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    if not words:
        return re.compile(r"(?!)")
    body = r"[^a-z0-9]+".join(re.escape(word) for word in words)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _aspect_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Compile one alternation per aspect to keep catalog scans inexpensive."""

    bodies: list[str] = []
    for term in terms:
        words = re.findall(r"[a-z0-9]+", term.lower())
        if words:
            bodies.append(r"[^a-z0-9]+".join(re.escape(word) for word in words))
    alternatives = "|".join(f"(?:{body})" for body in bodies)
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


ASPECT_PATTERNS = {
    aspect: _aspect_pattern(terms)
    for aspect, terms in PROFILE_ASPECT_TERMS.items()
}

DEFAULT_STATS_PATH = Path(__file__).with_name("profile_affinity_catalog_stats.json")


class CatalogProfileAffinityScorer:
    """Scores profile-aspect evidence using inverse catalog prevalence.

    A profile tag such as ``durability`` means that the user historically cares
    about that aspect.  It does not reveal a specific desired value.  Products
    receive credit when their catalog text contains evidence for the aspect.
    Less common aspects receive more weight so generic signals do not dominate.
    """

    def __init__(self, store: CatalogStore) -> None:
        product_count = len(store.products)
        cached_stats = self._load_cached_stats(product_count)
        if cached_stats is not None:
            self.catalog_coverage, self.aspect_weights = cached_stats
            return

        document_frequencies = {aspect: 0 for aspect in ASPECT_PATTERNS}
        for product in store.products:
            text = product.searchable_text.lower()
            for aspect, pattern in ASPECT_PATTERNS.items():
                if pattern.search(text):
                    document_frequencies[aspect] += 1

        self.catalog_coverage = {
            aspect: frequency / product_count if product_count else 0.0
            for aspect, frequency in document_frequencies.items()
        }
        self.aspect_weights = {
            aspect: math.log((product_count + 1.0) / (frequency + 1.0))
            for aspect, frequency in document_frequencies.items()
        }

    @staticmethod
    def _load_cached_stats(
        product_count: int,
    ) -> tuple[dict[str, float], dict[str, float]] | None:
        """Use catalog-only offline statistics when they match the loaded catalog."""

        try:
            payload = json.loads(DEFAULT_STATS_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if int(payload.get("catalog_product_count", -1)) != product_count:
            return None

        aspects = payload.get("aspects", {})
        if not all(aspect in aspects for aspect in ASPECT_PATTERNS):
            return None
        coverage = {
            aspect: float(aspects[aspect]["catalog_coverage"])
            for aspect in ASPECT_PATTERNS
        }
        weights = {
            aspect: float(aspects[aspect]["inverse_prevalence_weight"])
            for aspect in ASPECT_PATTERNS
        }
        return coverage, weights

    def score(self, profile_terms: tuple[str, ...], product: Product) -> float:
        terms = tuple(dict.fromkeys(term.strip().lower() for term in profile_terms if term.strip()))
        if not terms:
            return 0.0

        text = product.searchable_text.lower()
        matched_weight = 0.0
        total_weight = 0.0
        for term in terms:
            pattern = ASPECT_PATTERNS.get(term)
            if pattern is None:
                pattern = _phrase_pattern(term)
                weight = 1.0
            else:
                weight = self.aspect_weights[term]
            total_weight += weight
            if pattern.search(text):
                matched_weight += weight

        return matched_weight / total_weight if total_weight > 0.0 else 0.0
