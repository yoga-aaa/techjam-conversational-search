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
    structured_enabled: bool
    strict_rescue_enabled: bool
    protected_rescue_enabled: bool = False
    protected_rescue_fetch_k: int = 60
    protected_rescue_pool_size: int = 10
    protected_rescue_min_slot_matches: int = 2


@dataclass(frozen=True)
class PolicyConfig:
    implementation: str
    broad_recommendation_count: int
    uncertain_recommendation_count: int
    confident_score_gap: float
    question_strategy: str
    question_candidate_limit: int
    question_min_coverage: float
    question_min_score: float
    question_attribute_priors: dict[str, float]
    coverage_enabled: bool
    coverage_candidate_limit: int
    coverage_overlap_threshold: float
    coverage_min_turn: int
    coverage_stagnant_turns: int
    adaptive_semantic_slate: bool
    adaptive_semantic_slate_mode: str
    adaptive_semantic_slate_anchor: str
    full_rerank_clarification_response: bool
    protected_rescue_head_size: int = 7
    protected_rescue_quota: int = 3
    protected_rescue_rank_limit: int = 10


@dataclass(frozen=True)
class RankingConfig:
    implementation: str
    dynamic_constraint_weight: bool
    constraint_weight_step: float
    maximum_constraint_weight: float
    rarity_weighting: bool
    semantic_priority_mode: str


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool
    path: str


@dataclass(frozen=True)
class AppConfig:
    state_implementation: str
    ranking: RankingConfig
    search: SearchConfig
    policy: PolicyConfig
    trace: TraceConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "final.json"


