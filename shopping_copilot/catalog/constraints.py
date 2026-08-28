from __future__ import annotations

import re
import unicodedata

from shopping_copilot.core.contracts import Product


# Keep one tokenization rule for planning, state values, and product constraints.
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalized_tokens(text: str) -> tuple[str, ...]:
    """Return case-folded Unicode word tokens with punctuation as boundaries."""

    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return tuple(TOKEN_RE.findall(normalized))


def normalized_value(value: str) -> str:
    """Return the stable, human-readable canonical form used in state and plans."""

    return " ".join(normalized_tokens(value))


def _product_tokens(product: Product, slot: str) -> tuple[str, ...]:
    if slot == "brand":
        text = f"{product.store} {product.title}"
    elif slot == "category":
        text = " ".join(product.categories)
    else:
        text = product.searchable_text
    return normalized_tokens(text)


def _contains_value(tokens: tuple[str, ...], value_tokens: tuple[str, ...]) -> bool:
    if not tokens or not value_tokens:
        return False
    if len(value_tokens) == 1:
        return value_tokens[0] in set(tokens)
    width = len(value_tokens)
    if any(tokens[index:index + width] == value_tokens for index in range(len(tokens) - width + 1)):
        return True
    # Catalog punctuation and field boundaries can split a phrase. In that
    # case, requiring every token still avoids substring false positives.
    token_set = set(tokens)
    return all(token in token_set for token in value_tokens)


def product_matches_value(product: Product, slot: str, value: str) -> bool:
    """Check a slot value with token boundaries and slot-aware product fields."""

    value_tokens = normalized_tokens(value)
    if not value_tokens:
        return False
    return _contains_value(_product_tokens(product, slot), value_tokens)


def violates_negative_slots(
    product: Product,
    negative_slots: dict[str, list[str]],
) -> bool:
    """Return whether a product matches any explicit negative slot value."""

    return any(
        product_matches_value(product, slot, value)
        for slot, values in negative_slots.items()
        for value in values
        if normalized_tokens(value)
    )


def matches_any_excluded_term(product: Product, values: tuple[str, ...]) -> bool:
    """Compatibility projection matcher for SearchPlan.excluded_terms."""

    searchable_tokens = normalized_tokens(product.searchable_text)
    return any(
        _contains_value(searchable_tokens, normalized_tokens(value))
        for value in values
        if normalized_tokens(value)
    )
