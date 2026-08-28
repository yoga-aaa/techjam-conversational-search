from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import load_config
from shopping_copilot.ranking.profile_affinity import CatalogProfileAffinityScorer


class ProfileAffinityTest(unittest.TestCase):
    @staticmethod
    def _catalog(root: Path) -> Path:
        products = [
            {
                "parent_asin": "A",
                "title": "Soft cushioned walking shoe",
                "features": ["comfortable padded lining"],
                "categories": ["Clothing", "Shoes"],
            },
            {
                "parent_asin": "B",
                "title": "Rugged trail shoe",
                "features": ["reinforced construction"],
                "categories": ["Clothing", "Shoes"],
            },
            {
                "parent_asin": "C",
                "title": "Classic casual shoe",
                "features": ["soft lining"],
                "categories": ["Clothing", "Shoes"],
            },
        ]
        path = root / "catalog.jsonl"
        path.write_text(
            "".join(json.dumps(item) + "\n" for item in products),
            encoding="utf-8",
        )
        return path

    def test_expanded_aspect_matches_without_literal_profile_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            scorer = CatalogProfileAffinityScorer(store)

            rugged = scorer.score(("durability",), store.get("B"))
            comfort_only = scorer.score(("durability",), store.get("A"))

            self.assertEqual(rugged, 1.0)
            self.assertEqual(comfort_only, 0.0)

    def test_rare_profile_aspect_has_more_weight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CatalogStore(self._catalog(Path(directory)))
            scorer = CatalogProfileAffinityScorer(store)

            comfort = scorer.score(("comfort", "durability"), store.get("A"))
            durability = scorer.score(("comfort", "durability"), store.get("B"))

            self.assertGreater(durability, comfort)

    def test_experiment_enables_profile_affinity(self) -> None:
        config = load_config("configs/experiments/profile_affinity.json")

        self.assertTrue(config.ranking.profile_affinity_enabled)


if __name__ == "__main__":
    unittest.main()
