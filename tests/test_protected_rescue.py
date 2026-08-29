from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import load_config
from shopping_copilot.core.contracts import (
    Candidate,
    PolicyDecision,
    RankedCandidate,
    RetrievalDiagnostics,
    RetrievalResult,
    SearchPlan,
    SessionState,
)
from shopping_copilot.core.factory import build_components
from shopping_copilot.planning.query_planner import RuleQueryPlanner
from shopping_copilot.ranking.protected_rescue import ProtectedRescueSelector
from shopping_copilot.retrieval.bm25 import BM25Retriever
from shopping_copilot.retrieval.protected_rescue import StrictAndCandidateRescuer
from shopping_copilot.core.pipeline import ShoppingCopilotAgent


def _plan(
    *,
    lexical_terms: tuple[str, ...],
    constraints: dict[str, tuple[str, ...]],
    candidate_k: int = 100,
    hard_filters: dict[str, object] | None = None,
    excluded_terms: tuple[str, ...] = (),
) -> SearchPlan:
    return SearchPlan(
        route="buying",
        lexical_terms=lexical_terms,
        semantic_query=" ".join(lexical_terms),
        structured_constraints=constraints,
        hard_filters=hard_filters or {},
        excluded_terms=excluded_terms,
        bm25_weight=0.55,
        dense_weight=0.20,
        constraint_weight=0.20,
        profile_weight=0.05,
        candidate_k=candidate_k,
        use_dense=False,
        use_structured=False,
        diversity_enabled=False,
    )


