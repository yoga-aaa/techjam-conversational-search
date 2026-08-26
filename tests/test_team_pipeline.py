from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate
from shopping_copilot.state.rule_state import RuleStateTracker
from starter.agent import Agent


PROFILE = {
    "purchase_frequency": "1-2 prior purchases",
    "average_prior_rating": 4.5,
    "rating_style": "usually positive",
    "preference_tags": ["comfort"],
    "summary": "Prior purchases emphasize comfort.",
}


class TeamPipelineTest(unittest.TestCase):
    def _catalog(self, root: Path) -> Path:
        products = [
            {
                "parent_asin": "A",
                "title": "Waterproof hiking shoes",
                "features": ["waterproof", "comfortable"],
                "details": {"material": "synthetic"},
                "description": ["winter outdoor hiking shoe"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
                "price": 79.0,
                "average_rating": 4.5,
                "rating_number": 50,
            },
            {
                "parent_asin": "B",
                "title": "Cotton casual shirt",
                "features": ["cotton"],
                "details": {"department": "mens"},
                "description": ["casual summer shirt"],
                "categories": ["Clothing", "Shirts"],
                "store": "Example",
                "price": 29.0,
                "average_rating": 4.0,
                "rating_number": 20,
            },
        ]
        path = root / "catalog.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in products), encoding="utf-8")
        return path

    def test_official_entry_returns_contract_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            agent = Agent(catalog, config_path="configs/baseline.json")
            agent.reset("s1", PROFILE)
            response = agent.respond(
                "s1",
                "I'm looking for shoes. A key requirement is waterproof.",
                1,
                10,
            )
            self.assertEqual(response["recommendations"][0]["parent_asin"], "A")
            self.assertIsInstance(response["message"], str)
            self.assertIn(response["ask_attribute"], {None, "material", "budget", "size", "color", "style", "use_case", "feature", "other"})
            self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_state_accumulates_and_override_erases_old_preference(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("s2", PROFILE)
        tracker.update("s2", "I'm looking for shoes with leather material.", 1)
        state = tracker.update(
            "s2",
            "Actually, ignore my earlier preference. What I need is cotton.",
            3,
        )
        self.assertEqual(state.active_slots["material"], ["cotton"])
        self.assertIn("leather", state.excluded_terms)

    def test_browsing_turn_asks_a_structured_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            agent = Agent(catalog, config_path="configs/baseline.json")
            agent.reset("s3", PROFILE)
            response = agent.respond("s3", "I'm looking for shoes, but I'm still exploring.", 1, 10)
            self.assertEqual(response["ask_attribute"], "use_case")

    def test_last_turn_does_not_ask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            agent = Agent(catalog, config_path="configs/baseline.json")
            agent.reset("s4", PROFILE)
            response = agent.respond("s4", "I'm looking for shoes, but I'm still exploring.", 10, 10)
            self.assertIsNone(response["ask_attribute"])

    def test_pipeline_runs_inside_official_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            catalog_ids, categories, products = catalog_index(catalog)
            samples = [{
                "sample_id": "team_smoke_0001",
                "scenario_type": "buying",
                "user_profile": PROFILE,
                "ground_truth": {"parent_asin": "A"},
            }]
            result = evaluate(
                Agent(catalog, config_path="configs/baseline.json"),
                samples,
                catalog_ids,
                categories,
                products,
            )
            self.assertEqual(result["hit_rate_at_10"], 1.0)


if __name__ == "__main__":
    unittest.main()
