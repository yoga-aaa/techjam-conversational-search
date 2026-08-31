from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evaluator.local_evaluator import catalog_index, load_jsonl
from shopping_copilot.evaluation.shadow import shadow_evaluate
from starter.agent import Agent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all ten turns and record the target rank after every turn"
    )
    parser.add_argument("--config", default="configs/final.json")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="output/shadow_results.json")
    args = parser.parse_args()

    os.environ["SHOPPING_COPILOT_CONFIG"] = args.config
    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    result = shadow_evaluate(Agent(args.catalog), samples, catalog_ids, categories, products)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "sessions"}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
