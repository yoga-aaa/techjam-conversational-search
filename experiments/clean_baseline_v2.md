# Clean Baseline V2

## Decision

The selected `configs/final.json` baseline disables every rejected E6 behavior
that had remained active on `dev`:

- Strict-AND rescue candidates are disabled in live retrieval.
- Full-pool reranking for clarification responses is disabled; the response
  reranks the bounded original retrieval prefix.
- Adaptive semantic slates are disabled.
- Semantic priority ordering remains in legacy mode.

The rejected paths remain available only through explicit experiment configs:

- `configs/experiments/e6_r1_strict_rescue.json`
- `configs/experiments/e6_r2a_full_rerank_response.json`
- the existing E6 R4/R5 configs, which now opt into their dependencies
  explicitly.

Implementation commit: `7b08b1f` (`fix: isolate rejected retrieval experiments`).
The result below also includes `b91782c` (`feat: optimize clarification question order`).

## Verification

```bash
python3 -m unittest discover -s tests -p 'test*.py' -v
python3 -m scripts.run_evaluation \
  --config configs/final.json \
  --output /tmp/techjam-clean-baseline-b91782c.json
```

Unit tests: 59 passed.

## Official public-set result

| Metric | Clean baseline V2 |
| --- | ---: |
| Sessions | 200 |
| Hit Rate@10 | 0.995000 |
| MRR | 0.778343 |
| MTTC | 3.335000 |
| Efficiency | 0.766500 |
| Recommended Technical Score | 0.884303 |
| Runtime model tokens | 0 |

Scenario metrics:

| Scenario | Sessions | Hit Rate@10 | MRR | MTTC |
| --- | ---: | ---: | ---: | ---: |
| Boundary | 10 | 1.000000 | 0.844444 | 4.200000 |
| Browsing | 80 | 1.000000 | 0.811592 | 2.962500 |
| Buying | 80 | 0.987500 | 0.734544 | 3.162500 |
| Intent Override | 30 | 1.000000 | 0.784444 | 4.500000 |

## Comparison

Against the latest documented E6/override stack (`0.848467`):

- Hit Rate@10: `-0.005000`
- MRR: `+0.138454`
- MTTC: `+0.160000` turns
- Technical Score: `+0.035836`

Against the pre-E6 `dev` baseline (`0.867626`):

- Hit Rate@10: unchanged at `0.995000`
- MRR: `+0.034591`
- MTTC: `-0.315000` turns
- Technical Score: `+0.016677`

The rejected stack found nearly every target slightly earlier, but moved target
products much lower within the returned Top 10. Restoring the bounded response
prefix recovers enough reciprocal-rank quality to produce the best documented
technical score in this repository while retaining the latest state-hardening
changes.
