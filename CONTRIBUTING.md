# Contributing

Use short-lived branches such as `feat/session-state`, `feat/hybrid-search`,
`exp/llm-ranker`, or `fix/response-contract`. Open a pull request before merging to
`main`; algorithm pull requests must state the hypothesis, config, verification
command, metric change, regressions, and rollback method.

Keep one owner per module and avoid editing another owner's path without review.
Experiments add an implementation or config and preserve the contracts. Do not commit
catalog data, results, traces, credentials, model caches, or generated submission
archives.
