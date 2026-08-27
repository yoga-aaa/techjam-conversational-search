from __future__ import annotations

import sqlite3

from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan


class BM25Retriever:
    """Fast offline candidate search based on the official SQLite FTS5 baseline."""

    def __init__(self, store: CatalogStore) -> None:
        self.store = store
        self.connection = sqlite3.connect(":memory:")
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
        return RetrievalDiagnostics(candidate_count, category_count, max(0.0, score_gap), plan.route)

    def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        expression = self._expression(plan)
        if not expression:
            diagnostics = RetrievalDiagnostics(0, 0, 0.0, plan.route)
            return RetrievalResult((), diagnostics)

        fetch_limit = max(plan.candidate_k, plan.candidate_k * 4)
        rows = self.connection.execute(
            "SELECT parent_asin, bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
            "FROM products WHERE products MATCH ? ORDER BY 2 LIMIT ?",
            (expression, fetch_limit),
        ).fetchall()

        price_max = plan.hard_filters.get("price_max")
        excluded = tuple(term.lower() for term in plan.excluded_terms)
        candidates: list[Candidate] = []
        for parent_asin, raw_score in rows:
            product = self.store.get(str(parent_asin))
            if product is None:
                continue
            if price_max is not None and product.price is not None and product.price > float(price_max):
                continue
            searchable = product.searchable_text.lower()
            if excluded and any(term in searchable for term in excluded):
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
