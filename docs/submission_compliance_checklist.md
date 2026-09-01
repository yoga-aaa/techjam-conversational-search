# Final Submission Compliance Checklist

This checklist audits the selected `main` configuration and the official Devpost
requirement to provide a written description, a public code
repository, and a public three-minute YouTube demo.

## 1. Devpost written description

- [x] Explains the problem and how the solution addresses it.
- [x] Describes the state, retrieval, ranking, and policy architecture.
- [x] Lists development tools: Visual Studio Code, Git, GitHub, and Python CLI.
- [x] Discloses APIs: no external API; official local `Agent` interface only.
- [x] Lists libraries/frameworks: Python standard library and SQLite FTS5/BM25;
      no required third-party ML framework.
- [x] Identifies datasets and assets, including Amazon Reviews 2023 attribution.
- [x] Reports selected `main` results, zero token usage, cost, latency reference,
      and the public/private evaluation caveat.
- [x] States limitations, future work, and team contributions in English.
- [ ] Paste `docs/devpost_submission_description.md` into the team's Devpost
      project page and verify formatting before final submission.

## 2. Public code repository

- [x] Public repository URL:
      https://github.com/yoga-aaa/techjam-conversational-search
- [x] Official entry point exists at `starter/agent.py`.
- [x] Runtime components are separated into state, planning, retrieval, ranking,
      policy, response, observability, and evaluation modules.
- [x] `README.md` includes overview, installation, data preparation, reproduction,
      metrics, tools, APIs, libraries, limitations, and team contributions.
- [x] `requirements.txt` declares that the selected runtime has no third-party
      dependency.
- [x] Data attribution is documented in `DATA_ATTRIBUTION.md`.
- [x] No API key, credential, private holdout, or organizer-only file is tracked.
- [x] The selected configuration is `configs/final.json`.
- [x] All 75 unit tests pass.
- [x] Official public-set reproduction matches the documented result:
      Hit@10 `1.0`, MRR `0.953667`, MTTC `2.215`, TechnicalScore `0.9618`.

## 3. Public three-minute YouTube demo

- [ ] Record a real end-to-end run using the selected `main` configuration.
- [ ] Keep the video at or below three minutes.
- [ ] Show the problem, architecture, one complete session, results, limitations,
      and practical TikTok Shop relevance.
- [ ] Use readable English subtitles and legally usable audio/assets.
- [ ] Upload as a public YouTube video, not private or unlisted.
- [ ] Verify the URL in a signed-out/incognito browser.
- [ ] Add the URL to the Devpost video field.

## 4. Final consistency check

- [ ] Devpost, README, video, and repository all identify `main`/E27 as the selected
      build and use the same metrics.
- [ ] Do not combine E13 public-set metrics with E27 development-set metrics.
- [ ] Confirm the GitHub repository and YouTube video remain publicly accessible.
- [ ] Confirm all five team members are attached to the Devpost project.
- [ ] Submit before the Devpost deadline and retain a screenshot of the completed
      submission page.

The repository-side requirements are complete once this checklist is committed and
pushed. The unchecked items require action in the team's Devpost/YouTube accounts
and cannot be completed by a source-code change alone.