def load_config(path: str | Path | None = None) -> AppConfig:
    configured_path = path or os.getenv("SHOPPING_COPILOT_CONFIG") or DEFAULT_CONFIG
    config_path = Path(configured_path)
    if not config_path.is_absolute() and not config_path.exists():
        config_path = PROJECT_ROOT / config_path
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    search = payload.get("search", {})
    ranking = payload.get("ranking", {})
    policy = payload.get("policy", {})
    trace = payload.get("trace", {})

    result = AppConfig(
        state_implementation=str(payload.get("state", {}).get("implementation", "rule")),
        ranking=RankingConfig(
            implementation=str(ranking.get("implementation", "heuristic")),
            dynamic_constraint_weight=bool(ranking.get("dynamic_constraint_weight", False)),
            constraint_weight_step=max(0.0, float(ranking.get("constraint_weight_step", 0.08))),
            maximum_constraint_weight=max(
                0.0,
                min(1.0, float(ranking.get("maximum_constraint_weight", 0.55))),
            ),
            rarity_weighting=bool(ranking.get("rarity_weighting", False)),
            semantic_priority_mode=str(
                ranking.get(
                    "semantic_priority_mode",
                    "global_idf_first"
                    if ranking.get("global_idf_semantic_priority", False)
                    else "legacy",
                )
            ),
        ),
        search=SearchConfig(
            implementation=str(search.get("implementation", "hybrid")),
            candidate_k=max(10, int(search.get("candidate_k", 100))),
            probe_overload_threshold=max(10, int(search.get("probe_overload_threshold", 2000))),
            dense_enabled=bool(search.get("dense_enabled", False)),
            structured_enabled=bool(search.get("structured_enabled", False)),
            strict_rescue_enabled=bool(search.get("strict_rescue_enabled", False)),
            protected_rescue_enabled=bool(search.get("protected_rescue_enabled", False)),
            protected_rescue_fetch_k=max(
                10,
                min(200, int(search.get("protected_rescue_fetch_k", 60))),
            ),
            protected_rescue_pool_size=max(
                1,
                min(20, int(search.get("protected_rescue_pool_size", 10))),
            ),
            protected_rescue_min_slot_matches=max(
                2,
                min(8, int(search.get("protected_rescue_min_slot_matches", 2))),
            ),
        ),
        policy=PolicyConfig(
            implementation=str(policy.get("implementation", "heuristic")),
            broad_recommendation_count=max(0, int(policy.get("broad_recommendation_count", 3))),
            uncertain_recommendation_count=max(0, int(policy.get("uncertain_recommendation_count", 5))),
            confident_score_gap=max(0.0, float(policy.get("confident_score_gap", 0.15))),
            question_strategy=str(policy.get("question_strategy", "fixed")),
            question_candidate_limit=max(10, int(policy.get("question_candidate_limit", 60))),
            question_min_coverage=max(0.0, min(1.0, float(policy.get("question_min_coverage", 0.12)))),
            question_min_score=max(0.0, float(policy.get("question_min_score", 0.01))),
            question_attribute_priors={
                str(name): max(0.0, float(value))
                for name, value in (policy.get("question_attribute_priors") or {}).items()
            },
            coverage_enabled=bool(policy.get("coverage_enabled", False)),
            coverage_candidate_limit=max(10, int(policy.get("coverage_candidate_limit", 60))),
            coverage_overlap_threshold=max(
                0.0,
                min(1.0, float(policy.get("coverage_overlap_threshold", 0.90))),
            ),
            coverage_min_turn=max(1, int(policy.get("coverage_min_turn", 2))),
            coverage_stagnant_turns=max(1, int(policy.get("coverage_stagnant_turns", 1))),
            adaptive_semantic_slate=bool(policy.get("adaptive_semantic_slate", False)),
            adaptive_semantic_slate_mode=str(
                policy.get("adaptive_semantic_slate_mode", "count")
            ),
            adaptive_semantic_slate_anchor=str(
                policy.get("adaptive_semantic_slate_anchor", "rank_one")
            ),
            full_rerank_clarification_response=bool(
                policy.get("full_rerank_clarification_response", False)
            ),
            protected_rescue_head_size=max(
                0,
                min(9, int(policy.get("protected_rescue_head_size", 7))),
            ),
            protected_rescue_quota=max(
                1,
                min(10, int(policy.get("protected_rescue_quota", 3))),
            ),
            protected_rescue_rank_limit=max(
                1,
                min(60, int(policy.get("protected_rescue_rank_limit", 10))),
            ),
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
    if result.ranking.implementation != "heuristic":
        raise ValueError(f"Unsupported ranking implementation: {result.ranking.implementation}")
    if result.search.implementation != "hybrid":
        raise ValueError(f"Unsupported search implementation: {result.search.implementation}")
    if result.policy.implementation != "heuristic":
        raise ValueError(f"Unsupported policy implementation: {result.policy.implementation}")
    if result.policy.question_strategy not in {"fixed", "information_gain"}:
        raise ValueError(f"Unsupported question strategy: {result.policy.question_strategy}")
    if result.policy.adaptive_semantic_slate_mode not in {"count", "exact"}:
        raise ValueError(
            "Unsupported adaptive semantic slate mode: "
            f"{result.policy.adaptive_semantic_slate_mode}"
        )
    if result.policy.adaptive_semantic_slate_anchor not in {
        "rank_one",
        "strongest_semantic_tier",
    }:
        raise ValueError(
            "Unsupported adaptive semantic slate anchor: "
            f"{result.policy.adaptive_semantic_slate_anchor}"
        )
    if result.ranking.semantic_priority_mode not in {
        "legacy",
        "global_idf_first",
        "match_count_then_global_idf",
    }:
        raise ValueError(
            "Unsupported semantic priority mode: "
            f"{result.ranking.semantic_priority_mode}"
        )
    if result.policy.protected_rescue_head_size + result.policy.protected_rescue_quota > 10:
        raise ValueError("Protected rescue head size plus quota must not exceed 10")
    if result.search.protected_rescue_enabled and result.search.strict_rescue_enabled:
        raise ValueError("Protected rescue and legacy Strict append are mutually exclusive")
    if (
        result.search.protected_rescue_enabled
        and result.policy.full_rerank_clarification_response
    ):
        raise ValueError("Protected rescue requires bounded clarification reranking")

    return result
