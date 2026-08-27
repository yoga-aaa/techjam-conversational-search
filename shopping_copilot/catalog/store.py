from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from shopping_copilot.core.contracts import Product


def _flatten(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        return tuple(
            f"{key} {item}".strip()
            for key, item in value.items()
            if item not in (None, "", [])
        )
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _float_or_none(value: object) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class CatalogStore:
    """Read-only normalized view of the frozen competition catalog."""

    def __init__(self, catalog_path: str | Path) -> None:
        self.catalog_path = Path(catalog_path)
        self._products = tuple(self._load())
        self._by_id = {product.parent_asin: product for product in self._products}

    def _load(self) -> Iterable[Product]:
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                parent_asin = str(payload.get("parent_asin") or "").strip()
                if not parent_asin:
                    raise ValueError(f"Catalog row {line_number} has no parent_asin")

                title = str(payload.get("title") or "").strip()
                categories = _flatten(payload.get("categories"))
                features = _flatten(payload.get("features"))
                details = _flatten(payload.get("details"))
                description = _flatten(payload.get("description"))
                store = str(payload.get("store") or "").strip()
                searchable_text = " ".join(
                    (title, *categories, *features, *details, store, *description)
                ).strip()

                yield Product(
                    parent_asin=parent_asin,
                    title=title,
                    categories=categories,
                    features=features,
                    details=details,
                    description=description,
                    store=store,
                    price=_float_or_none(payload.get("price")),
                    average_rating=_float_or_none(payload.get("average_rating")),
                    rating_number=_int_or_zero(payload.get("rating_number")),
                    searchable_text=searchable_text,
                )

    @property
    def products(self) -> tuple[Product, ...]:
        return self._products

    def contains(self, parent_asin: str) -> bool:
        return parent_asin in self._by_id

    def get(self, parent_asin: str) -> Product | None:
        return self._by_id.get(parent_asin)
