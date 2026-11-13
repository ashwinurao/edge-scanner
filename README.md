# Edge Scanner

[![CI](https://github.com/simonro/edge-scanner/actions/workflows/ci.yml/badge.svg)](https://github.com/simonro/edge-scanner/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/simonro/edge-scanner)](https://github.com/simonro/edge-scanner/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A real-time intraday stock scanner for US equities that runs on your own machine. It streams
1-minute bars for thousands of symbols, checks the setups you define on every bar, and shows the
alerts in a browser dashboard you lay out yourself. For day traders who want their own scans
instead of someone else's alert list.

- **Your machine, your keys.** No account, no subscription, no telemetry. Market data comes from
  your own Alpaca or Charles Schwab login, and everything it builds stays in `data/`. Any other
  provider can be added behind the same interface.
- **Setups without code.** Compose them in the dashboard from about 45 triggers and a list of
  conditions. Changes apply on the next bar, with no restart.
- **It tells you why.** For any stock, Setup Check shows what each setup did in the last few
  minutes and exactly which filter stopped an alert.
- **A dashboard you arrange.** Free-floating windows, several saved screens, linked symbols, and a
  WebSocket feed other programs can subscribe to.
- **Free and MIT licensed.** No paid tier.

**[Quick start](#quick-start)** · **[Watch the walkthrough](https://youtu.be/fGerDFkKGUI)** · **[User guide](USER_GUIDE.md)** · **[Releases](https://github.com/simonro/edge-scanner/releases)** · **[Privacy](#privacy-and-your-data)**

**What this is not:** not financial advice, not a signal service, and not a broker. It never places
an order. The sample setups are starting points, not a strategy. The screenshots below come from a
replayed past session, so the alerts are synthetic in the sense that they were produced by a replay:
they are not anyone's real account, positions or orders.

[![Watch: I build my own stock scanner setups, free and open source](docs/video-thumbnail.jpg)](https://youtu.be/fGerDFkKGUI)

**[I build my own stock scanner setups, free and open source](https://youtu.be/fGerDFkKGUI)**: why I
stopped paying for a scanner, how the setups are built, and the app running on a live session.

## Privacy and your data

Edge Scanner is local-first. The scanner and the dashboard listen on `localhost` only, and there is
no login because nothing is exposed to the network. Requests must also be addressed to this machine
by name, and settings can only be changed from a page this machine served, so a web page you visit
cannot reach the scanner through your browser.

- **Stays on your machine:** your API keys in `.env`, the setups, universe filters, watchlists and
  screens you build, the bar caches, the alert archives, and the record of outside calls and
  warnings (`data/netlog/`, `data/logs/`). All of it under `data/`, which is gitignored.
- **Goes out, to your own accounts:** market data requests to Alpaca or Charles Schwab, signed with
  your keys. They see the symbols you scan, as any market-data client would.
- **Goes out, unauthenticated:** company fundamentals from Yahoo Finance, and per-symbol news
  headlines from Yahoo Finance and Nasdaq RSS. Those services see the symbol being requested and
  your IP. Turn the news ones off with `NEWS_RSS_SOURCES=` in `.env`.
- **Goes out, once at startup:** one anonymous request to GitHub's public releases page, to tell you
  in the dashboard when a newer release exists. GitHub sees your IP and the scanner version, nothing else. Nothing is
  downloaded or installed; the update is `git pull`. Turn it off with `UPDATE_CHECK=0` in `.env`.
- **Never collected:** no analytics, no crash reports, no usage data. The project has no server.

## What's inside

- **Custom setups, no code.** A catalog of about 45 triggers (candle patterns, level breaks,
  crosses, VWAP and EMA behaviour, opening range, momentum, relative strength against SPY), each
  with its own options and parameters. Combine them with AND, OR or "at least N of", add conditions
  the stock must meet, and pick the universe the setup runs on.
- **Universe filters.** Named screens over price, liquidity, ATR, float, sector, relative volume and
  more, deciding which stocks a setup may alert on.
- **Dashboard windows.** Alert tables, charts, rankings (gainers, losers, most active, pre-market
  lists, new highs and lows), news, stock info, watchlists, Setup Check and a market clock.
  Charts can draw opening-range breakout studies (OR1 / OR2, flat at 10:00 or 10:30 ET): entry,
  stop and exit on each day, display only ([ORB studies](USER_GUIDE.md#orb-studies-on-the-chart)).
- **One data connection.** Everything runs in one process on one market-data websocket: Alpaca by
  default, or Charles Schwab, which is free with a brokerage account. Providers sit behind one
  `DataFeed` interface, so anything that can stream 1-minute bars and answer for history can be
  plugged in: see [adding a data provider](USER_GUIDE.md#adding-a-data-provider).
- **A feed for other programs.** `ws://localhost:7777/ws/alerts`, with server-side filters by
  source, setup or symbol.
- **Extensible.** Optional engine plugins can add built-in setups and data providers
  (`scanner/plugins.py`).

## Screenshots

**Price Action**: alerts on the left, the one you clicked pinned above the table and marked on the
intraday chart, a daily chart and news on the right, and a second link group along the bottom where
rankings drive their own chart.

![The Price Action screen: an alert table with the selected alert pinned above it, an intraday chart marking the alert bar, a daily chart, news, and rankings driving a second chart](docs/screenshot-price-action.png)

**Pre-Market**: pre-market gainers, losers and volume leaders. Click any symbol and the chart, the
daily chart, news and stock info all follow it.

![The Pre-Market screen showing three pre-market ranking tables on the left, with a chart, a daily chart, news and stock info following the selected symbol](docs/screenshot-pre-market.png)

**Config**: every setup in one list, with how it fires, when it may fire, and the universe filter it
runs on. Alerts, parameters, a plain-English summary and a per-stock check are the other tabs.

![The Config window: the setup list on the left, and on the right the selected setup's alert mode, direction, sessions, notes and universe filter](docs/screenshot-setup-builder.png)

## Requirements

- Python 3.11 to 3.14 (3.13 recommended; Python 3.15 is too new for some packages yet), and
  Node.js 22 LTS or newer (20.19+ also works) to build the dashboard
- Market data, either:
  - an [Alpaca](https://alpaca.markets) account (paper is fine). The default `ALPACA_FEED=sip`
    needs their paid market-data plan. The free `ALPACA_FEED=iex` works, but it is one exchange, so
    volume and relative volume read far lower and volume-based setups fire much less; or
  - a Charles Schwab brokerage account, free, using `DATA_PROVIDER=schwab`. It covers the whole
    universe: real 1-minute bars for the 300 most liquid symbols (Schwab's limit) and bars built from
    Schwab's quotes for the rest, which are close but not identical. See the
    [user guide](USER_GUIDE.md#step-3-add-your-alpaca-keys) for the one-time login, which Schwab
    expires every 7 days.

Those two ship because they are the two the project runs on and tests against. Another provider is
one class and one line in a registry: [adding a data provider](USER_GUIDE.md#adding-a-data-provider).

Tested on Windows. macOS and Linux should work through `start_scanner.sh`.

## Quick start

On Windows, clone the repository and run `setup.bat`. It checks your Python and Node versions,
installs everything, builds the dashboard and creates `.env`. Then add your keys to `.env` and run
`start_scanner.bat`.

By hand, or on macOS and Linux:

```bash
git clone https://github.com/simonro/edge-scanner.git
cd edge-scanner
python -m venv .venv
```

Activate the environment (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
elsewhere), then:

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `ALPACA_FEED`, or set
`DATA_PROVIDER=schwab` and the Schwab keys.

Build the dashboard once:

```bash
npm --prefix dashboard-v2 install
npm --prefix dashboard-v2 run build
```

Run it (`start_scanner.bat` on Windows):

```bash
./start_scanner.sh
```

Open http://localhost:7777.

**Every install starts empty.** No watchlists, no alert history and none of anyone else's setups.
You get two sample screens (Pre-Market and Price Action) so the dashboard is not a blank page. The
first start downloads a year of daily bars and 20 days of 5-minute bars for the whole universe,
which takes 10 to 20 minutes; later starts use the cache in `data/`.

Optional, to start from working examples instead of an empty setup list:

```bash
python scripts/install_setup_library.py
```

The full guide is [USER_GUIDE.md](USER_GUIDE.md).

## Updating to a new release

```bash
git pull
pip install -r requirements.txt
npm --prefix dashboard-v2 install
npm --prefix dashboard-v2 run build
```

Your `.env` and everything in `data/` (setups, universe filters, screens, watchlists, caches,
alert archives) are left alone. Release notes call out anything that needs a one-time step.

The dashboard shows a small "vX.Y.Z available" link next to the feed status when a newer release
exists. To be told by GitHub instead, use **Watch, then Custom, then Releases** on the repo page.

## Configuration

`.env` keys, all optional unless you use the feature:

| Key | Default | What it does |
|---|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | none | Alpaca market data and Benzinga news |
| `ALPACA_FEED` | `sip` | `sip` (paid, consolidated tape) or `iex` (free, one exchange) |
| `DATA_PROVIDER` | `alpaca` | `alpaca`, `schwab`, or a provider you add yourself |
| `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_CALLBACK_URL` | none | Charles Schwab market data |
| `NEWS_RSS_SOURCES` | `yahoo,nasdaq` | Free per-symbol news feeds. Empty turns them off |

## Layout

| Path | What |
|---|---|
| `scanner/` | the engine: data providers, per-symbol state, indicators, setups, universe filters, API |
| `scanner/trigger_catalog.py` | every trigger a custom setup can use |
| `scanner/conditions.py` | the conditions setups and universe filters can check |
| `scanner/data/` | market-data providers (`alpaca.py`, `schwab.py`) behind one interface |
| `scanner/custom_setups_defaults.json` | sample setups, seeded on first run |
| `scanner/setup_library.json` | the setup library (`scripts/install_setup_library.py`) |
| `scanner/plugins.py` | extension points for optional engine plugins |
| `ov_picks/` | the OV picks file contract and sample days; the module itself is in a separate private repo ([OV picks](#ov-picks)) |
| `scripts/run_live.py` | the live scanner |
| `dashboard-v2/` | the React dashboard |
| `data/` | runtime data: caches, settings, your setups, alert archives (gitignored) |

## Running on a home server

`Dockerfile` and `compose.yaml` run the scanner in Docker on a server on your LAN, for example
Unraid. The image builds the dashboard and starts `start_scanner.sh`. Settings come from `.env` next
to `compose.yaml`; the comments at the top of `compose.yaml` list the server-only keys. The port is
published on `LAN_IP` only (8772 by default), and `SCANNER_ALLOWED_HOSTS` must list that address.
There is no login: never publish the port to the internet.

```bash
docker compose up -d --build
```

A scanner left running rolls its daily context forward by itself: prior close, high, low and open,
ADV, ATR, SMAs, relative strength, the 60-day levels and the RVOL profile. Every day at
`DAILY_REFRESH_ET` (03:30 New York time by default, before the 04:00 pre-market) it downloads the
new day of history, the same way startup does, and swaps the new values in between two bars. If it
missed that time, the first bar of the new date starts the refresh. `DAILY_REFRESH_ET=off` turns it
off. The code is in `scanner/daily_refresh.py`. The universe itself is still built only at startup.

**Sharing another app's Schwab login.** Schwab keeps one valid sign-in per app, so two programs on
the same app key sign each other out. When another app on the server already signs in with your
Schwab app and keeps its login in a schwab-py token file (Fernet-encrypted), set
`SCHWAB_SHARED_TOKEN_FILE` and `SCHWAB_SHARED_TOKEN_KEY`. The scanner then never signs in itself:
it reads that file (read-only) every minute and copies a new login into its own `tokens.db`, and
`scripts/schwab_auth.py` refuses to run. When the login runs out, sign in again in the other app.
The code is in `scanner/data/shared_login.py`.

In Schwab mode the scanner also opens a Schwab streamer. Schwab is understood to allow one streamer
per user (not tested with this scanner), so do not run it beside another app that holds a Schwab
stream on the same account until that is checked.

**Sharing Alpaca keys with another app.** Alpaca allows one market-data websocket per account. When
another app already streams on the same keys, set `ALPACA_STREAM=0`: the scanner then never opens a
websocket and asks Alpaca's REST bars endpoint every 5 seconds for the last few minutes of 1-minute
bars, 1,000 symbols per request. These are Alpaca's own bars, handed on 3 seconds after each minute
closes, so alerts come a few seconds later than on the stream. The code is in
`scanner/data/alpaca_poll.py`.

**A record of every outside call (fork).** The scanner writes one JSON line per outside call to
`data/netlog/YYYY-MM-DD.jsonl` (UTC date), so a slow or failed call, late bars or a shared rate
limit can be explained afterwards. It is a record only: nothing reads it back, nothing waits for it,
and a failure to write it never reaches the scanner. `EDGE_SCANNER_NETLOG=0` in `.env` turns it off,
together with the log file below. The code is in `scanner/netlog.py`. Files are kept; expect about
15 MB a day, most of it the 5-second bar poll, which runs around the clock. Every line has `ts` (UTC), `kind` and
`app` (`edge-scanner`); Edge Trader writes the same format.

| kind | one line per |
|---|---|
| `answer` | HTTP answer from Alpaca, Alpaca news, Yahoo or Nasdaq RSS, Nasdaq Trader: `vendor`, `path` (no query), `http`, `wire_ms` (send until headers), `bytes`, the vendor's `date`, `retry_after`, and Alpaca's rate-limit headers as `rl_limit`, `rl_remaining`, `rl_reset`. alpaca-py retries a 429 or 504 inside one call; each attempt is its own line. |
| `call` | logical call, written when it ends: `vendor`, `endpoint`, `purpose` (`poller`, `ov_picks_live`, `chart`, `today`, `history`, `warmup`, `news`, ...), `wait_ms` + `wire_ms` + `between_ms` (retry sleeps, pages) + `after_ms` (body, parsing, DataFrame) = `total_ms`, `attempts`, `http`, `ok`, `error` (`timeout_connect`, `timeout_read`, `tls`, `dns`, `refused`, `reset`, `http_429`, `http_4xx`, `http_5xx`, `other`), `items`, `data_ts` (newest vendor time in the answer) and `data_age_ms`. The bar poll writes one per pass, with `pass_ms`, `handed` and `overrun` (the pass took longer than 5 s). yfinance and the GitHub check do not use a hooked session, so their `attempts` and `wire_ms` are null. |
| `inflight` | call still open after 2, 5, 10, 30 and 60 s. alpaca-py sets no request timeout, so this is how a hung call shows. |
| `bars` | minute bar, written once the poll stops asking for that minute: `expected` symbols, `with_bar`, `first_seen_ms_after_close` and `handed_ms_after_close` (min, p50, p95, max over the symbols with a bar), `seen_before_close` (symbols whose bar first came before the minute ended), and `changed_after_handed` with `max_volume_change_pct` (symbols whose volume or close changed in a later answer after the bar was handed on, and the largest volume change in % of the handed volume, signed). The poll does this work after it hands the bars on. |
| `route` | request to `/api/v2/ov-picks*`, `/api/v2/clock` or `/api/bars/...`: `route`, `http`, `ms`, `from` and `req` (Edge Trader's `X-Edge-App` and `X-Edge-Req` headers). Requests the local-only guard refuses are timed too; they have no route pattern, so `route` is their path, cut at 200 characters. |
| `lag` | minute: how late a 100 ms heartbeat thread and the web server's event loop wake, threads, CPU seconds, memory, queue, `dropped` lines, calls in flight, live-feed clients. |
| `note` | start (version, commit when known), the bar poll's start. |

No line holds API keys, request or answer bodies, query strings or `.env` values; error texts have
URLs cut at the query and the Alpaca keys replaced. `GET /api/v2/diag/net` (read only) sums up the
calls in memory: per vendor, endpoint and purpose, for the last 5 minutes and since 09:25 ET today,
the count, errors by class, p50, p95 and max of `wire_ms` and `total_ms`, and the age of the last
good call; also the lowest `rl_remaining` seen, the calls in flight, the last `lag` and `bars` lines
and `dropped`. WARNING and worse log lines also go to `data/logs/edge-scanner-YYYY-MM-DD.log` (UTC
date), so they survive a rebuild of the container.

**SPY RV20 in the top bar (fork).** The top bar shows SPY's 20-day realized volatility next to the
market regime: red at 10.5 or lower (a calm SPY day), green above. It comes from the SPY daily
history the scanner already loads, so it adds no network call. See
[USER_GUIDE: The top bar](USER_GUIDE.md#the-top-bar) and `scanner/spy_rv.py`.

## OV picks

This fork can show the pre-market ranked list that a separate module, "OV picks", freezes at
09:25 ET each trading day: every name it ranked, with its top picks (at most four) marked. You
pick and size from the list by hand; nothing here sizes or trades. The module's code lives in a separate private repo. This repo holds
only what reads its files: the shared picks-file contract (`ov_picks/contract.py`,
`ov_picks/CONTRACT.md`, `ov_picks/samples/`), the route, the window, the watchlist and notice, and
the module's opt-in compose service. The two repos talk through files only, and a change to the
contract needs the same change in the module's repo ([CONTRIBUTING.md](CONTRIBUTING.md#ov-picks)).

**In the scanner.** When `OV_PICKS_DIR` names the module's output folder (mounted read-only), the
scanner shows the day's picks. It never writes that folder.

- The **OV picks** window (Add window menu) shows the day's ranked list in rank order, the top
  picks highlighted, in the columns the module sends. Each rank is a coloured badge (gold for the
  first pick), and when the module sends what made up each score, its parts follow as coloured
  cells, with the part that puts a name ahead of the next one ringed (the owner's layout "C1",
  2026-11-08). Each part cell has its score out of 100 in the corner and an arrow when it gives
  more or fewer points than the next name, and each part's header says where full points start
  (round 4, "A1 + B1 + H1"). A click on a row sends its ticker to the
  window's linked charts and news (a linked 1-minute chart opens on 08:30-11:30 ET); the ▸
  arrow shows all of its details.
  It also shows the warmup, LATE, settled and shadow flags, the check against OV, the week's new
  listings, and when the market is closed, the next scan. Its **Jobs** button shows each module
  job's last state, when it ran and when it runs next (2026-11-09). It is display only: it places no orders.
  It reads `GET /api/v2/ov-picks` (`scanner/ov_picks_route.py`).
- From 09:00 to 09:25 ET, before the picks freeze, the window shows the module's **preview**: the
  same ranked list as of the latest minute, marked "PREVIEW: not frozen". The module previews
  each minute from 09:00 to 09:24 ET, so the top contenders show early and there is time to read
  why they rank there (the owner's request, 2026-11-06). The watchlist and the notice still come
  from the frozen 09:25 picks only.
  [USER_GUIDE: The OV picks window](USER_GUIDE.md#the-ov-picks-window) explains every part of it.
- **Past days** (the owner's request, 2026-11-07). The window has a calendar: ‹ and › step
  through the days that have a list, a date box picks any day, and **Today** goes back to the
  live list. A past day shows the module's own frozen picks for it, or, for a day before the
  module ran, OV's own list from the module's history files (`history/YYYY-MM-DD.json`, written
  once by the module's `history` job), labelled "OV 2026 rebuild" or "OV live scan". A click on
  a row there sends the ticker and that day to the window's linked charts and news: charts
  end on that day and news covers the 3 days up to its 16:00 ET close. The "OV picks"
  watchlist then holds that day's top picks, named with the date (`ov_picks_day` on the list;
  the 09:25 fill in `scanner/ov_picks_day.py` clears it), and its clicks send that day too.
  Any other click puts
  them back on live. The routes take an optional `?date=YYYY-MM-DD`: `GET /api/v2/ov-picks`,
  `GET /api/bars/{symbol}/{timeframe}` and `GET /api/v2/news` (`scanner/past_day.py`);
  `GET /api/v2/ov-picks/days` lists the days. Without `date` every route is unchanged. Past
  days use the same Alpaca calls with an earlier end; there is no new source.
- **What each value means** (the owner's layout "C1 + E1", 2026-11-07). The module can give a
  cell a tone (good, caution, warning) and a one-line note, group the row's details, and send
  the row's price levels (`ov_picks/CONTRACT.md`, "What a value means"). The window colours the
  cell and shows the note on hover; the ▸ arrow then opens a trade card: a ladder of the row's
  levels, a tile with the note for each explained number, the first group of fields, and every
  field grouped behind "All fields". This repo holds no research numbers: the module decides
  what each value means.
- **Columns that fill from the open.** A column the module marks `live` is filled by the
  scanner from the day's 09:30 ET minute: the first minute's range in ATR, and where the open
  sits against the pre-market range. During the minute they show the minute so far and update
  every 2 seconds, so OR1 or OR2 can be chosen right after 09:31 (the owner's request,
  2026-11-08): while the window is open, the route asks Alpaca for a snapshot of the day's
  ranked tickers at most every 2 seconds, and the window refreshes every 2 seconds. The
  snapshot's day bar counts only regular-hours trades, so before 09:31 it is the first minute.
  From 09:31:03 ET the minute's bar replaces it: the route asks Alpaca once per day shown, for
  that day's ranked tickers, and keeps the answer (the owner's go, 2026-11-07). Both are REST
  requests through the scanner's own data client, never a websocket
  (`scanner/ov_picks_live.py`). Before 09:30 ET, without an Alpaca feed, or when a request
  fails, the cells stay blank and say why. Past days fill from that day's bar.
- A name outside the scanner's universe gets no live bars and no setups. The universe is built at
  startup and a watchlist does not add to it, so the window marks that name "no setups".
- Each trading morning, when the picks freeze, the scanner fills a watchlist called "OV picks"
  (id `ov-picks`) with the day's top picks and sends one "OV picks ready: A, B, C, D" notice, which
  lists them, on the alert feed. The rest of the ranked list stays in the window. It shows in Scanner windows set to all setups. The marker `data/ov_picks/notified.json` keeps
  a restart from sending it twice. A scanner that first sees the picks after 16:00 ET fills the
  watchlist but sends no notice. The code is in `scanner/ov_picks_day.py`.
- **The opening recorder** (the owner's request, 2026-11-13: "i want to have tracking in today so
  we have some data to analyze"). On a weekday with top picks, from 09:29:00 to 09:36:00 ET, the
  scanner saves what Alpaca answers for the day's top picks and SPY: a snapshot every half
  second, the 09:30 ET bars each second from 09:31:00 until every ticker has one (at most until
  09:31:30), and the same bars once more from 09:35:30. Each request is one line in
  `data/open_recorder/YYYY-MM-DD.jsonl` with the time just before and after it, how long it
  took and what came back, so the age of the data at the open, how the first minute's range
  forms and when its bar first appears can be measured. A day is about 2 MB, and the newest 90
  days are kept. It only
  reads: it changes nothing the scanner scans or shows, places no orders and never opens a
  websocket (about 870 REST requests a day through the scanner's own data client, two a second
  at most). A failed request is written down and the next one waits 5 seconds; a rate-limit
  answer stops it until the next day. It runs in its own thread, so a slow answer holds up
  nothing else. `OPEN_RECORDER=0` in `.env` turns it off. The code is in
  `scanner/open_recorder.py`.
- **The open tape** (brief 15 of the OV entries build, for Edge Trader's armed plan).
  `GET /api/v2/ov-picks/open-tape` serves what the opening recorder just read, from memory: the
  day, the top picks in rank order and SPY (`tickers`, `reference`), the newest good snapshot
  (`snapshot`: its `t0`, `t1`, `ms` and per ticker the trade, quote, minute bar and day bar as the
  recorder writes them), every 09:30 ET bar seen so far per ticker (`bars`: the snapshot's minute
  bar once it is the 09:30 bar, and the bars request's, each with `seen`, the `t1` it was first
  seen, and a new entry only when it changed), the recorder's `state` (`recording`, `waiting`,
  `outside` its window, `stopped` for the day, or `off`, with `why` and the last failed request),
  `seq` (rises with each change), `now` and `poll_seconds`. It answers with
  `Cache-Control: no-store`, makes no Alpaca request and reads no file, so Edge Trader may ask it
  several times a second. Yesterday's tape is never served. The recorder's requests and file do
  not change, and a tape that fails changes nothing the recorder asks or writes. On 2026-11-13 the
  snapshot's day bar stayed the PRIOR day's until 09:31:00.65 ET, and the minute bar became the
  09:30 bar at 09:31:00.08-0.65 ET, so the tape never takes a day bar as the first minute. Not
  confirmed: whether the minute bar can change after it first shows the 09:30 bar (one day's file
  says no; a change would show as a second `snapshot` entry). Each request writes one `route` line
  to the netlog (about 190 bytes; at 4 a second from 09:25 to 09:36 ET about 0.5 MB a day). The
  code is in `scanner/open_tape.py`.
- Without `OV_PICKS_DIR` (when the scanner starts) none of this runs, and the window says so.
  `python scripts/ov_picks_demo.py --sample frozen` shows the window on a sample day, on
  127.0.0.1:7787, without keys or market data (`--sample preview --at "2026-11-05 09:14:30"` for
  a preview; `--sample past_days --at "2026-11-05 10:40" --demo-market` for the calendar, with a
  linked chart and news window on made-up data; `--sample meanings --at "2026-11-05 10:40"
  --demo-market` for coloured cells, the columns that fill after the open and the trade card).

**The module on a home server.** The module runs as the `ov-picks` service in `compose.yaml`.
Compose builds its image from a local clone of the private repo. The service is off unless
`COMPOSE_PROFILES=ov-picks` is in `.env`, so the scanner's own `docker compose up -d --build` never
needs that clone. It publishes no port and opens no websocket, and it has CPU and memory limits.
These `.env` keys set it up; each has a placeholder default (under `./ov-picks-unused/`), so an
unset one never breaks a compose command:

| Key | What |
|---|---|
| `COMPOSE_PROFILES=ov-picks` | turns the service on |
| `OV_PICKS_SRC` | the local clone of the module's repo (default `../ov-picks`) |
| `OV_PICKS_DATA` | the module's data folder, read-write for the module only |
| `OV_PICKS_OUT` | the module's output folder. The scanner mounts it read-only at `/ov-picks`, and `OV_PICKS_DIR` points there only when this key is set |
| `OV_MARKET_DIR`, `OV_OUTPUTS_DIR` | OpenVector's market and outputs folders, mounted read-only into the module for its shadow run |
| `SEC_USER_AGENT` | the SEC contact the module sends: a name and a contact email. Without one the module sends no SEC request |
| `OV_PICKS_CPUS`, `OV_PICKS_MEMORY` | the module's limits (default 2 CPUs and 4g; not measured yet) |
| `OV_PICKS_GO_LIVE`, `OV_PICKS_NEW_LISTINGS` | the module's two switches, both `0` (off) by default: go-live ends the shadow run, and the weekly new-listing rule changes the universe. Turning either on is the owner's call; the module's docs say when |

In its environment the module gets only the scanner's `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`, as
its own Alpaca keys, `SEC_USER_AGENT` and its two switches; no other key from `.env` reaches it
(`tests/test_ov_picks_compose.py` pins that list). Create its data and output folders before the
first start, owned by 99:100; the module runs as that user. Keep them outside `EDGE_SCANNER_DATA`.

```bash
docker compose --profile ov-picks up -d --build ov-picks
```

The module starts in its shadow run next to OpenVector, and the window says "shadow: checking
against OV". It stays there until `OV_PICKS_GO_LIVE=1` and the module's own go-live gate is
met: 5 qualifying shadow days since the last unexplained difference (spec US55, US56). The
module's own docs, in its repo, cover its schedule, its states, going live and its pinned tests.

## Development

```bash
python -m pytest
npm --prefix dashboard-v2 run lint
npm --prefix dashboard-v2 run dev
```

`npm run dev` serves the dashboard with hot reload on http://localhost:5174 and proxies the API to a
running scanner on 7777.

## Contributing

Bug reports, new triggers and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers
the setup, the tests a change needs, and how to add a trigger, a condition or a data provider.
Please report security problems privately, as described in [SECURITY.md](SECURITY.md), not in a
public issue.

## More free tools from Tape to Edge

Same rules for each one: free, MIT, runs on your own machine, and the build is on the [YouTube channel](https://www.youtube.com/@tapetoedge).

- [Trading Journal AI](https://github.com/simonro/Trading-Journal-AI): a local-first journal. Import your broker CSV, get round-trip trades, KPIs and AI diary analysis.
- [Edge GEX Lab](https://github.com/simonro/Edge-GEX-Lab): a live gamma exposure (GEX) map for SPY, QQQ, SPX, NDX and stocks, with walls, gamma flip and regime.
- [AI Research Desk](https://github.com/simonro/ai-research-desk): three AI research teams debate one stock into swing and long-term calls.

## Who made this

A day trader who wanted scans that match how he actually trades, and a dashboard that fits one
ultrawide monitor. Built by describing the problems to Claude Code.

- YouTube: [@tapetoedge](https://www.youtube.com/@tapetoedge)
- X: [@tapetoedge](https://x.com/tapetoedge)

Educational software, not financial advice. No affiliate relationship with Alpaca, Schwab or
anything else shown here. You are responsible for your own trading decisions, and the software
comes with no warranty.

## License

MIT, see [LICENSE](LICENSE). Third-party notices, including the TradingView Lightweight Charts
attribution, are in [NOTICE](NOTICE).
