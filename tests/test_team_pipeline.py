from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate
from shopping_copilot.core.config import load_config
from shopping_copilot.core.factory import build_components
from shopping_copilot.state.rule_state import RuleStateTracker
from starter.agent import Agent
from shopping_copilot.retrieval.hybrid import protected_candidate_ids
from shopping_copilot.retrieval.structured import grouped_rrf_signals, reciprocal_rank_fusion


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
        self.assertNotIn("leather", state.excluded_terms)
        self.assertNotIn("leather", state.active_context[-1].lower())

    def test_pending_attribute_classifies_short_clarification_answer(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("s", {})
        tracker.mark_asked("s", "material")

        state = tracker.update("s", "For that, what matters is: merino wool.", 2)

        self.assertEqual(state.active_slots["material"], ["merino wool"])
        self.assertIsNone(state.pending_attribute)

    def test_boundary_answer_uses_pending_attribute_without_query_pollution(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("s", {})
        tracker.mark_asked("s", "color")

        state = tracker.update("s", "Either is fine; please use your judgment.", 2)

        self.assertIn("color", state.no_preference_attributes)
        self.assertNotIn("color", state.active_slots)
        self.assertEqual(state.active_context, [])

    def test_key_requirement_marker_creates_feature_slot(self) -> None:
        tracker = RuleStateTracker()
        tracker.reset("s", {})

        state = tracker.update(
            "s",
            "I'm looking for shirts. A key requirement is: machine washable.",
            1,
        )

        self.assertEqual(state.active_slots["feature"], ["machine washable"])
        self.assertEqual(state.intent_mode, "buying")

    def test_structured_route_contributes_candidates_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            config = load_config("configs/experiments/structured_hybrid.json")
            components = build_components(catalog, config)
            state = components.state_tracker.reset("structured", PROFILE)
            state = components.state_tracker.update(
                "structured",
                "I'm looking for shoes. A key requirement is: waterproof.",
                1,
            )
            plan = components.planner.build(state)

            result = components.retriever.retrieve(plan)
            by_id = {candidate.parent_asin: candidate for candidate in result.candidates}

            self.assertTrue(plan.use_structured)
            self.assertIn("A", by_id)
            self.assertIn("structured", by_id["A"].source_routes)

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

    def test_standard_rrf_rewards_multi_attribute_candidate(self) -> None:
        fused = reciprocal_rank_fusion(
            {
                "category": [("A", 1.0), ("B", 0.5)],
                "material": [("B", 1.0)],
                "color": [("B", 1.0)],
            },
            k=60,
        )
        self.assertGreater(fused["B"], fused["A"])
        self.assertLessEqual(max(fused.values()), 1.0)

    def test_optimized_rrf_discounts_correlated_attribute_contributions(self) -> None:
        signals = grouped_rrf_signals(
            {
                "feature": [("A", 1.0)],
                "use_case": [("A", 1.0)],
                "material": [("B", 1.0)],
            },
            k=60,
            weights={},
            attribute_groups={"intent": ["feature", "use_case"], "physical": ["material"]},
            secondary_discount=0.5,
        )
        self.assertGreater(signals["A"].attribute_coverage, signals["B"].attribute_coverage)
        self.assertLessEqual(signals["A"].rank_quality, 1.0)

    def test_protected_union_keeps_both_recall_routes(self) -> None:
        selected = protected_candidate_ids(
            ["lexical-1", "shared"],
            ["structured-1", "shared"],
            ["structured-1", "lexical-1", "shared"],
            limit=3,
            protected_fraction=0.5,
        )
        self.assertIn("lexical-1", selected)
        self.assertIn("structured-1", selected)
        self.assertEqual(len(selected), len(set(selected)))
if __name__ == "__main__":
    unittest.main()
