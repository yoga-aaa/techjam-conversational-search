# E6-R1 Broad primary + Strict AND rescue v1

## Reproducibility

- Base: `origin/dev@3672d1b`
- Experiment implementation: `6e2060b`
- Branch: `codex/e6-r1-broad-or60-strict-and-rescue10`
- Config: `configs/experiments/e6_r1_strict_rescue.json`
- Tests: 44 passed

No RRF score is used.  The original Broad OR pool is preserved and Strict AND
may append at most ten candidates.  A rescued candidate retains its Broad OR
BM25 score for downstream reranking.

## R1a route diagnostics

The first Strict query used AND between slot groups and OR within one slot.  It
rescued target candidates in 14 sessions, but did not rescue the only public
miss (`public_0020`).  That target matched the Strict expression but ranked
117--283 because the conditions still matched hundreds or thousands of items.

The promoted diagnostic added one catalog-derived category anchor to the same
Strict route.  The rarest category token is selected from all 50,000 products
and required in title/store; no category value is hard-coded.

- Active target turns: 651
- Strict-eligible turns: 557
- Non-empty Strict turns: 535
- Mean Strict-only rescue count: 7.4973
- Target rescue turns: 23
- Target rescue sessions: 5

For `public_0020`, Strict ranked the target at 20 on turn 1, 7 after `imported`,
and 4 after `grey`.  After removing candidates already in Broad60, the target
entered the ten rescue slots from turn 1.  Simulated full reranking placed it at
rank 8 on turn 1, then 28 and 63 as more broad attributes accumulated.

## R1b public 200 result

- Dev baseline: Hit 0.995; MRR 0.743752; MTTC 3.65; score 0.867626
- E6-R1b: Hit 0.995; MRR 0.739085; MTTC 3.64; score 0.866425
- Delta: Hit +0.000000; MRR -0.004667; MTTC -0.010000; score -0.001201

Only three sessions changed, all `intent_override`:

- `public_0002`: turn 7 rank 2 -> turn 7 rank 3
- `public_0003`: turn 9 rank 1 -> turn 8 rank 10
- `public_0198`: turn 9 rank 5 -> turn 8 rank 3

`public_0020` remained a miss even though it entered the full candidate pool.
During early clarification, the response path takes the original retrieval
prefix and reranks only that prefix; appended rescue candidates are therefore
not eligible for display.  The experiment also performs repeated per-candidate
Broad score lookups and is an unoptimized diagnostic implementation.

## Decision

Do not promote E6-R1b.  Do not tune the Strict quota or introduce RRF next.  The
candidate-recall hypothesis is validated for `public_0020`, but the immediate
blocker is the response-stage prefix truncation and the later rerank interaction.
The next isolated experiment should consume the already-computed full ranking
when generating clarification responses, with retrieval held fixed.
