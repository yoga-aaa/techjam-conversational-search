from __future__ import annotations

import math
from collections import Counter

from shopping_copilot.catalog.constraints import normalized_tokens
from shopping_copilot.catalog.store import CatalogStore


class GlobalCatalogIDF:
    """Stable full-catalog token selectivity without public-session labels."""

    def __init__(self, store: CatalogStore) -> None:
        self.document_count = len(store.products)
        self.general_df: Counter[str] = Counter()
        self.category_df: Counter[str] = Counter()
        self.brand_df: Counter[str] = Counter()
        for product in store.products:
            self.general_df.update(set(normalized_tokens(product.searchable_text)))
            self.category_df.update(set(normalized_tokens(" ".join(product.categories))))
            self.brand_df.update(set(normalized_tokens(f"{product.store} {product.title}")))

    def value_idf(self, slot: str, value: str) -> float:
        tokens = tuple(dict.fromkeys(normalized_tokens(value)))
        if not tokens or self.document_count <= 0:
            return 0.0
        if slot == "category":
            frequencies = self.category_df
        elif slot == "brand":
            frequencies = self.brand_df
        else:
            frequencies = self.general_df
        return sum(
            math.log((self.document_count + 1.0) / (frequencies.get(token, 0) + 1.0))
            for token in tokens
        ) / len(tokens)
