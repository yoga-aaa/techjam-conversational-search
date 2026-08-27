"""Build a deterministic portable ZIP containing only the two V2 evaluations."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / "packaging" / "portable_v2_tests"
DEFAULT_TAG = "20260827"
ASSETS = {
    "private_sessions": Path("data/synthetic/generated/v2/private_sessions.jsonl"),
    "response_bank": Path("data/synthetic/generated/v2/response_bank.jsonl"),
    "catalog": Path("data/catalog.jsonl"),
    "templates": Path("data/templates/user_utterance_templates.json"),
    "modes": Path("configs/language_stress_modes.json"),
}
ENGINE_SOURCES = {
    "local_evaluator.py": ROOT / "evaluator" / "local_evaluator.py",
    "v2_session_evaluator.py": ROOT / "evaluator" / "v2_session_evaluator.py",
    "v2_language_support.py": ROOT / "evaluator" / "v2_language_support.py",
    "language_stress_driver.py": ROOT / "evaluator" / "language_stress_driver.py",
    "runner.py": ROOT / "scripts" / "run_v2_holdout.py",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _is_complete_testkit(path: Path) -> bool:
    return path.is_dir() and (path / "package_manifest.json").is_file() and all(
        (path / relative).is_file() for relative in ASSETS.values()
    )


def resolve_testkit(explicit: str | Path | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    parent = ROOT.parent / "_testkit"
    candidates.extend([
        parent / "techjam-test-v2-simplified-20260827",
        parent / "techjam-test-v2-full-20260827" / "techjam-test-v2-full-20260827",
    ])
    if parent.exists():
        candidates.extend(sorted(parent.glob("techjam-test-v2-*"), reverse=True))
    checked: list[str] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if str(resolved) in checked:
            continue
        checked.append(str(resolved))
        if _is_complete_testkit(resolved):
            return resolved
    raise FileNotFoundError("no complete V2 testkit found; checked: %s" % ", ".join(checked))


def _validate_source(testkit: Path) -> dict[str, Any]:
    manifest = json.loads((testkit / "package_manifest.json").read_text(encoding="utf-8"))
    for name in ("private_sessions", "response_bank"):
        path = testkit / ASSETS[name]
        expected = str((manifest.get(name) or {}).get("sha256") or "").lower()
        actual = _sha256(path)
        if not expected or actual != expected:
            raise ValueError("%s hash mismatch" % name)
        expected_count = int((manifest.get(name) or {}).get("count") or 0)
        if expected_count != _row_count(path):
            raise ValueError("%s row count mismatch" % name)
    return manifest


def _portable_source(name: str, source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    if name == "v2_session_evaluator.py":
        text = text.replace(
            "from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl",
            "from .local_evaluator import catalog_index, evaluate, load_jsonl",
        )
    elif name == "language_stress_driver.py":
        text = text.replace("from evaluator.local_evaluator import (", "from .local_evaluator import (")
        text = text.replace("from evaluator.v2_language_support import (", "from .v2_language_support import (")
    elif name == "runner.py":
        text = text.replace(
            "from evaluator.language_stress_driver import DEFAULT_MODES, run_language_stress  # noqa: E402",
            "from .language_stress_driver import DEFAULT_MODES, run_language_stress  # noqa: E402",
        )
        text = text.replace(
            "from evaluator.v2_session_evaluator import evaluate_v2_sessions  # noqa: E402",
            "from .v2_session_evaluator import evaluate_v2_sessions  # noqa: E402",
        )
        old_root = "ROOT = Path(__file__).resolve().parents[1]"
        new_root = "ROOT = Path(os.environ.get('PORTABLE_TARGET_PROJECT_ROOT') or Path(__file__).resolve().parents[1])"
        if old_root not in text:
            raise ValueError("runner ROOT declaration changed; update portable patch")
        text = text.replace(old_root, new_root, 1)
    return text


def _copy_engine(bundle: Path) -> dict[str, str]:
    destination = bundle / "engine" / "portable_v2_engine"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "__init__.py").write_text(
        '"""Namespaced portable V2 evaluation engine."""\n', encoding="utf-8"
    )
    hashes: dict[str, str] = {}
    for name, source in ENGINE_SOURCES.items():
        output = destination / name
        output.write_text(_portable_source(name, source), encoding="utf-8", newline="\n")
        hashes[name] = _sha256(output)
    return hashes


def _write_zip(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source.parent).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 8, 27, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_package(testkit: Path, output: Path, tag: str) -> dict[str, Any]:
    source_manifest = _validate_source(testkit)
    bundle_name = "techjam-v2-two-tests-portable-%s" % tag
    with tempfile.TemporaryDirectory() as directory:
        bundle = Path(directory) / bundle_name
        shutil.copytree(
            TEMPLATE_ROOT,
            bundle,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        engine_hashes = _copy_engine(bundle)
        asset_details: dict[str, dict[str, Any]] = {}
        for name, relative in ASSETS.items():
            source = testkit / relative
            destination = bundle / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            asset_details[name] = {
                "path": relative.as_posix(),
                "bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
            if destination.suffix == ".jsonl":
                asset_details[name]["count"] = _row_count(destination)

        manifest = {
            "package_schema_version": 1,
            "package_tag": tag,
            "package_variant": "portable-two-tests",
            "tests": ["standard_sessions", "wording_pressure"],
            "source_package": {
                "tag": source_manifest.get("package_tag"),
                "variant": source_manifest.get("package_variant"),
            },
            "private_sessions": {
                "count": asset_details["private_sessions"]["count"],
                "sha256": asset_details["private_sessions"]["sha256"],
            },
            "response_bank": {
                "count": asset_details["response_bank"]["count"],
                "sha256": asset_details["response_bank"]["sha256"],
            },
            "assets": asset_details,
            "engine_sha256": engine_hashes,
        }
        (bundle / "package_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_zip(bundle, output)
    checksum = _sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        "%s  %s\n" % (checksum, output.name), encoding="utf-8"
    )
    return {"output": str(output), "bytes": output.stat().st_size, "sha256": checksum}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testkit", help="Source full or simplified V2 testkit.")
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--output", help="Destination ZIP path.")
    args = parser.parse_args()
    testkit = resolve_testkit(args.testkit)
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else ROOT / "packages" / ("techjam-v2-two-tests-portable-%s.zip" % args.tag)
    )
    result = build_package(testkit, output, args.tag)
    print(json.dumps({"testkit": str(testkit), **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
