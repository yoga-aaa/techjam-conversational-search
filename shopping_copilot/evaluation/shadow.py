from __future__ import annotations

import statistics
import uuid
from collections import defaultdict

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    coarse_category,
    customer_reply,
    initial_message,
    materialize_hidden_fields,
    metric_summary,
    normalize_recommendations,
)


def _turn_summary(sessions: list[dict]) -> list[dict]:
    """Aggregate target visibility without stopping at the first hit."""

    rows: list[dict] = []
    for turn in range(1, MAX_TURNS + 1):
        eligible = [
            observation
            for session in sessions
            for observation in session["turns"]
            if observation["turn"] == turn and observation["eligible"]
        ]
        ranks = [item["target_rank"] for item in eligible if item["target_rank"] is not None]
        rows.append({
            "turn": turn,
            "eligible_sessions": len(eligible),
            "hit_at_10": len(ranks),
            "top_1": sum(rank == 1 for rank in ranks),
            "top_3": sum(rank <= 3 for rank in ranks),
            "mean_reciprocal_rank": round(
                statistics.fmean(1.0 / rank for rank in ranks) if ranks else 0.0,
                6,
            ),
        })
    return rows


def shadow_evaluate(
    agent: object,
    samples: list[dict],
    catalog_ids: set[str],
    categories: dict[str, list[str]],
    products: dict[str, dict],
) -> dict:
    """Replay every session for all turns while preserving official eligibility.

    The official evaluator stops as soon as the target appears. This diagnostic
    evaluator records that same first eligible hit, but keeps simulating replies
    so later-turn ranking changes remain observable.
    """

    sessions: list[dict] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    for sample in samples:
        session_id = f"shadow_{uuid.uuid4().hex}"
        agent.reset(session_id, sample["user_profile"])
        target = str(sample["ground_truth"]["parent_asin"])
        intent_card, behavior = materialize_hidden_fields(sample, products)
        effective_sample = {**sample, "intent_card": intent_card, "behavior": behavior}
        disclosed: set[str] = set()
        boundary_used = False
        override_applied = sample["scenario_type"] != "intent_override"
        user_message = initial_message(
            effective_sample,
            coarse_category(categories.get(target, [])),
            disclosed,
        )
        first_hit_turn: int | None = None
        first_hit_rank: int | None = None
        observations: list[dict] = []

        for turn in range(1, MAX_TURNS + 1):
            try:
                response = agent.respond(session_id, user_message, turn, TOP_K)
            except Exception:
                response = {"message": "", "ask_attribute": None, "recommendations": []}
            if not isinstance(response, dict) or not isinstance(response.get("message"), str):
                response = {"message": "", "ask_attribute": None, "recommendations": []}

            usage = response.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
                    total_prompt_tokens += prompt_tokens
                if isinstance(completion_tokens, int) and completion_tokens >= 0:
                    total_completion_tokens += completion_tokens

            ranked = normalize_recommendations(response.get("recommendations"), catalog_ids)
            target_rank = ranked.index(target) + 1 if target in ranked else None
            eligible = override_applied
            if eligible and target_rank is not None and first_hit_turn is None:
                first_hit_turn = turn
                first_hit_rank = target_rank
            observations.append({
                "turn": turn,
                "eligible": eligible,
                "target_rank": target_rank,
                "ask_attribute": response.get("ask_attribute"),
                "recommendation_count": len(ranked),
            })

            if turn == MAX_TURNS:
                continue
            override = effective_sample.get("behavior", {}).get("override") or {}
            if not override_applied and turn + 1 == int(override.get("turn", 3)):
                override_applied = True
                new_value = str(override.get("new_value", ""))
                if new_value:
                    disclosed.add(new_value)
                user_message = str(
                    override.get("message", "Actually, please ignore my earlier preference.")
                )
            else:
                user_message, boundary_used = customer_reply(
                    effective_sample,
                    response.get("ask_attribute"),
                    disclosed,
                    boundary_used,
                )

        sessions.append({
            "sample_id": sample["sample_id"],
            "scenario_type": sample["scenario_type"],
            "hit": first_hit_turn is not None,
            "first_hit_turn": first_hit_turn,
            "best_rank": first_hit_rank,
            "reciprocal_rank": 0.0 if first_hit_rank is None else 1.0 / first_hit_rank,
            "turns": observations,
        })

    overall = metric_summary(sessions)
    efficiency = max(0.0, min(1.0, (11.0 - float(overall["mttc"])) / 10.0))
    technical_score = 0.50 * overall["hit_rate_at_10"] + 0.30 * overall["mrr"] + 0.20 * efficiency
    grouped: dict[str, list[dict]] = defaultdict(list)
    for session in sessions:
        grouped[session["scenario_type"]].append(session)
    return {
        **overall,
        "efficiency": round(efficiency, 6),
        "recommended_technical_score": round(technical_score, 6),
        "reported_token_usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
        },
        "scenario_metrics": {name: metric_summary(grouped[name]) for name in sorted(grouped)},
        "per_turn": _turn_summary(sessions),
        "sessions": sessions,
    }
