from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent


ALLOWED_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand", "budget",
    "feature", "use_case", "other",
}


class V2ContractTest(unittest.TestCase):
    def _catalog(self, root: Path) -> Path:
        path = root / "catalog.jsonl"
        path.write_text(
            json.dumps({
                "parent_asin": "A",
                "title": "Blue cotton shirt",
                "features": ["soft"],
                "description": [],
                "categories": ["Clothing", "Shirts"],
                "details": {},
                "store": "Example",
                "price": 20,
            }) + "\n",
            encoding="utf-8",
        )
        return path

    def test_agent_response_is_contract_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(self._catalog(Path(directory)), "configs/final.json")
            agent.reset("session", {"preference_tags": [], "summary": ""})

            response = agent.respond("session", "I'm looking for Shirts.", 1, 10)

        self.assertIsInstance(response, dict)
        self.assertIsInstance(response["message"], str)
        self.assertIn(response["ask_attribute"], ALLOWED_ATTRIBUTES | {None})
        self.assertLessEqual(len(response["recommendations"]), 10)
        self.assertEqual(
            len({item["parent_asin"] for item in response["recommendations"]}),
            len(response["recommendations"]),
        )
        self.assertEqual(
            response["usage"],
            {"prompt_tokens": 0, "completion_tokens": 0},
        )
        json.dumps(response)

    def test_unreset_session_returns_safe_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(self._catalog(Path(directory)), "configs/final.json")

            response = agent.respond("missing", "hello", 1, 10)

        self.assertIsInstance(response["message"], str)
        self.assertIsNone(response["ask_attribute"])
        self.assertEqual(response["recommendations"], [])
        self.assertEqual(
            response["usage"],
            {"prompt_tokens": 0, "completion_tokens": 0},
        )

    def test_reset_keeps_session_state_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(self._catalog(Path(directory)), "configs/final.json")
            profile = {"preference_tags": [], "summary": ""}
            agent.reset("first", profile)
            agent.reset("second", profile)
            agent.respond(
                "first",
                "I'm looking for Shirts. A key requirement is: cotton.",
                1,
                10,
            )
            tracker = agent._impl.components.state_tracker
            first = tracker.get("first")
            second = tracker.get("second")

        self.assertEqual(first.turn, 1)
        self.assertEqual(second.turn, 0)
        self.assertEqual(first.active_slots["material"], ["cotton"])
        self.assertEqual(second.active_slots, {})


if __name__ == "__main__":
    unittest.main()
