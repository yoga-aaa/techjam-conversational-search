# E6-R5C MATCH-count then Global-IDF hierarchy v1

## Reproducibility

- Base experiment: `E6-R5@e79b837`
- Implementation: `5ce76dc`
- Branch: `codex/e6-r5c-match-count-global-idf-hierarchy`
- Config: `configs/experiments/e6_r5c_match_count_then_global_idf_then_rerank.json`
- Tests: 51 passed
- Public result: `/private/tmp/E6_R5c_MatchCountThenGlobalIDFThenRerank_Public200.json`
- Synthetic development result:
  `/private/tmp/E6_R5c_MatchCountThenGlobalIDFThenRerank_SyntheticDev662.json`

Retrieval, Strict rescue quota 10, explicit-negative hard guards, Coverage
activation, and final no-question Top10 behavior are unchanged.  The experiment
does not use session labels or target ASINs.

## Ranking rule

E5-B used `(Global IDF coverage, legacy rerank score)`.  This allowed one rare
MATCH to outrank several ordinary MATCHes.

E5-C uses the lexicographic key:

1. semantic MATCH count, descending;
2. Global IDF matched-constraint coverage, descending;
3. existing rerank score, descending.

The adaptive slate and Coverage semantic-group prioritizer now support a
`strongest_semantic_tier` anchor.  That anchor uses the same MATCH-count and
Global-IDF hierarchy instead of assuming the legacy rank-one item is the
strongest semantic candidate.

Legacy configs remain compatible.  E5-A resolves to `legacy`, E5-B resolves to
`global_idf_first`, and E5-C explicitly selects
`match_count_then_global_idf`.

## Public 200

- E5-B: Hit 1.000; MRR 0.626020; MTTC 3.130; score 0.845206
- E5-C: Hit 1.000; MRR 0.626020; MTTC 3.130; score 0.845206

All 200 per-session results are identical.  The public set does not contain a
case where E5-B's IDF-first ordering lets a lower-MATCH candidate determine the
reported hit.  `public_0020` remains a turn-1 rank-8 hit.

## Synthetic development 662

- E5-B: Hit 0.956193; MRR 0.600082; MTTC 3.560423; score 0.806913
- E5-C: Hit 0.956193; MRR 0.600837; MTTC 3.560423; score 0.807139

Only `synthetic_v2_0473` changes.  It remains a turn-9 hit, while the target
improves from rank 2 to rank 1.

At the hit turn, E5-B ranked a non-target first with four MATCHes and IDF
coverage 0.5579.  The target had five MATCHes and IDF coverage 0.5546.  E5-C's
MATCH-count-first hierarchy correctly places the five-MATCH target first.  No
session hits are gained or lost and no hit turn changes.

## Decision

E5-C is strictly safer than E5-B and should replace E5-B whenever Global IDF is
tested: it is identical on public 200, improves one synthetic-development rank,
and prevents a lower-MATCH candidate from winning solely because of a tiny IDF
difference.

Do not select the Global-IDF family as the final configuration yet.  Relative
to R4, E5-C still scores lower on public 200 and only slightly higher on
synthetic development.  It does not rescue any additional hits.  The remaining
problem is the interaction between early display and evaluator stopping: an
early rank-8/9 hit can replace a later rank-1 hit.  Additional weight tuning is
unlikely to solve that policy-level tradeoff.
