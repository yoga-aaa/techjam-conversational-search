# E27 Production Integration

This document records the clean promotion of the tested E27 implementation from
`dev` commit `479c067` into the stable production line. It is intentionally a
short integration record rather than a dump of every intermediate experiment.

## Included production behavior

- Semantic state operations for incremental constraints, negation, removal,
  no-preference answers, and same-turn intent overrides.
- Query planning from the current state, with stale and excluded terms removed.
- Category-aware retrieval, optional lexical multi-query fan-out, and deterministic
  candidate fusion.
- Evidence-based reranking across the anchored category pool.
- Information-gain clarification, compact recommendation gating, and
  stagnation-aware candidate coverage.
- Shadow evaluation support and focused unit tests for each retained capability.

The official `starter.agent.Agent` interface remains unchanged. The production
entry point continues to load `configs/final.json` through the stable modular
pipeline.

## Deliberately excluded

- `test_package/` and its copied catalog, response banks, generated datasets, and
  portable evaluator assets.
- Configuration files for rejected or superseded experiments.
- Large per-run reports and duplicated evaluation outputs.
- Test-package-specific CI and ignore rules.

Tests that exercise optional algorithm switches construct those settings from the
final configuration in memory. This keeps code-path coverage without presenting a
rejected experiment as a supported production preset.

## Reproducible checks

The clean integration passes 75 standard-library unit tests:

```bash
python -m unittest discover -s tests -p "test*.py" -v
```

The source E27 build reported the following frozen results:

| Evaluation scope | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development 2,000 sessions | 0.9925 | 0.888941 | 2.5515 | 0.84485 | 0.931902 |
| Official public 200 sessions | 1.0000 | 0.953667 | 2.2150 | 0.87850 | 0.961800 |

These figures are local evidence, not a guarantee of performance on the private
set. The experiment scope is recorded explicitly in `experiments/registry.csv` so
the 2,000-session development results are not compared as though they came from
the public 200-session set.

## Source attribution

The promoted `dev` history contains production contributions from vepharix,
SHU ZIXUAN, goAustin, and Big Pig. The clean integration preserves the tested
behavior while avoiding the bulk artifacts carried by the full development
branch.
