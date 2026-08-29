from __future__ import annotations

from typing import Protocol, Sequence

from shopping_copilot.core.contracts import (
    Candidate,
    ModelUsage,
    PolicyDecision,
    RankedCandidate,
    RetrievalDiagnostics,
    RetrievalResult,
    SearchPlan,
    SessionState,
)


class StateTracker(Protocol):
    def reset(self, session_id: str, user_profile: dict) -> SessionState: ...

    def update(self, session_id: str, user_message: str, turn: int) -> SessionState: ...

    def get(self, session_id: str) -> SessionState: ...

    def mark_asked(self, session_id: str, attribute: str | None) -> None: ...


class QueryPlanner(Protocol):
    def build(self, state: SessionState) -> SearchPlan: ...


class Retriever(Protocol):
    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics: ...

    def retrieve(self, plan: SearchPlan) -> RetrievalResult: ...


class CandidateRescuer(Protocol):
    def retrieve(
        self,
        plan: SearchPlan,
        primary: RetrievalResult,
    ) -> tuple[Candidate, ...]: ...


class Ranker(Protocol):
    def rank(
        self,
        state: SessionState,
        plan: SearchPlan,
        result: RetrievalResult,
    ) -> list[RankedCandidate]: ...


class Policy(Protocol):
    def before_search(
        self,
        state: SessionState,
        plan: SearchPlan,
        diagnostics: RetrievalDiagnostics,
    ) -> PolicyDecision | None: ...

    def after_ranking(
        self,
        state: SessionState,
        plan: SearchPlan,
        ranked: Sequence[RankedCandidate],
        diagnostics: RetrievalDiagnostics,
        early_decision: PolicyDecision | None = None,
    ) -> PolicyDecision: ...


class ResponseBuilder(Protocol):
    def build(
        self,
        ranked: Sequence[RankedCandidate],
        decision: PolicyDecision,
        top_k: int,
        usage: ModelUsage | None = None,
    ) -> dict: ...


class TraceSink(Protocol):
    def record(self, event: dict) -> None: ...
