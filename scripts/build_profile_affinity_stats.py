from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.ranking.profile_affinity import ASPECT_PATTERNS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build catalog-only prevalence statistics for profile affinity"
    )
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument(
        "--output",
        default="shopping_copilot/ranking/profile_affinity_catalog_stats.json",
    )
    args = parser.parse_args()

    store = CatalogStore(args.catalog)
    product_count = len(store.products)
    frequencies = {aspect: 0 for aspect in ASPECT_PATTERNS}
    for product in store.products:
        text = product.searchable_text.lower()
        for aspect, pattern in ASPECT_PATTERNS.items():
            if pattern.search(text):
                frequencies[aspect] += 1

    payload = {
        "schema_version": 1,
        "source": str(args.catalog),
        "catalog_product_count": product_count,
        "aspects": {
            aspect: {
                "catalog_coverage": frequency / product_count if product_count else 0.0,
                "inverse_prevalence_weight": math.log(
                    (product_count + 1.0) / (frequency + 1.0)
                ),
            }
            for aspect, frequency in frequencies.items()
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Built {output} from {product_count} catalog products")


if __name__ == "__main__":
    main()
