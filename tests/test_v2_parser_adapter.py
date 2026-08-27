from __future__ import annotations

import unittest

from shopping_copilot.state.rule_state import RuleStateTracker, parse_message


class V2ParserAdapterTest(unittest.TestCase):
    def test_initial_buying_exposes_category_constraint_and_hardness(self) -> None:
        parsed = parse_message(
            "I'm looking for Piercing Jewelry Barbells. One requirement is: Ball closure."
        )

        self.assertEqual(parsed.category_values, ["piercing jewelry barbells"])
        self.assertEqual(parsed.slot_values, {"feature": ["ball closure"]})
        self.assertEqual(parsed.hard_attributes, {"feature"})
        self.assertFalse(parsed.override)

    def test_initial_browsing_template_exposes_category(self) -> None:
        parsed = parse_message("I'm exploring options for Sandals Flats.")

        self.assertEqual(parsed.category_values, ["sandals flats"])
        self.assertEqual(parsed.slot_values, {})

    def test_other_attribute_keeps_the_requested_namespace(self) -> None:
        parsed = parse_message(
            "For other requirement, what matters is: leather and color: brown.",
            "other",
        )

        self.assertEqual(
            parsed.slot_values,
            {"other": ["leather and color: brown"]},
        )

    def test_override_exposes_only_the_new_value(self) -> None:
        parsed = parse_message(
            "I changed my mind. Ignore leather; what I need now is cotton."
        )

        self.assertTrue(parsed.override)
        self.assertEqual(parsed.slot_values, {"material": ["cotton"]})
        self.assertEqual(parsed.hard_attributes, {"material"})
        self.assertNotIn("leather", " ".join(parsed.slot_values["material"]))

    def test_state_update_uses_the_same_override_parse_result(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("session", {})
        tracker.update("session", "I'm looking for shoes with leather material.", 1)
        state = tracker.update(
            "session",
            "I changed my mind. Ignore leather; what I need now is cotton.",
            2,
        )

        self.assertEqual(state.active_slots["material"], ["cotton"])
        self.assertEqual(state.active_context, ["cotton"])
        self.assertEqual(state.hard_attributes, {"material"})

    def test_no_preference_can_clear_category(self) -> None:
        parsed = parse_message(
            "I don't have a preference for category; use your judgment.",
            "category",
        )

        self.assertTrue(parsed.boundary)
        self.assertEqual(parsed.no_preference, {"category"})


if __name__ == "__main__":
    unittest.main()
