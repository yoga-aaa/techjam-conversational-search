from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_v2_holdout import (
    REQUIRED_ASSETS,
    TestkitPaths,
    _safe_run_id,
    build_markdown_report,
    resolve_testkit,
    validate_testkit,
)


class V2HoldoutRunnerTest(unittest.TestCase):
    def _testkit(self, root: Path) -> TestkitPaths:
        payloads = {
            "private_sessions": b'{"sample_id":"s"}\n',
            "response_bank": b'{"sample_id":"s"}\n',
            "catalog": b'{"parent_asin":"A"}\n',
            "templates": b'{"groups":{}}\n',
            "modes": b'{"modes":{}}\n',
        }
        paths: dict[str, Path] = {}
        for name, relative in REQUIRED_ASSETS.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            paths[name] = path
            if name in payloads:
                path.write_bytes(payloads[name])
        manifest = {
            "package_schema_version": 1,
            "package_tag": "fixture",
            "package_variant": "simplified",
        }
        for name in ("private_sessions", "response_bank"):
            manifest[name] = {
                "sha256": hashlib.sha256(payloads[name]).hexdigest(),
            }
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
        return TestkitPaths(root=root, **paths)

    def test_resolves_and_validates_explicit_testkit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = self._testkit(Path(directory))

            resolved = resolve_testkit(expected.root)
            result = validate_testkit(resolved)

        self.assertEqual(resolved.root, expected.root.resolve())
        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            result["assets"]["private_sessions"]["row_count"],
            1,
        )

    def test_validation_rejects_tampered_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._testkit(Path(directory))
            paths.private_sessions.write_text("tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_testkit(paths)

    def test_run_id_is_safe_for_a_directory(self) -> None:
        self.assertEqual(_safe_run_id("holdout run: 1"), "holdout-run-1")
        with self.assertRaises(ValueError):
            _safe_run_id("***")

    def test_markdown_report_contains_methodology_and_holdout_warning(self) -> None:
        summary = {
            "run": {
                "run_id": "fixture",
                "status": "completed",
                "started_at": "2026-01-01T00:00:00+08:00",
                "finished_at": "2026-01-01T00:00:01+08:00",
                "duration_seconds": 1.0,
                "config_path": "configs/final.json",
                "git": {
                    "branch": "test",
                    "commit": "0123456789abcdef",
                    "dirty": False,
                },
            },
            "stages": {
                name: {"status": "passed", "duration_seconds": 0.1}
                for name in ("integrity", "sessions", "language")
            },
            "results": {},
        }

        report = build_markdown_report(summary)

        self.assertIn("V2 Holdout 一键测试报告", report)
        self.assertIn("数据完整性", report)
        self.assertIn("只保留两种评分模式", report)
        self.assertIn("不是主办方私有集成绩", report)


if __name__ == "__main__":
    unittest.main()
