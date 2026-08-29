# E6-R2a Full rerank response v1

## Reproducibility

- Base experiment: `E6-R1@ce9e815`
- Experiment implementation: `0543b1f`
- Branch: `codex/e6-r2a-full-rerank-response`
- Config: `configs/experiments/e6_r2a_full_rerank_response.json`
- Tests: 45 passed
- Result: `/private/tmp/E6_R2a_FullRerankResponse_Public200.json`

Retrieval is unchanged from R1: the Broad pool is preserved and Strict AND may
append at most ten rescue candidates.  This experiment changes only response
selection during clarification.  Instead of reranking the original retrieval
prefix again, it passes the already-computed full ranking to the response
builder.

## Public 200 result

- Dev baseline: Hit 0.995; MRR 0.743752; MTTC 3.65; score 0.867626
- E6-R2a: Hit 1.000; MRR 0.627153; MTTC 3.145; score 0.845246
- Delta: Hit +0.005000; MRR -0.116599; MTTC -0.505000; score -0.022380

`public_0020` changed from a miss to a turn-1 rank-8 hit, confirming that the
R1 Strict rescue candidate can now reach the output.  Across all sessions, 89
results changed: 16 reciprocal-rank gains, 60 losses, and 66 earlier hits.
Many sessions now stop one or more turns earlier with the target near ranks
4--10, whereas the baseline would have placed the same target at rank 1 after
one more clarification.  The earlier hit improves MTTC but sharply reduces MRR.

## Decision

Reject R2a as a final configuration.  It fixes the response-stage truncation
and reaches 100% public recall, but unrestricted early exposure of candidates
outside the former response prefix causes a material MRR regression.  The next
experiment should retain the full ranking while gating newly exposed candidates
by confidence and stability.
