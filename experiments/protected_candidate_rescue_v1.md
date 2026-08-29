# Protected Candidate Rescue V1 — R4 Evaluation Report

Decision: **keep-disabled**.

The rescue lane recovers the only baseline Hit@10 miss and improves TechnicalScore,
but it fails the hard MRR and latency gates. R5 promotion was not performed, and
`configs/final.json` remains protected-rescue disabled.

## Revision and configuration

- Required base: `dev@efc5250`
- Verified branch: `dev`
- Evaluation revision: the same uncommitted working-tree revision for both runs,
  rooted at `efc5250cec5bfd4478dd5f5376a7e35e6557e545`
- Clean config: `configs/final.json`
- Experiment config: `configs/experiments/protected_candidate_rescue.json`
- Experiment values: fetch `60`, rescue pool `10`, minimum independent slots `2`,
  protected head `7`, quota `3`, expanded rank limit `10`
- Both configs use zero runtime model tokens, dense retrieval disabled, structured
  retrieval disabled, and legacy Strict append disabled.

Commands:

```bash
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output /tmp/protected-rescue-baseline-final.json

python -m scripts.run_evaluation \
  --config configs/experiments/protected_candidate_rescue.json \
  --output /tmp/protected-rescue-v1.json

python -m scripts.compare_runs \
  /tmp/protected-rescue-baseline-final.json \
  /tmp/protected-rescue-v1.json
```

## Metrics

| Metric | Clean baseline | V1 experiment | Delta |
| --- | ---: | ---: | ---: |
| Sessions | 200 | 200 | 0 |
| Hit Rate@10 | 0.995000 | 1.000000 | +0.005000 |
| MRR | 0.778343 | 0.774079 | -0.004264 |
| MTTC | 3.335000 | 3.260000 | -0.075000 |
| Efficiency | 0.766500 | 0.774000 | +0.007500 |
| TechnicalScore | 0.884303 | 0.887024 | +0.002721 |
| Runtime model tokens | 0 | 0 | 0 |

## Scenario results

| Scenario | Baseline Hit/MRR/MTTC | Experiment Hit/MRR/MTTC | Delta Hit/MRR/MTTC |
| --- | --- | --- | --- |
| Boundary (10) | 1.000000 / 0.844444 / 4.200000 | 1.000000 / 0.844444 / 4.200000 | 0.000000 / 0.000000 / 0.000000 |
| Browsing (80) | 1.000000 / 0.811592 / 2.962500 | 1.000000 / 0.800655 / 2.950000 | 0.000000 / -0.010937 / -0.012500 |
| Buying (80) | 0.987500 / 0.734544 / 3.162500 | 1.000000 / 0.735933 / 3.037500 | +0.012500 / +0.001389 / -0.125000 |
| Intent Override (30) | 1.000000 / 0.784444 / 4.500000 | 1.000000 / 0.781481 / 4.366667 | 0.000000 / -0.002963 / -0.133333 |

Inserted rescue recommendations observed in the experiment trace:

| Scenario | Turns with insertion | Rescue items inserted |
| --- | ---: | ---: |
| Boundary | 4 | 6 |
| Browsing | 22 | 32 |
| Buying | 24 | 38 |
| Intent Override | 6 | 9 |

## Changed-session analysis

Three of 200 session summaries changed.

| Session | Scenario | Baseline | Experiment | Interpretation |
| --- | --- | --- | --- | --- |
| `public_0007` | Browsing | hit, turn 3, rank 1, RR 1.000000 | hit, turn 2, rank 8, RR 0.125000 | Earlier hit, but protected-tail exposure replaced the earlier rank-1 result. |
| `public_0020` | Buying | miss | hit, turn 1, rank 9, RR 0.111111 | The Strict-only candidate entered the protected tail and recovered the miss. |
| `public_0198` | Intent Override | hit, turn 9, rank 5, RR 0.200000 | hit, turn 5, rank 9, RR 0.111111 | Earlier hit, but with a worse rank. |

- Reciprocal-rank gains: `1` (Buying).
- Reciprocal-rank losses: `2` (Browsing `1`, Intent Override `1`).
- New hits: `1`; lost hits: `0`.
- Earlier hits: `2`; later hits: `0`.
- No scenario acquired a new miss.

## Offline target-stage analysis

These are session-level counts: target presence at any evaluator turn that was
actually executed. The analysis wrapped the runtime components after evaluation
and joined target labels offline; no labels or ground truth were passed to the
Agent, rescuer, policy, coverage manager, or trace event. The runtime trace itself
contains only the specified ground-truth-free rescue fields.

