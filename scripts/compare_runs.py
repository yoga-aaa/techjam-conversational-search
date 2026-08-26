from __future__ import annotations

import argparse
import json
from pathlib import Path


METRICS = ("hit_rate_at_10", "mrr", "mttc", "efficiency", "recommended_technical_score")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two official evaluator result files")
    parser.add_argument("before")
    parser.add_argument("after")
    args = parser.parse_args()

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    comparison = {
        metric: {
            "before": before.get(metric),
            "after": after.get(metric),
            "delta": round(float(after.get(metric, 0)) - float(before.get(metric, 0)), 6),
        }
        for metric in METRICS
        if before.get(metric) is not None and after.get(metric) is not None
    }
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
