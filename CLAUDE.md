# Edge Scanner working agreement

Rules for Claude Code and other agents working on this fork
(`ashwinurao/edge-scanner`, upstream `simonro/edge-scanner`).
Read [CONTRIBUTING.md](CONTRIBUTING.md) first: its "What the project cares about"
list (local first, one market-data connection, the evaluator never looks ahead,
saved work survives) wins over anything here.

## Scope and protected logic

- Stop at the requested scope. No new or upgraded dependencies, new network calls,
  external services, public exposure or new open ports unless the owner asked.
- Protected rules: the CONTRIBUTING.md list above, plus on the home server the
  scanner never opens an Alpaca websocket (`ALPACA_STREAM=0`, fork PR #3) because
  OpenVector holds the account's one stream, and the port is published on the LAN
  address only (never through a tunnel or to the internet). A change to any of
  these needs the owner's explicit go, quoted in the PR (who, when, where).
- OV picks numbers are protected like money logic (Ash, 2026-11-06, for the
  OV picks module): any change to a scoring, gate, universe or
  allocation number of that module needs its own PR with a
  full 2026 replay of how the picks change, and the owner's go. Since the
  hand-over (2026-11-10, Ash's go in the project thread) the private repo owns
  OV's pick logic and changes it there by PR, re-pinned in that PR; there are
  no more syncs from OV (Ash, 2026-11-06 and 2026-11-10; its docs/changing.md,
  "The hand-over"). The module,
  its vendored OV code and OV's numbers live in the private repo
  `ashwinurao/ov-picks`, never in this public one (CONTRIBUTING.md, "OV picks").
- **The contract is shared.** A change to `ov_picks/contract.py`,
  `ov_picks/CONTRACT.md` or `ov_picks/samples/` needs the same change in
  `ashwinurao/ov-picks` in a paired PR. Both repos pin its hash in
  `tests/test_ov_picks_contract_pin.py`.
- Never change an approved test expectation to make a failure go away. If an
  approved rule changed, list each changed expectation and why in the PR.
- Never put secrets, tokens, account numbers, real positions, `.env` files or
  `data/` contents in the repo, logs, screenshots, PRs or docs.
- Live systems: beyond the standard pull and rebuild of `main` on the server, no
  data deletion, token-file changes or other live-container operations without the
  owner's explicit go. The server only ever runs `main`.

## Tests and evidence

- Run `python -m pytest -q` before calling work done, and again on the PR's head
  commit after merging the latest `main` in. `main` stays green.
- Dashboard changes: `npm --prefix dashboard-v2 run lint` and
  `npm --prefix dashboard-v2 run build`, plus screenshots from a real browser.
- A fix comes with a test that fails without it. Triggers, conditions and
  indicators are tested with hand-built bars, including the bar where they must
  NOT fire. Tests never reach the network: inject fakes.
- Keep scratch artifacts in the ignored `.logs/` folder.
- GitHub checks (Ash, project thread, 2026-11-07): `.github/workflows/docker.yml` runs
  the tests inside `python:3.13-slim` (the Dockerfile's base) and builds the server image,
  dashboard included, on every PR and push to `main`. Upstream's `ci.yml` runs too, with
  the dashboard lint. Do not merge on a red check. GitHub-hosted machines only: this repo
  is public, so it never gets a self-hosted runner.

## Branches and pull requests

- Branch from the latest `origin/main`. Agent branches are `claude/<topic>`.
- One PR per coherent change. Stack a PR on another only when it truly depends on
  it, and say so in the description.
- PR titles say what changed for the trader, in plain words.
- Describe every PR with [.github/pull_request_template.md](.github/pull_request_template.md).
  Fill every section and write "None" rather than deleting one. A future reader
  should understand the change from the PR alone.
- Docs change in the same PR as the code: README, CONTRIBUTING, USER_GUIDE and
  anything else that describes the changed behavior.
- Merge PRs one at a time. Upstream syncs use merge commits, not squash, so
  upstream history stays in the fork.
- Keep edits to upstream files small (`scanner/api_v2.py`, `scanner/live_scanner.py`,
  `dashboard-v2/`, README, CONTRIBUTING). Put new logic in new files where it
  fits, so pulling from upstream stays easy.

## Ask the owner first

In the thread, with the options and a recommendation. Keep building on the
recommended option only where it is reversible and not protected.

- The protected rules above, and OV picks numbers.
- Approved test expectations.
- New or upgraded dependencies, new network calls or external services.
- Anything on the live server beyond the standard pull and rebuild of `main`.
- New workflows that deploy, use secrets or run on a self-hosted runner.

## Style

- Code: match the code around you (PEP 8, snake_case; React function components
  in the dashboard). Comment why, not what, and name the reason or PR a rule
  comes from. User-facing text says what happened and what to do next.
- Writing (docs, PRs, comments): plain direct English, short sentences, present
  tense. Dates `YYYY-MM-DD`, times UTC unless a market time is meant (then ET,
  computed with a time-zone library, never a fixed offset). Name the file that
  holds each rule or number. If something is not confirmed in code, say so;
  never guess.

## Claude Code plugins

`.claude/settings.json` enables Matt Pocock's `mattpocock-skills` plugin
(marketplace `mattpocock/skills` on GitHub). Its skills are optional helpers; this
agreement wins where they disagree.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues on `ashwinurao/edge-scanner`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root, created when first needed. See `docs/agents/domain.md`.
