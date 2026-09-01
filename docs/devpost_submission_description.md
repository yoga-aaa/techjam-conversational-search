# Devpost Written Submission

## Project title

Evidence-Aware Conversational Shopping Copilot

## Short description

A deterministic, offline shopping agent that remembers changing customer
requirements, retrieves products from a 50,000-item catalog, asks useful follow-up
questions, and ranks the target product using explainable product evidence.

## Inspiration and problem

Shopping queries are rarely complete. A customer may begin with a broad request,
reveal material, budget, or use-case requirements over several turns, and later
change an earlier preference. A one-shot keyword search can retain stale terms,
retrieve the wrong category, or recommend too many weak matches. Our goal was to
build an agent that turns a changing conversation into a clean, current search
state and finds the correct product as early and as highly ranked as possible.

## What the project does

The agent implements the official `starter.agent.Agent` interface and processes
each turn through a stable modular pipeline:

1. A state tracker records positive, negative, removed, and overridden
   requirements.
2. A query planner rebuilds the search request from the current state, preventing
   superseded preferences from leaking into later turns.
3. SQLite FTS5/BM25 and a full-category anchor retrieve candidates from the frozen
   catalog.
4. An Evidence Ranker rewards specific and complete constraint matches, applies a
   soft budget preference, and retains route-level evidence for explanation.
5. A deterministic policy selects the next clarification question, controls the
   visible recommendation slate, and rotates bounded unseen candidates only when
   the result pool has genuinely stagnated.

This design supports Buying, Browsing, Intent Override, and Boundary scenarios
without reading public labels or ground truth at agent runtime.

## How we built it

The project uses Python 3.10+ and the Python standard library. SQLite FTS5/BM25
provides lexical retrieval; `dataclasses`, `json`, `pathlib`, `re`, `math`, and
`sqlite3` support the runtime; `unittest` provides regression coverage. Development
used Visual Studio Code, Git, GitHub, and Python command-line tools.

The final configuration is fully offline. It uses no OpenAI, Google, TikTok, or
other external API, requires no API key or network connection during inference, and
reports zero model tokens and zero external model cost. The only API-like contract
is the local Python interface supplied by the official evaluator.

## Dataset and assets

We use the official frozen 50,000-product `Clothing_Shoes_and_Jewelry` catalog and
the 200 released public development sessions. The catalog is derived from Amazon
Reviews 2023 by McAuley Lab at UCSD and is joined by `parent_asin`. The agent uses
text and structured product metadata only. It does not use product images, private
labels, private holdout sessions, credentials, or undisclosed external assets.

## Results and reproducibility

On the official 200-session public development set, the selected `main`/E27
configuration produced:

| Metric | Result |
| --- | ---: |
| Hit Rate@10 | 1.000000 |
| MRR | 0.953667 |
| MTTC | 2.215000 |
| Efficiency | 0.878500 |
| TechnicalScore | 0.961800 |
| Reported model tokens | 0 |

All 75 unit tests pass. A local full public-set reproduction using Python 3.12.10
completed in 57.981 seconds, including one-time catalog loading. Public development
results are not presented as a guarantee of private-set performance.

## Challenges and lessons learned

The hardest issue was not retrieving more products, but keeping the conversational
state correct. Intent overrides can leave stale words in a query, and strict budget
filters can remove a correct product after an ambiguous parse. We learned to treat
state changes as explicit operations, to separate category recall from evidence
ranking, and to validate each optimization through controlled experiments and
regression tests.

## Limitations and future work

The current state interpreter is primarily rule-based and lexical, so unseen
paraphrases, misspellings, multilingual input, and highly implicit requests remain
challenging. Ranking uses catalog metadata rather than images, reviews, inventory,
shipping, or live prices. The system also has no online learning from real users.

With more time, we would add a calibrated semantic parser with a deterministic
offline fallback, broader paraphrase and multilingual stress tests, richer product
signals, online user studies, and monitoring for latency, fairness, and drift.

## Team contributions

- **Zhe Gao and Bin Liu:** MVP architecture, end-to-end implementation, and
  performance optimization.
- **Runzhong Chen:** evaluation-data validation, data-quality checks, and
  construction of additional test sets.
- **Zixuan Shu and Yujia Zhao:** algorithm and system optimization, regression
  validation, and demo/video production.

## Public links

- Source code: https://github.com/yoga-aaa/techjam-conversational-search
- The public three-minute YouTube demo URL must be added to the Devpost video field
  after upload.
