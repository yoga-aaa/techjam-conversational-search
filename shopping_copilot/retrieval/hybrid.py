from __future__ import annotations

from dataclasses import replace

from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan
from shopping_copilot.core.interfaces import Retriever


class HybridRetriever:
    """Combines candidate-search routes without changing the pipeline contract."""

    def __init__(
        self,
        lexical: Retriever,
        dense: Retriever,
        structured: Retriever,
        category_anchor: Retriever,
    ) -> None:
        self.lexical = lexical
        self.dense = dense
        self.structured = structured
        self.category_anchor = category_anchor

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        lexical = self.lexical.probe(plan)
        diagnostics = [lexical]
        if plan.structured_constraints.get("category"):
            diagnostics.append(self.category_anchor.probe(plan))
        if plan.use_structured:
            diagnostics.append(self.structured.probe(plan))
        if plan.use_dense:
            diagnostics.append(self.dense.probe(plan))
        return RetrievalDiagnostics(
            candidate_count=max(item.candidate_count for item in diagnostics),
            category_count=max(item.category_count for item in diagnostics),
            top_score_gap=max(item.top_score_gap for item in diagnostics),
            route=plan.route,
        )

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        lexical_result = self.lexical.retrieve(plan)
        structured_result = (
            self.structured.retrieve(plan)
            if plan.use_structured
            else RetrievalResult((), lexical_result.diagnostics)
        )
        dense_result = (
            self.dense.retrieve(plan)
            if plan.use_dense
            else RetrievalResult((), lexical_result.diagnostics)
        )
        category_result = (
            self.category_anchor.retrieve(plan)
            if plan.structured_constraints.get("category")
            else RetrievalResult((), lexical_result.diagnostics)
        )
        if not structured_result.candidates and not dense_result.candidates and not category_result.candidates:
            return lexical_result

        combined: dict[str, Candidate] = {}
        fusion_scores: dict[str, float] = {}
        for rank, candidate in enumerate(lexical_result.candidates, start=1):
            existing = combined.get(candidate.parent_asin, Candidate(candidate.parent_asin))
            combined[candidate.parent_asin] = replace(
                existing,
                lexical_score=max(existing.lexical_score, candidate.lexical_score),
                source_routes=tuple(sorted(set(existing.source_routes) | set(candidate.source_routes) | {"bm25"})),
            )
            fusion_scores[candidate.parent_asin] = fusion_scores.get(candidate.parent_asin, 0.0) + 1.0 / (60 + rank)
        for rank, candidate in enumerate(structured_result.candidates, start=1):
            existing = combined.get(candidate.parent_asin, Candidate(candidate.parent_asin))
            combined[candidate.parent_asin] = replace(
                existing,
                constraint_score=max(existing.constraint_score, candidate.constraint_score),
                source_routes=tuple(sorted(set(existing.source_routes) | set(candidate.source_routes) | {"structured"})),
            )
            fusion_scores[candidate.parent_asin] = fusion_scores.get(candidate.parent_asin, 0.0) + 1.0 / (60 + rank)
        for rank, candidate in enumerate(dense_result.candidates, start=1):
            existing = combined.get(candidate.parent_asin, Candidate(candidate.parent_asin))
            combined[candidate.parent_asin] = replace(
                existing,
                dense_score=max(existing.dense_score, candidate.dense_score),
                source_routes=tuple(sorted(set(existing.source_routes) | {"dense"})),
            )
            fusion_scores[candidate.parent_asin] = fusion_scores.get(candidate.parent_asin, 0.0) + 1.0 / (60 + rank)
        for rank, candidate in enumerate(category_result.candidates, start=1):
            existing = combined.get(candidate.parent_asin, Candidate(candidate.parent_asin))
            combined[candidate.parent_asin] = replace(
                existing,
                lexical_score=max(existing.lexical_score, candidate.lexical_score),
                constraint_score=max(existing.constraint_score, candidate.constraint_score),
                source_routes=tuple(sorted(set(existing.source_routes) | set(candidate.source_routes))),
            )
            # Category is a recall anchor, not a popularity signal. A small
            # deterministic contribution keeps ties stable without letting the
            # route dominate the actual constraint ranker.
            fusion_scores[candidate.parent_asin] = fusion_scores.get(candidate.parent_asin, 0.0) + 1.0 / (6000 + rank)

        if category_result.candidates:
            # A category anchor is a high-recall boundary, not another vote in
            # the fusion pool. Never let an unrelated-category lexical hit
            # displace a product from the requested category.
            has_non_category_constraint = any(
                attribute != "category"
                for attribute in plan.structured_constraints
            )
            category_ids = {
                candidate.parent_asin
                for candidate in category_result.candidates
                if not has_non_category_constraint or candidate.constraint_score > 0.0
            }
            # Keep lexical/structured hits when the category route cannot
            # score an OOV phrase; the hard category boundary still applies.
            if has_non_category_constraint and not category_ids:
                category_ids = {candidate.parent_asin for candidate in category_result.candidates}
            combined = {
                parent_asin: candidate
                for parent_asin, candidate in combined.items()
                if parent_asin in category_ids
            }

        candidates = sorted(
            combined.values(),
            key=lambda item: (
                fusion_scores.get(item.parent_asin, 0.0),
                item.constraint_score,
                item.lexical_score,
                item.parent_asin,
            ),
            reverse=True,
        )
        if not category_result.candidates:
            candidates = candidates[: plan.candidate_k]
        diagnostics = self.probe(plan)
        return RetrievalResult(tuple(candidates), diagnostics)
