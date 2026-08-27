# Code Ownership

| Owner | Primary paths | Responsibility |
| --- | --- | --- |
| Member 1 | `shopping_copilot/ranking/`, `shopping_copilot/policy/` | reranking, clarification, dynamic result count |
| Member 2 | `shopping_copilot/state/`, `shopping_copilot/planning/` | multi-turn state, override, intent and search planning |
| Member 3 | `starter/`, `shopping_copilot/core/`, `shopping_copilot/response/`, `.github/` | integration, contracts, config, CI, submission |
| Member 4 | `shopping_copilot/catalog/`, `shopping_copilot/retrieval/` | catalog access, BM25, dense search and fusion |
| Member 5 | `shopping_copilot/observability/`, `scripts/`, `experiments/`, team docs | evaluation, traces, failure analysis and reporting |

Changes to `core/contracts.py`, `core/interfaces.py`, or `core/pipeline.py` require
review by Member 3, Member 1, and each affected module owner. The official
`evaluator/` directory and public labels must not be changed.
