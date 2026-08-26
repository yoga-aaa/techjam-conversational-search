from __future__ import annotations

import re

from shopping_copilot.core.contracts import SessionState


MATERIALS = ("cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk", "rayon", "fabric", "synthetic")
COLORS = ("black", "white", "blue", "red", "pink", "green", "brown", "gray", "grey", "purple", "yellow", "orange")
USE_CASES = ("hiking", "running", "gym", "winter", "outdoor", "work", "wedding", "walking", "travel", "sports")
FEATURES = ("waterproof", "breathable", "comfortable", "durable", "lightweight", "warm", "insulated", "non-slip")
STYLES = ("casual", "formal", "vintage", "classic", "modern", "sporty")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


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
        if not attribute:
            return
        state = self.get(session_id)
        if attribute not in state.asked_attributes:
            state.asked_attributes.append(attribute)

    def update(self, session_id: str, user_message: str, turn: int) -> SessionState:
        state = self.get(session_id)
        lowered = user_message.lower()
        is_override = bool(re.search(r"\b(actually|instead)\b|ignore my earlier|no longer", lowered))

        if is_override:
            for name, values in list(state.active_slots.items()):
                if name == "category":
                    continue
                state.excluded_terms.update(value.lower() for value in values)
                del state.active_slots[name]
            state.active_context = []

        no_preference = re.search(
            r"(?:do not|don't) have (?:an additional |a )?preference for\s+([a-z_]+)",
            lowered,
        )
        if no_preference:
            attribute = no_preference.group(1)
            state.no_preference_attributes.add(attribute)
            state.active_slots.pop(attribute, None)

        slots = self._extract_slots(lowered)
        for name, values in slots.items():
            if name in state.no_preference_attributes:
                state.no_preference_attributes.remove(name)
            state.active_slots[name] = _unique(values)

        state.turn = turn
        state.messages.append(user_message)
        state.active_context.append(user_message)
        state.intent_mode = self._infer_intent(state, lowered)
        return state

    def _extract_slots(self, text: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}

        category_match = re.search(
            r"looking for\s+(.+?)(?:,|\.|\s+but\b|\s+with\b|\s+under\b|$)",
            text,
        )
        if category_match:
            category = category_match.group(1).strip(" -")
            if category:
                result["category"] = [category]

        materials = [value for value in MATERIALS if re.search(rf"\b{re.escape(value)}\b", text)]
        colors = [value for value in COLORS if re.search(rf"\b{re.escape(value)}\b", text)]
        use_cases = [value for value in USE_CASES if re.search(rf"\b{re.escape(value)}\b", text)]
        features = [value for value in FEATURES if re.search(rf"\b{re.escape(value)}\b", text)]
        styles = [value for value in STYLES if re.search(rf"\b{re.escape(value)}\b", text)]

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
            r"(?:under|below|less than|max(?:imum)?|budget(?:\s+around)?)\s*\$?\s*(\d+(?:\.\d+)?)",
            text,
        )
        if budget:
            result["budget"] = [budget.group(1)]

        size = re.search(r"\bsize\s+([a-z0-9.-]+)", text)
        if size:
            result["size"] = [size.group(1)]

        return result

    @staticmethod
    def _infer_intent(state: SessionState, message: str) -> str:
        if "still exploring" in message and len(state.active_slots) <= 1:
            return "browsing"
        hard_slots = set(state.active_slots) - {"category"}
        if hard_slots or "key requirement" in message or "what i need is" in message:
            return "buying"
        return state.intent_mode if state.intent_mode != "uncertain" else "browsing"
