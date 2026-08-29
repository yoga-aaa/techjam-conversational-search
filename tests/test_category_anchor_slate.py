from __future__ import annotations

import unittest

from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import PolicyDecision, RankedCandidate, SessionState
from shopping_copilot.policy.heuristic import HeuristicPolicy
from shopping_copilot.retrieval.category_anchor import category_anchor
from shopping_copilot.state.rule_state import RuleStateTracker


class CategoryAnchorSlateTest(unittest.TestCase):
    def test_category_anchor_matches_evaluator_tail_rule(self) -> None:
        self.assertEqual(category_anchor(("Clothing, Shoes & Jewelry", "Necklaces")), "shoes jewelry necklaces")
        self.assertEqual(category_anchor(("Clothing", "Shoes")), "shoes")

    def test_repeated_other_counter_tracks_missing_disclosure(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("other", {})
        tracker.mark_asked("other", "other")
        state = tracker.update("other", "I don't have an additional preference for other.", 2)
        self.assertEqual(state.other_question_count, 1)
        self.assertEqual(state.other_no_additional_count, 1)

        tracker.mark_asked("other", "other")
        state = tracker.update("other", "For that, what matters is: waterproof.", 3)
        self.assertEqual(state.other_no_additional_count, 0)

    def test_gate_compacts_then_expands_by_match_count(self) -> None:
        config = load_config("configs/experiments/slate_gate_v2.json")
        policy = HeuristicPolicy.__new__(HeuristicPolicy)
        policy.policy_config = config.policy
        state = SessionState("gate", turn=2)
        ranked = [RankedCandidate("A", 1.0, {"matched_constraint_count": 1.0})]
        decision = PolicyDecision(True, "other", "question", 10)
        compact = policy._apply_slate_gate(state, ranked, decision)
        self.assertEqual(compact.recommendation_count, 1)

        state.turn = 3
        ranked = [RankedCandidate("A", 1.0, {"matched_constraint_count": 2.0})]
        expanded = policy._apply_slate_gate(state, ranked, decision)
        self.assertEqual(expanded.recommendation_count, 10)


if __name__ == "__main__":
    unittest.main()
