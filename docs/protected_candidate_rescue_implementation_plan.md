# Protected Candidate Rescue V1 — Executable Implementation Specification

> Status: Proposed, experiment only
>
> Intended implementer: Luna Max
>
> Required base: `dev@efc5250`
>
> Last verified baseline: 2026-08-29, 59 tests passing
>
> Production default: disabled
>
> Scope: deterministic retrieval and response-slate integration only

## 0. How to use this document

This is a normative implementation specification, not a brainstorming document.
The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are requirements.

Luna Max should implement the phases in order, run the specified tests after each
phase, and stop if an invariant cannot be preserved. It should not invent a
different architecture, tune values against one public session, modify the
official evaluator, enable the experiment in `configs/final.json`, commit, or push
unless separately instructed.

The implementation target is a small, reversible experiment that tests whether a
high-confidence candidate recovered by a structured lexical route can enter the
unprotected tail of a clarification response without exposing the entire ranked
pool or changing the primary policy decision.

## 1. Decision

Implement a **protected candidate rescue** experiment with this shape:

1. Run the existing Broad BM25 retrieval and primary ranking unchanged.
2. Let the existing policy and coverage logic observe only the primary ranking.
3. On eligible early-clarification turns, run one field-aware Strict-AND rescue
   route using the same canonical `SearchPlan`.
4. Exclude candidates already present in the primary pool and reject candidates
   without evidence from at least two independent active slots.
5. Rank the primary and rescue union only for response-slate selection.
6. Preserve the head of the existing response exactly.
7. Permit at most three qualified rescue candidates to compete for the remaining
   Top-10 tail positions.
8. If no rescue candidate qualifies, return the exact baseline response order.

This experiment MUST NOT use unrestricted full-pool clarification reranking. It
MUST NOT replace Broad BM25 with lexical RRF. It MUST NOT change question selection,
coverage state, the ranker formula, or the official response schema.

## 2. Why this technology is selected

### 2.1 Industry pattern

Public Amazon material describes modern search as a multi-stage funnel with
multiple candidate generators followed by ranking. It also describes selecting
retrieval strategies and candidate allocations from query, context, candidate,
and source features:

