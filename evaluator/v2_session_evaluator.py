"""Evaluate the current Agent against fixed V2 synthetic sessions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_samples(
    samples: list[dict[str, Any]],
    *,
    split: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    if split not in {"development", "holdout", "all"}:
        raise ValueError("split must be development, holdout, or all")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive when supplied")
    selected = (
        list(samples)
        if split == "all"
        else [
            sample
            for sample in samples
            if str(sample.get("split") or "development") == split
        ]
    )
    selected.sort(key=lambda sample: str(sample.get("sample_id") or ""))
    return selected[:limit] if limit is not None else selected


def evaluate_v2_sessions(
    *,
    dataset_path: str | Path,
    catalog_path: str | Path,
    config_path: str | Path,
    split: str = "development",
    limit: int | None = None,
) -> dict[str, Any]:
    samples = select_samples(load_jsonl(dataset_path), split=split, limit=limit)
    if not samples:
        raise ValueError("no V2 sessions match the requested selection")
    config_path = Path(config_path).resolve()
    catalog_ids, categories, products = catalog_index(catalog_path)
    result = evaluate(
        Agent(catalog_path, config_path=config_path),
        samples,
        catalog_ids,
        categories,
        products,
    )
    return {
        "schema_version": 1,
        "evaluator": {"name": "shopping_copilot_v2_session_evaluator", "version": "1.1.0"},
        "inputs": {
            "dataset": {"path": str(Path(dataset_path).resolve()), "sha256": _sha256_file(dataset_path)},
            "catalog": {"path": str(Path(catalog_path).resolve()), "sha256": _sha256_file(catalog_path)},
            "config": {"path": str(config_path), "sha256": _sha256_file(config_path)},
        },
        "selection": {"split": split, "limit": limit, "session_count": len(samples)},
        **result,
    }
