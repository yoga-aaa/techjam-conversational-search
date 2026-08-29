from __future__ import annotations

from shopping_copilot.catalog.constraints import normalized_tokens, normalized_value
from shopping_copilot.core.contracts import SessionState, SupersedablePreference
from shopping_copilot.state.semantic_delta import (
    MATERIALS,
    COLORS,
    USE_CASES,
    FEATURES,
    STYLES,
    OperationKind,
    TurnDelta,
    extract_supersedable_preference,
    apply_operations,
    parse_turn,
    sanitize_positive_context,
)


ALLOWED_CONTEXT_ATTRIBUTES = {
    "material", "color", "size", "style", "brand", "budget",
    "feature", "use_case", "category", "other",
}


def _remove_value_tokens(text: str, values: set[str]) -> str:
    tokens = list(normalized_tokens(text))
    for value in values:
        value_tokens = list(normalized_tokens(value))
        if not value_tokens:
            continue
        index = 0
        while index <= len(tokens) - len(value_tokens):
            if tokens[index:index + len(value_tokens)] == value_tokens:
                del tokens[index:index + len(value_tokens)]
            else:
                index += 1
    return " ".join(tokens)


class RuleStateTracker:
    """Deterministic offline state tracker with accumulation and slot overrides."""

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
        state.asked_attribute_counts[attribute] = state.asked_attribute_counts.get(attribute, 0) + 1
        if attribute not in state.asked_attributes:
            state.asked_attributes.append(attribute)

    def update(self, session_id: str, user_message: str, turn: int) -> SessionState:
        state = self.get(session_id)

        # Pending is a one-turn parser hint, never persistent semantic state.
        pending_attribute = state.pending_attribute
        state.pending_attribute = None
        before_active = {slot: list(values) for slot, values in state.active_slots.items()}
        before_negative = {slot: list(values) for slot, values in state.negative_slots.items()}
        before_preference = self._preference_signature(state)

        delta = parse_turn(
            user_message=user_message,
            pending_attribute=pending_attribute,
            active_slots=state.active_slots,
            negative_slots=state.negative_slots,
        )
        if turn == 1 and not delta.explicit_reference_override:
            extracted = extract_supersedable_preference(user_message)
            if extracted is not None:
                raw_text, parsed_values = extracted
                state.supersedable_preference = SupersedablePreference(
                    source_turn=turn,
                    raw_text=raw_text,
                    parsed_values=parsed_values,
                )
        if delta.explicit_reference_override:
            self._remove_supersedable_preference(state)
        apply_operations(state, delta)
        state.excluded_terms = {
            value
            for values in state.negative_slots.values()
            for value in values
            if normalized_value(value)
        }
        self._update_active_context(
            state,
            delta,
            before_active=before_active,
            before_negative=before_negative,
        )

        after_preference = self._preference_signature(state)
        retrieval_constraints_changed = before_preference[:2] != after_preference[:2]
        if before_preference != after_preference and retrieval_constraints_changed:
            self._reset_recommendation_history(state)

        state.turn = turn
        state.messages.append(user_message)
        state.intent_mode = self._infer_intent(state, user_message.casefold())
        return state

    @staticmethod
    def _remove_supersedable_preference(state: SessionState) -> None:
        reference = state.supersedable_preference
        if reference is None:
            return
        soft_values = [value for _, value in reference.parsed_values if value]
        removals = {
            normalized_value(value)
            for _, value in reference.parsed_values
            if normalized_value(value)
        }
        if removals:
            for slot in list(state.active_slots):
                state.active_slots[slot] = [
                    value
                    for value in state.active_slots[slot]
                    if normalized_value(value) not in removals
                ]
                if not state.active_slots[slot]:
                    state.active_slots.pop(slot)
        context_removals = removals | {
            normalized_value(reference.raw_text)
        }
        state.active_context = [
            cleaned
            for cleaned in state.active_context
            if normalized_value(cleaned) not in context_removals
        ]
        # An explicit override removes the old preference as a hard/structured
        # constraint, but keeps it as a weak lexical signal. The public
        # simulator derives both values from the target product, so deleting
        # the only trace can hurt early ranking even when the old preference
        # should no longer constrain the result.
        for value in [*soft_values, reference.raw_text]:
            cleaned = sanitize_positive_context(value)
            if cleaned and cleaned not in state.active_context:
                state.active_context.append(cleaned)
        state.supersedable_preference = None

    @staticmethod
    def _preference_signature(state: SessionState) -> tuple[object, ...]:
        def slots_signature(slots: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
            return tuple(
                sorted(
                    (
                        slot,
                        tuple(sorted({normalized_value(value) for value in values if normalized_value(value)})),
                    )
                    for slot, values in slots.items()
                    if values
                )
            )

        return (
            slots_signature(state.active_slots),
            slots_signature(state.negative_slots),
            tuple(sorted(state.no_preference_attributes)),
        )

    @staticmethod
    def _reset_recommendation_history(state: SessionState) -> None:
        state.question_scores = {}
        state.shown_asins.clear()
        state.previous_candidate_ids = ()
        state.previous_slot_signature = ()
        state.candidate_overlap = 0.0
        state.stagnant_candidate_turns = 0
        state.coverage_mode = False

    @staticmethod
    def _update_active_context(
        state: SessionState,
        delta: TurnDelta,
        before_active: dict[str, list[str]],
        before_negative: dict[str, list[str]],
    ) -> None:
        remove_values: set[str] = set()
        for operation in delta.operations:
            remove_values.update(before_active.get(operation.slot, ()))
            remove_values.update(before_negative.get(operation.slot, ()))
            if operation.kind is not OperationKind.SET:
                remove_values.update(operation.values)
            else:
                current_values = {
                    normalized_value(value)
                    for value in state.active_slots.get(operation.slot, ())
                }
                remove_values.update(
                    value
                    for value in operation.values
                    if normalized_value(value) not in current_values
                )

        negative_values = {
            value
            for values in state.negative_slots.values()
            for value in values
        }
        contexts = [*state.active_context, *delta.positive_context]
        cleaned_context: list[str] = []
        for context in contexts:
            cleaned = sanitize_positive_context(context)
            cleaned = _remove_value_tokens(cleaned, remove_values | negative_values)
            if cleaned:
                cleaned_context.append(cleaned)
        state.active_context = list(dict.fromkeys(cleaned_context))

    @staticmethod
    def _infer_intent(state: SessionState, message: str) -> str:
        if "still exploring" in message and len(state.active_slots) <= 1:
            return "browsing"
        hard_slots = set(state.active_slots) - {"category"}
        if hard_slots or "key requirement" in message or "what i need is" in message:
            return "buying"
        return state.intent_mode if state.intent_mode != "uncertain" else "browsing"
