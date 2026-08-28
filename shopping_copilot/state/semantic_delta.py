from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from shopping_copilot.catalog.constraints import normalized_tokens, normalized_value
from shopping_copilot.core.contracts import SessionState


class OperationKind(str, Enum):
    SET = "set"
    EXCLUDE = "exclude"
    REMOVE = "remove"
    ALLOW = "allow"
    DONTCARE = "dontcare"


@dataclass(frozen=True)
class SlotOperation:
    kind: OperationKind
    slot: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnDelta:
    operations: tuple[SlotOperation, ...] = ()
    positive_context: tuple[str, ...] = ()


MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
    "rayon", "fabric", "synthetic",
)
COLORS = (
    "black", "white", "blue", "red", "pink", "green", "brown", "gray",
    "grey", "purple", "yellow", "orange", "navy", "beige", "teal", "gold",
    "silver",
)
USE_CASES = (
    "hiking", "running", "gym", "winter", "outdoor", "work", "wedding",
    "walking", "travel", "sports",
)
FEATURES = (
    "waterproof", "breathable", "comfortable", "durable", "lightweight", "warm",
    "insulated", "non-slip",
)
STYLES = ("casual", "formal", "vintage", "classic", "modern", "sporty")
SLOT_NAMES = {
    "color", "material", "size", "style", "brand", "budget", "feature",
    "use_case", "category", "other",
}
SLOT_ALIASES = {
    "use case": "use_case",
    "use_case": "use_case",
    "price": "budget",
    "price range": "budget",
}

_SLOT_LABEL = r"(?:color|material|size|style|brand|budget|feature|use(?:\s|_)*case|category)"
_SLOT_LABEL_RE = re.compile(rf"\b(?P<slot>{_SLOT_LABEL})\b", re.IGNORECASE)
_POSITIVE_CUE_RE = re.compile(
    r"\b(?:what\s+(?:i\s+)?need\s+is|what\s+matters\s+is|"
    r"a\s+key\s+requirement\s+is|want|prefer|need|make\s+it|switch\s+to)\b",
    re.IGNORECASE,
)
_CONTROL_TOKENS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do",
    "does", "for", "from", "i", "if", "in", "is", "it", "make", "me", "my",
    "of", "on", "or", "please", "rather", "switch", "that", "the", "this",
    "to", "use", "want", "what", "with", "would", "you", "looking",
    "actually", "instead", "after", "all", "ignore", "earlier",
    "preference", "prefer", "need", "matters", "key", "requirement", "fine",
    "okay", "include", "have", "sure", "about", "only", "also", "not", "no",
    "am", "m", "don", "doesn", "dont", "doesnt", "color", "material", "size", "style", "brand", "budget", "feature",
    "category", "case", "price",
    "under", "below", "less", "maximum", "max", "most", "around",
    "longer", "drop", "remove", "forget", "avoid", "exclude", "without", "anything",
    "please", "additional", "judgment", "your", "necessarily", "too",
}


def _canonical_slot(slot: str | None) -> str | None:
    if not slot:
        return None
    lowered = " ".join(normalized_tokens(slot))
    canonical = SLOT_ALIASES.get(lowered, lowered.replace(" ", "_"))
    return canonical if canonical in SLOT_NAMES else (canonical or None)


def _explicit_slot(text: str) -> str | None:
    match = _SLOT_LABEL_RE.search(text)
    if not match:
        return None
    return _canonical_slot(match.group("slot"))


