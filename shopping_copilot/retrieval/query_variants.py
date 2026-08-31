from __future__ import annotations

from dataclasses import dataclass

from shopping_copilot.catalog.constraints import normalized_tokens
from shopping_copilot.core.contracts import SearchPlan


@dataclass(frozen=True)
class LexicalQueryVariant:
    """One deterministic, transient SQLite FTS5 query route."""

    name: str
    expression: str
    weight: float = 1.0


SLOT_FIELDS: dict[str, tuple[str, ...]] = {
    "category": ("categories",),
    "brand": ("store", "title"),
    "material": ("title", "features", "details", "description"),
    "color": ("title", "features", "details", "description"),
    "size": ("title", "features", "details", "description"),
    "style": ("title", "features", "details", "description"),
    "feature": ("title", "features", "details", "description"),
    "use_case": ("title", "features", "description", "categories"),
    "other": (),
}


def _fts_phrase(value: str) -> str:
    tokens = normalized_tokens(value)
    return f'"{" ".join(tokens)}"' if tokens else ""


def _value_group(values: tuple[str, ...], fields: tuple[str, ...] = ()) -> str:
    clauses: list[str] = []
    field_prefix = f'{{{" ".join(fields)}}}: ' if fields else ""
    for value in values:
        phrase = _fts_phrase(value)
        if phrase:
            clauses.append(f"{field_prefix}{phrase}")
    clauses = list(dict.fromkeys(clauses))
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


class LexicalQueryVariantBuilder:
    """Build complementary strict and fallback FTS5 queries from a SearchPlan."""

    def __init__(self, max_variants: int = 5) -> None:
        self.max_variants = max(1, min(5, max_variants))

    def build(self, plan: SearchPlan) -> tuple[LexicalQueryVariant, ...]:
        terms = tuple(dict.fromkeys(term for term in plan.lexical_terms if normalized_tokens(term)))
        broad = " OR ".join(_fts_phrase(term) for term in terms)
        if not broad:
            return ()

        variants: list[LexicalQueryVariant] = []
        if len(terms) >= 2:
            variants.append(LexicalQueryVariant(
                "strict_all_tokens",
                " AND ".join(_fts_phrase(term) for term in terms),
            ))

        slot_groups = [
            (attribute, _value_group(tuple(values)))
            for attribute, values in plan.structured_constraints.items()
            if attribute != "budget" and values
        ]
        slot_groups = [(attribute, group) for attribute, group in slot_groups if group]
        if len(slot_groups) >= 2:
            variants.append(LexicalQueryVariant(
                "slot_phrase_and",
                " AND ".join(group for _, group in slot_groups),
            ))

        fielded_groups = [
            _value_group(
                tuple(plan.structured_constraints[attribute]),
                SLOT_FIELDS.get(attribute, ()),
            )
            for attribute, _ in slot_groups
        ]
        fielded_groups = [group for group in fielded_groups if group]
        if fielded_groups:
            variants.append(LexicalQueryVariant(
                "fielded_slot_and",
                " AND ".join(fielded_groups),
            ))

        category_values = tuple(plan.structured_constraints.get("category", ()))
        category_group = _value_group(category_values, SLOT_FIELDS["category"])
        category_tokens = {
            token
            for value in category_values
            for token in normalized_tokens(value)
        }
        other_terms = tuple(term for term in terms if term not in category_tokens)
        if category_group and other_terms:
            variants.append(LexicalQueryVariant(
                "category_anchor",
                f"{category_group} AND (" + " OR ".join(_fts_phrase(term) for term in other_terms) + ")",
            ))

        variants.append(LexicalQueryVariant("broad_or", broad))

        unique: list[LexicalQueryVariant] = []
        seen: set[str] = set()
        for variant in variants:
            if variant.expression in seen:
                continue
            seen.add(variant.expression)
            unique.append(variant)

        if len(unique) <= self.max_variants:
            return tuple(unique)
        broad_variant = unique[-1]
        return tuple([*unique[: self.max_variants - 1], broad_variant])
