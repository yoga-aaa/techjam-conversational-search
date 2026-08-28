# E6-R4 Adaptive semantic-equivalence slate v1

## Reproducibility

- Base experiment: `E6-R2a@5ab76df`
- Experiment implementation: `89a8c4e`
- Branch: `codex/e6-r4-adaptive-semantic-slate`
- Config: `configs/experiments/e6_r4_adaptive_semantic_group_slate.json`
- Tests: 47 passed
- Result: `/private/tmp/E6_R4_AdaptiveSemanticGroupSlate_Public200.json`

The current dev semantic state layer is deterministic rather than embedding- or
LLM-based.  Its 25 focused semantic-state and negative-constraint tests pass.
Explicit negative values continue through the existing canonical negative state
and shared retrieval/ranker guards.  This experiment does not infer additional
hard conflicts from absent catalog text.

For every positive non-budget constraint, the ranker records a conservative
MATCH or UNKNOWN count.  While the policy is still asking a question, it counts
all candidates with the same semantic count signature as rank one and returns:

`min(10, semantic_equivalence_group_size)`

When Coverage is active, candidates from the rank-one semantic group are kept
ahead of weaker signatures.  Retrieval, Strict rescue quota, rerank weights,
and final no-question Top10 behavior are unchanged.

## Public 200 result

- Dev baseline: Hit 0.995; MRR 0.743752; MTTC 3.650; score 0.867626
- Fixed Top10/R2a: Hit 1.000; MRR 0.627153; MTTC 3.145; score 0.845246
- Adaptive semantic group: Hit 1.000; MRR 0.635853; MTTC 3.175;
  score 0.847256

Relative to fixed Top10, only six sessions change.  Five gain reciprocal rank
and none lose reciprocal rank, but one conversion is delayed by four turns.
The total score improves by only 0.002010 and remains 0.020370 below dev.

`public_0020` is rescued at turn 1 rank 8, the same as fixed Top10.  Its target
has full positive-constraint coverage, but many catalog products share the same
generic apparel template attributes, so it belongs to a semantic equivalence
group larger than the API cap.

Across 612 clarification decisions, selected slate sizes are:

- size 0: 7
- size 1: 74
- size 2: 9
- size 3: 9
- size 4: 18
- size 5: 12
- size 6: 4
- size 7: 6
- size 8: 11
- size 9: 9
- size 10: 453

Approximately 74% of clarification decisions therefore return Top10.  Simple
MATCH/UNKNOWN count equality is too coarse for this catalog and almost always
classifies template-heavy clothing products as ambiguous.

## Decision

Reject E6-R4.  It is contract-valid and solves the known missing case, but it
mostly degenerates into fixed Top10 and repeats R2a's MRR problem.  Do not make
negative interpretation more aggressive to force smaller groups.  A future
ambiguity detector would need field-aware, catalog-selectivity evidence or a
separately validated score calibration; otherwise the simpler Top3 policy is
the safer product-facing alternative.
