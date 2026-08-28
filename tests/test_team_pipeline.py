from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import (
    Candidate,
    RankedCandidate,
    RetrievalDiagnostics,
    RetrievalResult,
    SessionState,
)
from shopping_copilot.core.factory import build_components
from shopping_copilot.core.pipeline import _prioritize_top_semantic_group
from shopping_copilot.policy.information_gain import InformationGainQuestionScorer
from shopping_copilot.policy.heuristic import adaptive_semantic_slate_size
from shopping_copilot.ranking.global_idf import GlobalCatalogIDF
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
    @staticmethod
    def _semantic_candidate(
        parent_asin: str,
        matches: int,
        unknowns: int,
    ) -> RankedCandidate:
        return RankedCandidate(
            parent_asin,
            1.0,
            {
                "semantic_match_count": float(matches),
                "semantic_unknown_count": float(unknowns),
                "semantic_conflict_count": 0.0,
            },
        )

    def test_adaptive_slate_equals_rank_one_semantic_group_size(self) -> None:
        ranked = [
            self._semantic_candidate("A", 3, 0),
            self._semantic_candidate("B", 3, 0),
            self._semantic_candidate("C", 2, 1),
        ]

        self.assertEqual(adaptive_semantic_slate_size(ranked), 2)

    def test_exact_semantic_slate_distinguishes_which_attribute_matched(self) -> None:
        ranked = [
            RankedCandidate("A", 1.0, {}, semantic_signature=(2, 2, 1)),
            RankedCandidate("B", 0.9, {}, semantic_signature=(2, 2, 1)),
            RankedCandidate("C", 0.8, {}, semantic_signature=(2, 1, 2)),
        ]

        self.assertEqual(adaptive_semantic_slate_size(ranked, "exact"), 2)

    def test_global_idf_gives_rare_catalog_value_more_weight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            products = [
                {"parent_asin": "A", "title": "common rare", "features": []},
                {"parent_asin": "B", "title": "common", "features": []},
                {"parent_asin": "C", "title": "common", "features": []},
            ]
            catalog_path.write_text(
                "\n".join(json.dumps(product) for product in products) + "\n",
                encoding="utf-8",
            )

            index = GlobalCatalogIDF(CatalogStore(catalog_path))

        self.assertGreater(
            index.value_idf("feature", "rare"),
            index.value_idf("feature", "common"),
        )

    def test_coverage_stays_within_rank_one_semantic_group(self) -> None:
        ranked = [
            self._semantic_candidate("A", 3, 0),
            self._semantic_candidate("B", 3, 0),
            self._semantic_candidate("C", 2, 1),
        ]
        coverage_order = [ranked[2], ranked[1], ranked[0]]

        ordered = _prioritize_top_semantic_group(ranked, coverage_order)

        self.assertEqual([item.parent_asin for item in ordered], ["B", "A", "C"])

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

    def test_information_gain_prefers_attribute_that_splits_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = [
                {
                    "parent_asin": "A",
                    "title": "Black cotton walking shoe",
                    "features": ["comfortable"],
                    "details": {"material": "cotton"},
                    "description": ["walking shoe"],
                    "categories": ["Clothing", "Shoes"],
                    "store": "Example",
                    "price": 50,
                },
                {
                    "parent_asin": "B",
                    "title": "Black leather walking shoe",
                    "features": ["comfortable"],
                    "details": {"material": "leather"},
                    "description": ["walking shoe"],
                    "categories": ["Clothing", "Shoes"],
                    "store": "Example",
                    "price": 50,
                },
                {
                    "parent_asin": "C",
                    "title": "Black wool walking shoe",
                    "features": ["comfortable"],
                    "details": {"material": "wool"},
                    "description": ["walking shoe"],
                    "categories": ["Clothing", "Shoes"],
                    "store": "Example",
                    "price": 50,
                },
            ]
            catalog = root / "catalog.jsonl"
            catalog.write_text("".join(json.dumps(item) + "\n" for item in products), encoding="utf-8")
            config = load_config("configs/experiments/information_gain_policy.json")
            components = build_components(catalog, config)
            scorer = InformationGainQuestionScorer(
                components.ranker.store,
                candidate_limit=60,
                minimum_coverage=0.12,
                minimum_score=0.01,
            )
            ranked = [
                RankedCandidate(item, 1.0, {})
                for item in ("A", "B", "C")
            ]

            selected = scorer.choose(SessionState("information-gain"), ranked)

            self.assertEqual(selected, "material")

    def test_information_gain_policy_is_used_after_candidate_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._catalog(root)
            agent = Agent(catalog, config_path="configs/experiments/information_gain_policy.json")
            agent.reset("ig", PROFILE)

            response = agent.respond("ig", "I'm looking for clothing, but I'm still exploring.", 1, 10)

            self.assertIn(response["ask_attribute"], {
                "material", "feature", "color", "size", "style", "use_case", "budget", "brand", "other"
            })

    def test_last_turn_does_not_ask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            agent = Agent(catalog, config_path="configs/baseline.json")
            agent.reset("s4", PROFILE)
            response = agent.respond("s4", "I'm looking for shoes, but I'm still exploring.", 10, 10)
            self.assertIsNone(response["ask_attribute"])

    def test_stagnant_candidate_pool_rotates_to_unseen_products(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = [
                {
                    "parent_asin": f"P{index:02d}",
                    "title": f"Cotton casual shirt model {index:02d}",
                    "features": ["cotton", "comfortable"],
                    "details": {"department": "mens"},
                    "description": ["casual summer shirt"],
                    "categories": ["Clothing", "Shirts"],
                    "store": "Example",
                    "price": 29.0,
                }
                for index in range(25)
            ]
            catalog = root / "catalog.jsonl"
            catalog.write_text(
                "".join(json.dumps(item) + "\n" for item in products),
                encoding="utf-8",
            )
            agent = Agent(catalog, config_path="configs/experiments/stagnation_coverage.json")
            agent.reset("coverage", PROFILE)

            first = agent.respond(
                "coverage",
                "I'm looking for shirts, but I'm still exploring.",
                1,
                10,
            )
            response = first
            for turn in range(2, 5):
                response = agent.respond(
                    "coverage",
                    f"I don't have an additional preference for {response['ask_attribute']}.",
                    turn,
                    10,
                )

            first_ids = {item["parent_asin"] for item in first["recommendations"]}
            rotated_ids = {item["parent_asin"] for item in response["recommendations"]}
            self.assertEqual(len(first_ids), 10)
            self.assertEqual(len(rotated_ids), 10)
            self.assertTrue(first_ids.isdisjoint(rotated_ids))

    def test_override_clears_candidate_coverage_history(self) -> None:
        tracker = RuleStateTracker()
        state = tracker.reset("override-coverage", PROFILE)
        state.shown_asins.update({"A", "B"})
        state.previous_candidate_ids = ("A", "B")
        state.previous_slot_signature = (("category", ("shoes",)),)
        state.stagnant_candidate_turns = 2
        state.coverage_mode = True

        state = tracker.update(
            "override-coverage",
            "Actually, ignore my earlier preference. What I need is cotton.",
            3,
        )

        self.assertEqual(state.shown_asins, set())
        self.assertEqual(state.previous_candidate_ids, ())
        self.assertFalse(state.coverage_mode)

    def test_dynamic_ranking_boosts_accumulated_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._catalog(Path(directory))
            config = load_config("configs/experiments/stagnation_coverage.json")
            components = build_components(catalog, config)
            state = components.state_tracker.reset("dynamic-rank", PROFILE)
            state = components.state_tracker.update(
                "dynamic-rank",
                "I'm looking for shoes. A key requirement is: waterproof.",
                1,
            )
            plan = components.planner.build(state)
            result = RetrievalResult(
                candidates=(
                    Candidate("A", lexical_score=1.0),
                    Candidate("B", lexical_score=1.0),
                ),
                diagnostics=RetrievalDiagnostics(2, 2, 0.0, plan.route),
            )

            ranked = components.ranker.rank(state, plan, result)

            self.assertEqual(ranked[0].parent_asin, "A")
            self.assertGreater(
                ranked[0].component_scores["constraint_weight"],
                plan.constraint_weight,
            )

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
