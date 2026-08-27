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
        state = self.components.state_tracker.update(session_id, user_message, turn)
        plan = self.components.planner.build(state)
        probe = self.components.retriever.probe(plan)
        early_decision = self.components.policy.before_search(state, plan, probe)

        if early_decision is not None:
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
        ):
            response_candidate_count = max(10, decision.recommendation_count)
            response_result = replace(
                result,
                candidates=result.candidates[:response_candidate_count],
            )
            response_plan = replace(search_plan, candidate_k=response_candidate_count)
            response_ranked = self.components.ranker.rank(state, response_plan, response_result)

        self.components.state_tracker.mark_asked(session_id, decision.ask_attribute)
        latency_ms = round((perf_counter() - started) * 1000.0, 3)
        self.components.trace_sink.record({
            "session_id": session_id,
            "turn": turn,
            "intent_mode": state.intent_mode,
            "active_slots": {key: list(values) for key, values in state.active_slots.items()},
            "excluded_terms": sorted(state.excluded_terms),
            "route": plan.route,
            "lexical_terms": list(plan.lexical_terms),
            "candidate_count": result.diagnostics.candidate_count,
            "returned_candidate_count": len(result.candidates),
            "ask_attribute": decision.ask_attribute,
            "question_scores": dict(state.question_scores),
            "recommendation_count": decision.recommendation_count,
            "latency_ms": latency_ms,
        })
        return self.components.response_builder.build(
            ranked=response_ranked,
            decision=decision,
            top_k=top_k,
            usage=ModelUsage(),
        )
