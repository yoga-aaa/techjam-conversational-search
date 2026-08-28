from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shopping_copilot.catalog.constraints import (
    matches_any_excluded_term,
    product_matches_value,
    violates_negative_slots,
)
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import (
    Candidate,
    RetrievalDiagnostics,
    RetrievalResult,
    SessionState,
)
from shopping_copilot.planning.query_planner import RuleQueryPlanner
from shopping_copilot.ranking.heuristic import HeuristicRanker
from shopping_copilot.retrieval.bm25 import BM25Retriever
from shopping_copilot.retrieval.structured import StructuredRetriever


class NegativeConstraintTest(unittest.TestCase):
    def _catalog(self, root: Path) -> Path:
        products = [
            {
                "parent_asin": "A",
                "title": "Black leather walking shoe",
                "features": ["comfortable"],
                "details": {},
                "description": ["walking shoe"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
                "price": 70,
            },
            {
                "parent_asin": "B",
                "title": "White cotton walking shoe",
                "features": ["comfortable"],
                "details": {},
                "description": ["walking shoe"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
                "price": 40,
            },
            {
                "parent_asin": "C",
                "title": "Blackberry graphic white shirt",
                "features": [],
                "details": {},
                "description": ["graphic shirt"],
                "categories": ["Clothing", "Shirts"],
                "store": "Example",
                "price": 30,
            },
            {
                "parent_asin": "D",
                "title": "Merino wool winter sock",
                "features": ["warm"],
                "details": {},
                "description": ["winter sock"],
                "categories": ["Clothing", "Socks"],
                "store": "Example",
                "price": 25,
            },
            {
                "parent_asin": "E",
                "title": "Wool-blend winter sock",
                "features": ["warm"],
                "details": {},
                "description": ["winter sock"],
                "categories": ["Clothing", "Socks"],
                "store": "Example",
                "price": 20,
            },
        ]
        path = root / "catalog.jsonl"
        path.write_text("".join(json.dumps(product) + "\n" for product in products), encoding="utf-8")
        return path

    def test_token_boundary_and_multiword_matching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            black = store.get("A")
            blackberry = store.get("C")
            merino = store.get("D")
            wool_blend = store.get("E")
            assert black and blackberry and merino and wool_blend

            self.assertTrue(product_matches_value(black, "color", "black"))
            self.assertFalse(product_matches_value(blackberry, "color", "black"))
            self.assertTrue(product_matches_value(merino, "material", "merino wool"))
            self.assertFalse(product_matches_value(wool_blend, "material", "merino wool"))
            self.assertTrue(violates_negative_slots(black, {"color": ["black"]}))
            self.assertTrue(matches_any_excluded_term(black, ("black",)))
            self.assertFalse(matches_any_excluded_term(blackberry, ("black",)))

    def test_slot_specific_brand_and_category_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            product = store.get("B")
            assert product
            self.assertTrue(product_matches_value(product, "brand", "Example"))
            self.assertTrue(product_matches_value(product, "category", "Shoes"))
            self.assertFalse(product_matches_value(product, "category", "Shirts"))

    def test_bm25_filters_and_uses_expansion_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            retriever = BM25Retriever(store)
            state = SessionState(
                "bm25",
                intent_mode="buying",
                active_slots={"category": ["socks"]},
                negative_slots={"material": ["merino wool"]},
            )
            config = load_config("configs/final.json")
            plan = RuleQueryPlanner(config.search).build(state)
            plan = replace(plan, candidate_k=1)

            result = retriever.retrieve(plan)
            self.assertEqual([candidate.parent_asin for candidate in result.candidates], ["E"])

    def test_structured_retriever_uses_same_exclusion_matcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            retriever = StructuredRetriever(store)
            state = SessionState(
                "structured",
                intent_mode="buying",
                active_slots={"material": ["wool"]},
                negative_slots={"material": ["merino wool"]},
            )
            config = load_config("configs/final.json")
            plan = replace(RuleQueryPlanner(config.search).build(state), candidate_k=10)

            result = retriever.retrieve(plan)
            self.assertNotIn("D", {candidate.parent_asin for candidate in result.candidates})
            self.assertIn("E", {candidate.parent_asin for candidate in result.candidates})

    def test_ranker_guard_filters_manual_dense_like_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            config = load_config("configs/final.json")
            ranker = HeuristicRanker(store, config.ranking)
            state = SessionState(
                "ranker",
                intent_mode="buying",
                active_slots={"category": ["shoes"]},
                negative_slots={"color": ["black"]},
            )
            plan = RuleQueryPlanner(config.search).build(state)
            result = RetrievalResult(
                candidates=(
                    Candidate("A", dense_score=1.0, source_routes=("dense",)),
                    Candidate("B", dense_score=0.5, source_routes=("dense",)),
                    Candidate("C", dense_score=0.9, source_routes=("dense",)),
                ),
                diagnostics=RetrievalDiagnostics(3, 1, 0.0, plan.route),
            )

            ranked = ranker.rank(state, plan, result)
            self.assertEqual([item.parent_asin for item in ranked], ["B", "C"])

    def test_all_route_candidates_and_plan_projection_share_negative_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            state = SessionState(
                "projection",
                intent_mode="buying",
                active_slots={"category": ["shoes"], "color": ["white"]},
                negative_slots={"color": ["black"], "material": ["merino wool"]},
            )
            plan = RuleQueryPlanner(load_config("configs/final.json").search).build(state)
            self.assertEqual(plan.excluded_terms, ("black", "merino wool"))
            self.assertEqual(
                set(plan.lexical_terms) & {"black", "merino", "wool"},
                set(),
            )
            for product in store.products:
                if product.parent_asin in {"A", "D"}:
                    self.assertTrue(violates_negative_slots(product, state.negative_slots))


if __name__ == "__main__":
    unittest.main()