| Stage | Clean baseline | V1 experiment |
| --- | ---: | ---: |
| Primary retrieval | 199 | 197 |
| Rescue retrieval | 0 | 6 |
| Expanded ranked Top 10 | 0 | 134 |
| Final response | 199 | 200 |

The experiment's lower primary-retrieval session count reflects early evaluator
stopping after an earlier final-response hit; the primary ranking itself remains
the policy/coverage input. Expanded Top-10 presence includes targets that were
already in the primary pool as well as targets newly available through rescue.

An offline comparison of the 652 turns executed by both configurations found zero
policy-question mismatches and zero coverage-activation mismatches. The selector
was called 317 times in the experiment and recorded zero protected-head
violations.

## Latency

Measured on the same machine and in the same process style over the full public
set, with per-turn timing around `respond()` and per-session sums:

| Measurement | Clean baseline | V1 experiment | Experiment / clean |
| --- | ---: | ---: | ---: |
| Turns evaluated | 666 | 652 | — |
| Turn p50 (ms) | 54.923 | 82.495 | 1.502x |
| Turn p95 (ms) | 123.182 | 215.798 | 1.752x |
| Session p50 (ms) | 159.495 | 260.028 | 1.631x |
| Session p95 (ms) | 438.160 | 688.042 | 1.570x |

The required turn p95 ceiling is `153.978 ms` (`1.25 * 123.182`), so the
experiment exceeds it. The existing BM25 index is reused and no model/network
latency is introduced. A semantics-preserving fetch-headroom optimization was
also tested: when no price or exclusion filter can discard rows, BM25 fetches the
requested limit rather than a 4x overfetch. It preserved all outputs and tests but
did not bring p95 within the gate.

## Tests and gates

R0 baseline:

- `python -m unittest discover -s tests -p 'test*.py' -v`: 59/59 passed before edits.
- Baseline public evaluation matched the specified `0.995000` Hit@10,
  `0.778343` MRR, `3.335000` MTTC, and `0.884303` TechnicalScore.
- `public_0020` was the only baseline miss.

Phase gates:

- R1 retrieval/config/negative/legacy tests: passed; flag-off evaluation was
  byte-for-byte and session-for-session identical to the pre-edit baseline.
- R2 selector and configuration-combination tests: passed.
- R3 integration and full regression suite: passed.
- Final `python -m unittest discover -s tests -p 'test*.py' -v`: 78/78 passed.
- `git diff --check`: passed.
- All JSON configs load successfully.
- Legacy Strict append and full-rerank reproducibility tests: passed.

Post-review verification:

- Protected strict fetches use a secondary `parent_asin` ordering only for
  protected-rescue mode, so more-than-`fetch_k` equal-score cutoffs are
  independent of catalog insertion order. The regression test uses 75 tied
  candidates with `fetch_k=60` and verifies identical rescued IDs and order.
- Trace insertion IDs/count now use the actual visible limit
  `min(top_k, recommendation_count, 10)`. The integration test uses `top_k=7`
  while the protected tail begins at position 8 and verifies zero visible
  insertions.
- Clean, R4, and legacy evaluation artifacts generated after these fixes were
  byte-for-byte identical to their pre-fix artifacts, including their session
  records. The clean flag remains disabled in `configs/final.json`.
- The existing public metrics remain valid: the deterministic tie-break is
  restricted to the opt-in protected-rescue strict fetch, and the trace-only
  visibility correction cannot change the public evaluator's `top_k=10`
  recommendations. No evaluated response or session result changed.

Promotion gates:

| Gate | Result |
| --- | --- |
| Hit Rate@10 = 1.000000 | Pass |
| MRR >= 0.778343 | **Fail** (`0.774079`) |
| MTTC <= 3.335000 | Pass (`3.260000`) |
| TechnicalScore > 0.884303 | Pass (`0.887024`) |
| Boundary new misses = 0 | Pass |
| Browsing new misses = 0 | Pass |
| Buying new misses = 0 | Pass |
| Intent Override new misses = 0 | Pass |
| Negative violations = 0 | Pass (retrieval/ranker guards and tests) |
| Turn p95 <= 1.25x clean | **Fail** (`1.752x`) |
| Flag-off equivalence | Pass |
| Primary head preserved on every rescue selection | Pass (317/317) |
| Policy questions/coverage activation unchanged on common turns | Pass (0/652 mismatches) |

## Recommendation

Keep the experiment disabled. It satisfies the retrieval, filtering, bounded
selector, primary-head, policy/coverage isolation, rollback, and correctness
requirements, but the public decision rules reject promotion because MRR declines
and latency exceeds the hard p95 ceiling. No target-specific rule was added, no
official evaluator or catalog data was modified, and no R5 promotion change was
made.