class ProtectedRescueRetrievalTest(unittest.TestCase):
    @staticmethod
    def _write_catalog(root: Path, products: list[dict]) -> CatalogStore:
        path = root / "catalog.jsonl"
        path.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )
        return CatalogStore(path)

    @staticmethod
    def _product(parent_asin: str, title: str, *, categories: list[str], features: list[str], price: float = 20.0, details: dict | None = None, store: str = "Neutral Shop") -> dict:
        return {
            "parent_asin": parent_asin,
            "title": title,
            "features": features,
            "details": details or {},
            "description": [],
            "categories": categories,
            "store": store,
            "price": price,
        }

    def _rescue_fixture(self, root: Path) -> tuple[CatalogStore, SearchPlan]:
        products = [
            self._product(
                f"cat-{index:03d}",
                f"Everyday item {index:03d}",
                categories=["Gadget"],
                features=[],
            )
            for index in range(100)
        ]
        products.extend(
            self._product(
                f"mat-{index:03d}",
                f"Ceramic item {index:03d}",
                categories=["Other"],
                features=["ceramic"],
            )
            for index in range(80)
        )
        products.append(
            self._product(
                "core-item",
                "Compact utility item",
                categories=["Gadget"],
                features=["ceramic"],
                details={"finish": "ceramic"},
                store="Gadget House",
            )
        )
        store = self._write_catalog(root, products)
        plan = _plan(
            lexical_terms=("gadget", "ceramic"),
            constraints={"category": ("gadget",), "material": ("ceramic",)},
            candidate_k=60,
        )
        return store, plan

    def test_batched_scores_handle_empty_duplicates_unknowns_order_and_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self._write_catalog(root, [
                self._product("sku,one", "Amber widget", categories=["Widgets"], features=["amber"]),
                self._product("sku-two", "Blue widget", categories=["Widgets"], features=["blue"]),
            ])
            retriever = BM25Retriever(store)
            plan = _plan(lexical_terms=("widget",), constraints={"category": ("widgets",)})

            self.assertEqual(retriever.broad_scores_for_ids(plan, ()), {})
            scores = retriever.broad_scores_for_ids(plan, ("sku-two", "sku,one", "sku-two", "unknown"))
            reverse = retriever.broad_scores_for_ids(plan, ("sku,one", "sku-two"))

            self.assertEqual(set(scores), {"sku,one", "sku-two"})
            self.assertEqual(scores, reverse)
            self.assertEqual(scores["sku,one"], retriever._broad_score(plan, "sku,one"))
            self.assertEqual(scores["sku-two"], retriever._broad_score(plan, "sku-two"))

    def test_rescuer_recovers_strict_only_candidate_with_comparable_broad_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, plan = self._rescue_fixture(Path(directory))
            retriever = BM25Retriever(store)
            config = load_config("configs/final.json")
            rescuer = StrictAndCandidateRescuer(retriever, store, config.search)
            primary = retriever.retrieve(plan)
            rescued = rescuer.retrieve(plan, primary)

            self.assertNotIn("core-item", {item.parent_asin for item in primary.candidates})
            self.assertIn("core-item", {item.parent_asin for item in rescued})
            self.assertEqual(rescued[0].source_routes, ("bm25:protected_strict",))
            self.assertGreater(rescued[0].lexical_score, 0.0)
            self.assertEqual(
                rescued[0].lexical_score,
                retriever.broad_scores_for_ids(plan, ("core-item",))["core-item"],
            )

    def test_rescuer_applies_primary_dedup_slot_evidence_filters_and_pool_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = [
                self._product("primary", "Gadget ceramic primary", categories=["Gadget"], features=["ceramic"]),
                self._product("one-slot", "Gadget one slot", categories=["Gadget"], features=[]),
                self._product("bad-price", "Gadget ceramic expensive", categories=["Gadget"], features=["ceramic"], price=200.0),
                self._product("bad-negative", "Gadget ceramic red", categories=["Gadget"], features=["ceramic", "red"]),
            ]
            products.extend(
                self._product(
                    f"rescue-{index:02d}",
                    f"Gadget ceramic candidate {index:02d}",
                    categories=["Gadget"],
                    features=["ceramic"],
                )
                for index in range(4)
            )
            store = self._write_catalog(root, products)
            plan = _plan(
                lexical_terms=("gadget", "ceramic"),
                constraints={
                    "category": ("gadget",),
                    "material": ("ceramic", "stone"),
                },
                hard_filters={"price_max": 50},
                excluded_terms=("red",),
            )
            retriever = BM25Retriever(store)
            config = replace(
                load_config("configs/final.json").search,
                protected_rescue_pool_size=2,
            )
            rescuer = StrictAndCandidateRescuer(retriever, store, config)
            primary = RetrievalResult(
                candidates=(retriever.retrieve(plan).candidates[0],),
                diagnostics=RetrievalDiagnostics(1, 1, 0.0, plan.route),
            )
            rescued = rescuer.retrieve(plan, primary)

            self.assertEqual(len(rescued), 2)
            self.assertNotIn("primary", {item.parent_asin for item in rescued})
            self.assertNotIn("one-slot", {item.parent_asin for item in rescued})
            self.assertNotIn("bad-price", {item.parent_asin for item in rescued})
            self.assertNotIn("bad-negative", {item.parent_asin for item in rescued})

    def test_same_slot_alternatives_count_once_and_two_slots_count_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = [
                self._product("one-slot", "Gadget ceramic object", categories=["Gadget"], features=["ceramic"]),
                self._product("two-slot", "Gadget ceramic object", categories=["Gadget"], features=["ceramic"], details={"color": "teal"}),
            ]
            store = self._write_catalog(root, products)
            retriever = BM25Retriever(store)
            config = replace(load_config("configs/final.json").search, protected_rescue_pool_size=10)
            rescuer = StrictAndCandidateRescuer(retriever, store, config)
            same_slot_plan = _plan(
                lexical_terms=("gadget", "ceramic", "stone"),
                constraints={"category": ("gadget",), "material": ("ceramic", "stone")},
            )
            two_slot_plan = replace(
                same_slot_plan,
                lexical_terms=("gadget", "ceramic", "stone", "teal"),
                structured_constraints={
                    "category": ("gadget",),
                    "material": ("ceramic", "stone"),
                    "color": ("teal",),
                },
            )

            one_slot = store.get("one-slot")
            two_slot = store.get("two-slot")
            assert one_slot and two_slot
            self.assertEqual(rescuer._matched_slot_count(one_slot, same_slot_plan), 2)
            self.assertEqual(rescuer._matched_slot_count(two_slot, two_slot_plan), 3)

    def test_catalog_row_permutation_keeps_rescue_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = [
                self._product("rescue-a", "Gadget ceramic alpha", categories=["Gadget"], features=["ceramic"]),
                self._product("rescue-b", "Gadget ceramic beta", categories=["Gadget"], features=["ceramic"]),
                self._product("rescue-c", "Gadget ceramic gamma", categories=["Gadget"], features=["ceramic"]),
            ]
            plan = _plan(
                lexical_terms=("gadget", "ceramic"),
                constraints={"category": ("gadget",), "material": ("ceramic",)},
            )
            first_store = self._write_catalog(root, products)
            first_retriever = BM25Retriever(first_store)
            config = load_config("configs/final.json").search
            first = StrictAndCandidateRescuer(first_retriever, first_store, config).retrieve(
                plan,
                RetrievalResult((), RetrievalDiagnostics(0, 0, 0.0, "buying")),
            )
            second_store = self._write_catalog(root, list(reversed(products)))
            second_retriever = BM25Retriever(second_store)
            second = StrictAndCandidateRescuer(second_retriever, second_store, config).retrieve(
                plan,
                RetrievalResult((), RetrievalDiagnostics(0, 0, 0.0, "buying")),
            )

            self.assertEqual(
                [item.parent_asin for item in first],
                [item.parent_asin for item in second],
            )

    def test_fetch_cutoff_ties_are_catalog_order_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = [
                self._product(
                    f"tied-{index:03d}",
                    "Gadget ceramic identical",
                    categories=["Gadget"],
                    features=["ceramic"],
                )
                for index in range(75)
            ]
            self.assertGreater(len(products), 60)
            plan = _plan(
                lexical_terms=("gadget", "ceramic"),
                constraints={"category": ("gadget",), "material": ("ceramic",)},
            )
            config = replace(
                load_config("configs/final.json").search,
                protected_rescue_fetch_k=60,
                protected_rescue_pool_size=20,
            )
            empty_primary = RetrievalResult(
                (),
                RetrievalDiagnostics(0, 0, 0.0, "buying"),
            )

            first_root = root / "first"
            first_root.mkdir()
            first_store = self._write_catalog(first_root, products)
            first = StrictAndCandidateRescuer(
                BM25Retriever(first_store),
                first_store,
                config,
            ).retrieve(plan, empty_primary)
            second_root = root / "second"
            second_root.mkdir()
            second_store = self._write_catalog(second_root, list(reversed(products)))
            second = StrictAndCandidateRescuer(
                BM25Retriever(second_store),
                second_store,
                config,
            ).retrieve(plan, empty_primary)

            self.assertEqual(
                [item.parent_asin for item in first],
                [item.parent_asin for item in second],
            )
            self.assertEqual(
                [item.parent_asin for item in first],
                [f"tied-{index:03d}" for index in range(20)],
            )


