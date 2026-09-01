# TechJam Conversational E-Commerce Search Challenge

Build an AI shopping agent that asks useful follow-up questions and recommends the customer's hidden target product within at most 10 turns.

## Team Development Architecture

This fork keeps the official evaluator and data contract unchanged and places the
team implementation behind the required `starter.agent.Agent` entry point.

```text
official evaluator
  -> starter/agent.py
  -> stable pipeline
  -> state -> planning -> candidate search -> ranking -> policy
  -> official response
```

The default configuration is fully offline and uses rule-based conversation state,
SQLite FTS5/BM25 search plus a full-category candidate anchor, deterministic evidence
reranking, and a deterministic clarification policy. Dense retrieval and model-backed
implementations can be added behind the stable interfaces in
`shopping_copilot/core/interfaces.py` and selected through config without changing
the pipeline.

## Project Overview

The agent receives a short customer message and an anonymized preference profile,
then searches a frozen 50,000-product catalog for the hidden target product. It
maintains the requirements that are currently valid, removes preferences that the
customer explicitly changes, retrieves candidates from the correct product category,
and ranks them using observable evidence from product metadata. When the evidence is
not sufficient, it asks a focused follow-up question; when it is sufficient, it
returns a ranked recommendation slate.

The implementation is deliberately deterministic and offline. This makes the
submission reproducible under the competition's restricted final-evaluation
environment and makes each ranking decision inspectable.

## Development Tools, APIs, Libraries, and Data

### Development tools

- Visual Studio Code, with shared settings in `.vscode/`;
- Git and GitHub for version control, collaboration, and public source delivery;
- Python command-line tools for tests, data verification, and evaluation runs.

### APIs and external services

- **External APIs:** none. The final agent does not call OpenAI, Google, TikTok, or
  any other network service, and it does not require an API key or live credentials.
- **Official local interface:** the competition `starter.agent.Agent` contract,
  implemented by `reset(session_id, user_profile)` and
  `respond(session_id, user_message, turn, top_k)`. This is a local Python
  interface used by the official evaluator, not an external web API.
- **Network requirement:** none for inference or official scoring once the catalog
  has been downloaded.

### Libraries and frameworks

- Python 3.10 or later;
- Python standard library, including `dataclasses`, `json`, `pathlib`, `re`,
  `math`, `sqlite3`, and `unittest`;
- SQLite FTS5/BM25 for lexical candidate retrieval;
- No third-party machine-learning framework, vector database, hosted model, or
  external ranking service is required by the selected configuration.

### Dataset and assets

- The frozen 50,000-product `Clothing_Shoes_and_Jewelry` catalog;
- 200 labeled public development sessions in `data/public_set.jsonl`;
- Official evaluator and response contract in `evaluator/` and `docs/`;
- Catalog source: Amazon Reviews 2023, McAuley Lab, UCSD. See
  `DATA_ATTRIBUTION.md` for attribution and permitted-use notes;
- No product images, private labels, private holdout sessions, credentials, or
  other undisclosed assets are used by the agent.

Run the team tests without downloading the full catalog:

```bash
python -m unittest discover -s tests -p "test*.py" -v
```

After downloading and verifying the official catalog, run an explicit experiment:

```bash
python scripts/verify_data.py --catalog data/catalog.jsonl
python -m scripts.run_evaluation --config configs/final.json --output results.json
```

The current selected development configuration is `configs/final.json` (E27).
After the intent-override state and history fixes, the frozen 2,000-session
development set produced Hit Rate@10 `0.9925`, MRR `0.888941`, MTTC `2.5515`,
Efficiency `0.84485`, and recommended technical score `0.931902`.
On the 200-session public set it produced Hit Rate@10 `1.0`, MRR `0.953667`,
MTTC `2.215`, Efficiency `0.8785`, and technical score `0.9618`. These local
results are not a guarantee of private-set performance. The branch comparison,
holdout caveat, selected production scope, and controlled runs are documented in
`docs/e27_production_integration.md` and `experiments/registry.csv`.

See `docs/team/architecture.md` for module boundaries and
`docs/team/ownership.md` for the five-person ownership model.

## What You Receive

- A frozen catalog of 50,000 products from the `Clothing_Shoes_and_Jewelry` category of Amazon Reviews 2023.
- 200 labeled public sessions for local development.
- A weak BM25 starter agent and deterministic local evaluator.
- The Agent API contract and scoring rules.

The organizer keeps 800 additional sessions private for final evaluation.

## Task

For each session, your agent receives an anonymized preference profile and a short customer message. Raw user IDs, review text, timestamps, and purchase history are never disclosed. On every turn the agent may:

- ask a natural clarification question in `message` and identify one requested field in `ask_attribute`;
- return a ranked list of up to 10 catalog `parent_asin` values;
- do both in the same response.

The session ends when the target product appears in the scored Top 10 or after turn 10. Sessions cover Buying, Browsing, Intent Override, and Boundary behavior.

## Download the Catalog

Download `catalog.jsonl.gz` from the GitHub Release attached to this repository, then run:

```bash
gzip -dk catalog.jsonl.gz
mv catalog.jsonl data/catalog.jsonl
```

Verify the downloaded file using the published `SHA256SUMS` file.

## Run the Starter

Python 3.10 or later is recommended. The starter uses only the Python standard library.

```bash
python3 -m evaluator.local_evaluator
```

Edit `starter/agent.py` to implement your system. Do not edit the evaluator or public labels when reporting your local score.
The command writes per-session results and aggregate metrics to `results.json`.

The included weak BM25 starter scores Hit Rate@10 `0.125`, MRR `0.068034`, and
MTTC `9.81` on the released public set. See `docs/baseline_results.json`.

## Installation and Reproduction

### 1. Prepare Python

