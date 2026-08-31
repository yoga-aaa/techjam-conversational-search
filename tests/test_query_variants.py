from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import SearchPlan
from shopping_copilot.retrieval.bm25 import BM25Retriever
from shopping_copilot.retrieval.query_variants import LexicalQueryVariantBuilder


def _plan() -> SearchPlan:
    return SearchPlan(
        route="buying",
        lexical_terms=("shoes", "merino", "wool", "black"),
        semantic_query="shoes merino wool black",
        structured_constraints={
            "category": ("shoes",),
            "material": ("merino wool",),
            "color": ("black", "navy"),
        },
        hard_filters={},
        excluded_terms=(),
        bm25_weight=0.55,
        dense_weight=0.20,
        constraint_weight=0.20,
        profile_weight=0.05,
        candidate_k=10,
        use_dense=False,
        use_structured=False,
        diversity_enabled=False,
    )


class QueryVariantTest(unittest.TestCase):
    def test_builder_emits_fixed_safe_query_family_with_broad_fallback(self) -> None:
        variants = LexicalQueryVariantBuilder().build(_plan())

        self.assertEqual(
            [variant.name for variant in variants],
            ["strict_all_tokens", "slot_phrase_and", "fielded_slot_and", "category_anchor", "broad_or"],
        )
        self.assertEqual(variants[-1].expression, '"shoes" OR "merino" OR "wool" OR "black"')
        self.assertIn('{categories}: "shoes"', variants[2].expression)
        self.assertIn('"merino wool"', variants[1].expression)

    def test_multiquery_executes_fielded_routes_and_is_deterministic(self) -> None:
        products = [
            {
                "parent_asin": "A",
                "title": "Black merino wool walking shoes",
                "features": ["warm"],
                "details": {},
                "description": ["winter"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
            },
            {
                "parent_asin": "B",
                "title": "Black shoes",
                "features": ["comfortable"],
                "details": {},
                "description": ["walking"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            path.write_text("".join(json.dumps(item) + "\n" for item in products), encoding="utf-8")
            store = CatalogStore(path)
            search_config = replace(
                load_config("configs/final.json").search,
                lexical_multiquery_override_only=False,
            )
            retriever = BM25Retriever(store, search_config)

            first = retriever.retrieve(_plan())
            second = retriever.retrieve(_plan())

            self.assertEqual(first, second)
            self.assertEqual(first.candidates[0].parent_asin, "A")
            self.assertIn("bm25:strict_all_tokens", first.candidates[0].source_routes)

    def test_multiquery_can_preserve_original_broad_query_score(self) -> None:
        products = [
            {
                "parent_asin": "A",
                "title": "Black merino wool walking shoes",
                "features": ["warm"],
                "details": {},
                "description": ["winter"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
            },
            {
                "parent_asin": "B",
                "title": "Black shoes",
                "features": ["comfortable"],
                "details": {},
                "description": ["walking"],
                "categories": ["Clothing", "Shoes"],
                "store": "Example",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            path.write_text("".join(json.dumps(item) + "\n" for item in products), encoding="utf-8")
            config_path = Path(directory) / "config.json"
            payload = json.loads(Path("configs/experiments/e27_override_balanced_coverage.json").read_text())
            payload["search"]["lexical_preserve_broad_score"] = True
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_config(config_path)
            retriever = BM25Retriever(CatalogStore(path), config.search)

            plan = replace(_plan(), has_explicit_override=True)
            result = retriever.retrieve(plan)
            expected = -float(retriever.connection.execute(
                "SELECT bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
                "FROM products WHERE parent_asin = ? AND products MATCH ?",
                ("A", retriever._expression(plan)),
            ).fetchone()[0])
            candidate = next(item for item in result.candidates if item.parent_asin == "A")

            self.assertAlmostEqual(candidate.lexical_score, expected)


if __name__ == "__main__":
    unittest.main()