class ProtectedRescueSelectorTest(unittest.TestCase):
    @staticmethod
    def _item(parent_asin: str, score: float = 1.0) -> RankedCandidate:
        return RankedCandidate(parent_asin, score, {})

    @classmethod
    def _ranked(cls, *parent_asins: str) -> list:
        return [cls._item(parent_asin, 1.0 - index / 100.0) for index, parent_asin in enumerate(parent_asins)]

    @staticmethod
    def _selector(*, head: int = 7, quota: int = 3, rank_limit: int = 10) -> ProtectedRescueSelector:
        policy = replace(
            load_config("configs/final.json").policy,
            protected_rescue_head_size=head,
            protected_rescue_quota=quota,
            protected_rescue_rank_limit=rank_limit,
        )
        return ProtectedRescueSelector(policy)

    def test_no_eligible_rescue_is_an_exact_no_op(self) -> None:
        base = self._ranked("A", "B", "C", "D")
        expanded = self._ranked("A", "B", "C", "D", "R")
        selected = self._selector().select(base, expanded, set(), 10)

        self.assertEqual(selected, list(base))

    def test_protected_head_remains_identical_and_ordered(self) -> None:
        base = self._ranked("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")
        expanded = self._ranked("R", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J")
        selected = self._selector().select(base, expanded, {"R"}, 10)

        self.assertEqual(
            [item.parent_asin for item in selected[:7]],
            [item.parent_asin for item in base[:7]],
        )

    def test_quota_and_rank_limit_are_enforced(self) -> None:
        base = self._ranked(*[f"B{index}" for index in range(10)])
        expanded = self._ranked("R0", "R1", "R2", "R3", *[f"B{index}" for index in range(10)])
        selector = self._selector(quota=2, rank_limit=3)
        selected = selector.select(base, expanded, {"R0", "R1", "R2", "R3"}, 10)

        visible = [item.parent_asin for item in selected[:10]]
        self.assertEqual(sum(item.startswith("R") for item in visible), 2)
        self.assertEqual(visible[:7], [f"B{index}" for index in range(7)])
        self.assertEqual(visible[7:9], ["R0", "R1"])

    def test_rescue_inside_rank_limit_can_enter_tail_and_outside_is_rejected(self) -> None:
        base = self._ranked(*[f"B{index}" for index in range(10)])
        expanded = self._ranked(*[f"B{index}" for index in range(7)], "R-in", "B7", "R-out", "B8", "B9")
        selected = self._selector(rank_limit=8).select(base, expanded, {"R-in", "R-out"}, 10)
        visible = [item.parent_asin for item in selected[:10]]

        self.assertIn("R-in", visible)
        self.assertNotIn("R-out", visible)

    def test_duplicate_ids_are_removed_deterministically(self) -> None:
        base = self._ranked("A", "A", "B", "C", "D", "E", "F", "G", "H")
        expanded = self._ranked("A", "R", "R", "B", "C", "D", "E", "F", "G", "H")
        selected = self._selector().select(base, expanded, {"R"}, 10)

        self.assertEqual(
            [item.parent_asin for item in selected[:9]],
            ["A", "B", "C", "D", "E", "F", "G", "R", "H"],
        )
        self.assertEqual(len({item.parent_asin for item in selected}), len(selected))

    def test_short_base_is_filled_safely(self) -> None:
        base = self._ranked("A", "B")
        expanded = self._ranked("R", "A", "B")
        selected = self._selector().select(base, expanded, {"R"}, 10)

        self.assertEqual([item.parent_asin for item in selected], ["A", "B", "R"])

    def test_recommendation_counts_are_handled(self) -> None:
        base = self._ranked(*[f"B{index}" for index in range(10)])
        expanded = self._ranked(*[f"B{index}" for index in range(7)], "R", "B7", "B8", "B9")
        selector = self._selector()

        for count in (0, 1, 7, 8, 9, 10):
            selected = selector.select(base, expanded, {"R"}, count)
            self.assertGreaterEqual(len(selected), min(count, 10))
            if count <= 7:
                self.assertEqual(selected, list(base))
            else:
                self.assertEqual(
                    [item.parent_asin for item in selected[:7]],
                    [item.parent_asin for item in base[:7]],
                )

    def test_inputs_are_not_mutated_and_expanded_order_breaks_equal_score_ties(self) -> None:
        base = self._ranked("A", "B", "C", "D", "E", "F", "G", "H")
        expanded = self._ranked("A", "B", "C", "D", "E", "F", "G", "R1", "R2", "H")
        base_before = list(base)
        expanded_before = list(expanded)
        selected = self._selector().select(base, expanded, {"R1", "R2"}, 10)

        self.assertEqual(base, base_before)
        self.assertEqual(expanded, expanded_before)
        self.assertEqual([item.parent_asin for item in selected[7:9]], ["R1", "R2"])


class ProtectedRescueConfigTest(unittest.TestCase):
    def _config_with(self, root: Path, *, search: dict | None = None, policy: dict | None = None) -> Path:
        payload = json.loads(Path("configs/final.json").read_text(encoding="utf-8"))
        payload["search"].update(search or {})
        payload["policy"].update(policy or {})
        path = root / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_invalid_protected_rescue_combinations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                ({}, {"protected_rescue_head_size": 8, "protected_rescue_quota": 3}),
                ({"protected_rescue_enabled": True, "strict_rescue_enabled": True}, {}),
                ({"protected_rescue_enabled": True}, {"full_rerank_clarification_response": True}),
            )
            for search, policy in cases:
                with self.subTest(search=search, policy=policy):
                    with self.assertRaises(ValueError):
                        load_config(self._config_with(root, search=search, policy=policy))


