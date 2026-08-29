from __future__ import annotations

import sqlite3

from shopping_copilot.catalog.constraints import matches_any_excluded_term, normalized_tokens
from shopping_copilot.catalog.store import CatalogStore
from shopping_copilot.core.contracts import Candidate, RetrievalDiagnostics, RetrievalResult, SearchPlan


class BM25Retriever:
    """Fast offline candidate search based on the official SQLite FTS5 baseline."""

    def __init__(
        self,
        store: CatalogStore,
        strict_rescue_enabled: bool = False,
    ) -> None:
        self.store = store
        self.strict_rescue_enabled = strict_rescue_enabled
        self.connection = sqlite3.connect(":memory:")
        self._category_frequency_cache: dict[str, int] = {}
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

    @staticmethod
    def _or_group(tokens: tuple[str, ...], field: str | None = None) -> str:
        expressions = [
            f'{field} : "{token}"' if field else f'"{token}"'
            for token in tokens
        ]
        if len(expressions) == 1:
            return expressions[0]
        return "(" + " OR ".join(expressions) + ")"

    def _category_frequency(self, token: str) -> int:
        cached = self._category_frequency_cache.get(token)
        if cached is not None:
            return cached
        expression = f'categories : "{token}"'
        count = int(self.connection.execute(
            "SELECT count(*) FROM products WHERE products MATCH ?",
            (expression,),
        ).fetchone()[0])
        self._category_frequency_cache[token] = count
        return count

    def _strict_expression(self, plan: SearchPlan) -> str:
        """Build one high-precision slot AND route with a catalog-derived anchor."""

        lexical_terms = set(plan.lexical_terms)
        groups: list[tuple[str, ...]] = []
        seen_groups: set[tuple[str, ...]] = set()
        for slot in sorted(plan.structured_constraints):
            if slot == "category":
                continue
            tokens = tuple(dict.fromkeys(
                token
                for value in plan.structured_constraints[slot]
                for token in normalized_tokens(value)
                if len(token) > 1 and token in lexical_terms
            ))
            signature = tuple(sorted(tokens))
            if not signature or signature in seen_groups:
                continue
            seen_groups.add(signature)
            groups.append(tokens)

        category_tokens = tuple(dict.fromkeys(
            token
            for value in plan.structured_constraints.get("category", ())
            for token in normalized_tokens(value)
            if len(token) > 1 and token in lexical_terms
        ))
        category_signature = tuple(sorted(category_tokens))
        if category_signature:
            groups = [
                tokens for tokens in groups
                if tuple(sorted(tokens)) != category_signature
            ]
        independent_group_count = len(groups) + int(bool(category_tokens))
        if independent_group_count < 2:
            return ""

        expressions: list[str] = []
        if category_tokens:
            # Coarse evaluator categories often contain one generic and one
            # discriminative token (for example "women" and "novelty").  Select
            # the rarest token from the full catalog rather than hard-coding a
            # category list, then require that anchor in title/store as precision
            # evidence while retaining the catalog category check.
            anchor = min(
                category_tokens,
                key=lambda token: (self._category_frequency(token), token),
            )
            expressions.append(self._or_group(category_tokens, "categories"))
            expressions.append(f'{{title store}} : "{anchor}"')
        expressions.extend(self._or_group(tokens) for tokens in groups)
        return " AND ".join(expressions)

    def _retrieve_expression(
        self,
        plan: SearchPlan,
        expression: str,
        limit: int,
        source_route: str,
        *,
        deterministic_ties: bool = False,
    ) -> tuple[Candidate, ...]:
        if not expression or limit <= 0:
            return ()
        requires_filter_headroom = (
            plan.hard_filters.get("price_max") is not None
            or bool(plan.excluded_terms)
        )
        fetch_limit = max(limit, limit * 4) if requires_filter_headroom else limit
        order_by = "ORDER BY 2, parent_asin" if deterministic_ties else "ORDER BY 2"
        rows = self.connection.execute(
            "SELECT parent_asin, bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
            f"FROM products WHERE products MATCH ? {order_by} LIMIT ?",
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
                    source_routes=(source_route,),
                )
            )
            if len(candidates) >= limit:
                break
        return tuple(candidates)

    def broad_candidates(self, plan: SearchPlan, limit: int) -> tuple[Candidate, ...]:
        """Expose the unchanged broad route for route-level diagnostics."""

        return self._retrieve_expression(plan, self._expression(plan), limit, "bm25")

    def strict_candidates(
        self,
        plan: SearchPlan,
        limit: int,
        *,
        deterministic_ties: bool = False,
    ) -> tuple[Candidate, ...]:
        """Return slot-level Strict AND candidates without changing live retrieval."""

        return self._retrieve_expression(
            plan,
            self._strict_expression(plan),
            limit,
            "bm25:strict_and",
            deterministic_ties=deterministic_ties,
        )

    def _broad_score(self, plan: SearchPlan, parent_asin: str) -> float:
        return self.broad_scores_for_ids(plan, (parent_asin,)).get(parent_asin, 0.0)

    def broad_scores_for_ids(
        self,
        plan: SearchPlan,
        parent_asins: tuple[str, ...],
    ) -> dict[str, float]:
        """Return Broad BM25 scores for known IDs using one bound query."""

        expression = self._expression(plan)
        if not parent_asins or not expression:
            return {}
        unique_ids = tuple(sorted(set(parent_asins)))
        placeholders = ", ".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            "SELECT parent_asin, "
            "bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
            f"FROM products WHERE products MATCH ? AND parent_asin IN ({placeholders})",
            (expression, *unique_ids),
        ).fetchall()
        return {
            str(parent_asin): max(0.0, -float(raw_score))
            for parent_asin, raw_score in rows
        }

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

        broad = self._retrieve_expression(plan, expression, plan.candidate_k, "bm25")
        diagnostics = self.probe(plan)
        if not self.strict_rescue_enabled:
            return RetrievalResult(broad, diagnostics)

        broad_ids = {candidate.parent_asin for candidate in broad}
        strict = self.strict_candidates(plan, 60)
        rescue: list[Candidate] = []
        for candidate in strict:
            if candidate.parent_asin in broad_ids:
                continue
            rescue.append(Candidate(
                parent_asin=candidate.parent_asin,
                lexical_score=self._broad_score(plan, candidate.parent_asin),
                source_routes=("bm25", "bm25:strict_and"),
            ))
            if len(rescue) >= 10:
                break
        candidates = (*broad, *rescue)
        return RetrievalResult(candidates, diagnostics)