def _split_clauses(text: str) -> list[str]:
    """Split contrastive clauses without breaking protected constructions."""

    boundaries: set[int] = set()
    for match in re.finditer(r"[.;]", text):
        if match.group() == ".":
            if match.start() > 0 and match.end() < len(text):
                if text[match.start() - 1].isdigit() and text[match.end()].isdigit():
                    continue
                prior_boundaries = [boundary for boundary in boundaries if boundary < match.start()]
                clause_start = max(prior_boundaries, default=-1) + 1
                left = text[clause_start:match.start()].strip()
                right = text[match.end():].lstrip()
                if _POSITIVE_CUE_RE.search(left) and not _looks_like_clause_start(right):
                    continue
        if match.group() == ";":
            prior_boundaries = [boundary for boundary in boundaries if boundary < match.start()]
            clause_start = max(prior_boundaries, default=-1) + 1
            left = text[clause_start:match.start()].strip()
            right = text[match.end():].lstrip()
            if _POSITIVE_CUE_RE.search(left) and not _looks_like_clause_start(right):
                continue
        boundaries.add(match.start())
    for match in re.finditer(r",", text):
        remainder = text[match.end():].lstrip()
        prefix = text[:match.start()].strip()
        if _looks_like_clause_start(remainder) and not re.search(r"\bfor\s+that$", prefix, re.IGNORECASE):
            boundaries.add(match.start())
    for match in re.finditer(r"\b(?:but|however)\b", text):
        prefix = text[:match.start()]
        suffix = text[match.end():]
        if re.match(r"\s*(?:i['’]?m\s+)?(?:still\s+exploring|just\s+browsing|open\s+to)\b", suffix, re.IGNORECASE):
            continue
        if re.search(r"\banything\s*$", prefix):
            continue
        if re.search(r"\bnot\s+only\b", prefix) and re.search(r"\balso\b", suffix):
            continue
        boundaries.add(match.start())
    for match in re.finditer(r"\band\s+(?=i\b)", text):
        boundaries.add(match.start())

    clauses: list[str] = []
    start = 0
    for boundary in sorted(boundaries):
        clause = text[start:boundary].strip(" .;,")
        if clause:
            clauses.append(clause)
        delimiter = re.match(r"[.;,]|\b(?:but|however)\b|\band\s+(?=i\b)", text[boundary:])
        start = boundary + (delimiter.end() if delimiter else 1)
    tail = text[start:].strip(" .;,")
    if tail:
        clauses.append(tail)
    return clauses or ([text.strip(" .;,")] if text.strip(" .;,") else [])


def _looks_like_clause_start(text: str) -> bool:
    lowered = text.casefold().lstrip()
    if not lowered:
        return False
    return bool(re.match(
        r"(?:i\b|actually\b|please\b|avoid\b|exclude\b|without\b|anything\s+but\b|"
        r"(?:do\s+not|don't)\b|(?:no\s+longer)\b|drop\b|remove\b|forget\b|"
        r"want\b|prefer\b|need\b|make\s+it\b|switch\s+to\b|what\b|a\s+key\b|"
        r"color\b|material\b|size\b|style\b|brand\b|budget\b|feature\b|"
        r"category\b|either\b|any\b|no\s+preference\b|use\s+your\b)",
        lowered,
    ))


def _clean_value(value: str) -> str:
    value = value.casefold().strip(" \t\r\n.,;:!?()[]{}\"'")
    value = re.sub(r"^(?:please|also|actually|that|is|a|an|the|for\s+that)\s+", "", value)
    value = re.sub(r"\s+(?:instead|rather\s+than)(?:\s+of)?\s+.+$", "", value)
    value = re.sub(r"\s+(?:instead|after\s+all|as\s+(?:a\s+)?requirement)\s*$", "", value)
    value = re.sub(r"\s+(?:as|for)\s+(?:the\s+)?(?:color|material|size|style|brand|budget|feature|use(?:\s|_)*case|category)\s*$", "", value)
    value = re.sub(r"\s+(?:color|material|size|style|brand|budget|feature|use(?:\s|_)*case|category)\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip(" \t\r\n.,;:!?()[]{}\"'")


def _state_value(value: str) -> str:
    """Keep a cleaned phrase for state while comparisons use normalized tokens."""

    return re.sub(r"\s+", " ", str(value).casefold()).strip(" \t\r\n")


