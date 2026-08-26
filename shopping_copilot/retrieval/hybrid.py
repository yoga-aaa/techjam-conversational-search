from __future__ import annotations

from dataclasses import replace

from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan
from shopping_copilot.core.interfaces import Retriever


class HybridRetriever:
    """Combines candidate-search routes without changing the pipeline contract."""

    def __init__(self, lexical: Retriever, dense: Retriever) -> None:
        self.lexical = lexical
        self.dense = dense

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        lexical = self.lexical.probe(plan)
        if not plan.use_dense:
            return lexical
        dense = self.dense.probe(plan)
        return RetrievalDiagnostics(
            candidate_count=max(lexical.candidate_count, dense.candidate_count),
            category_count=max(lexical.category_count, dense.category_count),
            top_score_gap=max(lexical.top_score_gap, dense.top_score_gap),
            route=plan.route,
        )

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        lexical_result = self.lexical.retrieve(plan)
        dense_result = self.dense.retrieve(plan) if plan.use_dense else RetrievalResult((), lexical_result.diagnostics)
        if not dense_result.candidates:
            return lexical_result

        combined: dict[str, Candidate] = {}
        for rank, candidate in enumerate(lexical_result.candidates, start=1):
            combined[candidate.parent_asin] = replace(
                candidate,
                lexical_score=candidate.lexical_score + 1.0 / (60 + rank),
                source_routes=tuple(sorted(set(candidate.source_routes) | {"bm25"})),
            )
        for rank, candidate in enumerate(dense_result.candidates, start=1):
            existing = combined.get(candidate.parent_asin, Candidate(candidate.parent_asin))
            combined[candidate.parent_asin] = replace(
                existing,
                dense_score=candidate.dense_score + 1.0 / (60 + rank),
                source_routes=tuple(sorted(set(existing.source_routes) | {"dense"})),
            )

        candidates = sorted(
            combined.values(),
            key=lambda item: item.lexical_score + item.dense_score,
            reverse=True,
        )[: plan.candidate_k]
        diagnostics = self.probe(plan)
        return RetrievalResult(tuple(candidates), diagnostics)
