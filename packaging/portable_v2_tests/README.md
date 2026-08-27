# Portable V2 two-test kit

This package runs exactly two scored tests against a teammate's `Agent`:

1. **Standard sessions** — fixed multi-turn sessions scored with Hit Rate@10,
   MRR, MTTC, and the recommended technical score.
2. **Wording pressure** — the same semantic sessions rendered as `canonical`,
   `explicit`, `conversational`, and `contextual` English wording trajectories.

It intentionally excludes the dataset generator, sentence-level parser set,
historical artifacts, application implementation, and unit-test suite. The
50,000-product catalog is included because both tests need it.

## Requirements

- Python 3.10 or later; no third-party package is required by the test kit.
- A project containing `starter/agent.py` with the competition `Agent` API.
- Any dependencies or credentials required by that Agent remain the teammate's
  responsibility. The bundled evaluator itself is fully offline.

## Quick start

Extract the ZIP anywhere. From the extracted directory run:

```powershell
.\run_tests.ps1 --project-root D:\path\to\techjam-conversational-search
```

Cross-platform equivalent:

```bash
python run_tests.py --project-root /path/to/techjam-conversational-search
```

The default is a small development smoke test: 8 base sessions for the standard
test and the corresponding 32 wording trajectories. Reports are written under
`<project-root>/artifacts/`.

## Common commands

Run a 40-session development check:

```powershell
.\run_tests.ps1 --project-root D:\repo --limit 40 --trace-level failures
```

Run the complete development split:

```powershell
.\run_tests.ps1 --project-root D:\repo --limit 0
```

Run the locked holdout only for a milestone/final check:

```powershell
.\run_tests.ps1 --project-root D:\repo --split holdout --limit 0 --trace-level failures
```

Use another Agent module or an explicit config:

```powershell
.\run_tests.ps1 --project-root D:\repo `
  --agent-module starter.agent `
  --config configs\final.json
```

## Output

Each run produces a timestamped directory containing:

- `REPORT.md`: human-readable two-test report;
- `summary.json`: compact machine-readable result;
- `session_evaluation.json`: standard-session details;
- `language_stress_summary.json`: wording-pressure aggregates;
- `language_stress_traces.jsonl`: optional failed/all trajectories;
- `run.log`: stage timings and errors.

## Interpretation limits

- These are catalog-derived synthetic labels, not organizer private-set ground truth.
- Wording pressure currently covers English expression styles, not translation quality.
- Do not repeatedly tune against holdout results; use development for iteration.
- The test kit loads the teammate's Agent but does not copy or modify teammate code.
- Exact `parent_asin` equality is required for a hit.

See `package_manifest.json` for data hashes and bundled engine provenance.
