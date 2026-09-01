from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a clean participant submission archive")
    parser.add_argument("--output", default="artifacts/submission.zip")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:
        bundle = Path(directory) / "submission"
        bundle.mkdir()

        # Keep the official single-file entry point while also including the
        # repository package used by the public evaluator and reproduction
        # commands documented in README.md.
        shutil.copy2(root / "starter" / "agent.py", bundle / "agent.py")
        ignored = shutil.ignore_patterns("__pycache__", "*.pyc")
        for package in ("shopping_copilot", "starter", "evaluator", "tests"):
            shutil.copytree(root / package, bundle / package, ignore=ignored)

        # Include only the selected production config and the scripts needed to
        # verify data and reproduce the public evaluation. This avoids bundling
        # local or superseded experiment settings.
        included_files = (
            "README.md",
            "requirements.txt",
            "DATA_ATTRIBUTION.md",
            "configs/baseline.json",
            "configs/final.json",
            "configs/experiments/e13_soft_budget_on_full_rank.json",
            "configs/experiments/e17_stable_candidate_pool.json",
            "configs/experiments/e27_override_balanced_coverage.json",
            "configs/experiments/information_gain_policy.json",
            "configs/experiments/recommend_10.json",
            "configs/experiments/stagnation_coverage_catalog_prior.json",
            "configs/experiments/stagnation_coverage.json",
            "configs/experiments/structured_hybrid.json",
            "scripts/run_evaluation.py",
            "scripts/verify_data.py",
            "data/README.md",
            "data/public_set.jsonl",
            "experiments/registry.csv",
            "docs/agent_api_contract.json",
            "docs/competition_specification.md",
            "docs/evaluation_config.json",
            "docs/submission_rules.md",
            "docs/e27_production_integration.md",
            "docs/devpost_submission_description.md",
            "docs/submission_compliance_checklist.md",
            "docs/team/architecture.md",
            "docs/team/ownership.md",
        )
        for relative_name in included_files:
            source = root / relative_name
            destination = bundle / relative_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle.parent))

    print(f"Built {output}")


if __name__ == "__main__":
    main()
