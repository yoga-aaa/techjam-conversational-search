from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
    metric_summary,
    normalize_recommendations,
)
from starter.agent import Agent


def _rank(ids: list[str], target: str, limit: int) -> int | None:
    try:
        return ids.index(target) + 1
    except ValueError:
        return limit + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a full-turn ground-truth-free shadow evaluation")
    parser.add_argument("--config", required=True)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--trace", required=True)
    args = parser.parse_args()

    os.environ["SHOPPING_COPILOT_CONFIG"] = args.config
    trace_path = Path(args.trace)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    if trace_path.exists():
        trace_path.unlink()

    config_payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
    config_payload["trace"] = {"enabled": True, "path": str(trace_path), "include_rankings": True}
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as config_handle:
        json.dump(config_payload, config_handle)
        runtime_config = config_handle.name

    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = Agent(args.catalog, config_path=runtime_config)
    sessions: list[dict] = []
    for sample in samples:
        session_id = f"shadow_{uuid.uuid4().hex}"
        agent.reset(session_id, sample["user_profile"])
        target = str(sample["ground_truth"]["parent_asin"])
        card, behavior = materialize_hidden_fields(sample, products)
        effective = {**sample, "intent_card": card, "behavior": behavior}
        disclosed: set[str] = set()
        boundary_used = False
        override_applied = sample["scenario_type"] != "intent_override"
        message = initial_message(effective, coarse_category(categories.get(target, [])), disclosed)
        turns: list[dict] = []
        for turn in range(1, MAX_TURNS + 1):
            try:
                response = agent.respond(session_id, message, turn, TOP_K)
            except Exception as exc:  # Keep shadow evidence for a failed turn.
                response = {"message": "", "ask_attribute": None, "recommendations": [], "error": str(exc)}
            if not isinstance(response, dict):
                response = {"message": "", "ask_attribute": None, "recommendations": []}
            visible_ids = normalize_recommendations(response.get("recommendations"), catalog_ids)
            turns.append({
                "turn": turn,
                "visible_ids": visible_ids,
                "visible_rank": _rank(visible_ids, target, TOP_K),
                "ask_attribute": response.get("ask_attribute"),
            })
            if turn == MAX_TURNS:
                break
            override = effective.get("behavior", {}).get("override") or {}
            if not override_applied and turn + 1 == int(override.get("turn", 3)):
                override_applied = True
                new_value = str(override.get("new_value", ""))
                if new_value:
                    disclosed.add(new_value)
                message = str(override.get("message", "Actually, please ignore my earlier preference."))
            else:
                message, boundary_used = customer_reply(
                    effective,
                    response.get("ask_attribute"),
                    disclosed,
                    boundary_used,
                )
        sessions.append({
            "session_id": session_id,
            "sample_id": sample["sample_id"],
            "scenario_type": sample["scenario_type"],
            "target": target,
            "turns": turns,
        })

    trace_by_turn: dict[tuple[str, int], dict] = {}
    if trace_path.exists():
        with trace_path.open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                trace_by_turn[(str(event["session_id"]), int(event["turn"]))] = event

    # Trace records are keyed by opaque session ids, so join them through the
    # order in which the agent generated sessions. The output remains explicit
    # about missing trace rows rather than silently fabricating ranks.
    joined_sessions: list[dict] = []
    for session in sessions:
        events = [
            event
            for (session_id, _), event in trace_by_turn.items()
            if session_id == session["session_id"]
        ]
        target = session["target"]
        for turn_info, event in zip(session["turns"], sorted(events, key=lambda item: int(item.get("turn", 0)))):
            pre_gate = list(event.get("pre_gate_ranked_ids") or [])
            visible = list(event.get("visible_ids") or [])
            turn_info["pre_gate_rank"] = _rank(pre_gate, target, len(pre_gate) or 100)
            turn_info["trace_visible_rank"] = _rank(visible, target, TOP_K)
            turn_info["retrieved_rank"] = _rank(list(event.get("retrieved_ids") or []), target, 100)
            turn_info["leader_match_count"] = event.get("leader_match_count")
        first_visible = next((item["visible_rank"] for item in session["turns"] if item["visible_rank"] <= TOP_K), None)
        first_pre_gate = next((item.get("pre_gate_rank") for item in session["turns"] if item.get("pre_gate_rank", 101) <= TOP_K), None)
        joined_sessions.append({
            "sample_id": session["sample_id"],
            "scenario_type": session["scenario_type"],
            "hit": first_visible is not None,
            "first_hit_turn": next((item["turn"] for item in session["turns"] if item["visible_rank"] <= TOP_K), None),
            "best_rank": first_visible,
            "reciprocal_rank": 0.0 if first_visible is None else 1.0 / first_visible,
            "oracle_pre_gate_hit": first_pre_gate is not None,
            "turns": session["turns"],
        })

    overall = metric_summary(joined_sessions)
    shadow = {
        **overall,
        "oracle_pre_gate": {
            "hit_rate_at_10": round(sum(int(item["oracle_pre_gate_hit"]) for item in joined_sessions) / len(joined_sessions), 6),
            "mrr": round(sum(
                0.0 if next((turn.get("pre_gate_rank") for turn in item["turns"] if turn.get("pre_gate_rank", 101) <= TOP_K), None) is None
                else 1.0 / next(turn["pre_gate_rank"] for turn in item["turns"] if turn.get("pre_gate_rank", 101) <= TOP_K)
                for item in joined_sessions
            ) / len(joined_sessions), 6),
        },
        "sessions": joined_sessions,
        "trace_path": str(trace_path),
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(shadow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in shadow.items() if key != "sessions"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
