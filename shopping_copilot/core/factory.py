from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import AppConfig
from shopping_copilot.core.interfaces import Policy, QueryPlanner, Ranker, ResponseBuilder, Retriever, StateTracker, TraceSink
from shopping_copilot.observability.trace import JsonlTraceSink, NullTraceSink
from shopping_copilot.planning.query_planner import RuleQueryPlanner
from shopping_copilot.policy.heuristic import HeuristicPolicy
from shopping_copilot.ranking.heuristic import HeuristicRanker
from shopping_copilot.response.builder import OfficialResponseBuilder
from shopping_copilot.retrieval.bm25 import BM25Retriever
from shopping_copilot.retrieval.dense import DisabledDenseRetriever
from shopping_copilot.retrieval.hybrid import HybridRetriever
from shopping_copilot.state.rule_state import RuleStateTracker


@dataclass(frozen=True)
class Components:
    state_tracker: StateTracker
    planner: QueryPlanner
    retriever: Retriever
    ranker: Ranker
    policy: Policy
    response_builder: ResponseBuilder
    trace_sink: TraceSink


def build_components(catalog_path: str | Path, config: AppConfig) -> Components:
    """Builds config-selected implementations while keeping the pipeline fixed."""

    store = CatalogStore(catalog_path)
    lexical = BM25Retriever(store)
    dense = DisabledDenseRetriever()
    retriever = HybridRetriever(lexical=lexical, dense=dense)
    trace_sink: TraceSink = JsonlTraceSink(config.trace.path) if config.trace.enabled else NullTraceSink()
    return Components(
        state_tracker=RuleStateTracker(),
        planner=RuleQueryPlanner(config.search),
        retriever=retriever,
        ranker=HeuristicRanker(store),
        policy=HeuristicPolicy(config.search, config.policy),
        response_builder=OfficialResponseBuilder(store),
        trace_sink=trace_sink,
    )
