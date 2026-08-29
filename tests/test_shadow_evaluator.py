from __future__ import annotations

import unittest

from shopping_copilot.evaluation.shadow import shadow_evaluate


class AlwaysHitsAgent:
    def __init__(self) -> None:
        self.calls = 0

    def reset(self, session_id: str, user_profile: dict) -> None:
        pass

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        self.calls += 1
        return {
            "message": "ok",
            "ask_attribute": "other",
            "recommendations": [{"parent_asin": "A"}],
        }


class ShadowEvaluatorTest(unittest.TestCase):
    def test_keeps_running_after_first_hit(self) -> None:
        sample = {
            "sample_id": "sample-1",
            "scenario_type": "buying",
            "user_profile": {},
            "ground_truth": {"parent_asin": "A"},
            "intent_card": {
                "target_category": "Blue shoe",
                "hard_constraints": ["blue"],
                "soft_preferences": ["lightweight"],
            },
            "behavior": {"scenario_type": "buying"},
        }
        agent = AlwaysHitsAgent()
        result = shadow_evaluate(
            agent,
            [sample],
            {"A"},
            {"A": ["Clothing", "Shoes"]},
            {"A": {"parent_asin": "A"}},
        )

        self.assertEqual(agent.calls, 10)
        self.assertEqual(result["sessions"][0]["first_hit_turn"], 1)
        self.assertEqual(len(result["sessions"][0]["turns"]), 10)
        self.assertEqual(result["per_turn"][9]["top_1"], 1)


if __name__ == "__main__":
    unittest.main()
