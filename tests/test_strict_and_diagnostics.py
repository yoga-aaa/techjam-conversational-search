from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import SearchPlan
from shopping_copilot.retrieval.bm25 import BM25Retriever
from starter.agent import Agent


def _plan(
    *,
    lexical_terms: tuple[str, ...],
    constraints: dict[str, tuple[str, ...]],
    candidate_k: int = 60,
) -> SearchPlan:
    return SearchPlan(
        route="buying",
        lexical_terms=lexical_terms,
        semantic_query=" ".join(lexical_terms),
        structured_constraints=constraints,
        hard_filters={},
        excluded_terms=(),
        bm25_weight=0.55,
        dense_weight=0.20,
        constraint_weight=0.20,
        profile_weight=0.05,
        candidate_k=candidate_k,
        use_dense=False,
        use_structured=False,
        diversity_enabled=False,
    )


class StrictAndDiagnosticsTest(unittest.TestCase):
    def _store(self, root: Path) -> CatalogStore:
        products = [
            {
                "parent_asin": f"WOMEN{index:02d}",
                "title": f"Women model {index:02d}",
                "features": [],
                "details": {},
                "description": [],
                "categories": ["Other"],
                "store": "Example",
            }
            for index in range(100)
        ]
        products.extend(
            {
                "parent_asin": f"COTTON{index:02d}",
                "title": f"Cotton model {index:02d}",
                "features": [],
                "details": {},
                "description": [],
                "categories": ["Other"],
                "store": "Example",
            }
            for index in range(100)
        )
        products.append({
            "parent_asin": "TARGET",
            "title": "Target model",
            "features": [],
            "details": {},
            "description": ["cotton " + " ".join(f"filler{index}" for index in range(200))],
            "categories": ["Women"],
            "store": "Women",
        })
        path = root / "catalog.jsonl"
        path.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )
        return CatalogStore(path)

    def test_strict_route_is_opt_in_and_recovers_candidate_below_broad_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            clean_retriever = BM25Retriever(store)
            experimental_retriever = BM25Retriever(
                store,
                strict_rescue_enabled=True,
            )
            plan = _plan(
                lexical_terms=("women", "cotton"),
                constraints={"category": ("women",), "material": ("cotton",)},
            )

            broad = clean_retriever.broad_candidates(plan, 60)
            strict = experimental_retriever.strict_candidates(plan, 10)
            clean_live = clean_retriever.retrieve(plan).candidates
            experimental_live = experimental_retriever.retrieve(plan).candidates

            self.assertNotIn("TARGET", {candidate.parent_asin for candidate in broad})
            self.assertEqual(strict[0].parent_asin, "TARGET")
            self.assertIn(
                '{title store} : "women"',
                experimental_retriever._strict_expression(plan),
            )
            self.assertEqual(clean_live, broad)
            self.assertNotIn(
                "TARGET",
                {candidate.parent_asin for candidate in clean_live},
            )
            self.assertEqual(experimental_live[:60], broad)
            self.assertIn(
                "TARGET",
                {candidate.parent_asin for candidate in experimental_live[60:]},
            )
            target = next(
                candidate
                for candidate in experimental_live
                if candidate.parent_asin == "TARGET"
            )
            self.assertGreater(target.lexical_score, 0.0)
            self.assertEqual(target.source_routes, ("bm25", "bm25:strict_and"))

    def test_strict_route_is_disabled_for_one_or_duplicate_slot_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            retriever = BM25Retriever(self._store(Path(directory)))

            one_group = _plan(
                lexical_terms=("women",),
                constraints={"category": ("women",)},
            )
            duplicate_groups = _plan(
                lexical_terms=("women",),
                constraints={"category": ("women",), "feature": ("women",)},
            )

            self.assertEqual(retriever._strict_expression(one_group), "")
            self.assertEqual(retriever._strict_expression(duplicate_groups), "")
            self.assertEqual(retriever.strict_candidates(one_group, 10), ())
            self.assertEqual(retriever.strict_candidates(duplicate_groups, 10), ())

    def _clarification_response(
        self,
        root: Path,
        *,
        full_rerank: bool,
    ) -> dict:
        catalog = self._store(root).catalog_path
        config = json.loads(Path("configs/final.json").read_text(encoding="utf-8"))
        config["search"]["probe_overload_threshold"] = 1
        config["search"]["strict_rescue_enabled"] = True
        config["ranking"]["constraint_weight_step"] = 0.30
        config["ranking"]["maximum_constraint_weight"] = 0.95
        config["policy"]["full_rerank_clarification_response"] = full_rerank
        config_path = root / f"config-full-rerank-{full_rerank}.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        agent = Agent(catalog, config_path=config_path)
        agent.reset(f"full-rerank-{full_rerank}", {})
        return agent.respond(
            f"full-rerank-{full_rerank}",
            "I'm looking for women. A key requirement is: cotton.",
            1,
            10,
        )

    def test_clean_clarification_response_reranks_only_bounded_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = self._clarification_response(
                Path(directory),
                full_rerank=False,
            )

            self.assertNotIn(
                "TARGET",
                {item["parent_asin"] for item in response["recommendations"]},
            )

    def test_rejected_full_rerank_response_remains_explicitly_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = self._clarification_response(
                Path(directory),
                full_rerank=True,
            )

            self.assertIn(
                "TARGET",
                {item["parent_asin"] for item in response["recommendations"]},
            )

    def test_final_config_disables_rejected_experiments(self) -> None:
        config = load_config("configs/final.json")

        self.assertFalse(config.search.strict_rescue_enabled)
        self.assertFalse(config.policy.full_rerank_clarification_response)
        self.assertFalse(config.policy.adaptive_semantic_slate)
        self.assertEqual(config.ranking.semantic_priority_mode, "legacy")


if __name__ == "__main__":
    unittest.main()
