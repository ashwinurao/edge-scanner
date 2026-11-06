## Summary

<!-- One or two sentences: what this pull request changes and why. Link the issue if there is one. -->

**Before:** <!-- What the trader or operator saw or could do before. -->

**After:** <!-- What they see or can do now. -->

## Docs

- [ ] README or USER_GUIDE updated
- [ ] CONTRIBUTING.md or CLAUDE.md updated
- [ ] No docs change needed (say why)

## How it works

<!-- A short technical walk-through: the flow from the data feed to the scanner to the dashboard, the main files touched, and what a reviewer should read first. -->

## Data changes

<!-- Changes to saved files under data/ (screens, setups, universe filters, caches, alert archives) or their formats. Files written by an older version must keep loading. "None" if none. -->

## Scanner rules and protected values

<!-- Any change to the rules in CONTRIBUTING.md "What the project cares about", to the server's no-websocket and LAN-only setup, or to OV picks numbers. These are protected: quote Ash's go (who, when, where). List every approved test expectation you changed and why. "None" if none. -->

- [ ] No second market-data connection, and no websocket on the server (`ALPACA_STREAM=0`)
- [ ] The evaluator still sees only closed bars (no look-ahead)
- [ ] New triggers, conditions or indicators have tests with the bar where they must NOT fire

## Tests and evidence

<!-- Commands run on the head commit and their results, and what you checked by hand. Screenshots from a real browser for dashboard changes; keep artifacts in .logs/. -->

- [ ] `python -m pytest -q` passes
- [ ] `npm --prefix dashboard-v2 run lint` and `npm --prefix dashboard-v2 run build` pass (dashboard changes)

## Worth knowing for review

- [ ] New dependency (which, and why)
- [ ] New network call, open port, or background job
- [ ] File access outside the app folder
- [ ] None of the above

## Rollout

<!-- Environment variables, compose changes, the Unraid pull and rebuild of main, and anything to check after it. "None" if none. -->

## Risks and follow-ups

<!-- What could go wrong, what is deliberately left out, and follow-up work. -->

No API keys, `.env` files, `data/` contents or account details are included.
