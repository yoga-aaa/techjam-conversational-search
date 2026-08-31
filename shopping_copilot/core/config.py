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
    category_anchor_enabled: bool
    category_anchor_full_pool: bool
    lexical_multiquery_enabled: bool
    lexical_multiquery_override_only: bool
    lexical_max_variants: int
    lexical_fetch_multiplier: int
    lexical_rrf_k: float
    lexical_preserve_broad_score: bool
    query_hygiene_enabled: bool


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
    coverage_stratified_enabled: bool
    coverage_head_count: int
    coverage_slate_width: int
    coverage_override_zero_consensus_quota: int
    coverage_override_balance_min_stagnant: int
    other_first_enabled: bool
    other_first_max_questions: int
    slate_gate_enabled: bool
    slate_compact_count: int
    slate_expand_min_turn: int
    slate_expand_min_matches: int
    slate_expand_turn: int
    global_response_ranking: bool


@dataclass(frozen=True)
class RankingConfig:
    implementation: str
    dynamic_constraint_weight: bool
    constraint_weight_step: float
    maximum_constraint_weight: float
    rarity_weighting: bool
    specificity_weighting: bool
    specificity_weight_step: float
    all_constraints_bonus: float
    exact_base_weight: float
    exact_token_weight: float
    exact_unmatched_penalty: float
    exact_all_match_bonus: float
    exact_anchor_bonus: float
    soft_budget_as_cap: bool
    evidence_lexical_weight: float
    evidence_candidate_constraint_weight: float
    evidence_profile_weight: float
    popularity_rating_weight: float
    popularity_count_weight: float
    evidence_route_consensus_weight: float
    evidence_field_token_fallback: bool


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
            specificity_weighting=bool(ranking.get("specificity_weighting", False)),
            specificity_weight_step=max(
                0.0,
                float(ranking.get("specificity_weight_step", 0.12)),
            ),
            all_constraints_bonus=max(0.0, float(ranking.get("all_constraints_bonus", 0.0))),
            exact_base_weight=max(0.0, float(ranking.get("exact_base_weight", 0.8))),
            exact_token_weight=max(0.0, float(ranking.get("exact_token_weight", 0.55))),
            exact_unmatched_penalty=max(
                0.0,
                float(ranking.get("exact_unmatched_penalty", 0.35)),
            ),
            exact_all_match_bonus=max(
                0.0,
                float(ranking.get("exact_all_match_bonus", 4.0)),
            ),
            exact_anchor_bonus=max(0.0, float(ranking.get("exact_anchor_bonus", 20.0))),
            soft_budget_as_cap=bool(ranking.get("soft_budget_as_cap", False)),
            evidence_lexical_weight=max(0.0, float(ranking.get("evidence_lexical_weight", 0.20))),
            evidence_candidate_constraint_weight=max(
                0.0,
                float(ranking.get("evidence_candidate_constraint_weight", 0.10)),
            ),
            evidence_profile_weight=max(0.0, float(ranking.get("evidence_profile_weight", 0.10))),
            popularity_rating_weight=max(0.0, float(ranking.get("popularity_rating_weight", 0.08))),
            popularity_count_weight=max(0.0, float(ranking.get("popularity_count_weight", 0.05))),
            evidence_route_consensus_weight=max(
                0.0,
                float(ranking.get("evidence_route_consensus_weight", 0.0)),
            ),
            evidence_field_token_fallback=bool(
                ranking.get("evidence_field_token_fallback", False)
            ),
        ),
        search=SearchConfig(
            implementation=str(search.get("implementation", "hybrid")),
            candidate_k=max(10, int(search.get("candidate_k", 100))),
            probe_overload_threshold=max(10, int(search.get("probe_overload_threshold", 2000))),
            dense_enabled=bool(search.get("dense_enabled", False)),
            structured_enabled=bool(search.get("structured_enabled", False)),
            category_anchor_enabled=bool(search.get("category_anchor_enabled", False)),
            category_anchor_full_pool=bool(search.get("category_anchor_full_pool", False)),
            lexical_multiquery_enabled=bool(search.get("lexical_multiquery_enabled", False)),
            lexical_multiquery_override_only=bool(search.get("lexical_multiquery_override_only", False)),
            lexical_max_variants=max(1, min(5, int(search.get("lexical_max_variants", 5)))),
            lexical_fetch_multiplier=max(1, int(search.get("lexical_fetch_multiplier", 4))),
            lexical_rrf_k=max(1.0, float(search.get("lexical_rrf_k", 60.0))),
            lexical_preserve_broad_score=bool(
                search.get("lexical_preserve_broad_score", False)
            ),
            query_hygiene_enabled=bool(search.get("query_hygiene_enabled", False)),
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
            coverage_stratified_enabled=bool(policy.get("coverage_stratified_enabled", False)),
            coverage_head_count=max(0, int(policy.get("coverage_head_count", 3))),
            coverage_slate_width=max(1, int(policy.get("coverage_slate_width", 10))),
            coverage_override_zero_consensus_quota=max(
                0,
                int(policy.get("coverage_override_zero_consensus_quota", 0)),
            ),
            coverage_override_balance_min_stagnant=max(
                1,
                int(policy.get("coverage_override_balance_min_stagnant", 2)),
            ),
            other_first_enabled=bool(policy.get("other_first_enabled", False)),
            other_first_max_questions=max(1, int(policy.get("other_first_max_questions", 3))),
            slate_gate_enabled=bool(policy.get("slate_gate_enabled", False)),
            slate_compact_count=max(1, int(policy.get("slate_compact_count", 1))),
            slate_expand_min_turn=max(1, int(policy.get("slate_expand_min_turn", 3))),
            slate_expand_min_matches=max(1, int(policy.get("slate_expand_min_matches", 2))),
            slate_expand_turn=max(1, int(policy.get("slate_expand_turn", 5))),
            global_response_ranking=bool(policy.get("global_response_ranking", False)),
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
    if result.ranking.implementation not in {"heuristic", "evidence"}:
        raise ValueError(f"Unsupported ranking implementation: {result.ranking.implementation}")
    if result.search.implementation != "hybrid":
        raise ValueError(f"Unsupported search implementation: {result.search.implementation}")
    if result.policy.implementation != "heuristic":
        raise ValueError(f"Unsupported policy implementation: {result.policy.implementation}")
    if result.policy.question_strategy not in {"fixed", "information_gain"}:
        raise ValueError(f"Unsupported question strategy: {result.policy.question_strategy}")

    return result
