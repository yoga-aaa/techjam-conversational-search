from __future__ import annotations

import re
from dataclasses import dataclass, field

from shopping_copilot.core.contracts import SessionState


MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
    "rayon", "fabric", "synthetic", "denim", "suede", "cashmere", "linen",
    "modal", "acrylic", "lace",
)
COLORS = (
    "black", "white", "blue", "red", "pink", "green", "brown", "gray",
    "grey", "purple", "yellow", "orange", "beige", "gold", "silver", "navy",
    "teal",
)
USE_CASES = (
    "hiking", "running", "gym", "winter", "outdoor", "work", "wedding",
    "walking", "travel", "sports", "workout", "athletic", "office", "party",
    "rain", "snow", "camping",
)
FEATURES = (
    "waterproof", "breathable", "comfortable", "durable", "lightweight",
    "warm", "insulated", "non-slip",
)
STYLES = ("casual", "formal", "vintage", "classic", "modern", "sporty")
ALLOWED_CONTEXT_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand", "budget",
    "feature", "use_case", "other",
}

BOUNDARY_RE = re.compile(
    r"(?:no\s+(?:particular\s+)?preference|"
    r"(?:do not|don't) have (?:an additional |a )?preference|"
    r"either\s+is\s+fine|use\s+your\s+judgment|"
    r"does(?:n't| not)\s+(?:really\s+)?matter|any\s+.+?\s+(?:is\s+fine|works)|"
    r"flexible\s+(?:about|on)|no\s+strong\s+opinion|"
    r"(?:is|it's)\s+up\s+to\s+you|choose\s+what\s+fits|"
    r"use\s+your\s+best\s+judgment)",
    re.IGNORECASE,
)
OVERRIDE_RE = re.compile(
    r"(?:\bactually\b|\binstead\b|ignore(?:\s+my\s+earlier)?|"
    r"no\s+longer|changed?\s+my\s+mind|discard\s+the\s+previous|"
    r"on\s+second\s+thought|forget\s+my\s+earlier|"
    r"switch\s+from|replace\s+my\s+earlier)",
    re.IGNORECASE,
)
HARD_CONSTRAINT_RE = re.compile(
    r"(?:(?:a\s+key|one|this)\s+requirement\s+is|"
    r"meet\s+this\s+requirement|what\s+i\s+need(?:\s+now)?\s+is|"
    r"non-negotiable|(?:,|;)\s*(?:and\s+)?i\s+need\s+)",
    re.IGNORECASE,
)
VALUE_MARKER_PATTERNS = (
    re.compile(r"(?:a\s+key|one|this)\s+requirement\s+is\s*:?\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"meet\s+this\s+requirement\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"(?:with\s+)?the\s+following\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"my\s+current\s+preference\s+is\s*:?\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"(?:with\s+)?this\s+preference\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"keep\s+this\s+in\s+mind\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"at\s+the\s+moment,?\s+i\s+prefer\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"my\s+first\s+thought\s+is\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"what\s+i\s+need(?:\s+now)?\s+is\s*:?\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"what\s+matters\s+is\s*:?\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"preference\s+regarding\s+.+?\s+is\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"prioritize\s+this\s+for\s+.+?\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"replace\s+my\s+earlier\s+preference\s+with\s+this\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"discard\s+the\s+previous\s+choice\s+and\s+use\s+(?P<value>.+?)\s+instead", re.IGNORECASE),
    re.compile(r"on\s+second\s+thought,?\s*(?P<value>.+?)\s+is\s+the\s+requirement", re.IGNORECASE),
    re.compile(r"prioritize\s+(?P<value>.+?)\s+from\s+now\s+on", re.IGNORECASE),
    re.compile(r"switch\s+from\s+.+?\s+to\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"(?:,|;)\s*(?:and\s+)?i\s+need\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"non-negotiable\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"(?:,|;)\s*(?P<value>.+?)\s+is\s+important\.?$", re.IGNORECASE),
)
CATEGORY_PATTERNS = (
    re.compile(r"\blooking\s+for\s+(?P<value>.+?)(?=,|\.|;|\?|\s+but\b|\s+with\b|\s+under\b|$)", re.IGNORECASE),
    re.compile(r"\bexploring\s+options\s+for\s+(?P<value>.+?)(?=,|\.|;|\?|$)", re.IGNORECASE),
    re.compile(r"\bneed\s+help\s+finding\s+(?P<value>.+?)(?=,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bshow\s+me(?:\s+some)?\s+(?P<value>.+?)(?=\s+that\b|,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bshopping\s+for\s+(?P<value>.+?)(?=,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bcould\s+you\s+find\s+(?P<value>.+?)(?=\s+with\s+the\s+following|,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bbrowse\s+(?P<value>.+?)(?=\s+and\s+see|,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\binterested\s+in\s+(?P<value>.+?)(?=,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bideas\s+for\s+(?P<value>.+?)(?=,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bwhat\s+kinds\s+of\s+(?P<value>.+?)(?=\s+would\s+you\s+suggest|,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bwant\s+to\s+browse\s+(?P<value>.+?)(?=,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bconsidering\s+(?P<value>.+?)(?=\s+with\s+this\s+preference|,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bhelp\s+me\s+find\s+(?P<value>.+?)(?=,|;|\.|\?|$)", re.IGNORECASE),
    re.compile(r"\bfor\s+(?P<value>.+?),\s+this\s+is\s+non-negotiable", re.IGNORECASE),
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n.,;:!?-")


def _normalize_attribute(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", "_", value.strip().lower())
    aliases = {
        "use_case": "use_case",
        "other_requirement": "other",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in ALLOWED_CONTEXT_ATTRIBUTES else None


def _mentioned_attribute(text: str) -> str | None:
    for label, attribute in (
        ("other requirement", "other"),
        ("use case", "use_case"),
        ("category", "category"),
        ("material", "material"),
        ("color", "color"),
        ("size", "size"),
        ("style", "style"),
        ("brand", "brand"),
        ("budget", "budget"),
        ("feature", "feature"),
        ("other", "other"),
    ):
        if re.search(r"\b" + re.escape(label) + r"\b", text, re.IGNORECASE):
            return attribute
    return None


def _no_preference_attribute(text: str, context_attribute: str | None) -> str | None:
    patterns = (
        r"preference\s+for\s+(?P<attribute>[a-z_ ]+?)(?:[;,.]|$)",
        r"any\s+(?P<attribute>[a-z_ ]+?)\s+(?:is\s+fine|works)",
        r"the\s+(?P<attribute>[a-z_ ]+?)\s+(?:does(?:n't|\s+not)\s+(?:really\s+)?matter|is\s+up\s+to\s+you)",
        r"flexible\s+(?:about|on)\s+(?P<attribute>[a-z_ ]+?)(?:[;,.]|$)",
        r"no\s+particular\s+(?P<attribute>[a-z_ ]+?)\s+(?:i\s+need|stands\s+out)",
        r"no\s+strong\s+opinion\s+on\s+(?P<attribute>[a-z_ ]+?)(?:[;,.]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            normalized = _normalize_attribute(match.group("attribute"))
            if normalized:
                return normalized
    return _normalize_attribute(context_attribute) or _mentioned_attribute(text)


def _extract_category(text: str) -> str | None:
    for pattern in CATEGORY_PATTERNS:
        match = pattern.search(text)
        if match:
            value = _clean_value(match.group("value"))
            if value:
                return value
    return None


def _extract_constraint_text(text: str) -> str:
    for pattern in VALUE_MARKER_PATTERNS:
        match = pattern.search(text)
        if match:
            return _clean_value(match.group("value"))
    return ""


@dataclass
class ParsedMessage:
    normalized_text: str
    category_values: list[str] = field(default_factory=list)
    slot_values: dict[str, list[str]] = field(default_factory=dict)
    no_preference: set[str] = field(default_factory=set)
    explicit_attributes: set[str] = field(default_factory=set)
    hard_attributes: set[str] = field(default_factory=set)
    override: bool = False
    boundary: bool = False
    reasons: list[str] = field(default_factory=list)


class RuleStateTracker:
    """Deterministic offline state tracker with accumulation and override support."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def reset(self, session_id: str, user_profile: dict) -> SessionState:
        tags = tuple(str(item).lower() for item in user_profile.get("preference_tags", []) if str(item).strip())
        state = SessionState(session_id=session_id, profile_terms=tags)
        self._sessions[session_id] = state
        return state

    def get(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError("reset must be called before respond") from exc

    def mark_asked(self, session_id: str, attribute: str | None) -> None:
        state = self.get(session_id)
        state.pending_attribute = attribute
        if not attribute:
            return
        if attribute not in state.asked_attributes:
            state.asked_attributes.append(attribute)

    def analyze_message(
        self,
        user_message: object,
        context_attribute: str | None = None,
    ) -> ParsedMessage:
        normalized = re.sub(r"\s+", " ", str(user_message or "")).strip()
        lowered = normalized.lower()
        parsed = ParsedMessage(normalized_text=normalized)
        parsed.override = bool(OVERRIDE_RE.search(lowered))
        parsed.boundary = bool(BOUNDARY_RE.search(lowered))

        if parsed.boundary:
            attribute = _no_preference_attribute(lowered, context_attribute)
            if attribute:
                parsed.no_preference.add(attribute)
                parsed.explicit_attributes.add(attribute)
                parsed.reasons.append("boundary/no-preference")
            return parsed

        slots = self._extract_slots(lowered, context_attribute)
        parsed.category_values = slots.pop("category", [])
        parsed.slot_values = slots
        parsed.explicit_attributes.update(slots)
        if parsed.category_values:
            parsed.explicit_attributes.add("category")
        if parsed.slot_values:
            parsed.reasons.append("explicit slot evidence")
        if HARD_CONSTRAINT_RE.search(lowered):
            parsed.hard_attributes.update(parsed.slot_values)
            if parsed.hard_attributes:
                parsed.reasons.append("explicit hard constraint")
        if parsed.override:
            parsed.reasons.append("intent override")
        return parsed

    def update(self, session_id: str, user_message: str, turn: int) -> SessionState:
        state = self.get(session_id)
        context_attribute = state.pending_attribute
        state.pending_attribute = None
        parsed = self.analyze_message(user_message, context_attribute)

        if parsed.override:
            state.excluded_terms.clear()
            state.hard_attributes.clear()
            for name in list(state.active_slots):
                if name != "category":
                    del state.active_slots[name]
            state.active_context = []

        for attribute in parsed.no_preference:
            state.no_preference_attributes.add(attribute)
            state.active_slots.pop(attribute, None)
            state.hard_attributes.discard(attribute)

        slots = {"category": parsed.category_values, **parsed.slot_values}
        for name, values in slots.items():
            if not values:
                continue
            state.no_preference_attributes.discard(name)
            state.active_slots[name] = _unique(values)
            if name in parsed.hard_attributes:
                state.hard_attributes.add(name)
            elif name != "category":
                state.hard_attributes.discard(name)
            for value in values:
                state.excluded_terms.discard(value.lower())

        state.turn = turn
        state.messages.append(user_message)
        if not parsed.boundary:
            if parsed.override:
                new_context = [
                    *parsed.category_values,
                    *(value for values in parsed.slot_values.values() for value in values),
                ]
                if new_context:
                    state.active_context.append(" ".join(new_context))
            else:
                state.active_context.append(user_message)
        state.intent_mode = self._infer_intent(state, parsed.normalized_text.lower())
        return state

    def _extract_slots(
        self,
        text: str,
        context_attribute: str | None = None,
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        normalized_context = _normalize_attribute(context_attribute)
        category = _extract_category(text) if normalized_context is None else None
        if category:
            result["category"] = [category]

        constraint_text = _extract_constraint_text(text)
        if constraint_text:
            values = [
                _clean_value(value)
                for value in re.split(r"\s*;\s*", constraint_text)
                if _clean_value(value)
            ]
            marker_slots: dict[str, list[str]] = {}
            for value in values:
                attribute = self._classify_value(value, normalized_context)
                if attribute == "category":
                    result.setdefault("category", []).append(value)
                else:
                    marker_slots.setdefault(attribute, []).append(value)
            for attribute, attribute_values in marker_slots.items():
                result[attribute] = _unique(attribute_values)
            return result

        if normalized_context and normalized_context != "category":
            cleaned = _clean_value(text)
            if cleaned and len(re.findall(r"[a-z0-9]+", cleaned)) <= 40:
                result[normalized_context] = [cleaned]
                return result

        scan_text = text.replace(category, " ", 1) if category else text
        materials = [value for value in MATERIALS if re.search(rf"\b{re.escape(value)}\b", scan_text)]
        colors = [value for value in COLORS if re.search(rf"\b{re.escape(value)}\b", scan_text)]
        use_cases = [value for value in USE_CASES if re.search(rf"\b{re.escape(value)}\b", scan_text)]
        features = [value for value in FEATURES if re.search(rf"\b{re.escape(value)}\b", scan_text)]
        styles = [value for value in STYLES if re.search(rf"\b{re.escape(value)}\b", scan_text)]

        if materials:
            result["material"] = materials
        if colors:
            result["color"] = colors
        if use_cases:
            result["use_case"] = use_cases
        if features:
            result["feature"] = features
        if styles:
            result["style"] = styles

        budget = re.search(
            r"(?:under|below|less than|at most|max(?:imum)?|budget(?:\s+around)?)\s*\$?\s*(\d+(?:\.\d+)?)",
            scan_text,
        )
        if budget:
            result["budget"] = [budget.group(1)]

        size = re.search(r"\bsize\s+([a-z0-9.-]+)", scan_text)
        if size:
            result["size"] = [size.group(1)]

        return result

    @staticmethod
    def _classify_value(value: str, context_attribute: str | None = None) -> str:
        if context_attribute in ALLOWED_CONTEXT_ATTRIBUTES:
            return context_attribute
        lowered = value.lower()
        if re.search(r"(?:\$|budget|price|under|below|less than|at most)\s*\$?\s*\d", lowered):
            return "budget"
        if any(item in lowered for item in MATERIALS):
            return "material"
        if any(item in lowered for item in COLORS):
            return "color"
        if re.search(r"\b(?:size|sizing|width|wide|narrow|fit)\b", lowered):
            return "size"
        if any(item in lowered for item in USE_CASES):
            return "use_case"
        if any(item in lowered for item in STYLES):
            return "style"
        return "feature"

    @staticmethod
    def _infer_intent(state: SessionState, message: str) -> str:
        browsing_language = re.search(
            r"(?:still\s+exploring|exploring\s+options|still\s+deciding|"
            r"like\s+to\s+browse|not\s+sure\s+exactly|start\s+with\s+some\s+ideas|"
            r"what\s+kinds\s+of)",
            message,
        )
        if browsing_language and len(state.active_slots) <= 1:
            return "browsing"
        constrained_slots = set(state.active_slots) - {"category"}
        if constrained_slots or state.hard_attributes:
            return "buying"
        return state.intent_mode if state.intent_mode != "uncertain" else "browsing"


def parse_message(text: object, context_attribute: str | None = None) -> ParsedMessage:
    """Parse one turn with the exact rules used by the production state tracker."""

    return RuleStateTracker().analyze_message(text, context_attribute)
