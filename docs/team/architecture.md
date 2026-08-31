# Team Architecture

## Design rule

The official entry point and the orchestration order stay fixed. Experiments replace
implementations through config; they do not rewrite the pipeline.

```text
Evaluator
  -> Official Agent adapter
  -> State tracker
  -> Query planner
  -> Candidate probe
  -> Pre-search policy
       -> clarify path: cheap candidates + structured question
       -> search path: hybrid candidate search -> reranking
  -> Post-ranking policy
  -> Response builder
```

## Stable contracts

`shopping_copilot/core/contracts.py` defines the shared records:

- `SessionState`: accumulated and overridden user requirements;
- `SearchPlan`: route, query, filters, weights, and candidate budget;
- `RetrievalResult`: candidates plus broadness diagnostics;
- `RankedCandidate`: final score and component scores;
- `PolicyDecision`: question, message, and recommendation count.

`shopping_copilot/core/interfaces.py` defines the replaceable components. Changes to
contracts, interfaces, or `core/pipeline.py` require integration review because they
can affect every owner.

## Default implementations

- `state/rule_state.py`: offline accumulation, no-preference handling, and explicit intent-override tracking;
- `planning/query_planner.py`: shared Buying/Browsing planning with route-specific weights and current-state-only query projection;
- `retrieval/bm25.py`: official-style SQLite FTS5 candidate search; after an explicit override, the selected config conditionally fuses at most five deterministic query variants with RRF;
- `retrieval/hybrid.py`: stable multi-route fusion point;
- `retrieval/category_anchor.py`: complete same-category candidate pool used for global evidence ranking;
- `retrieval/structured.py`: optional attribute-aware route, disabled in the selected final config after a public-set regression;
- `ranking/evidence.py`: phrase evidence, category anchoring, lexical-route consensus, budget, profile, and popularity scoring over the complete candidate set;
- `policy/heuristic.py`: over-generality gate, question selection, and dynamic result count;
- `policy/coverage.py`: candidate-stagnation detection and bounded unseen-candidate scheduling, including the override-only 7/3 consensus-tail balance;
- `policy/information_gain.py`: candidate-driven question scoring from attribute
  coverage, entropy, expected reduction, and answerability priors;
- `response/builder.py`: strict official output validation;
- `observability/trace.py`: ground-truth-free internal turn traces.

## Model extension rule

Optional model-backed state, ranking, or policy implementations must use one model
gateway, validate structured output, report usage, and declare an offline fallback.
They must implement the existing interface instead of adding model calls directly to
`core/pipeline.py`.

## Evaluation boundary

The Agent never reads `data/public_set.jsonl`, scenario labels, or ground truth. Local
analysis scripts may combine public labels with traces only after an evaluation run.
The official evaluator and public labels remain unmodified.