Install Python 3.10 or later, then create an isolated environment from the
repository root:

```bash
python -m venv .venv
```

Activate it as appropriate for your shell:

```bash
# Windows PowerShell
.venv\\Scripts\\Activate.ps1

# macOS or Linux
source .venv/bin/activate
```

This project has no required third-party runtime packages. The following command
is intentionally kept for reproducibility and will install the contents of the
dependency manifest if that policy changes:

```bash
python -m pip install -r requirements.txt
```

### 2. Obtain and verify the catalog

Download the official `catalog.jsonl.gz` release asset, extract it, and place the
result at `data/catalog.jsonl`. Verify it before running the agent:

```bash
python scripts/verify_data.py --catalog data/catalog.jsonl
```

The public sessions are already included at `data/public_set.jsonl`. Do not modify
the evaluator or public labels when reproducing a score.

### 3. Run tests and evaluation

Run the standard-library regression suite:

```bash
python -m unittest discover -s tests -p "test*.py" -v
```

Run the selected `main` configuration against the official public set:

```bash
python -m scripts.run_evaluation --config configs/final.json --output results.json
```

The output contains per-session results and aggregate Hit@10, MRR, MTTC,
Efficiency, and TechnicalScore metrics. The recorded `main`/E27 results are
development evidence only and are not a guarantee of private-set performance.

## Agent Interface

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        ...

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {
            "message": "Do you have a material preference?",
            "ask_attribute": "material",
            "recommendations": [
                {"parent_asin": "B000..."},
                {"parent_asin": "B001..."}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30}
        }
```

`ask_attribute` is one of `category`, `material`, `color`, `size`, `style`, `brand`, `budget`, `feature`, `use_case`, `other`, or `null`. See `docs/agent_api_contract.json`.

## Technical Metrics

- **Hit Rate@10:** fraction of sessions that find the target within 10 turns.
- **MRR:** mean reciprocal rank of the target; a miss contributes zero.
- **MTTC:** mean first-hit turn; a miss is assigned turn 11.
- **Reported token usage:** prompt and completion tokens returned by the team's model client.

```text
TechnicalScore = 0.50 × HitRate@10 + 0.30 × MRR + 0.20 × Efficiency
Efficiency = clip((11 - MTTC) / 10, 0, 1)
```

`TechnicalScore` is an objective input to the `Technical Execution` assessment.
It is not a separate judging criterion and does not represent the entire
`Technical Execution` score.

Only exact `parent_asin` equality produces a hit. Core metrics are also reported by scenario.

## Model Choice and Cost

This selected configuration uses no LLM or external model API. It runs locally with
zero model tokens and zero external API cost; reported model usage is therefore
zero. Runtime latency depends on the local machine and catalog storage. In a local
reproduction using Python 3.12.10 and the frozen 50,000-product catalog, the complete
200-session public evaluation finished in 57.981 seconds (approximately 0.290 seconds
per session, including one-time catalog loading). This is a hardware-dependent
reference, not a guaranteed service-level latency. The source tree contains no API
keys or credentials.

## Limitations and Future Work

The current implementation is a strong, reproducible competition baseline, but it
is not a complete production shopping system:

- State interpretation is primarily rule-based and lexical. Unseen paraphrases,
  spelling errors, multilingual input, and highly implicit preferences may not be
  interpreted as reliably as they would be by a validated semantic parser.
- Product evidence comes from catalog metadata only. The system does not use
  images, reviews, click history, inventory, shipping, or real-time price data.
- Budget is handled as a ranking preference/soft constraint to avoid discarding a
  correct result because of noisy parsing or borderline prices; complicated ranges,
  currency expressions, and ambiguous budget wording remain edge cases.
- The deterministic policy and weights were developed and checked on the released
  public data and controlled development experiments. Private-set performance may
  differ.
- There is no online learning or personalization from real user feedback.

With more time, we would add a calibrated semantic parser with an offline fallback,
broader paraphrase and multilingual stress tests, richer product-quality signals,
online user studies, and monitoring for ranking fairness, latency, and drift.

## Team Contributions

The team divided work by ownership while reviewing the end-to-end integration
together:

| Members | Contribution |
| --- | --- |
| Zhe Gao, Bin Liu | MVP architecture, end-to-end implementation, and performance optimization |
| Runzhong Chen | Evaluation-data validation, data-quality checks, and construction of additional test sets |
| Zixuan Shu, Yujia Zhao | Algorithm and system optimization, regression validation, and demo/video production |

Detailed module ownership and review boundaries are documented in
`docs/team/ownership.md`.

## Files

```text
data/public_set.jsonl             200 labeled development sessions
docs/competition_specification.md participant rules and evaluation protocol
docs/agent_api_contract.json      machine-readable Agent contract
docs/evaluation_config.json       scoring configuration
docs/baseline_results.json        reproducible weak-starter reference score
starter/agent.py                  editable weak starter
evaluator/local_evaluator.py      public-set simulator and scorer
```

## Judging and Submission Policy

- Participant submission requirements: `docs/submission_rules.md`
- Devpost-ready written description: `docs/devpost_submission_description.md`
- Final submission compliance checklist: `docs/submission_compliance_checklist.md`
- Official event page: https://tiktoktechjam2026.devpost.com/

## Data Source

The catalog and sessions are derived from Amazon Reviews 2023 by McAuley Lab, UCSD. See `DATA_ATTRIBUTION.md` before using or redistributing the data.
Sessions are sampled deterministically from the official Clothing 5-core leave-last-out split and joined to the frozen catalog.

## Submission Checklist

Before submitting through Devpost, verify that the project page contains the
written solution description and technology disclosure, links to this public
repository, and the required public three-minute YouTube demonstration. Ensure
that the metrics, selected configuration, Git commit, and limitations described on
Devpost match this repository.
