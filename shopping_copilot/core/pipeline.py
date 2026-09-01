from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter

from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import ModelUsage
from shopping_copilot.core.factory import Components, build_components


class ShoppingCopilotAgent:
    """Stable orchestration pipeline behind the official Agent interface."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        config_path: str | Path | None = None,
        components: Components | None = None,
    ) -> None:
        self.config = load_config(config_path)
        self.components = components or build_components(catalog_path, self.config)

    def reset(self, session_id: str, user_profile: dict) -> None:
        self.components.state_tracker.reset(session_id, user_profile)

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        started = perf_counter()
        # Every turn follows the same order: update current state first, then
        # plan retrieval from that state. This prevents stale preferences from
        # leaking into the next query after an explicit user override.
        state = self.components.state_tracker.update(session_id, user_message, turn)
        plan = self.components.planner.build(state)

        # The inexpensive probe lets policy decide whether the request is too
        # broad for a useful answer before spending the full retrieval budget.
        probe = self.components.retriever.probe(plan)
        early_decision = self.components.policy.before_search(state, plan, probe)

        if early_decision is not None:
            # Clarification turns still produce a small slate, but do not use
            # dense/structured routes that are unnecessary for the question.
            question_candidate_k = (
                self.config.policy.question_candidate_limit
                if self.config.policy.question_strategy == "information_gain"
                else 10
            )
            search_plan = replace(
                plan,
                candidate_k=max(question_candidate_k, early_decision.recommendation_count),
                use_dense=False,
                use_structured=False,
            )
        else:
            search_plan = plan

        result = self.components.retriever.retrieve(search_plan)
        ranked = self.components.ranker.rank(state, search_plan, result)
        self.components.coverage_manager.observe(state, ranked)
        # Post-ranking policy chooses both the next question and how many items
        # are safe to expose. The slate gate keeps weak evidence from looking
        # like a confident Top-10 answer.
        decision = self.components.policy.after_ranking(
            state,
            search_plan,
            ranked,
            result.diagnostics,
            early_decision=early_decision,
        )

        response_ranked = ranked
        if (
            early_decision is not None
            and self.config.policy.question_strategy == "information_gain"
            and not self.config.policy.global_response_ranking
        ):
            # For a clarification response, rerank a compact prefix so the
            # visible items match the same evidence logic as the final slate.
            response_candidate_count = max(10, decision.recommendation_count)
            response_result = replace(
                result,
                candidates=result.candidates[:response_candidate_count],
            )
            response_plan = replace(search_plan, candidate_k=response_candidate_count)
            response_ranked = self.components.ranker.rank(state, response_plan, response_result)

        if state.coverage_mode:
            # If consecutive turns produce nearly the same candidates without
            # new constraints, bounded unseen-candidate rotation improves
            # coverage while preserving the ranker's ordering signal.
            response_ranked = self.components.coverage_manager.order_for_response(state, ranked)

        self.components.state_tracker.mark_asked(session_id, decision.ask_attribute)
        latency_ms = round((perf_counter() - started) * 1000.0, 3)
        self.components.trace_sink.record({
            "session_id": session_id,
            "turn": turn,
            "intent_mode": state.intent_mode,
            "active_slots": {key: list(values) for key, values in state.active_slots.items()},
            "negative_slots": {key: list(values) for key, values in state.negative_slots.items()},
            "excluded_terms": sorted(state.excluded_terms),
            "route": plan.route,
            "lexical_terms": list(plan.lexical_terms),
            "candidate_count": result.diagnostics.candidate_count,
            "returned_candidate_count": len(result.candidates),
            "ask_attribute": decision.ask_attribute,
            "question_scores": dict(state.question_scores),
            "candidate_overlap": round(state.candidate_overlap, 6),
            "stagnant_candidate_turns": state.stagnant_candidate_turns,
            "coverage_mode": state.coverage_mode,
            "shown_candidate_count": len(state.shown_asins),
            "recommendation_count": decision.recommendation_count,
            "latency_ms": latency_ms,
        })
        response = self.components.response_builder.build(
            ranked=response_ranked,
            decision=decision,
            top_k=top_k,
            usage=ModelUsage(),
        )
        self.components.coverage_manager.record_response(state, response["recommendations"])
        return response
