# E6-R5 Exact semantic signature and Global IDF v1

## Reproducibility

- Base experiment: `E6-R4@ca38245`
- Experiment implementation: `18decf0`
- Branch: `codex/e6-r5-exact-signature-global-idf`
- E5-A config: `configs/experiments/e6_r5a_exact_semantic_signature_slate.json`
- E5-B config: `configs/experiments/e6_r5b_exact_signature_global_idf_slate.json`
- Tests: 49 passed
- Public E5-A result: `/private/tmp/E6_R5a_ExactSemanticSignatureSlate_Public200.json`
- Public E5-B result: `/private/tmp/E6_R5b_ExactSignatureGlobalIDFSlate_Public200.json`
- Synthetic development R4 result: `/private/tmp/E6_R4_AdaptiveSemanticGroupSlate_SyntheticDev662.json`
- Synthetic development E5-A result: `/private/tmp/E6_R5a_ExactSemanticSignatureSlate_SyntheticDev662.json`
- Synthetic development E5-B result: `/private/tmp/E6_R5b_ExactSignatureGlobalIDFSlate_SyntheticDev662.json`

Strict rescue remains fixed at 10.  Explicit negative matches continue to be
removed by the existing shared guard.  Coverage remains enabled, and the final
no-question response remains Top10.  No public-session labels or target ASINs
are used by either experiment.

## Variants

E5-A replaces R4's coarse `(conflict count, MATCH count, UNKNOWN count)`
ambiguity signature with the exact ordered MATCH/UNKNOWN vector.  Candidates
that match the same number of constraints but match different constraints are
therefore no longer treated as semantically equivalent.  Existing rerank
scores and ordering are unchanged.

E5-B adds a startup-time Global IDF index over all 50,000 catalog products.
Category constraints use category-token document frequency, brand constraints
use store/title token frequency, and other constraints use full searchable-text
frequency.  The weighted matched-constraint coverage is placed before the
existing rerank score in the ordering key.  IDF contains no evaluation labels.

## Public 200 results

- R4 count signature: Hit 1.000; MRR 0.635853; MTTC 3.175; score 0.847256
- E5-A exact signature: Hit 1.000; MRR 0.641964; MTTC 3.270; score 0.847189
- E5-B exact plus Global IDF: Hit 1.000; MRR 0.626020; MTTC 3.130;
  score 0.845206

R4 to E5-A changes only three sessions.  All three improve reciprocal rank,
but all three hit later, so the score is effectively unchanged.  The exact
vector is a more faithful representation, but it is not a strong performance
lever on this set.

E5-A to E5-B changes 17 sessions: eight improve reciprocal rank and nine
worsen it; nine hit earlier and one hits later.  No hit is rescued or lost.
The IDF-first ordering therefore changes rank and stopping time rather than
candidate recall.

`public_0020` remains a turn-1 rank-8 hit in both variants.  Its target and many
other apparel candidates match the same disclosed category/material evidence,
so catalog rarity cannot distinguish the target inside that full-match group.

## Synthetic development 662 results

- R4 count signature: Hit 0.956193; MRR 0.593943; MTTC 3.533233;
  score 0.805615
- E5-A exact signature: Hit 0.956193; MRR 0.595175; MTTC 3.543807;
  score 0.805773
- E5-B exact plus Global IDF: Hit 0.956193; MRR 0.600082; MTTC 3.560423;
  score 0.806913

R4 to E5-A changes only two sessions.  E5-A to E5-B changes 61 sessions:
40 improve reciprocal rank, 16 worsen it, 15 hit earlier, and 12 hit later.
Again, no hit is rescued or lost.  The small positive synthetic-development
effect does not reproduce on public 200.

## Cost

On the local machine, loading the 50,000-product catalog took 0.407 seconds and
building the Global IDF counters took an additional 1.428 seconds.  The index
is built once when the Agent starts, not once per turn.  It contains 102,382
general tokens, 827 category tokens, and 41,872 brand/title tokens.

## Failure mechanism

IDF-first ordering can move a full or rare-evidence candidate into the visible
Top10 before the simulator has disclosed its most discriminating attribute.
The evaluator stops immediately after a hit.  A target that would have reached
rank 1 after a later clarification can therefore finish early at rank 8 or 9:
MTTC improves, but MRR falls.

E5-A has a separate anchoring weakness: its adaptive slate uses the signature
of the current rerank rank-one candidate.  If the existing weighted rerank puts
a partial-match candidate first, the policy can prioritize that candidate's
exact group ahead of a stronger full-match group.

## Decision

Do not select E5-A or E5-B as the final configuration yet.

Keep the exact-signature and full-catalog IDF capabilities as disabled
experiments.  E5-A is logically cleaner but nearly inert.  E5-B has a small
positive result on synthetic development and a negative result on public 200,
so its current primary-key use is not stable enough for hidden evaluation.

The next test should preserve semantic hierarchy explicitly: first prefer more
matched constraints, then use Global IDF only among candidates with the same
MATCH count, and finally use the existing rerank score.  The adaptive slate
must anchor to the strongest semantic tier rather than whichever item happens
to have the highest legacy final score.  This avoids allowing one rare match to
outrank several ordinary matches while retaining label-free catalog
selectivity.