class _PipelineStateTracker:
    def __init__(self) -> None:
        self.state: SessionState | None = None

    def reset(self, session_id: str, user_profile: dict) -> SessionState:
        self.state = SessionState(session_id=session_id)
        return self.state

    def update(self, session_id: str, user_message: str, turn: int) -> SessionState:
        assert self.state is not None
        self.state.turn = turn
        self.state.messages.append(user_message)
        return self.state

    def get(self, session_id: str) -> SessionState:
        assert self.state is not None
        return self.state

    def mark_asked(self, session_id: str, attribute: str | None) -> None:
        assert self.state is not None
        if attribute is not None:
            self.state.asked_attributes.append(attribute)


class _PipelinePlanner:
    def __init__(self, plan: SearchPlan) -> None:
        self.plan = plan

    def build(self, state: SessionState) -> SearchPlan:
        return self.plan


class _PipelineRetriever:
    def __init__(self, candidates: tuple[Candidate, ...], plan: SearchPlan) -> None:
        self.candidates = candidates
        self.diagnostics = RetrievalDiagnostics(len(candidates), 1, 0.0, plan.route)

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        return self.diagnostics

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        return RetrievalResult(self.candidates, self.diagnostics)


class _PipelineRanker:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, ...]] = []

    def rank(
        self,
        state: SessionState,
        plan: SearchPlan,
        result: RetrievalResult,
    ) -> list[RankedCandidate]:
        ids = tuple(item.parent_asin for item in result.candidates)
        self.inputs.append(ids)
        if "rescue-neutral" in ids:
            ids = tuple(ids[:7]) + ("rescue-neutral",) + tuple(
                parent_asin
                for parent_asin in ids[7:]
                if parent_asin != "rescue-neutral"
            )
        return [
            RankedCandidate(parent_asin, 1.0 - index / 100.0, {})
            for index, parent_asin in enumerate(ids)
        ]