def _unique_state_values(values: tuple[str, ...] | list[str]) -> list[str]:
    """Keep the first cleaned phrase for each normalized value."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _state_value(value)
        key = normalized_value(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _split_values(value: str) -> tuple[str, ...]:
    value = re.sub(r"\s*/\s*", " or ", value)
    parts = re.split(r"\s*(?:[,;]|\bor\b|\band\b)\s*", value, flags=re.IGNORECASE)
    cleaned = tuple(item for item in (_clean_value(part) for part in parts) if item)
    return tuple(dict.fromkeys(cleaned))


def _seed_slot(value: str) -> str | None:
    tokens = set(normalized_tokens(value))
    if tokens & set(MATERIALS):
        return "material"
    if tokens & set(COLORS):
        return "color"
    if tokens & set(USE_CASES):
        return "use_case"
    if tokens & set(FEATURES):
        return "feature"
    if tokens & set(STYLES):
        return "style"
    return None


def _matches_existing(
    value: str,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
) -> str | None:
    key = normalized_value(value)
    for slots in (active_slots, negative_slots):
        for slot, values in slots.items():
            if any(normalized_value(existing) == key for existing in values):
                return _canonical_slot(slot) or slot
    return None


def _infer_slot(
    value: str,
    clause: str,
    pending_attribute: str | None,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
    explicit_slot: str | None = None,
) -> str:
    explicit = _canonical_slot(explicit_slot)
    if explicit:
        return explicit
    pending = _canonical_slot(pending_attribute)
    if pending:
        return pending
    existing = _matches_existing(value, active_slots, negative_slots)
    if existing:
        return existing
    seeded = _seed_slot(value)
    if seeded:
        return seeded
    lowered = clause.casefold()
    if re.search(rf"\bfor\s+{re.escape(value)}\b", lowered):
        return "use_case"
    if re.search(r"\b(?:under|below|less\s+than|at\s+most|budget|price)\b", lowered):
        return "budget"
    if re.search(r"\b(?:size|sizing|width|wide|narrow|fit)\b", lowered):
        return "size"
    if re.search(r"\b(?:key\s+requirement|what\s+(?:i\s+)?need\s+is|what\s+matters\s+is)\b", lowered):
        return "feature"
    return "other"


def _value_with_context(raw: str, explicit_slot: str | None, pending_attribute: str | None) -> tuple[str, str]:
    """Separate a known slot value from product nouns such as ``shoes``."""

    value = raw.strip()
    residual = ""
    with_match = re.search(r"\bwith\s+(.+)$", value, re.IGNORECASE)
    if with_match and not (explicit_slot or pending_attribute):
        residual = value[:with_match.start()].strip()
        value = with_match.group(1)
    for_match = re.search(r"\bfor\s+([a-z0-9][\w -]*)$", value, re.IGNORECASE)
    if for_match and not (explicit_slot or pending_attribute):
        candidate = _clean_value(for_match.group(1))
        if _seed_slot(candidate) == "use_case":
            residual = " ".join(part for part in (residual, value[:for_match.start()].strip()) if part)
            value = candidate

    if not explicit_slot and not pending_attribute:
        tokens = list(normalized_tokens(value))
        known = set(MATERIALS) | set(COLORS) | set(USE_CASES) | set(FEATURES) | set(STYLES)
        known_indexes = [index for index, token in enumerate(tokens) if token in known]
        if len(known_indexes) == 1 and known_indexes[0] == 0 and len(tokens) > 1:
            residual = " ".join(part for part in (residual, " ".join(tokens[1:])) if part)
            value = tokens[0]
    return value, residual


def _typed_values(
    raw: str,
    clause: str,
    pending_attribute: str | None,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
    explicit_slot: str | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    raw = re.sub(r"^\s*(?:please|also|actually)\s*[,;:]?\s*", "", raw, flags=re.IGNORECASE)
    if explicit_slot and re.search(r"\bwith\s+", raw, re.IGNORECASE):
        raw = re.split(r"\bwith\s+", raw, maxsplit=1, flags=re.IGNORECASE)[-1]
    if explicit_slot:
        raw = re.sub(rf"^\s*{_SLOT_LABEL}\s*[:=-]?\s*", "", raw, count=1, flags=re.IGNORECASE)
    value, _ = _value_with_context(raw, explicit_slot, pending_attribute)
    parts = _split_values(value)
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for part in parts:
        cleaned = _clean_value(part)
        if not cleaned or cleaned in SLOT_NAMES:
            continue
        slot = _infer_slot(
            cleaned,
            clause,
            pending_attribute,
            active_slots,
            negative_slots,
            explicit_slot,
        )
        if slot not in grouped:
            grouped[slot] = []
            order.append(slot)
        if cleaned not in grouped[slot]:
            grouped[slot].append(cleaned)
    return tuple((slot, tuple(grouped[slot])) for slot in order)


def _operation(kind: OperationKind, typed: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[SlotOperation, ...]:
    return tuple(SlotOperation(kind, slot, values) for slot, values in typed if slot)


def _strip_cue_words(text: str) -> str:
    text = re.sub(
        r"\b(?:i|you|we|please|actually|also|what|matters|is|a|key|requirement|"
        r"want|prefer|need|make|it|switch|to|looking|for|with|that|after|all|"
        r"instead|rather|than)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(normalized_tokens(text))


def _positive_context(clause: str, operations: tuple[SlotOperation, ...]) -> tuple[str, ...]:
    if not any(operation.kind is OperationKind.SET for operation in operations):
        return ()
    clause = re.split(r"\s+(?:instead|rather\s+than)(?:\s+of)?\b", clause, maxsplit=1, flags=re.IGNORECASE)[0]
    tokens = list(normalized_tokens(_strip_cue_words(clause)))
    for operation in operations:
        if operation.kind is not OperationKind.SET:
            continue
        for value in operation.values:
            value_tokens = list(normalized_tokens(value))
            if not value_tokens:
                continue
            index = 0
            while index <= len(tokens) - len(value_tokens):
                if tokens[index:index + len(value_tokens)] == value_tokens:
                    del tokens[index:index + len(value_tokens)]
                else:
                    index += 1
    residual = " ".join(token for token in tokens if token not in _CONTROL_TOKENS)
    if residual:
        return (residual,)
    fallback = tuple(
        value
        for operation in operations
        if operation.kind is OperationKind.SET
        for value in operation.values
        if value
    )
    return fallback


def _boundary_operation(
    clause: str,
    pending_attribute: str | None,
) -> SlotOperation | None:
    lowered = clause.casefold()
    explicit_match = re.search(
        rf"\b(?P<slot>{_SLOT_LABEL})\s+(?:doesn['’]?t|does\s+not)\s+matter\b",
        lowered,
    )
    if explicit_match:
        return SlotOperation(OperationKind.DONTCARE, _canonical_slot(explicit_match.group("slot")) or "other")
    explicit_match = re.search(
        rf"\bany\s+(?P<slot>{_SLOT_LABEL})\s+is\s+fine\b",
        lowered,
    )
    if explicit_match:
        return SlotOperation(OperationKind.DONTCARE, _canonical_slot(explicit_match.group("slot")) or "other")
    explicit_match = re.search(
        rf"\bno\s+(?:particular\s+)?preference\s+for\s+(?P<slot>{_SLOT_LABEL})\b",
        lowered,
    )
    if explicit_match:
        return SlotOperation(OperationKind.DONTCARE, _canonical_slot(explicit_match.group("slot")) or "other")
    explicit_match = re.search(
        rf"\b(?:do\s+not|don['’]?t)\s+have\s+(?:an\s+additional\s+|a\s+)?preference(?:\s+for\s+(?P<slot>{_SLOT_LABEL}))?\b",
        lowered,
    )
    if explicit_match:
        slot = _canonical_slot(explicit_match.group("slot")) if explicit_match.group("slot") else _canonical_slot(pending_attribute)
        return SlotOperation(OperationKind.DONTCARE, slot) if slot else None
    if re.search(r"\bno\s+(?:particular\s+)?preference\b", lowered):
        slot = _canonical_slot(pending_attribute)
        return SlotOperation(OperationKind.DONTCARE, slot) if slot else None
    if re.search(r"\bdoes(?:n['’]?t|\s+not)\s+matter\b", lowered):
        slot = _canonical_slot(pending_attribute)
        return SlotOperation(OperationKind.DONTCARE, slot) if slot else None
    if re.search(r"\b(?:either|any)\s+is\s+fine\b|\buse\s+your\s+judgment\b", lowered):
        slot = _canonical_slot(pending_attribute)
        return SlotOperation(OperationKind.DONTCARE, slot) if slot else None
    return None


def _is_non_exclusion_negation(clause: str) -> bool:
    return bool(re.search(
        r"\bnot\s+(?:only|sure|necessarily|too|really|quite|just|have|matter)\b|"
        r"\bwithout\s+(?:sacrificing|compromising|compromise)\b",
        clause,
        flags=re.IGNORECASE,
    ))


def _is_incomplete_negative_value(value: str) -> bool:
    tokens = normalized_tokens(value)
    return bool(tokens and tokens[-1] in {"a", "an", "the", "of", "to", "in", "on", "for", "with"})


def _parse_clause(
    clause: str,
    pending_attribute: str | None,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
) -> tuple[tuple[SlotOperation, ...], tuple[str, ...]]:
    boundary = _boundary_operation(clause, pending_attribute)
    if boundary:
        return (boundary,), ()

    explicit_slot = _explicit_slot(clause)
    patterns: tuple[tuple[OperationKind, str], ...] = (
        (OperationKind.ALLOW, r"\bi\s+don['’]?t\s+mind\s+(.+)$"),
        (OperationKind.ALLOW, r"\byou\s+can\s+include\s+(.+)$"),
        (OperationKind.ALLOW, r"^(.+?)\s+is\s+(?:fine|okay)\s+after\s+all$"),
        (OperationKind.REMOVE, r"\bno\s+longer\s+(?:need|want)\s+(.+)$"),
        (OperationKind.REMOVE, r"\bdrop\s+(.+)$"),
        (OperationKind.REMOVE, r"\bremove\s+(.+?)\s+as\s+(?:a\s+)?requirement$"),
        (OperationKind.REMOVE, r"\bforget\s+(?:the\s+)?(.+?)\s+preference$"),
        (OperationKind.EXCLUDE, r"\b(?:do\s+not|don['’]?t)\s+want\s+(.+)$"),
        (OperationKind.EXCLUDE, r"\bavoid\s+(.+)$"),
        (OperationKind.EXCLUDE, r"\bexclude\s+(.+)$"),
        (OperationKind.EXCLUDE, r"\bwithout\s+(.+)$"),
        (OperationKind.EXCLUDE, r"\banything\s+but\s+(.+)$"),
        (OperationKind.EXCLUDE, r"\bnot\s+(.+)$"),
        (OperationKind.SET, r"\b(?:for\s+that,?\s+)?what\s+(?:i\s+)?need\s+is\s*:?\s*(.+)$"),
        (OperationKind.SET, r"\bwhat\s+matters\s+is\s*:?\s*(.+)$"),
        (OperationKind.SET, r"\ba\s+key\s+requirement\s+is\s*:?\s*(.+)$"),
        (OperationKind.SET, r"\b(?:want|prefer|need)\s+(.+)$"),
        (OperationKind.SET, r"\bmake\s+it\s+(.+)$"),
        (OperationKind.SET, r"\bswitch\s+to\s+(.+)$"),
    )

    for kind, pattern in patterns:
        match = re.search(pattern, clause, re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1)
        if kind is OperationKind.EXCLUDE:
            if _is_non_exclusion_negation(clause) or _is_incomplete_negative_value(raw):
                if _POSITIVE_CUE_RE.search(clause):
                    continue
                return (), ()
        if kind is OperationKind.REMOVE and _clean_value(raw) in SLOT_NAMES:
            typed = ((explicit_slot or _clean_value(raw), ()),)
        else:
            typed = _typed_values(
                raw,
                clause,
                pending_attribute,
                active_slots,
                negative_slots,
                explicit_slot,
            )
        operations = _operation(kind, typed)
        return operations, _positive_context(clause, operations)

    suffix_match = re.search(r"^(.+?)\s+(?:instead|rather\s+than)\s*$", clause, re.IGNORECASE)
    if suffix_match:
        typed = _typed_values(
            suffix_match.group(1),
            clause,
            pending_attribute,
            active_slots,
            negative_slots,
            explicit_slot,
        )
        operations = _operation(OperationKind.SET, typed)
        return operations, _positive_context(clause, operations)

    return _parse_implicit_clause(clause, pending_attribute, active_slots, negative_slots)


def _parse_implicit_clause(
    clause: str,
    pending_attribute: str | None,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
) -> tuple[tuple[SlotOperation, ...], tuple[str, ...]]:
    if _is_non_exclusion_negation(clause):
        return (), ()

    operations: list[SlotOperation] = []
    category_match = re.search(
        r"\b(?:looking|shopping)\s+for\s+(.+?)(?=\s+(?:with|under|below|but|however)\b|,|$)",
        clause,
        re.IGNORECASE,
    )
    if category_match:
        category = _clean_value(category_match.group(1))
        if category:
            operations.append(SlotOperation(OperationKind.SET, "category", (category,)))

    budget_match = re.search(
        r"\b(?:under|below|less\s+than|max(?:imum)?|budget(?:\s+around)?|at\s+most)\s*\$?\s*(\d+(?:\.\d+)?)",
        clause,
        re.IGNORECASE,
    )
    if budget_match:
        operations.append(SlotOperation(OperationKind.SET, "budget", (budget_match.group(1),)))

    size_match = re.search(r"\bsize\s+([a-z0-9.-]+)", clause, re.IGNORECASE)
    if size_match:
        operations.append(SlotOperation(OperationKind.SET, "size", (_clean_value(size_match.group(1)),)))

    attribute_value = re.search(
        rf"\bwith\s+(?P<value>[a-z0-9][\w -]*?)\s+(?P<slot>{_SLOT_LABEL})\b",
        clause,
        re.IGNORECASE,
    )
    if not attribute_value:
        attribute_value = re.search(
            rf"(?P<value>\b(?:[a-z0-9-]+\s+){{0,3}}[a-z0-9-]+)\s+(?P<slot>{_SLOT_LABEL})\b",
            clause,
            re.IGNORECASE,
        )
    if attribute_value:
        value = _clean_value(attribute_value.group("value").replace("with", " "))
        slot = _canonical_slot(attribute_value.group("slot"))
        if value and slot and value not in SLOT_NAMES:
            operations.append(SlotOperation(OperationKind.SET, slot, (value,)))

    leading_attribute = re.search(
        rf"\b(?P<slot>{_SLOT_LABEL})\s*(?:is|:)?\s*(?P<value>[a-z0-9][\w -]+)$",
        clause,
        re.IGNORECASE,
    )
    if leading_attribute:
        value = _clean_value(leading_attribute.group("value"))
        slot = _canonical_slot(leading_attribute.group("slot"))
        if value and slot and value not in SLOT_NAMES:
            operations.append(SlotOperation(OperationKind.SET, slot, (value,)))

    # A short answer to a question is an open value span, including OOV values.
    plain = _clean_value(clause)
    plain_tokens = normalized_tokens(clause)
    if (
        not operations
        and _canonical_slot(pending_attribute)
        and plain
        and len(plain_tokens) <= 8
        and not re.search(r"\b(?:i|we|you)\b|\b(?:not|sure|looking|exploring)\b", clause, re.IGNORECASE)
    ):
        operations.append(SlotOperation(OperationKind.SET, _canonical_slot(pending_attribute) or "other", _split_values(plain)))

    # Stable seed values preserve the pre-existing lightweight parser behavior.
    known = (
        (MATERIALS, "material"),
        (COLORS, "color"),
        (USE_CASES, "use_case"),
        (FEATURES, "feature"),
        (STYLES, "style"),
    )
    for vocabulary, slot in known:
        values = tuple(
            token
            for token in normalized_tokens(clause)
            if token in vocabulary
        )
        if values and not any(operation.slot == slot for operation in operations):
            operations.append(SlotOperation(OperationKind.SET, slot, tuple(dict.fromkeys(values))))

    if not operations:
        return (), ()
    operation_tuple = tuple(operations)
    return operation_tuple, _positive_context(clause, operation_tuple)


def parse_turn(
    user_message: str,
    pending_attribute: str | None,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
) -> TurnDelta:
    """Parse one turn into ordered, deterministic state operations."""

    operations: list[SlotOperation] = []
    positive_context: list[str] = []
    for clause in _split_clauses(user_message.casefold()):
        clause_operations, clause_context = _parse_clause(
            clause,
            pending_attribute,
            active_slots,
            negative_slots,
        )
        operations.extend(clause_operations)
        positive_context.extend(clause_context)
    return TurnDelta(tuple(operations), tuple(dict.fromkeys(item for item in positive_context if item)))


def _slot_map_signature(slots: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        sorted(
            (
                _canonical_slot(slot) or slot,
                tuple(sorted({normalized_value(value) for value in values if normalized_value(value)})),
            )
            for slot, values in slots.items()
            if values
        )
    )


def _canonical_state_signature(state: SessionState) -> tuple[object, ...]:
    return (
        _slot_map_signature(state.active_slots),
        _slot_map_signature(state.negative_slots),
        tuple(sorted(_canonical_slot(item) or item for item in state.no_preference_attributes)),
    )


def _normalize_slot_map(slots: dict[str, list[str]]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for raw_slot, values in slots.items():
        slot = _canonical_slot(raw_slot) or raw_slot
        cleaned = _unique_state_values(values)
        if cleaned:
            normalized[slot] = cleaned
    slots.clear()
    slots.update(normalized)
    return slots


def _remove_values(values: list[str], removals: set[str]) -> list[str]:
    return [value for value in values if normalized_value(value) not in removals]


def apply_operations(state: SessionState, delta: TurnDelta) -> bool:
    """Apply the ordered delta to canonical preference fields."""

    before = _canonical_state_signature(state)
    _normalize_slot_map(state.active_slots)
    _normalize_slot_map(state.negative_slots)
    state.no_preference_attributes = {
        _canonical_slot(attribute) or attribute
        for attribute in state.no_preference_attributes
    }

    for operation in delta.operations:
        slot = _canonical_slot(operation.slot) or operation.slot
        values = tuple(_unique_state_values(operation.values))
        if operation.kind is OperationKind.SET:
            if not values:
                continue
            state.active_slots[slot] = list(values)
            state.negative_slots[slot] = _remove_values(state.negative_slots.get(slot, []), set(values))
            if not state.negative_slots[slot]:
                state.negative_slots.pop(slot, None)
            state.no_preference_attributes.discard(slot)
        elif operation.kind is OperationKind.EXCLUDE:
            if not values:
                continue
            state.negative_slots[slot] = _unique_state_values(
                [*state.negative_slots.get(slot, []), *values]
            )
            state.active_slots[slot] = _remove_values(state.active_slots.get(slot, []), set(values))
            if not state.active_slots[slot]:
                state.active_slots.pop(slot, None)
            state.no_preference_attributes.discard(slot)
        elif operation.kind is OperationKind.REMOVE:
            if values:
                state.active_slots[slot] = _remove_values(state.active_slots.get(slot, []), set(values))
            else:
                state.active_slots.pop(slot, None)
            if not state.active_slots.get(slot):
                state.active_slots.pop(slot, None)
        elif operation.kind is OperationKind.ALLOW:
            if values:
                state.negative_slots[slot] = _remove_values(state.negative_slots.get(slot, []), set(values))
            else:
                state.negative_slots.pop(slot, None)
            if not state.negative_slots.get(slot):
                state.negative_slots.pop(slot, None)
        elif operation.kind is OperationKind.DONTCARE:
            state.active_slots.pop(slot, None)
            state.negative_slots.pop(slot, None)
            state.no_preference_attributes.add(slot)

    _normalize_slot_map(state.active_slots)
    _normalize_slot_map(state.negative_slots)
    for slot in set(state.active_slots) & set(state.negative_slots):
        negative = {normalized_value(value) for value in state.negative_slots[slot]}
        state.active_slots[slot] = _remove_values(state.active_slots[slot], negative)
        if not state.active_slots[slot]:
            state.active_slots.pop(slot, None)
    return before != _canonical_state_signature(state)


def sanitize_positive_context(text: str) -> str:
    """Strip conversational control words while retaining product-bearing text."""

    tokens = [token for token in normalized_tokens(text) if token not in _CONTROL_TOKENS]
    return " ".join(tokens)
