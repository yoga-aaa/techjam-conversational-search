from __future__ import annotations

import json
import tempfile
import unittest
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
                load_config("configs/experiments/full_rank.json"),
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


if __name__ == "__main__":
    unittest.main()
