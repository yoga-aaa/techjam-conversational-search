from __future__ import annotations

import sqlite3

from shopping_copilot.catalog.constraints import matches_any_excluded_term
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.config import SearchConfig
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan
from shopping_copilot.retrieval.query_variants import LexicalQueryVariantBuilder


class BM25Retriever:
    """Fast offline candidate search based on the official SQLite FTS5 baseline."""

    def __init__(self, store: CatalogStore, config: SearchConfig | None = None) -> None:
        self.store = store
        self.config = config
        self.connection = sqlite3.connect(":memory:")
        self._variant_builder = LexicalQueryVariantBuilder(
            config.lexical_max_variants if config is not None else 5
        )
        self._last_probe_key: tuple[str, str] | None = None
        self._last_probe: RetrievalDiagnostics | None = None
        self._build_index()

    def _build_index(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        rows = [
            (
                product.parent_asin,
                product.title,
                " ".join(product.categories),
                " ".join(product.features),
                " ".join(product.details),
                product.store,
                " ".join(product.description),
            )
            for product in self.store.products
        ]
        cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        self.connection.commit()

    @staticmethod
    def _expression(plan: SearchPlan) -> str:
        return " OR ".join(f'"{term}"' for term in plan.lexical_terms)

    def probe(self, plan: SearchPlan) -> RetrievalDiagnostics:
        expression = self._expression(plan)
        if not expression:
            return RetrievalDiagnostics(0, 0, 0.0, plan.route)
        cache_key = (expression, plan.route)
        if cache_key == self._last_probe_key and self._last_probe is not None:
            return self._last_probe

        candidate_count = int(
            self.connection.execute(
                "SELECT count(*) FROM products WHERE products MATCH ?",
                (expression,),
            ).fetchone()[0]
        )
        preview = self.connection.execute(
            "SELECT bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
            "FROM products WHERE products MATCH ? ORDER BY 1 LIMIT 2",
            (expression,),
        ).fetchall()
        scores = [-float(row[0]) for row in preview]
        score_gap = scores[0] - scores[1] if len(scores) > 1 else (scores[0] if scores else 0.0)

        category_rows = self.connection.execute(
            "SELECT categories FROM products WHERE products MATCH ? LIMIT 500",
            (expression,),
        ).fetchall()
        category_count = len({str(row[0]).lower() for row in category_rows if str(row[0]).strip()})
        diagnostics = RetrievalDiagnostics(candidate_count, category_count, max(0.0, score_gap), plan.route)
        self._last_probe_key = cache_key
        self._last_probe = diagnostics
        return diagnostics

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        expression = self._expression(plan)
        if not expression:
            diagnostics = RetrievalDiagnostics(0, 0, 0.0, plan.route)
            return RetrievalResult((), diagnostics)

        if (
            self.config is not None
            and self.config.lexical_multiquery_enabled
            and (
                not self.config.lexical_multiquery_override_only
                or plan.has_explicit_override
            )
        ):
            return self._retrieve_multiquery(plan)

        fetch_limit = max(plan.candidate_k, plan.candidate_k * 4)
        rows = self.connection.execute(
            "SELECT parent_asin, bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
            "FROM products WHERE products MATCH ? ORDER BY 2 LIMIT ?",
            (expression, fetch_limit),
        ).fetchall()

        price_max = plan.hard_filters.get("price_max")
        candidates: list[Candidate] = []
        for parent_asin, raw_score in rows:
            product = self.store.get(str(parent_asin))
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            if matches_any_excluded_term(product, plan.excluded_terms):
                continue
            candidates.append(
                Candidate(
                    parent_asin=product.parent_asin,
                    lexical_score=max(0.0, -float(raw_score)),
                    source_routes=("bm25",),
                )
            )
            if len(candidates) >= plan.candidate_k:
                break

        diagnostics = self.probe(plan)
        return RetrievalResult(tuple(candidates), diagnostics)

    def _retrieve_multiquery(self, plan: SearchPlan) -> RetrievalResult:
        variants = self._variant_builder.build(plan)
        if not variants:
            return RetrievalResult((), RetrievalDiagnostics(0, 0, 0.0, plan.route))

        multiplier = self.config.lexical_fetch_multiplier if self.config is not None else 4
        rrf_k = self.config.lexical_rrf_k if self.config is not None else 60.0
        fetch_limit = min(len(self.store.products), max(plan.candidate_k, plan.candidate_k * multiplier))
        fusion_scores: dict[str, float] = {}
        raw_scores: dict[str, float] = {}
        broad_scores: dict[str, float] = {}
        source_routes: dict[str, set[str]] = {}

        for variant in variants:
            rows = self.connection.execute(
                "SELECT parent_asin, bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
                "FROM products WHERE products MATCH ? ORDER BY 2 LIMIT ?",
                (variant.expression, fetch_limit),
            ).fetchall()
            eligible_rank = 0
            for parent_asin, raw_score in rows:
                product = self.store.get(str(parent_asin))
                if product is None:
                    continue
                price_max = plan.hard_filters.get("price_max")
                if price_max is not None and product.price is not None and product.price > float(price_max):
                    continue
                if matches_any_excluded_term(product, plan.excluded_terms):
                    continue
                eligible_rank += 1
                asin = product.parent_asin
                fusion_scores[asin] = fusion_scores.get(asin, 0.0) + variant.weight / (rrf_k + eligible_rank)
                raw_scores[asin] = max(raw_scores.get(asin, 0.0), max(0.0, -float(raw_score)))
                if variant.name == "broad_or":
                    broad_scores[asin] = max(0.0, -float(raw_score))
                source_routes.setdefault(asin, set()).add(f"bm25:{variant.name}")

        ordered = sorted(
            fusion_scores,
            key=lambda asin: (-fusion_scores[asin], -raw_scores.get(asin, 0.0), asin),
        )[: plan.candidate_k]
        candidates = tuple(
            Candidate(
                parent_asin=asin,
                lexical_score=(
                    broad_scores.get(asin, 0.0)
                    if self.config is not None and self.config.lexical_preserve_broad_score
                    else fusion_scores[asin]
                ),
                source_routes=tuple(sorted(source_routes[asin])),
            )
            for asin in ordered
        )
        return RetrievalResult(candidates, self.probe(plan))
