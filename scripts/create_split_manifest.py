from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


QUOTAS = {
    "buying": {"tune": 48, "validation": 16, "holdout": 16},
    "browsing": {"tune": 48, "validation": 16, "holdout": 16},
    "intent_override": {"tune": 18, "validation": 6, "holdout": 6},
    "boundary": {"tune": 6, "validation": 2, "holdout": 2},
}


def difficulty(session: dict) -> str:
    turn = session.get("first_hit_turn")
    if turn is None:
        return "miss"
    if turn <= 2:
        return "turn_1_2"
    if turn <= 5:
        return "turn_3_5"
    return "turn_6_10"


def stable_key(sample_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}\0{sample_id}".encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the fixed Tune/Validation/Holdout manifest")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", default="category-anchor-gated-slate-v2")
    args = parser.parse_args()

    samples = {
        item["sample_id"]: item
        for item in (
            json.loads(line)
            for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    by_stratum: dict[tuple[str, str], list[str]] = defaultdict(list)
    for session in baseline.get("sessions", []):
        sample_id = str(session["sample_id"])
        scenario = str(session["scenario_type"])
        if sample_id in samples:
            by_stratum[(scenario, difficulty(session))].append(sample_id)

    assignments: dict[str, str] = {}
    for scenario, quota in QUOTAS.items():
        remaining = dict(quota)
        strata = [
            (stratum, sorted(ids, key=lambda sample_id: stable_key(sample_id, args.seed)))
            for (candidate_scenario, stratum), ids in by_stratum.items()
            if candidate_scenario == scenario
        ]
        strata.sort(key=lambda item: item[0])
        for stratum, ids in strata:
            for sample_id in ids:
                eligible = [name for name in ("tune", "validation", "holdout") if remaining[name] > 0]
                if not eligible:
                    raise RuntimeError(f"quota exhausted before assigning {scenario}/{stratum}")
                choice = min(eligible, key=lambda name: (remaining[name], name))
                assignments[sample_id] = choice
                remaining[choice] -= 1
        if any(value != 0 for value in remaining.values()):
            raise RuntimeError(f"unfilled quota for {scenario}: {remaining}")

    manifest = {
        "seed": args.seed,
        "dataset_sha256": hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(Path(args.baseline).read_bytes()).hexdigest(),
        "assignments": assignments,
        "counts": {
            split: sum(value == split for value in assignments.values())
            for split in ("tune", "validation", "holdout")
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    main()