class _PipelinePolicy:
    def __init__(self) -> None:
        self.after_inputs: list[tuple[str, ...]] = []

    def before_search(
        self,
        state: SessionState,
        plan: SearchPlan,
        diagnostics: RetrievalDiagnostics,
    ) -> PolicyDecision | None:
        if state.turn >= 10:
            return None
        return PolicyDecision(True, "material", "Choose a material.", 10)

    def after_ranking(
        self,
        state: SessionState,
        plan: SearchPlan,
        ranked: list[RankedCandidate],
        diagnostics: RetrievalDiagnostics,
        early_decision: PolicyDecision | None = None,
    ) -> PolicyDecision:
        self.after_inputs.append(tuple(item.parent_asin for item in ranked))
        return PolicyDecision(True, "material", "Choose a material.", 10)


class _PipelineCoverage:
    def __init__(self, force_coverage_mode: bool = False) -> None:
        self.force_coverage_mode = force_coverage_mode
        self.observed_inputs: list[tuple[str, ...]] = []

    def observe(self, state: SessionState, ranked: list[RankedCandidate]) -> None:
        self.observed_inputs.append(tuple(item.parent_asin for item in ranked))
        state.coverage_mode = self.force_coverage_mode

    @staticmethod
    def order_for_response(state: SessionState, ranked: list[RankedCandidate]) -> list[RankedCandidate]:
        return list(ranked)

    @staticmethod
    def record_response(state: SessionState, recommendations: object) -> None:
        return None


