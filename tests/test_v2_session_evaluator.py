from __future__ import annotations

import unittest

from evaluator.v2_session_evaluator import select_samples


class V2SessionEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.samples = [
            {"sample_id": "b", "split": "holdout"},
            {"sample_id": "c", "split": "development"},
            {"sample_id": "a"},
        ]

    def test_development_is_default_for_legacy_rows(self) -> None:
        selected = select_samples(self.samples, split="development", limit=None)

        self.assertEqual([sample["sample_id"] for sample in selected], ["a", "c"])

    def test_holdout_selection_is_explicit_and_limit_is_deterministic(self) -> None:
        selected = select_samples(self.samples, split="holdout", limit=1)

        self.assertEqual([sample["sample_id"] for sample in selected], ["b"])

    def test_rejects_non_positive_limit(self) -> None:
        with self.assertRaises(ValueError):
            select_samples(self.samples, split="all", limit=0)


if __name__ == "__main__":
    unittest.main()
