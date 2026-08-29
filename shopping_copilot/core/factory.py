from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import AppConfig
from shopping_copilot.core.interfaces import CandidateRescuer, Policy, QueryPlanner, Ranker, ResponseBuilder, Retriever, StateTracker, TraceSink
from shopping_copilot.observability.trace import JsonlTraceSink, NullTraceSink
from shopping_copilot.planning.query_planner import RuleQueryPlanner
from shopping_copilot.policy.heuristic import HeuristicPolicy
from shopping_copilot.policy.coverage import CandidateCoverageManager
from shopping_copilot.ranking.heuristic import HeuristicRanker
from shopping_copilot.ranking.protected_rescue import ProtectedRescueSelector
from shopping_copilot.response.builder import OfficialResponseBuilder
from shopping_copilot.retrieval.bm25 import BM25Retriever
from shopping_copilot.retrieval.dense import DisabledDenseRetriever
from shopping_copilot.retrieval.hybrid import HybridRetriever
from shopping_copilot.retrieval.structured import StructuredRetriever
from shopping_copilot.retrieval.protected_rescue import StrictAndCandidateRescuer
from shopping_copilot.state.rule_state import RuleStateTracker


@dataclass(frozen=True)
class Components:
    state_tracker: StateTracker
    planner: QueryPlanner
    retriever: Retriever
    candidate_rescuer: CandidateRescuer
    ranker: Ranker
    policy: Policy
    coverage_manager: CandidateCoverageManager
    rescue_selector: ProtectedRescueSelector
    response_builder: ResponseBuilder
    trace_sink: TraceSink


def build_components(catalog_path: str | Path, config: AppConfig) -> Components:
    """Builds config-selected implementations while keeping the pipeline fixed."""

    store = CatalogStore(catalog_path)
    lexical = BM25Retriever(
        store,
        strict_rescue_enabled=config.search.strict_rescue_enabled,
    )
    dense = DisabledDenseRetriever()
    structured: Retriever = (
        StructuredRetriever(store)
        if config.search.structured_enabled
        else DisabledDenseRetriever()
    )
    retriever = HybridRetriever(lexical=lexical, dense=dense, structured=structured)
    candidate_rescuer = StrictAndCandidateRescuer(
        lexical=lexical,
        store=store,
        config=config.search,
    )
    rescue_selector = ProtectedRescueSelector(config.policy)
    trace_sink: TraceSink = JsonlTraceSink(config.trace.path) if config.trace.enabled else NullTraceSink()
    return Components(
        state_tracker=RuleStateTracker(),
        planner=RuleQueryPlanner(config.search),
        retriever=retriever,
        candidate_rescuer=candidate_rescuer,
        ranker=HeuristicRanker(store, config.ranking),
        policy=HeuristicPolicy(config.search, config.policy, store),
        coverage_manager=CandidateCoverageManager(config.policy),
        rescue_selector=rescue_selector,
        response_builder=OfficialResponseBuilder(store),
        trace_sink=trace_sink,
    )
