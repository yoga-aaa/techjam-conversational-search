from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from evaluator.local_evaluator import (
    TOP_K,
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
    normalize_recommendations,
)
from shopping_copilot.core.contracts import Candidate, RetrievalResult
from starter.agent import Agent


def _rank(values: list[str] | tuple[str, ...], target: str) -> int | None:
    try:
        return list(values).index(target) + 1
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Broad OR and slot-level Strict AND")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--config", default="configs/final.json")
    parser.add_argument("--output", default="artifacts/e6_r1a_strict_diagnostics.json")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = Agent(args.catalog, config_path=args.config)
    components = agent._impl.components
    lexical = components.retriever.lexical
    turns: list[dict[str, object]] = []

    for sample in samples:
        sample_id = str(sample["sample_id"])
        session_id = f"strict-diagnostic:{sample_id}"
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

        for turn in range(1, 11):
            response = agent.respond(session_id, user_message, turn, TOP_K)
            state = components.state_tracker.get(session_id)
            plan = components.planner.build(state)
            broad_60 = lexical.broad_candidates(plan, 60)
            broad_100 = lexical.broad_candidates(plan, 100)
            broad_deep = lexical.broad_candidates(plan, 240)
            strict_expression = lexical._strict_expression(plan)
            strict_60 = lexical.strict_candidates(plan, 60)

            broad_60_ids = [candidate.parent_asin for candidate in broad_60]
            broad_100_ids = [candidate.parent_asin for candidate in broad_100]
            broad_deep_by_id = {candidate.parent_asin: candidate for candidate in broad_deep}
            strict_ids = [candidate.parent_asin for candidate in strict_60]
            rescue_ids = [identifier for identifier in strict_ids if identifier not in broad_60_ids][:10]
            rescue_candidates: list[Candidate] = []
            strict_by_id = {candidate.parent_asin: candidate for candidate in strict_60}
            for identifier in rescue_ids:
                broad_candidate = broad_deep_by_id.get(identifier)
                if broad_candidate is not None:
                    rescue_candidates.append(replace(
                        broad_candidate,
                        source_routes=("bm25", "bm25:strict_and"),
                    ))
                else:
                    rescue_candidates.append(replace(
                        strict_by_id[identifier],
                        lexical_score=0.0,
                    ))
            union_candidates = (*broad_60, *rescue_candidates)
            union_result = RetrievalResult(union_candidates, lexical.probe(plan))
            union_plan = replace(plan, candidate_k=len(union_candidates))
            union_ranked = components.ranker.rank(state, union_plan, union_result)
            union_ranked_ids = [candidate.parent_asin for candidate in union_ranked]
            displayed_ids = normalize_recommendations(response.get("recommendations"), catalog_ids)

            turns.append({
                "sample_id": sample_id,
                "scenario_type": sample["scenario_type"],
                "turn": turn,
                "target": target,
                "target_is_active": override_applied,
                "lexical_terms": list(plan.lexical_terms),
                "structured_constraints": {
                    key: list(values) for key, values in plan.structured_constraints.items()
                },
                "strict_expression": strict_expression,
                "strict_eligible": bool(strict_expression),
                "strict_returned": len(strict_60),
                "broad_60_target_rank": _rank(broad_60_ids, target),
                "broad_100_target_rank": _rank(broad_100_ids, target),
                "broad_240_target_rank": _rank(list(broad_deep_by_id), target),
                "strict_60_target_rank": _rank(strict_ids, target),
                "rescue_count": len(rescue_ids),
                "target_rescued": target in rescue_ids,
                "union_rerank_target_rank": _rank(union_ranked_ids, target),
                "displayed_target_rank": _rank(displayed_ids, target),
            })

            if override_applied and target in displayed_ids:
                break
            if turn == 10:
                break
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

    active_turns = [turn for turn in turns if turn["target_is_active"]]
    eligible_turns = [turn for turn in active_turns if turn["strict_eligible"]]
    rescued_sessions = sorted({
        str(turn["sample_id"]) for turn in eligible_turns if turn["target_rescued"]
    })
    summary = {
        "sample_count": len(samples),
        "turn_count": len(turns),
        "active_target_turn_count": len(active_turns),
        "strict_eligible_turn_count": len(eligible_turns),
        "strict_eligible_rate": (
            len(eligible_turns) / len(active_turns) if active_turns else 0.0
        ),
        "strict_nonempty_turn_count": sum(
            int(turn["strict_returned"] > 0) for turn in eligible_turns
        ),
        "mean_rescue_count": (
            sum(int(turn["rescue_count"]) for turn in eligible_turns) / len(eligible_turns)
            if eligible_turns else 0.0
        ),
        "target_in_broad_60_turn_count": sum(
            int(turn["broad_60_target_rank"] is not None) for turn in active_turns
        ),
        "target_in_broad_100_turn_count": sum(
            int(turn["broad_100_target_rank"] is not None) for turn in active_turns
        ),
        "target_rescue_turn_count": sum(int(turn["target_rescued"]) for turn in eligible_turns),
        "target_rescued_session_count": len(rescued_sessions),
        "target_rescued_sessions": rescued_sessions,
        "public_0020": [turn for turn in turns if turn["sample_id"] == "public_0020"],
    }
    payload = {"summary": summary, "turns": turns}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
