from __future__ import annotations

import unittest

from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import RankedCandidate
from shopping_copilot.planning.query_planner import RuleQueryPlanner
from shopping_copilot.policy.coverage import CandidateCoverageManager
from shopping_copilot.state.rule_state import RuleStateTracker
from shopping_copilot.state.semantic_delta import OperationKind, parse_price_value, parse_turn


class SemanticStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = RuleStateTracker()
        self.tracker.reset("semantic", {})

    def update(self, message: str, turn: int = 1):
        return self.tracker.update("semantic", message, turn)

    def test_parser_emits_five_ordered_operation_kinds(self) -> None:
        delta = parse_turn(
            "I don't want black; actually black is fine after all; I no longer need waterproof; Color doesn't matter.",
            None,
            {},
            {},
        )

        self.assertEqual(
            [operation.kind for operation in delta.operations],
            [OperationKind.EXCLUDE, OperationKind.ALLOW, OperationKind.REMOVE, OperationKind.DONTCARE],
        )

    def test_negative_value_is_canonical_and_projected(self) -> None:
        state = self.update("I don't want black.")

        self.assertEqual(state.negative_slots, {"color": ["black"]})
        self.assertNotIn("black", state.active_slots.get("color", []))
        self.assertEqual(state.excluded_terms, {"black"})

    def test_same_turn_exclude_then_set_preserves_both_sides(self) -> None:
        state = self.update("I don't want black, I want white shoes.")

        self.assertEqual(state.active_slots["color"], ["white"])
        self.assertEqual(state.negative_slots["color"], ["black"])

    def test_positive_enumerations_are_delimiter_invariant(self) -> None:
        for message in (
            "For that, what matters is: Imported; Rubber sole.",
            "For that, what matters is: Imported or Rubber sole.",
        ):
            tracker = RuleStateTracker()
            tracker.reset("enumeration", {})
            tracker.mark_asked("enumeration", "feature")
            state = tracker.update("enumeration", message, 1)

            self.assertEqual(
                state.active_slots["feature"],
                ["imported", "rubber sole"],
                message,
            )
            self.assertEqual(state.negative_slots, {}, message)

    def test_state_preserves_cleaned_punctuation_for_comparison(self) -> None:
        self.tracker.mark_asked("semantic", "feature")
        state = self.update("What matters is: 100% Mesh; Shaft measures approximately 5.5\" from arch.")

        self.assertEqual(
            state.active_slots["feature"],
            ["100% mesh", "shaft measures approximately 5.5\" from arch"],
        )

    def test_discourse_prefix_and_idiomatic_negation_are_not_values(self) -> None:
        state = self.update("For that, what matters is: color: black.")
        self.assertEqual(state.active_slots, {"color": ["black"]})
        self.assertNotIn("for that", state.active_context)

        state = self.update("What matters is: comfortable without sacrificing durability.", 2)
        self.assertEqual(state.negative_slots, {})
        self.assertNotIn("sacrificing", state.negative_slots)

        state = self.update("What matters is: lightweight. Please avoid soaking the.", 3)
        self.assertEqual(state.negative_slots, {})

    def test_attribute_override_preserves_unrelated_slots(self) -> None:
        state = self.update("I'm looking for shoes under $80, size 9.")
        state = self.update("Actually, cotton instead.", 2)

        self.assertEqual(state.active_slots["category"], ["shoes"])
        self.assertEqual(state.active_slots["budget"], ["80"])
        self.assertEqual(state.active_slots["size"], ["9"])
        self.assertEqual(state.active_slots["material"], ["cotton"])

    def test_set_replaces_only_the_target_slot(self) -> None:
        state = self.update("I'm looking for shoes.")
        state.active_slots["color"] = ["black"]
        state = self.update("Make it white instead.", 2)

        self.assertEqual(state.active_slots, {"category": ["shoes"], "color": ["white"]})

    def test_remove_allow_and_dontcare_have_narrow_effects(self) -> None:
        state = self.update("I'm looking for shoes.")
        state.active_slots.update({"feature": ["waterproof"], "color": ["white"]})
        state.negative_slots["color"] = ["black"]

        state = self.update("I no longer need waterproof.", 2)
        self.assertNotIn("waterproof", state.active_slots.get("feature", []))
        self.assertNotIn("waterproof", state.negative_slots.get("feature", []))

        state = self.update("Black is fine after all.", 3)
        self.assertNotIn("black", state.negative_slots.get("color", []))
        self.assertNotIn("black", state.active_slots.get("color", []))

        state.negative_slots["color"] = ["black"]
        state = self.update("Color doesn't matter.", 4)
        self.assertNotIn("color", state.active_slots)
        self.assertNotIn("color", state.negative_slots)
        self.assertIn("color", state.no_preference_attributes)

    def test_pending_and_untyped_oov_values_are_supported(self) -> None:
        self.tracker.mark_asked("semantic", "color")
        state = self.update("Anything but ochre.")
        self.assertEqual(state.negative_slots, {"color": ["ochre"]})

        state = self.update("Please avoid ecru.", 2)
        self.assertEqual(state.negative_slots["other"], ["ecru"])

    def test_multiword_oov_span_is_not_split(self) -> None:
        self.tracker.mark_asked("semantic", "material")
        state = self.update("I don't want merino wool.")

        self.assertEqual(state.negative_slots["material"], ["merino wool"])

    def test_boundary_requires_pending_attribute_when_implicit(self) -> None:
        state = self.update("Either is fine.")
        self.assertEqual(state.no_preference_attributes, set())
        self.assertEqual(state.active_slots, {})

        self.tracker.mark_asked("semantic", "color")
        state = self.update("Either is fine.", 2)
        self.assertEqual(state.no_preference_attributes, {"color"})

    def test_false_positive_negation_protection(self) -> None:
        for message in (
            "Not only black but also white.",
            "I'm not sure about color.",
            "Not necessarily waterproof.",
            "Not too expensive.",
        ):
            state = self.update(message)
            self.assertEqual(state.negative_slots, {}, message)

    def test_positive_context_does_not_keep_control_words_or_stale_value(self) -> None:
        state = self.update("I'm looking for shoes with leather material.")
        state = self.update("Actually, cotton instead.", 2)

        context = " ".join(state.active_context)
        self.assertIn("shoes", context)
        self.assertIn("cotton", context)
        self.assertNotIn("leather", context)
        self.assertNotIn("actually", context)
        self.assertNotIn("preference", context)

    def test_negative_state_changes_reset_coverage_history(self) -> None:
        state = self.update("I'm looking for shoes.")
        state.shown_asins.update({"A", "B"})
        state.previous_candidate_ids = ("A", "B")
        state.stagnant_candidate_turns = 2
        state.coverage_mode = True

        state = self.update("I don't want black.", 2)

        self.assertEqual(state.shown_asins, set())
        self.assertEqual(state.previous_candidate_ids, ())
        self.assertEqual(state.stagnant_candidate_turns, 0)
        self.assertFalse(state.coverage_mode)

    def test_query_is_rebuilt_from_current_state(self) -> None:
        state = self.update("I don't want black, I want white shoes.")
        planner = RuleQueryPlanner(load_config("configs/final.json").search)
        plan = planner.build(state)

        self.assertIn("white", plan.lexical_terms)
        self.assertIn("shoes", plan.lexical_terms)
        self.assertNotIn("black", plan.lexical_terms)
        self.assertNotIn("want", plan.lexical_terms)
        self.assertNotIn("don", plan.lexical_terms)
        self.assertNotIn("black", plan.semantic_query.split())
        self.assertEqual(plan.excluded_terms, ("black",))

    def test_excluded_terms_is_a_cleaned_phrase_projection(self) -> None:
        state = self.update("Please avoid 100% Mesh.")
        plan = RuleQueryPlanner(load_config("configs/final.json").search).build(state)

        self.assertEqual(state.negative_slots, {"other": ["100% mesh"]})
        self.assertEqual(plan.excluded_terms, ("100% mesh",))

    def test_query_drops_replaced_value_but_retains_other_constraints(self) -> None:
        state = self.update("I'm looking for shoes with leather material.")
        state = self.update("Cotton instead.", 2)
        plan = RuleQueryPlanner(load_config("configs/final.json").search).build(state)

        self.assertIn("cotton", plan.lexical_terms)
        self.assertIn("shoes", plan.lexical_terms)
        self.assertNotIn("leather", plan.lexical_terms)
        self.assertNotIn("leather", plan.semantic_query.split())

    def test_official_reference_override_removes_old_value_and_ignores_pending(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("official-override", {})
        tracker.update("official-override", "I'm looking for shirts with waterproof feature.", 1)
        tracker.mark_asked("official-override", "budget")

        state = tracker.update(
            "official-override",
            "Actually, ignore my earlier preference. What I need is: cotton.",
            2,
        )

        self.assertEqual(state.active_slots["material"], ["cotton"])
        self.assertNotIn("waterproof", state.active_slots.get("feature", []))
        self.assertNotIn("budget", state.active_slots)
        self.assertNotIn("ignore my earlier preference", " ".join(state.active_context))
        self.assertIn("waterproof", " ".join(state.active_context))

    def test_non_price_pending_answer_is_not_budget(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("budget-guard", {})
        tracker.mark_asked("budget-guard", "budget")

        state = tracker.update("budget-guard", "cotton", 1)

        self.assertNotIn("budget", state.active_slots)
        self.assertEqual(state.active_slots["material"], ["cotton"])

    def test_budget_accepts_deterministic_text_number(self) -> None:
        self.assertEqual(parse_price_value("eighty", pending_is_budget=True), "80")
        self.assertEqual(parse_price_value("one hundred and twenty", pending_is_budget=True), "120")
        self.assertEqual(parse_price_value("under eighty dollars"), "80")
        self.assertIsNone(parse_price_value("cotton", pending_is_budget=True))

    def test_coverage_signature_encodes_positive_negative_and_no_preference(self) -> None:
        state = self.update("I want white.")
        state.negative_slots["color"] = ["black"]
        state.no_preference_attributes.add("size")
        manager = CandidateCoverageManager(load_config("configs/final.json").policy)

        manager.observe(state, [RankedCandidate("A", 1.0, {})])

        self.assertEqual(
            state.previous_slot_signature,
            (
                ("+color", ("white",)),
                ("-color", ("black",)),
                ("~size", ()),
            ),
        )


if __name__ == "__main__":
    unittest.main()
