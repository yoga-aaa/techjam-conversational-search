from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SessionState
from shopping_copilot.core.factory import build_components


class EvidenceRankerTest(unittest.TestCase):
    def test_complete_phrase_evidence_dominates_partial_match(self) -> None:
        products = [
            {
                "parent_asin": "A",
                "title": "Easy care shirt",
                "features": ["machine washable", "lightweight"],
                "categories": ["Clothing", "Shirts"],
                "store": "Example",
            },
            {
                "parent_asin": "B",
                "title": "Basic shirt",
                "features": ["machine washable"],
                "categories": ["Clothing", "Shirts"],
                "store": "Example",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.jsonl"
            catalog.write_text(
                "".join(json.dumps(product) + "\n" for product in products),
                encoding="utf-8",
            )
            components = build_components(
                catalog,
                load_config("configs/final.json"),
            )
            state = SessionState(
                "evidence",
                active_slots={"category": ["shirts"], "other": ["machine washable", "lightweight"]},
            )
            plan = components.planner.build(state)
            result = RetrievalResult(
                candidates=(Candidate("A", lexical_score=1.0), Candidate("B", lexical_score=1.0)),
                diagnostics=RetrievalDiagnostics(2, 1, 0.0, plan.route),
            )

            ranked = components.ranker.rank(state, plan, result)

            self.assertEqual(ranked[0].parent_asin, "A")
            self.assertEqual(ranked[0].component_scores["matched_constraint_count"], 2.0)
            self.assertEqual(ranked[1].component_scores["matched_constraint_count"], 1.0)

    def test_field_token_fallback_matches_reordered_words_only_within_one_field(self) -> None:
        products = [
            {
                "parent_asin": "A",
                "title": "Winter shirt",
                "features": ["washable in a machine"],
                "categories": ["Clothing", "Shirts"],
                "store": "Example",
            },
            {
                "parent_asin": "B",
                "title": "Machine shirt",
                "features": ["washable"],
                "categories": ["Clothing", "Shirts"],
                "store": "Example",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.jsonl"
            catalog.write_text("".join(json.dumps(product) + "\n" for product in products), encoding="utf-8")
            base_config = load_config("configs/final.json")
            components = build_components(
                catalog,
                replace(
                    base_config,
                    ranking=replace(
                        base_config.ranking,
                        evidence_field_token_fallback=True,
                    ),
                ),
            )
            state = SessionState("field-token", active_slots={"other": ["machine washable"]})
            plan = components.planner.build(state)
            result = RetrievalResult(
                candidates=(Candidate("A"), Candidate("B")),
                diagnostics=RetrievalDiagnostics(2, 1, 0.0, plan.route),
            )

            ranked = components.ranker.rank(state, plan, result)
            by_id = {item.parent_asin: item for item in ranked}

            self.assertEqual(by_id["A"].component_scores["matched_constraint_count"], 1.0)
            self.assertEqual(by_id["B"].component_scores["matched_constraint_count"], 0.0)


if __name__ == "__main__":
    unittest.main()
