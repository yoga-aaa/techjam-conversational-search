from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter

from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import ModelUsage
from shopping_copilot.core.factory import Components, build_components
from shopping_copilot.policy.heuristic import semantic_anchor_signature, semantic_signature


def _prioritize_top_semantic_group(
    ranked: list,
    response_ranked: list,
    mode: str = "count",
    anchor: str = "rank_one",
) -> list:
    if not ranked:
        return response_ranked
    top_signature = semantic_anchor_signature(ranked, mode, anchor)
    equivalent = [
        item for item in response_ranked
        if semantic_signature(item, mode) == top_signature
    ]
    others = [
        item for item in response_ranked
        if semantic_signature(item, mode) != top_signature
    ]
    return equivalent + others


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

        primary_result = self.components.retriever.retrieve(search_plan)
        primary_ranked = self.components.ranker.rank(state, search_plan, primary_result)
        self.components.coverage_manager.observe(state, primary_ranked)
        decision = self.components.policy.after_ranking(
            state,
            search_plan,
            primary_ranked,
            primary_result.diagnostics,
            early_decision=early_decision,
        )

        base_response_ranked = primary_ranked
        if (
            early_decision is not None
            and self.config.policy.question_strategy == "information_gain"
            and not self.config.policy.full_rerank_clarification_response
        ):
            response_candidate_count = max(10, decision.recommendation_count)
            response_result = replace(
                primary_result,
                candidates=primary_result.candidates[:response_candidate_count],
            )
            response_plan = replace(search_plan, candidate_k=response_candidate_count)
            base_response_ranked = self.components.ranker.rank(
                state,
                response_plan,
                response_result,
            )

        if state.coverage_mode:
            base_response_ranked = self.components.coverage_manager.order_for_response(
                state,
                primary_ranked,
            )
            if (
                self.config.policy.adaptive_semantic_slate
                and decision.ask_attribute is not None
            ):
                base_response_ranked = _prioritize_top_semantic_group(
                    list(primary_ranked),
                    list(base_response_ranked),
                    self.config.policy.adaptive_semantic_slate_mode,
                    self.config.policy.adaptive_semantic_slate_anchor,
                )

        response_ranked = base_response_ranked
        protected_rescue_attempted = False
        protected_rescue_returned_count = 0
        protected_rescue_expanded_top10_count = 0
        protected_rescue_inserted_count = 0
        protected_rescue_inserted_ids: list[str] = []
        if (
            self.config.search.protected_rescue_enabled
            and early_decision is not None
            and decision.recommendation_count > 0
            and not state.coverage_mode
            and search_plan.route in {"buying", "browsing"}
            and len(search_plan.structured_constraints) >= 2
        ):
            protected_rescue_attempted = True
            rescue_candidates = self.components.candidate_rescuer.retrieve(
                search_plan,
                primary_result,
            )
            primary_ids = {
                item.parent_asin
                for item in primary_result.candidates
            }
            rescue_candidates = tuple(
                item
                for item in rescue_candidates
                if item.parent_asin not in primary_ids
            )
            protected_rescue_returned_count = len(rescue_candidates)
            if rescue_candidates:
                rescue_ids = {
                    item.parent_asin
                    for item in rescue_candidates
                }
                expanded_result = replace(
                    primary_result,
                    candidates=(*primary_result.candidates, *rescue_candidates),
                )
                expanded_plan = replace(
                    search_plan,
                    candidate_k=len(expanded_result.candidates),
                )
                expanded_ranked = self.components.ranker.rank(
                    state,
                    expanded_plan,
                    expanded_result,
                )
                protected_rescue_expanded_top10_count = len({
                    item.parent_asin
                    for item in expanded_ranked[:10]
                    if item.parent_asin in rescue_ids
                })
                response_ranked = self.components.rescue_selector.select(
                    base_response_ranked,
                    expanded_ranked,
                    rescue_ids,
                    decision.recommendation_count,
                )
                visible_limit = max(
                    0,
                    min(10, int(top_k), int(decision.recommendation_count)),
                )
                inserted_seen: set[str] = set()
                for item in response_ranked[:visible_limit]:
                    if item.parent_asin not in rescue_ids or item.parent_asin in inserted_seen:
                        continue
                    inserted_seen.add(item.parent_asin)
                    protected_rescue_inserted_ids.append(item.parent_asin)
                protected_rescue_inserted_count = len(protected_rescue_inserted_ids)

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
            "candidate_count": primary_result.diagnostics.candidate_count,
            "returned_candidate_count": len(primary_result.candidates),
            "ask_attribute": decision.ask_attribute,
            "question_scores": dict(state.question_scores),
            "candidate_overlap": round(state.candidate_overlap, 6),
            "stagnant_candidate_turns": state.stagnant_candidate_turns,
            "coverage_mode": state.coverage_mode,
            "shown_candidate_count": len(state.shown_asins),
            "recommendation_count": decision.recommendation_count,
            "semantic_slate_size": decision.recommendation_count,
            "protected_rescue_attempted": protected_rescue_attempted,
            "protected_rescue_returned_count": protected_rescue_returned_count,
            "protected_rescue_expanded_top10_count": protected_rescue_expanded_top10_count,
            "protected_rescue_inserted_count": protected_rescue_inserted_count,
            "protected_rescue_inserted_ids": protected_rescue_inserted_ids,
            "protected_rescue_head_size": self.config.policy.protected_rescue_head_size,
            "protected_rescue_quota": self.config.policy.protected_rescue_quota,
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
