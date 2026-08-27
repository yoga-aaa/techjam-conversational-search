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
SQLite FTS5/BM25 candidate search, deterministic reranking, and a deterministic
clarification policy. Dense retrieval and model-backed implementations can be added
behind the stable interfaces in `shopping_copilot/core/interfaces.py` and selected
through config without changing the pipeline.

Run the team tests without downloading the full catalog:

```bash
python -m unittest discover -s tests -p "test*.py" -v
```

## Run the V2 synthetic test kit

The teammate test archive can be extracted outside the Git repository so its
50,000-product catalog and generated datasets are not accidentally committed.

### One-click two-mode report

On Windows, run the complete synthetic holdout and generate a timestamped
Markdown/JSON report bundle with one command:

```powershell
.\scripts\run_v2_holdout.ps1
```

The runner automatically discovers the sibling `_testkit` directory, checks the
input package, and runs exactly two scored modes:

1. **Standard sessions**, reported with Hit Rate@10, MRR, MTTC, and technical score.
2. **Language pressure**, reported with the same four metrics both overall and for
   canonical, explicit, conversational, and contextual wording variants.

Results are written under `artifacts/v2_holdout_YYYYMMDD-HHMMSS/`. By default only
failed language trajectories are saved, which keeps the bundle small without
changing any metric. Use `--trace-level all` for full traces or `--trace-level none`
for summaries only. Use the Python entry point directly on other platforms:

```bash
python scripts/run_v2_holdout.py
```

This intentionally consumes the synthetic holdout. For a quick tuning smoke test,
use `--split development --limit 40`; do not repeatedly tune on holdout results
without creating a new locked validation set. The "language" mode currently tests
multiple English wording styles, not translation quality across natural languages.
These catalog-derived labels are a local robustness benchmark, not organizer ground
truth or an official private score.

### Portable teammate package

The branch also contains a minimal portable ZIP under `packages/`. It bundles only
the two evaluators, their configuration/templates, and required synthetic data. A
teammate extracts it anywhere and points the runner at their own project; the bundle
loads their `starter.agent.Agent` without overwriting project files:

```powershell
.\run_tests.ps1 --project-root D:\path\to\teammate-project
```

Rebuild it with `python scripts/build_portable_v2_tests.py`. See
`docs/testing/V2_TWO_MODE_TESTS.md` for the repository layout, full-run commands,
output contract, and interpretation limits.

After downloading and verifying the official catalog, run an explicit experiment:

```bash
python scripts/verify_data.py --catalog data/catalog.jsonl
python scripts/run_evaluation.py --config configs/baseline.json --output results.json
```

The current selected development configuration is `configs/final.json`. After
the shared V2 parser adapter was added, a clean 200-session public-set run produced
Hit Rate@10 `0.950`, MRR `0.732849`, MTTC `4.995`, and recommended technical
score `0.814955`. These are public-set
development results, not evidence of private-set performance. Controlled runs
and rejected alternatives are recorded in `experiments/registry.csv`.

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

Only exact `parent_asin` equality produces a hit. Core metrics are also reported by scenario.

## Model Choice and Cost

Teams may use any legally accessible LLM API or local model. Teams manage their own credentials and must never commit API keys. Model choice, estimated cost, token usage, and latency must be disclosed. Token usage is a feasibility metric, not part of the core technical score. The organizer does not provide or reimburse model API credits; teams are responsible for any costs incurred through optional external services.

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
- Participant release checklist: `docs/participant_release_checklist.md`
- Organizer-only final judging controls: `organizer/JUDGING_RUNBOOK.md`
- Organizer private release checklist: `organizer/private_release_checklist.md`
- Judging day operations SOP: `organizer/JUDGING_DAY_SOP.md`

## Data Source

The catalog and sessions are derived from Amazon Reviews 2023 by McAuley Lab, UCSD. See `DATA_ATTRIBUTION.md` before using or redistributing the data.
Sessions are sampled deterministically from the official Clothing 5-core leave-last-out split and joined to the frozen catalog.
