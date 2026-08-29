from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one pre-registered manifest split")
    parser.add_argument("--config", required=True)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--split", choices=("tune", "validation", "holdout"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.environ["SHOPPING_COPILOT_CONFIG"] = args.config
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assignments = manifest["assignments"]
    samples = [
        sample
        for sample in load_jsonl(args.dataset)
        if assignments.get(sample["sample_id"]) == args.split
    ]
    ids, categories, products = catalog_index(args.catalog)
    result = evaluate(Agent(args.catalog), samples, ids, categories, products)
    result["split"] = args.split
    result["config_sha256"] = hashlib.sha256(Path(args.config).read_bytes()).hexdigest()
    result["catalog_sha256"] = hashlib.sha256(Path(args.catalog).read_bytes()).hexdigest()
    result["dataset_sha256"] = hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
