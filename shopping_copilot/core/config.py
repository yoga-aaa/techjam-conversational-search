from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchConfig:
    implementation: str
    candidate_k: int
    probe_overload_threshold: int
    dense_enabled: bool


@dataclass(frozen=True)
class PolicyConfig:
    implementation: str
    broad_recommendation_count: int
    uncertain_recommendation_count: int
    confident_score_gap: float


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool
    path: str


@dataclass(frozen=True)
class AppConfig:
    state_implementation: str
    ranking_implementation: str
    search: SearchConfig
    policy: PolicyConfig
    trace: TraceConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "baseline.json"


def load_config(path: str | Path | None = None) -> AppConfig:
    configured_path = path or os.getenv("SHOPPING_COPILOT_CONFIG") or DEFAULT_CONFIG
    config_path = Path(configured_path)
    if not config_path.is_absolute() and not config_path.exists():
        config_path = PROJECT_ROOT / config_path
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    search = payload.get("search", {})
    policy = payload.get("policy", {})
    trace = payload.get("trace", {})

    result = AppConfig(
        state_implementation=str(payload.get("state", {}).get("implementation", "rule")),
        ranking_implementation=str(payload.get("ranking", {}).get("implementation", "heuristic")),
        search=SearchConfig(
            implementation=str(search.get("implementation", "hybrid")),
            candidate_k=max(10, int(search.get("candidate_k", 100))),
            probe_overload_threshold=max(10, int(search.get("probe_overload_threshold", 2000))),
            dense_enabled=bool(search.get("dense_enabled", False)),
        ),
        policy=PolicyConfig(
            implementation=str(policy.get("implementation", "heuristic")),
            broad_recommendation_count=max(0, int(policy.get("broad_recommendation_count", 3))),
            uncertain_recommendation_count=max(0, int(policy.get("uncertain_recommendation_count", 5))),
            confident_score_gap=max(0.0, float(policy.get("confident_score_gap", 0.15))),
        ),
        trace=TraceConfig(
            enabled=bool(trace.get("enabled", False)),
            path=str(
                Path(trace.get("path", "artifacts/traces.jsonl"))
                if Path(trace.get("path", "artifacts/traces.jsonl")).is_absolute()
                else PROJECT_ROOT / Path(trace.get("path", "artifacts/traces.jsonl"))
            ),
        ),
    )

    if result.state_implementation != "rule":
        raise ValueError(f"Unsupported state implementation: {result.state_implementation}")
    if result.ranking_implementation != "heuristic":
        raise ValueError(f"Unsupported ranking implementation: {result.ranking_implementation}")
    if result.search.implementation != "hybrid":
        raise ValueError(f"Unsupported search implementation: {result.search.implementation}")
    if result.policy.implementation != "heuristic":
        raise ValueError(f"Unsupported policy implementation: {result.policy.implementation}")

    return result
