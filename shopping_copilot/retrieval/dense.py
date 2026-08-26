from __future__ import annotations

from shopping_copilot.core.contracts import RetrievalDiagnostics, RetrievalResult, SearchPlan


class DisabledDenseRetriever:
    """Stable extension point for a later embedding-based retriever."""

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        return RetrievalDiagnostics(0, 0, 0.0, plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        return RetrievalResult((), self.probe(plan))
