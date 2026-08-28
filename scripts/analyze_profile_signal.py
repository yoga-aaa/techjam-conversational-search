from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.ranking.profile_affinity import (
    ASPECT_PATTERNS,
    CatalogProfileAffinityScorer,
)


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator if denominator else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure which public profile fields carry product signal"
    )
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    args = parser.parse_args()

    samples = [
        json.loads(line)
        for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    store = CatalogStore(args.catalog)
    affinity = CatalogProfileAffinityScorer(store)

    tag_counts: Counter[str] = Counter()
    target_matches: Counter[str] = Counter()
    purchase_frequencies: Counter[str] = Counter()
    profile_ratings: list[float] = []
    target_ratings: list[float] = []
    ratings_by_style: defaultdict[str, list[float]] = defaultdict(list)

    for sample in samples:
        profile = sample.get("user_profile", {})
        purchase_frequencies[str(profile.get("purchase_frequency", ""))] += 1
        target = store.get(str(sample.get("ground_truth", {}).get("parent_asin", "")))
        if target is None:
            continue
        text = target.searchable_text.lower()
        for raw_tag in profile.get("preference_tags", []):
            tag = str(raw_tag).strip().lower()
            tag_counts[tag] += 1
            pattern = ASPECT_PATTERNS.get(tag)
            if pattern is not None and pattern.search(text):
                target_matches[tag] += 1

        prior_rating = profile.get("average_prior_rating")
        if prior_rating is not None and target.average_rating is not None:
            profile_ratings.append(float(prior_rating))
            target_ratings.append(target.average_rating)
            ratings_by_style[str(profile.get("rating_style", ""))].append(
                target.average_rating
            )

    aspect_signal = {}
    for tag, count in tag_counts.items():
        target_coverage = target_matches[tag] / count
        catalog_coverage = affinity.catalog_coverage.get(tag)
        aspect_signal[tag] = {
            "profile_count": count,
            "target_coverage": target_coverage,
            "catalog_coverage": catalog_coverage,
            "target_to_catalog_lift": (
                target_coverage / catalog_coverage
                if catalog_coverage
                else None
            ),
        }

    report = {
        "sample_count": len(samples),
        "purchase_frequency_distribution": dict(purchase_frequencies),
        "profile_aspect_signal": aspect_signal,
        "prior_to_target_rating_pearson": _pearson(profile_ratings, target_ratings),
        "target_rating_mean_by_profile_style": {
            style: sum(values) / len(values)
            for style, values in ratings_by_style.items()
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