class _PipelineRescuer:
    def __init__(self, return_candidate: bool = True) -> None:
        self.return_candidate = return_candidate
        self.calls = 0

    def retrieve(self, plan: SearchPlan, primary: RetrievalResult) -> tuple[Candidate, ...]:
        self.calls += 1
        if not self.return_candidate:
            return ()
        return (Candidate("rescue-neutral", lexical_score=0.4, source_routes=("bm25:protected_strict",)),)


class _PipelineTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, event: dict) -> None:
        self.events.append(event)


class ProtectedRescuePipelineTest(unittest.TestCase):
    @staticmethod
    def _catalog(root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "catalog.jsonl"
        products = [
            {
                "parent_asin": f"base-{index}",
                "title": f"Base item {index}",
                "categories": ["Gadget"],
                "features": ["ceramic"],
                "details": {},
                "description": [],
                "store": "Neutral Shop",
                "price": 20,
            }
            for index in range(10)
        ]
        products.append({
            "parent_asin": "rescue-neutral",
            "title": "Rescue item",
            "categories": ["Gadget"],
            "features": ["ceramic"],
            "details": {},
            "description": [],
            "store": "Neutral Shop",
            "price": 20,
        })
        path.write_text("".join(json.dumps(product) + "\n" for product in products), encoding="utf-8")
        return path

    @staticmethod
    def _components(catalog: Path, config_path: str, *, rescuer: _PipelineRescuer, coverage: _PipelineCoverage):
        config = load_config(config_path)
        components = build_components(catalog, config)
        plan = _plan(
            lexical_terms=("gadget", "ceramic"),
            constraints={"category": ("gadget",), "material": ("ceramic",)},
            candidate_k=10,
        )
        primary = tuple(
            Candidate(f"base-{index}", lexical_score=1.0 - index / 100.0)
            for index in range(10)
        )
        policy = _PipelinePolicy()
        ranker = _PipelineRanker()
        trace = _PipelineTrace()
        return replace(
            components,
            state_tracker=_PipelineStateTracker(),
            planner=_PipelinePlanner(plan),
            retriever=_PipelineRetriever(primary, plan),
            ranker=ranker,
            policy=policy,
            coverage_manager=coverage,
            candidate_rescuer=rescuer,
            trace_sink=trace,
        ), policy, ranker, trace

    def _agent(self, root: Path, config_path: str, *, rescuer: _PipelineRescuer, coverage: _PipelineCoverage):
        catalog = self._catalog(root)
        components, policy, ranker, trace = self._components(
            catalog,
            config_path,
            rescuer=rescuer,
            coverage=coverage,
        )
        return ShoppingCopilotAgent(catalog, config_path=config_path, components=components), policy, ranker, trace

    def test_flag_off_response_is_identical_and_rescue_is_not_queried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            off_rescuer = _PipelineRescuer()
            off_agent, _, _, _ = self._agent(
                root / "off",
                "configs/final.json",
                rescuer=off_rescuer,
                coverage=_PipelineCoverage(),
            )
            off_agent.reset("off", {})
            off_response = off_agent.respond("off", "neutral request", 1, 10)

            payload = json.loads(Path("configs/final.json").read_text(encoding="utf-8"))
            payload["search"]["protected_rescue_enabled"] = True
            enabled_path = root / "enabled.json"
            enabled_path.write_text(json.dumps(payload), encoding="utf-8")
            no_result_agent, _, _, _ = self._agent(
                root / "enabled",
                enabled_path,
                rescuer=_PipelineRescuer(return_candidate=False),
                coverage=_PipelineCoverage(),
            )
            no_result_agent.reset("enabled", {})
            no_result_response = no_result_agent.respond("enabled", "neutral request", 1, 10)

            self.assertEqual(off_response, no_result_response)
            self.assertEqual(off_rescuer.calls, 0)

    def test_rescue_is_gated_by_early_decision_and_coverage_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rescuer = _PipelineRescuer()
            agent, _, _, trace = self._agent(
                root / "late",
                "configs/experiments/protected_candidate_rescue.json",
                rescuer=rescuer,
                coverage=_PipelineCoverage(),
            )
            agent.reset("late", {})
            agent.respond("late", "neutral request", 10, 10)
            self.assertEqual(rescuer.calls, 0)
            self.assertFalse(trace.events[-1]["protected_rescue_attempted"])

            coverage_rescuer = _PipelineRescuer()
            coverage_agent, _, _, coverage_trace = self._agent(
                root / "coverage",
                "configs/experiments/protected_candidate_rescue.json",
                rescuer=coverage_rescuer,
                coverage=_PipelineCoverage(force_coverage_mode=True),
            )
            coverage_agent.reset("coverage", {})
            coverage_agent.respond("coverage", "neutral request", 1, 10)
            self.assertEqual(coverage_rescuer.calls, 0)
            self.assertFalse(coverage_trace.events[-1]["protected_rescue_attempted"])

    def test_policy_and_coverage_see_primary_while_rescue_enters_only_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rescuer = _PipelineRescuer()
            coverage = _PipelineCoverage()
            agent, policy, ranker, trace = self._agent(
                root / "integrated",
                "configs/experiments/protected_candidate_rescue.json",
                rescuer=rescuer,
                coverage=coverage,
            )
            agent.reset("integrated", {})
            response = agent.respond("integrated", "neutral request", 1, 10)
            visible = [item["parent_asin"] for item in response["recommendations"]]

            primary_ids = [f"base-{index}" for index in range(10)]
            self.assertEqual(policy.after_inputs, [tuple(primary_ids)])
            self.assertEqual(coverage.observed_inputs, [tuple(primary_ids)])
            self.assertEqual(visible[:7], primary_ids[:7])
            self.assertEqual(visible[7], "rescue-neutral")
            self.assertEqual(ranker.inputs[0], tuple(primary_ids))
            self.assertEqual(ranker.inputs[-1], tuple(primary_ids) + ("rescue-neutral",))
            self.assertEqual(rescuer.calls, 1)
            self.assertEqual(trace.events[-1]["protected_rescue_attempted"], True)
            self.assertEqual(trace.events[-1]["protected_rescue_inserted_ids"], ["rescue-neutral"])
            self.assertEqual(trace.events[-1]["protected_rescue_inserted_count"], 1)

    def test_trace_insertion_counts_only_recommendations_visible_at_top_k(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rescuer = _PipelineRescuer()
            agent, _, _, trace = self._agent(
                root / "top-k",
                "configs/experiments/protected_candidate_rescue.json",
                rescuer=rescuer,
                coverage=_PipelineCoverage(),
            )
            agent.reset("top-k", {})
            response = agent.respond("top-k", "neutral request", 1, 7)
            visible = [item["parent_asin"] for item in response["recommendations"]]

            self.assertEqual(visible, [f"base-{index}" for index in range(7)])
            self.assertEqual(trace.events[-1]["protected_rescue_inserted_ids"], [])
            self.assertEqual(trace.events[-1]["protected_rescue_inserted_count"], 0)
            self.assertEqual(trace.events[-1]["protected_rescue_expanded_top10_count"], 1)


if __name__ == "__main__":
    unittest.main()
