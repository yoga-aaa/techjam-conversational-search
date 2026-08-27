from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_portable_v2_tests import ASSETS, build_package


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PortableV2PackagingTest(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        rows = {
            "private_sessions": '{"sample_id":"fixture"}\n',
            "response_bank": '{"sample_id":"fixture"}\n',
            "catalog": '{"parent_asin":"B000FIXTURE"}\n',
            "templates": '{}\n',
            "modes": '{}\n',
        }
        for name, relative in ASSETS.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rows[name], encoding="utf-8")
        manifest = {
            "package_schema_version": 1,
            "package_tag": "fixture",
            "package_variant": "fixture",
            "private_sessions": {
                "count": 1,
                "sha256": _sha256(root / ASSETS["private_sessions"]),
            },
            "response_bank": {
                "count": 1,
                "sha256": _sha256(root / ASSETS["response_bank"]),
            },
        }
        (root / "package_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return root

    def test_build_is_deterministic_and_contains_only_portable_test_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            testkit = self._fixture(root / "testkit")
            first = root / "first.zip"
            second = root / "second.zip"
            first_result = build_package(testkit, first, "fixture")
            second_result = build_package(testkit, second, "fixture")
            self.assertEqual(first_result["sha256"], second_result["sha256"])
            self.assertEqual(first.read_bytes(), second.read_bytes())

            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            prefix = "techjam-v2-two-tests-portable-fixture/"
            self.assertIn(prefix + "README.md", names)
            self.assertIn(prefix + "run_tests.py", names)
            self.assertIn(prefix + "package_manifest.json", names)
            self.assertIn(
                prefix + "engine/portable_v2_engine/runner.py", names
            )
            self.assertFalse(any("sentence_cases" in name for name in names))
            self.assertFalse(any("generate_synthetic" in name for name in names))
            self.assertFalse(any("starter/agent.py" in name for name in names))
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith(".pyc") for name in names))


if __name__ == "__main__":
    unittest.main()
