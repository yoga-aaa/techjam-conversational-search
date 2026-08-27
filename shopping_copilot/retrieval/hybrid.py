from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan
from shopping_copilot.core.interfaces import Retriever


def protected_candidate_ids(
    lexical_ids: Sequence[str],
    structured_ids: Sequence[str],
    rough_ranked_ids: Sequence[str],
    limit: int,
    protected_fraction: float,
) -> list[str]:
    """Keep deterministic minimum lexical and structured representation."""
    candidate_limit = max(0, int(limit))
    if candidate_limit <= 1:
        return list(dict.fromkeys(rough_ranked_ids))[:candidate_limit]
    quota = min(candidate_limit, int(candidate_limit * max(0.0, min(0.5, protected_fraction))))
    selected: list[str] = []
    selected_set: set[str] = set()
    lexical_set = set(lexical_ids)
    structured_set = set(structured_ids)
    lexical_count = 0
    structured_count = 0

    def add(parent_asin: str, source: str) -> None:
        nonlocal lexical_count, structured_count
        if parent_asin in selected_set or len(selected) >= candidate_limit:
            return
        selected.append(parent_asin)
        selected_set.add(parent_asin)
        if source == "lexical" or parent_asin in lexical_set:
            lexical_count += 1
        if source == "structured" or parent_asin in structured_set:
            structured_count += 1

    for index in range(max(len(lexical_ids), len(structured_ids))):
        if index < len(lexical_ids) and lexical_count < quota:
            add(lexical_ids[index], "lexical")
        if index < len(structured_ids) and structured_count < quota:
            add(structured_ids[index], "structured")
        if lexical_count >= quota and structured_count >= quota:
            break
    for parent_asin in rough_ranked_ids:
        add(parent_asin, "rough")
        if len(selected) >= candidate_limit:
            break
    return selected


class HybridRetriever:
    """Combines candidate-search routes without changing the pipeline contract."""

    def __init__(self, lexical: Retriever, dense: Retriever, structured: Retriever, config: dict[str, object] | None = None) -> None:
        self.lexical = lexical
        self.dense = dense
        self.structured = structured
        merge_config = dict((config or {}).get("candidate_merge") or {})
        self.merge_mode = str(merge_config.get("mode", "ranked_union")).lower()
        self.protected_fraction = max(0.0, min(0.5, float(merge_config.get("protected_fraction", 0.35))))

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        lexical = self.lexical.probe(plan)
        diagnostics = [lexical]
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
        structured_result = self.structured.retrieve(plan) if plan.use_structured else RetrievalResult((), lexical_result.diagnostics)
        dense_result = self.dense.retrieve(plan) if plan.use_dense else RetrievalResult((), lexical_result.diagnostics)
        if not structured_result.candidates and not dense_result.candidates:
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

        ranked = sorted(
            combined.values(),
            key=lambda item: (
                fusion_scores.get(item.parent_asin, 0.0),
                item.constraint_score,
                item.lexical_score,
                item.parent_asin,
            ),
            reverse=True,
        )
        if self.merge_mode == "protected_union":
            selected = set(protected_candidate_ids(
                [candidate.parent_asin for candidate in lexical_result.candidates],
                [candidate.parent_asin for candidate in structured_result.candidates],
                [candidate.parent_asin for candidate in ranked],
                plan.candidate_k,
                self.protected_fraction,
            ))
            ranked = [candidate for candidate in ranked if candidate.parent_asin in selected]
        elif self.merge_mode != "ranked_union":
            raise ValueError(f"Unsupported candidate merge mode: {self.merge_mode}")
        diagnostics = self.probe(plan)
        return RetrievalResult(tuple(ranked[: plan.candidate_k]), diagnostics)
