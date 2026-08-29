from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Product:
    """Normalized catalog record used by search and ranking."""

    parent_asin: str
    title: str
    categories: tuple[str, ...]
    features: tuple[str, ...]
    details: tuple[str, ...]
    description: tuple[str, ...]
    store: str
    price: float | None
    average_rating: float | None
    rating_number: int
    searchable_text: str


@dataclass(frozen=True)
class SupersedablePreference:
    """One small, explicitly replaceable preference reference."""

    source_turn: int
    raw_text: str
    parsed_values: tuple[tuple[str, str], ...] = ()


@dataclass
class SessionState:
    """Mutable, per-session understanding of the current shopping intent."""

    session_id: str
    turn: int = 0
    intent_mode: str = "uncertain"
    profile_terms: tuple[str, ...] = ()
    active_slots: dict[str, list[str]] = field(default_factory=dict)
    negative_slots: dict[str, list[str]] = field(default_factory=dict)
    excluded_terms: set[str] = field(default_factory=set)
    no_preference_attributes: set[str] = field(default_factory=set)
    asked_attributes: list[str] = field(default_factory=list)
    pending_attribute: str | None = None
    messages: list[str] = field(default_factory=list)
    active_context: list[str] = field(default_factory=list)
    question_scores: dict[str, float] = field(default_factory=dict)
    shown_asins: set[str] = field(default_factory=set)
    previous_candidate_ids: tuple[str, ...] = ()
    previous_slot_signature: tuple[tuple[str, tuple[str, ...]], ...] = ()
    candidate_overlap: float = 0.0
    stagnant_candidate_turns: int = 0
    coverage_mode: bool = False
    supersedable_preference: SupersedablePreference | None = None


@dataclass(frozen=True)
class SearchPlan:
    """Stable description of how the current turn should search the catalog."""

    route: str
    lexical_terms: tuple[str, ...]
    semantic_query: str
    structured_constraints: dict[str, tuple[str, ...]]
    hard_filters: dict[str, Any]
    excluded_terms: tuple[str, ...]
    bm25_weight: float
    dense_weight: float
    constraint_weight: float
    profile_weight: float
    candidate_k: int
    use_dense: bool
    use_structured: bool
    diversity_enabled: bool


@dataclass(frozen=True)
class Candidate:
    """Product found by one or more candidate-search routes."""

    parent_asin: str
    lexical_score: float = 0.0
    dense_score: float = 0.0
    constraint_score: float = 0.0
    profile_score: float = 0.0
    source_routes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalDiagnostics:
    """Cheap signals used by the policy to detect an overly broad request."""

    candidate_count: int
    category_count: int
    top_score_gap: float
    route: str


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[Candidate, ...]
    diagnostics: RetrievalDiagnostics


@dataclass(frozen=True)
class RankedCandidate:
    parent_asin: str
    final_score: float
    component_scores: dict[str, float]


@dataclass(frozen=True)
class PolicyDecision:
    should_ask: bool
    ask_attribute: str | None
    message: str
    recommendation_count: int


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