- [Amazon: From structured search to learning-to-rank-and-retrieve](https://www.amazon.science/blog/from-structured-search-to-learning-to-rank-and-retrieve)

Google's commerce search documentation exposes the matching semantics needed here:
values inside one facet are OR alternatives, while different facets are combined
with AND. Google also exposes a precision-preserving expansion control that can
keep unexpanded results ahead of expanded results:

- [Google AI Commerce Search: text, filters, and facets](https://docs.cloud.google.com/retail/docs/search-basic)
- [Google QueryExpansionSpec and pinned unexpanded results](https://docs.cloud.google.com/retail/docs/reference/rest/v2/QueryExpansionSpec)

Google research also treats retrieval and reranking as a coupled, multi-stage
problem rather than assuming that higher candidate recall automatically produces
a better displayed ranking:

- [Google: Stochastic Retrieval-Conditioned Reranking](https://research.google/pubs/stochastic-retrieval-conditioned-reranking/)

Protected Candidate Rescue V1 is the deterministic, low-dependency analogue of
that architecture. It uses source-specific candidate generation, a small union,
second-stage ranking, and a protected serving head.

### 2.2 Technology comparison

| Option | Expected value for the current failure | Risk/cost | V1 decision |
| --- | --- | --- | --- |
| Larger Broad BM25 `candidate_k` | Low; target is diluted by common OR terms | More ranking and policy work | Reject |
| Always-on Strict-AND append | Candidate can be recovered, but the bounded response never sees appended candidates | Already regressed MRR | Reject |
| Full-pool clarification reranking | Recovers the miss | Material MRR and score regression | Reject |
| Five-query lexical fan-out + RRF | Could improve recall, but rewrites lexical ordering globally | High interaction and latency risk | Defer |
| Existing StructuredRetriever promotion | Attribute-aware, but previous public evaluation regressed | Broad behavioral change | Keep disabled |
| Dense bi-encoder retrieval | Valuable for genuine semantic/lexical gaps | Needs model, data, dependency, and latency work | Defer |
| Cross-encoder/listwise LTR | Strong ranking approach with suitable labels | No independent relevance training set | Defer |
| LLM query decomposition | Useful for latent or superlative intent | Canonical state already supplies explicit slots | Defer |
| **Protected Strict rescue** | Directly targets truncation while bounding exposure | Small cross-module change | **Select** |

Amazon's published web-scale semantic product-search work supports dense
bi-encoders when lexical matching is the actual bottleneck, but `public_0020` is
already found by lexical structured retrieval. Dense retrieval is therefore an
upgrade path, not the first fix:

- [Amazon: Web-scale semantic product search with large language models](https://www.amazon.science/publications/web-scale-semantic-product-search-with-large-language-models)

## 3. Current baseline and evidence

### 3.1 Required baseline

The implementation MUST start from `dev@efc5250` or a descendant containing that
commit. Before editing, record:

```bash
git status --short --branch
git log -1 --oneline --decorate
python -m unittest discover -s tests -p "test*.py" -v
```

Selected `configs/final.json` result on the 200-session public set:

| Metric | Baseline |
| --- | ---: |
| Sessions | 200 |
| Hit Rate@10 | 0.995000 |
| MRR | 0.778343 |
| MTTC | 3.335000 |
| Efficiency | 0.766500 |
| Recommended Technical Score | 0.884303 |
| Runtime model tokens | 0 |

Scenario baseline:

| Scenario | Sessions | Hit Rate@10 | MRR | MTTC |
| --- | ---: | ---: | ---: | ---: |
| Boundary | 10 | 1.000000 | 0.844444 | 4.200000 |
| Browsing | 80 | 1.000000 | 0.811592 | 2.962500 |
| Buying | 80 | 0.987500 | 0.734544 | 3.162500 |
| Intent Override | 30 | 1.000000 | 0.784444 | 4.500000 |

### 3.2 Failure classification

The only current public miss is `public_0020`. It is a Buying session. The
diagnostic evidence is:

| Stage | Turn-1 observation |
| --- | ---: |
| Broad BM25 rank at 60 | absent |
| Broad BM25 rank at 100 | absent |
| Broad deep rank | 117 |
| Field-aware Strict-AND rank | 20 |
| Strict-only rescue membership | yes |
| Rank after primary + rescue reranking | 8 |
| Displayed rank in the safe baseline | absent |

This establishes four facts:

1. State extraction is sufficient for this case.
2. The product has lexical catalog evidence; this is not primarily a dense
   semantic-retrieval failure.
3. A structured route can recover the candidate.
4. The current safe response path does not expose that candidate.

`public_0020` MAY be used to verify the end-to-end hypothesis after implementation.
Its target ASIN, title, complete feature text, or session-specific values MUST NOT
appear in runtime code, configuration, or synthetic unit-test fixtures.

### 3.3 Rejected experiments that constrain this design

The implementation MUST preserve these lessons:

| Experiment | Hit | MRR | MTTC | Score | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Pre-E6 baseline | 0.995 | 0.743752 | 3.650 | 0.867626 | Reference at that time |
| Strict rescue append | 0.995 | 0.739085 | 3.640 | 0.866425 | Reject |
| Strict rescue + full-pool response | 1.000 | 0.627153 | 3.145 | 0.845246 | Reject |

The full-pool experiment changed 89 sessions: 16 reciprocal-rank gains, 60 losses,
and 66 earlier hits. The problem was not lack of recall; it was uncontrolled early
exposure of lower-ranked candidates. V1 therefore protects the primary head, limits
the rescue quota, and leaves policy/coverage on the primary pool.

## 4. Goals, non-goals, and hard invariants

### 4.1 Goals

1. Give strongly supported Strict-only candidates a bounded path into the visible
   clarification Top 10.
2. Preserve primary Broad retrieval, question selection, and coverage behavior.
3. Bound the number and positions of newly exposed candidates.
4. Preserve deterministic, offline, zero-token execution.
5. Provide a clean feature flag and complete rollback path.
6. Decide promotion using the combined TechnicalScore and scenario metrics, not a
   single recovered session.

### 4.2 Non-goals

V1 MUST NOT:

- implement the old five-query FTS fan-out plan;
- add RRF inside BM25;
- enable the existing StructuredRetriever;
- enable dense retrieval;
- add an LLM, embedding model, vector index, or third-party runtime dependency;
- change state parsing, query planning semantics, policy scoring, coverage rules,
  ranker coefficients, or response formatting;
- use click, purchase, or behavioral signals that are not provided by the
  competition;
- optimize a rule for one ASIN, title, scenario label, or public target;
- modify `evaluator/`, `data/public_set.jsonl`, or the catalog;
- promote the experiment directly into `configs/final.json`.

### 4.3 Hard invariants

- `Agent.reset/respond` and the official response schema remain unchanged.
- `SearchPlan` remains the canonical positive/negative query projection.
- Rescue reads only `SearchPlan`; it never reads messages, labels, or ground truth.
- Same-slot values are alternatives; independent slots are separate evidence.
- Budget remains a numeric hard filter and never becomes an FTS term.
- Explicit negative constraints are never relaxed.
- Both retrieval filtering and the ranker final negative guard remain active.
- Primary retrieval and primary ranking are byte-for-byte equivalent when the new
  flag is false.
- Policy and coverage always observe the primary ranking, never the expanded union.
- Rescue runs only on eligible early-clarification turns and never in coverage mode.
- The protected primary head is preserved exactly and in order.
- At most the configured rescue quota can enter the visible slate.
- If no rescue qualifies, the final recommendation order is exactly the baseline
  order.
- Tie-breaking is deterministic and catalog insertion order cannot affect output.
- Existing rejected experiment configs remain reproducible.

## 5. Target architecture

```text
RuleStateTracker
      |
      v
RuleQueryPlanner -> SearchPlan
      |
      v
HybridRetriever / Broad BM25 -----------------------+
      |                                              |
      v                                              |
primary RetrievalResult                             |
      |                                              |
      v                                              |
primary HeuristicRanker                             |
      |                                              |
      +--> Coverage.observe(primary)                 |
      +--> Policy.after_ranking(primary)             |
      +--> existing bounded response ranking         |
                                                     |
early clarification + experiment enabled            |
      |                                              |
      v                                              |
StrictAndCandidateRescuer(SearchPlan, primary)       |
      |                                              |
      v                                              |
qualified Strict-only rescue pool                    |
      |                                              |
      +----------------> rank(primary + rescue) <----+
                              |
                              v
                    ProtectedRescueSelector
                    - lock primary head
                    - max three rescues
                    - only expanded Top-10 evidence
                              |
                              v
                    OfficialResponseBuilder
```

The expanded union is an ephemeral response-selection input. It MUST NOT replace
the primary result used by policy, coverage, candidate-stagnation state, or normal
ranking diagnostics.

## 6. Exact runtime behavior

### 6.1 Eligibility gate

Attempt rescue only when every condition is true:

```python
config.search.protected_rescue_enabled
and early_decision is not None
and decision.recommendation_count > 0
and not state.coverage_mode
and search_plan.route in {"buying", "browsing"}
and len(search_plan.structured_constraints) >= 2
```

`len(structured_constraints) >= 2` is a cheap precondition. The existing Strict
expression builder remains the authoritative independent-group test and MAY still
return an empty expression.

Do not attempt rescue on turn 10 because `early_decision` is already absent under
the current policy contract. Do not add another turn check unless required for
clarity in tests.

### 6.2 Primary path

The following operations MUST run exactly as they do in the baseline:

```python
result = components.retriever.retrieve(search_plan)
ranked = components.ranker.rank(state, search_plan, result)
components.coverage_manager.observe(state, ranked)
decision = components.policy.after_ranking(..., ranked, ...)
response_ranked = existing_bounded_and_coverage_logic(...)
```

Name these values `primary_result`, `primary_ranked`, and
`base_response_ranked` during the refactor. The rename is for clarity; behavior
must remain unchanged when the experiment is disabled.

### 6.3 Rescue retrieval

Add `StrictAndCandidateRescuer`, which receives the existing `BM25Retriever` and
the new search configuration.

Its public method is:

```python
def retrieve(
    self,
    plan: SearchPlan,
    primary: RetrievalResult,
) -> tuple[Candidate, ...]:
    ...
```

Algorithm:

```text
1. Ask BM25Retriever.strict_candidates(plan, protected_rescue_fetch_k).
2. If empty, return ().
3. Remove every candidate already in primary.candidates.
4. For each remaining product, count distinct non-budget slots for which at least
   one active value satisfies product_matches_value(product, slot, value).
5. Reject candidates with fewer than protected_rescue_min_slot_matches slots.
6. Reuse BM25Retriever's shared price and excluded-term filtering; never restore a
   candidate removed by those filters.
7. Fetch Broad-expression BM25 scores for all remaining IDs in one batched SQL
   operation. Strict candidates necessarily use positive lexical terms, so their
   Broad score should normally exist; reject a candidate if it has no Broad score.
8. Rebuild Candidate with the comparable Broad lexical score and source route
   ("bm25:protected_strict",).
9. Sort deterministically by:
     a. matched independent slot count descending;
     b. Strict rank ascending;
     c. Broad lexical score descending;
     d. parent_asin ascending.
10. Return at most protected_rescue_pool_size candidates.
```

The source route MUST be exactly `"bm25:protected_strict"`. Do not reuse
`"bm25:strict_and"`, because that tag belongs to the rejected append experiment
and would make diagnostics ambiguous.

### 6.4 Batched Broad scoring

Replace rescue-time per-candidate `_broad_score()` lookups with a batched helper in
`BM25Retriever`:

```python
def broad_scores_for_ids(
    self,
    plan: SearchPlan,
    parent_asins: tuple[str, ...],
) -> dict[str, float]:
    ...
```

Requirements:

- Empty input returns `{}` without SQL.
- IDs are de-duplicated and sorted before binding.
- The MATCH expression is exactly `_expression(plan)`.
- The BM25 column weights are exactly those used by `_retrieve_expression()`.
- Use bound parameters for the MATCH expression and all IDs.
- Do not construct ID literals in SQL.
- Return `max(0.0, -raw_score)` values.
- Never modify retrieval order or candidate objects.
- Keep the old `_broad_score()` method while rejected experiment code/tests still
  need it, or safely reimplement it through the batch helper without changing its
  output.

The intended query shape is:

```sql
SELECT parent_asin,
       bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0)
FROM products
WHERE products MATCH ?
  AND parent_asin IN (?, ?, ...)
```

The configured fetch limit is small enough to remain below SQLite parameter limits.

### 6.5 Expanded ranking

If the rescuer returns candidates:

```python
primary_ids = {item.parent_asin for item in primary_result.candidates}
rescue_candidates = tuple(
    item for item in rescue_candidates
    if item.parent_asin not in primary_ids
)
expanded_result = replace(
    primary_result,
    candidates=(*primary_result.candidates, *rescue_candidates),
)
expanded_plan = replace(
    search_plan,
    candidate_k=len(expanded_result.candidates),
)
expanded_ranked = components.ranker.rank(
    state,
    expanded_plan,
    expanded_result,
)
```

This second ranking is allowed to exceed the primary `candidate_k` only inside the
ephemeral response-selection path. It MUST NOT be passed to policy, coverage, or
stored as `state.previous_candidate_ids`.

Do not change the ranker formula. A rescue candidate's lexical score is its Broad
BM25 score, so the existing max normalization remains comparable. The existing
ranker recomputes constraint coverage and applies the negative final guard.

### 6.6 Protected response selection

Add a pure `ProtectedRescueSelector` in
`shopping_copilot/ranking/protected_rescue.py`.

Signature:

```python
class ProtectedRescueSelector:
    def __init__(self, policy_config: PolicyConfig) -> None:
        ...

    def select(
        self,
        base_ranked: Sequence[RankedCandidate],
        expanded_ranked: Sequence[RankedCandidate],
        rescue_ids: set[str],
        recommendation_count: int,
    ) -> list[RankedCandidate]:
        ...
```

V1 defaults:

```text
protected_rescue_head_size = 7
protected_rescue_quota = 3
protected_rescue_rank_limit = 10
```

Algorithm:

```python
limit = max(0, min(10, int(recommendation_count)))
if limit == 0:
    return list(base_ranked)

base_unique = first_unique_by_parent_asin(base_ranked)
expanded_unique = first_unique_by_parent_asin(expanded_ranked)
head_size = min(configured_head_size, limit, len(base_unique))
head = base_unique[:head_size]
head_ids = {item.parent_asin for item in head}

eligible_rescue_ids = []
for item in expanded_unique[:configured_rank_limit]:
    if item.parent_asin in rescue_ids and item.parent_asin not in head_ids:
        eligible_rescue_ids.append(item.parent_asin)
    if len(eligible_rescue_ids) >= min(configured_quota, limit - head_size):
        break

if not eligible_rescue_ids:
    return list(base_ranked)

allowed_ids = (
    {item.parent_asin for item in base_unique}
    | set(eligible_rescue_ids)
)
selected = list(head)
selected_ids = set(head_ids)

for item in expanded_unique:
    if item.parent_asin not in allowed_ids:
        continue
    if item.parent_asin in rescue_ids and item.parent_asin not in eligible_rescue_ids:
        continue
    if item.parent_asin in selected_ids:
        continue
    selected.append(item)
    selected_ids.add(item.parent_asin)
    if len(selected) >= limit:
        break

for item in base_unique:
    if len(selected) >= limit:
        break
    if item.parent_asin not in selected_ids:
        selected.append(item)
        selected_ids.add(item.parent_asin)

remaining_base = [
    item for item in base_unique
    if item.parent_asin not in selected_ids
]
return selected + remaining_base
```

The selector returns the selected visible prefix followed by unused baseline
candidates. This lets `OfficialResponseBuilder` enforce the requested count while
preserving enough candidates for existing call sites that inspect the sequence.

Required properties:

- The first `head_size` results equal the baseline IDs in the same order.
- No more than `protected_rescue_quota` rescue IDs occur in the first `limit`.
- A rescue candidate outside expanded `protected_rescue_rank_limit` cannot enter.
- No duplicates occur.
- With no eligible rescue, the returned sequence equals `list(base_ranked)`.
- Input sequences are not mutated.

### 6.7 Pipeline placement

Apply the selector after the existing bounded-prefix and coverage response logic,
but execute it only when `state.coverage_mode` is false.

Normative order:

```text
state update
-> plan
-> probe
-> early policy
-> primary retrieval
-> primary ranking
-> coverage observe(primary)
-> final policy(primary)
-> existing bounded response ranking
-> existing coverage response ordering
-> protected rescue attempt, expanded ranking, protected selection
-> mark asked
-> trace
-> OfficialResponseBuilder
-> coverage record_response(actual response)
```

The rescue selector MUST NOT run before the existing coverage block. It MUST NOT
run during coverage mode in V1.

### 6.8 Failure and fallback behavior

| Condition | Required behavior |
| --- | --- |
| Feature disabled | Exact baseline path; no Strict query |
| No Strict expression | Exact baseline response |
| Strict query returns zero | Exact baseline response |
| All Strict results already primary | Exact baseline response |
| Fewer than two matched slots | Reject candidate |
| Missing Broad score | Reject candidate |
| Price or negative violation | Candidate must already be filtered; reject defensively |
| No rescue in expanded Top 10 | Exact baseline response |
| Recommendation count <= protected head | Exact baseline response |
| Coverage mode active | Exact current coverage behavior |
| SQLite query syntax error | Fail the experiment visibly in tests; do not silently weaken constraints |

Do not catch broad `Exception`. Existing Agent/evaluator error handling remains the
outer safety boundary.

## 7. Interfaces and configuration

### 7.1 New protocol

Add to `shopping_copilot/core/interfaces.py`:

```python
class CandidateRescuer(Protocol):
    def retrieve(
        self,
        plan: SearchPlan,
        primary: RetrievalResult,
    ) -> tuple[Candidate, ...]: ...
```

Import `Candidate` in that file. Do not add a method to the existing `Retriever`
protocol.

### 7.2 Components

Extend `Components` in `shopping_copilot/core/factory.py` with:

```python
candidate_rescuer: CandidateRescuer
rescue_selector: ProtectedRescueSelector
```

Construct:

```python
lexical = BM25Retriever(...)
candidate_rescuer = StrictAndCandidateRescuer(
    lexical=lexical,
    store=store,
    config=config.search,
)
rescue_selector = ProtectedRescueSelector(config.policy)
```

The rescuer MUST reuse the same `BM25Retriever` instance and SQLite index. Do not
build a second catalog or FTS index.

### 7.3 Search configuration

Add fields to `SearchConfig`:

```python
protected_rescue_enabled: bool
protected_rescue_fetch_k: int
protected_rescue_pool_size: int
protected_rescue_min_slot_matches: int
```

Defaults and validation:

```text
protected_rescue_enabled:          false
protected_rescue_fetch_k:          60, clamp 10..200
protected_rescue_pool_size:        10, clamp 1..20
protected_rescue_min_slot_matches: 2, clamp 2..8
```

Do not repurpose `strict_rescue_enabled`. It belongs to a rejected experiment that
must remain reproducible.

### 7.4 Policy configuration

Add fields to `PolicyConfig`:

```python
protected_rescue_head_size: int
protected_rescue_quota: int
protected_rescue_rank_limit: int
```

Defaults and validation:

```text
protected_rescue_head_size:  7, clamp 0..9
protected_rescue_quota:      3, clamp 1..10
protected_rescue_rank_limit: 10, clamp 1..60
```

After parsing, reject invalid combinations:

```python
if result.policy.protected_rescue_head_size + result.policy.protected_rescue_quota > 10:
    raise ValueError("Protected rescue head size plus quota must not exceed 10")

if result.search.protected_rescue_enabled and result.search.strict_rescue_enabled:
    raise ValueError("Protected rescue and legacy Strict append are mutually exclusive")

if (
    result.search.protected_rescue_enabled
    and result.policy.full_rerank_clarification_response
):
    raise ValueError("Protected rescue requires bounded clarification reranking")
```

### 7.5 Config files

Add explicit safe default to `configs/final.json`:

```json
"protected_rescue_enabled": false
```

Do not change any other selected-final value.

Create `configs/experiments/protected_candidate_rescue.json` as a full copy of the
current final config with only these experiment changes:

```json
{
  "search": {
    "strict_rescue_enabled": false,
    "protected_rescue_enabled": true,
    "protected_rescue_fetch_k": 60,
    "protected_rescue_pool_size": 10,
    "protected_rescue_min_slot_matches": 2
  },
  "policy": {
    "full_rerank_clarification_response": false,
    "protected_rescue_head_size": 7,
    "protected_rescue_quota": 3,
    "protected_rescue_rank_limit": 10
  }
}
```

The real file must contain the complete configuration, not a partial JSON fragment.

Do not grid-search these values on the public set. Any change to one constant is a
new named experiment with its own result.

## 8. File-level implementation plan

### 8.1 Add

#### `shopping_copilot/retrieval/protected_rescue.py`

Implement `StrictAndCandidateRescuer`:

- constructor with shared `BM25Retriever`, `CatalogStore`, and `SearchConfig`;
- `retrieve(plan, primary)` method;
- distinct-slot evidence counting with `product_matches_value()`;
- deterministic ordering;
- no target or label access.

#### `shopping_copilot/ranking/protected_rescue.py`

Implement `ProtectedRescueSelector` as a pure deterministic selector. It must not
load the catalog, mutate state, choose questions, or format responses.

#### `tests/test_protected_rescue.py`

Add unit, metamorphic, selector, and integration tests listed in section 10.

#### `configs/experiments/protected_candidate_rescue.json`

Complete experiment configuration derived from current final.

#### `experiments/protected_candidate_rescue_v1.md`

Create only after the official public evaluation. Record commit/base, config,
commands, tests, metrics, scenario metrics, latency, changed sessions, and the
promotion decision.

### 8.2 Modify

#### `shopping_copilot/retrieval/bm25.py`

- add `broad_scores_for_ids()`;
- retain existing Broad and Strict route behavior;
- do not change `retrieve()` when both rescue flags are false;
- preserve legacy `strict_rescue_enabled` behavior for reproduction.

#### `shopping_copilot/core/config.py`

- add and validate the seven new configuration values;
- add mutual-exclusion validation;
- preserve defaults for every existing config.

#### `shopping_copilot/core/interfaces.py`

- add only `CandidateRescuer`;
- do not change existing protocol signatures.

#### `shopping_copilot/core/factory.py`

- add the rescuer and selector to `Components`;
- reuse the existing lexical BM25 instance;
- keep the existing hybrid topology unchanged.

#### `shopping_copilot/core/pipeline.py`

- name the current path's values as primary values;
- preserve current policy/coverage inputs;
- call rescue only through the eligibility gate;
- create an ephemeral expanded ranking;
- call the selector after current response-order logic;
- add trace fields from section 9.

#### `configs/final.json`

- add only `"protected_rescue_enabled": false` under `search`;
- do not change the current baseline settings.

#### `docs/team/architecture.md`

- after the experiment is stable, document the optional response-only rescue lane;
- explicitly state that policy and coverage use the primary ranking.

### 8.3 Do not modify

- `evaluator/`
- `data/public_set.jsonl`
- `data/catalog.jsonl`
- `starter/agent.py`
- `shopping_copilot/state/`
- `shopping_copilot/planning/query_planner.py`
- `shopping_copilot/policy/information_gain.py`
- `shopping_copilot/policy/coverage.py`
- `shopping_copilot/ranking/heuristic.py`
- `shopping_copilot/response/builder.py`
- `shopping_copilot/core/contracts.py`
- the official Agent API contract

If implementation appears to require modifying one of these paths, stop and explain
the blocker before proceeding.

## 9. Observability

When trace is enabled, add ground-truth-free fields to the existing turn event:

```json
{
  "protected_rescue_attempted": true,
  "protected_rescue_returned_count": 7,
  "protected_rescue_expanded_top10_count": 1,
  "protected_rescue_inserted_count": 1,
  "protected_rescue_inserted_ids": ["..."],
  "protected_rescue_head_size": 7,
  "protected_rescue_quota": 3
}
```

Requirements:

- When disabled, `protected_rescue_attempted` is `false` and counts are zero.
- Do not record target ID, target rank, hit status, scenario label, or ground truth.
- Inserted IDs are allowed because they are runtime recommendations, but tracing
  remains disabled in final config.
- Do not write a second trace file or change `TraceSink`.

Offline analysis MAY join trace IDs with public labels only after evaluation, in a
script outside Agent runtime.

## 10. Test specification

### 10.1 Unit tests for batched Broad scores

Add tests proving:

1. Empty IDs return `{}`.
2. Duplicate IDs are scored once.
3. Unknown IDs are omitted.
4. Batched scores equal the existing single-ID score for the same plan.
5. Result is independent of input ID order.
6. Bound parameters safely handle punctuation-like IDs.

### 10.2 Rescuer tests

Use a synthetic catalog with neutral IDs and invented values. Do not reuse a public
title, ASIN, or full public constraint phrase.

Required cases:

1. At least 100 distractors match only a common category term.
2. At least 80 distractors match only a material term.
3. One target-like synthetic product matches category and material but lies below
   the primary Broad cutoff.
4. Strict retrieval recovers it.
5. A primary candidate is never returned again as rescue.
6. A one-slot candidate is rejected.
7. Same-slot multiple values count as one independent slot.
8. Two different slots count as two.
9. Explicit negative matches are never returned.
10. Price filtering is preserved.
11. Rescue output is capped by pool size.
12. Catalog row permutation produces identical rescue IDs and order.
13. The source route is exactly `bm25:protected_strict`.
14. Rescue candidates carry comparable Broad lexical scores.

### 10.3 Selector tests

Required pure tests:

1. Disabled/no eligible input is an exact no-op.
2. The protected head remains identical and ordered.
3. No more than the quota enters the visible prefix.
4. Rescue outside expanded rank limit is rejected.
5. Rescue inside rank limit can enter the tail.
6. Duplicate IDs are removed deterministically.
7. A short base list is filled safely.
8. Recommendation counts 0, 1, 7, 8, 9, and 10 are handled.
9. Inputs are not mutated.
10. Equal-score ordering remains deterministic because expanded ranking order is
    authoritative.

### 10.4 Pipeline integration tests

Required cases:

1. With the flag false, response equals the clean baseline response.
2. Rescue is not queried when `early_decision is None`.
3. Rescue is not queried during coverage mode.
4. Policy receives primary ranking only.
5. Coverage observes primary ranking only.
6. A qualified synthetic rescue candidate can enter positions 8–10.
7. Primary positions 1–7 remain unchanged.
8. A rescue candidate cannot enter when full rerank is enabled because config
   validation rejects that combination.
9. Legacy Strict append experiment remains reproducible.
10. Agent response still satisfies the official contract.

### 10.5 Existing regression suites

All existing tests MUST remain green, especially:

```bash
python -m unittest tests.test_semantic_state -v
python -m unittest tests.test_negative_constraints -v
python -m unittest tests.test_strict_and_diagnostics -v
python -m unittest tests.test_team_pipeline -v
python -m unittest tests.test_evaluator -v
python -m unittest discover -s tests -p "test*.py" -v
```

Expected count after adding the new tests is greater than 59; do not hard-code the
new count in code.

## 11. Implementation phases

### R0 — Freeze baseline

Actions:

1. Record branch, HEAD, and status.
2. Run all 59 current tests.
3. Run `configs/final.json` evaluation and retain JSON outside the repository.
4. Measure per-turn latency with the current trace or a read-only benchmark.
5. Confirm `public_0020` is still the only miss.

Stop if the baseline differs materially from section 3. Do not rewrite this plan to
hide an unexplained difference.

### R1 — Pure rescue retrieval

Actions:

1. Add config parsing with defaults disabled.
2. Add batched Broad scoring.
3. Add `CandidateRescuer` protocol.
4. Implement `StrictAndCandidateRescuer`.
5. Add unit and metamorphic tests.

Do not integrate with the pipeline in R1.

R1 completion gate:

- new retrieval tests pass;
- all negative-constraint tests pass;
- flag-off BM25 output equals baseline;
- no existing config breaks.

### R2 — Pure protected selector

Actions:

1. Implement `ProtectedRescueSelector`.
2. Add exhaustive pure selector tests.
3. Add configuration combination validation.

Do not integrate with the pipeline in R2.

R2 completion gate:

- protected head invariant passes;
- no-rescue exact no-op passes;
- quota/rank-limit/deduplication tests pass.

### R3 — Pipeline integration

Actions:

1. Extend `Components` and factory.
2. Refactor names to primary values without behavior change.
3. Add the eligibility gate.
4. Rank the ephemeral union.
5. Apply selector after current response-order logic.
6. Add trace fields.
7. Add integration tests.

R3 completion gate:

- full unit suite passes;
- final config flag-off evaluation is exactly equal at aggregate and session level;
- experiment config changes only eligible early-clarification turns;
- no policy question changes are caused by rescue candidates.

### R4 — Public evaluation and decision

Run the clean baseline and experiment from the same code revision:

```bash
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output /tmp/protected-rescue-baseline.json

python -m scripts.run_evaluation \
  --config configs/experiments/protected_candidate_rescue.json \
  --output /tmp/protected-rescue-v1.json

python -m scripts.compare_runs \
  /tmp/protected-rescue-baseline.json \
  /tmp/protected-rescue-v1.json
```

Also report:

- changed-session count;
- reciprocal-rank gains and losses;
- earlier and later hit counts;
- inserted-rescue count by scenario;
- per-turn and per-session p50/p95 latency;
- target presence at primary retrieval, rescue retrieval, expanded Top 10, and final
  response, joined offline only.

Write `experiments/protected_candidate_rescue_v1.md` with a clear `promote` or
`reject` conclusion.

### R5 — Promotion, only if separately authorized

Promotion MUST be a separate change from algorithm implementation.

If every gate passes, the only behavioral promotion change should be:

```json
"protected_rescue_enabled": true
```

Do not promote automatically. Do not delete the experiment config after promotion.

## 12. Acceptance gates

### 12.1 Correctness

- All tests pass.
- `git diff --check` passes.
- Final config with the flag off is session-by-session identical to its baseline.
- Zero explicit negative-constraint violations.
- Zero duplicate or invalid recommendation IDs.
- Primary head preservation holds on every rescue-attempt turn.
- Policy questions and coverage activation match the clean baseline.
- `evaluator/`, catalog, and public labels have no diff.

### 12.2 Promotion metrics

All hard gates must pass:

| Metric | Required experiment result |
| --- | ---: |
| Hit Rate@10 | 1.000000 |
| MRR | >= 0.778343 |
| MTTC | <= 3.335000 |
| TechnicalScore | > 0.884303 |
| Boundary new misses | 0 |
| Browsing new misses | 0 |
| Buying new misses | 0 |
| Intent Override new misses | 0 |
| Negative violations | 0 |

Recovering `public_0020` is necessary for the Hit Rate gate but is not sufficient
for promotion.

### 12.3 Latency

R0 must establish a clean p50/p95 baseline using the same machine and process.

Required:

- flag-off latency remains within measurement noise;
- experiment p95 response latency is no more than 1.25 times the clean p95;
- FTS index construction time and memory do not materially increase because the
  same BM25 index is reused;
- no network or model latency exists.

If p95 fails, optimize query batching and avoid unnecessary rescue attempts. Do not
relax negative constraints or increase the visible rescue quota.

## 13. Decision rules after evaluation

### Promote candidate

Recommend promotion only if every correctness, metric, and latency gate passes and
the improvement is caused by general route evidence rather than a session-specific
exception.

### Keep experiment only

Keep disabled if candidate recall improves but any of these occur:

- Hit Rate remains 0.995;
- MRR falls below baseline;
- TechnicalScore does not improve;
- the candidate is recovered but remains outside the protected tail;
- too many sessions change for a one-session recall gain;
- latency exceeds the gate.

### Reject and revert implementation

Reject if:

- explicit negative constraints are violated;
- policy or coverage behavior changes when the feature is disabled;
- the primary head cannot be preserved;
- config combinations are ambiguous;
- implementation requires target-specific rules;
- evaluator or public data changes are needed.

## 14. Rollback and compatibility

Rollback is setting:

```json
"protected_rescue_enabled": false
```

With that value:

- no Strict rescue SQL executes;
- no expanded reranking executes;
- no selector executes;
- output must equal the clean baseline;
- legacy experiment flags retain their original meaning.

Do not remove these existing fields or experimental behaviors:

- `strict_rescue_enabled`;
- `full_rerank_clarification_response`;
- `adaptive_semantic_slate`;
- `semantic_priority_mode`.

They are required for reproducibility of rejected experiments.

## 15. Code-quality requirements

- Use standard-library Python only.
- Add type annotations to public and non-trivial private methods.
- Prefer pure helpers for qualification, ordering, and selection.
- Avoid target-specific constants and string dictionaries.
- Use `normalized_tokens()` and `product_matches_value()` rather than new tokenizers.
- Use parameterized SQLite queries.
- Do not use sets as an ordering source.
- Keep comments focused on invariants and non-obvious tradeoffs.
- Do not duplicate the ranker formula inside retrieval or selection.
- Do not add broad exception handling.
- Keep config defaults safe and backward compatible.
- Preserve unrelated user changes.

## 16. Required implementation report from Luna Max

At completion, Luna Max must report:

1. Files added and modified.
2. Exact algorithm implemented.
3. Any deviation from this document and why.
4. Unit-test commands and results.
5. Flag-off equivalence result.
6. Public metrics for baseline and experiment.
7. Scenario deltas.
8. Changed-session analysis.
9. Latency comparison.
10. Promotion recommendation.
11. Git status.

It must not claim success merely because `public_0020` becomes a hit.

## 17. Definition of Done

- [ ] Work started from `dev@efc5250` or a verified descendant.
- [ ] Baseline status, tests, metrics, and latency recorded.
- [ ] Seven new config values parsed with safe defaults and validation.
- [ ] Legacy Strict append and full-pool experiments remain reproducible.
- [ ] `CandidateRescuer` protocol added without changing `Retriever`.
- [ ] Existing BM25 instance is reused.
- [ ] Batched Broad scoring implemented with bound parameters.
- [ ] Strict-only rescue candidates require at least two independent slots.
- [ ] Price and negative filters remain mandatory.
- [ ] Rescue candidates carry comparable Broad lexical scores.
- [ ] Rescue pool size and ordering are deterministic.
- [ ] Primary retrieval/ranking remain the only policy and coverage inputs.
- [ ] Expanded ranking is ephemeral and response-only.
- [ ] Protected head is unchanged.
- [ ] Rescue quota and expanded-rank limit are enforced.
- [ ] No eligible rescue produces an exact no-op.
- [ ] Rescue is early-clarification-only and disabled during coverage.
- [ ] Ground-truth-free trace fields implemented.
- [ ] Synthetic/metamorphic/unit/integration tests pass.
- [ ] All existing tests pass.
- [ ] Flag-off evaluation is identical.
- [ ] Baseline and experiment evaluated from the same revision.
- [ ] All promotion gates evaluated explicitly.
- [ ] Experiment report records promote/keep-disabled/reject.
- [ ] `git diff --check` passes.
- [ ] No evaluator, catalog, public-label, Agent-contract, state, planner, ranker-formula,
      coverage, or response-builder changes.
- [ ] No commit or push performed unless separately requested.

## 18. Final instruction to Luna Max

Implement only Protected Candidate Rescue V1 as specified. Do not implement the old
multi-query RRF roadmap, dense retrieval, an LLM, a learned ranker, or unrestricted
full-pool reranking. Preserve the clean baseline behind a default-off flag. Treat
TechnicalScore, MRR, scenario stability, and latency as hard constraints. Stop and
report rather than silently changing architecture if a required invariant cannot be
met.
