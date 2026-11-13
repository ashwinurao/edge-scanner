"""Demo mode for the OV picks card: the dashboard on a sample picks folder, at a fake time.

Fork-owned (ashwinurao/edge-scanner#20). For screenshots and for trying the card without
the OV picks module, market data or broker keys:

    npm --prefix dashboard-v2 run build
    python scripts/ov_picks_demo.py --sample frozen --at "2026-11-05 09:25:05"
    python scripts/ov_picks_demo.py --sample preview --at "2026-11-05 09:14:30"
    python scripts/ov_picks_demo.py --sample past_days --at "2026-11-05 10:40" --demo-market
    # then open http://127.0.0.1:7787/v2/

It runs the real app (scanner/api.py create_app) with a stub scanner and no data feed,
in a new scratch folder under .logs/ (ignored), so the data/ it writes (layouts,
watchlists, the alert archive, the notice marker) never touches yours. It copies the
sample (ov_picks/samples/<name>, or any out/ folder) there, points OV_PICKS_DIR at the
copy and seeds one screen: the OV picks card, the "OV picks" watchlist and a Scanner
window that shows every alert. The route and the morning watcher run on a clock that
starts at --at (ET) and then moves with real time. It listens on 127.0.0.1 only, on the
demo port 7787 by default, and makes no network call.

--demo-market adds a linked chart and a linked news window, fed with made-up bars and
headlines (every headline says "Demo"), so a click on a past day's ticker can be seen
end to end (the calendar, Ash, 2026-11-07). Intraday bars run 04:00-20:00 ET, so the EXT
toggle and the lighter extended hours show too. Each day's 1-minute bars are seeded by the day,
and the longer intraday frames are rolled up from them, so a 5m chart and the chart's ORB studies
agree (Ash, 2026-11-08). It also makes up each row's 09:30 ET bar
from the row's levels, so the columns that fill after the open (scanner/ov_picks_live.py)
show values and tones. Without it the demo has no data feed and those cells stay blank.
Started inside the 09:30 minute, it also makes up Alpaca's snapshot in alpaca-py's shape,
read by the scanner's own rule (scanner/ov_picks_live.py so_far): during the minute its day
bar grows towards that bar, so the cells update every 2 seconds (Ash, 2026-11-08); from
09:31:00 its minute bar is the 09:30 bar, stamped 09:30:00 ET, and the made-up bars request
answers only from 09:31:06 ET, as on 2026-11-13, so the cells show the snapshot's 09:30 bar,
marked not confirmed, for a few seconds first (brief 20):

    python scripts/ov_picks_demo.py --sample meanings --at "2026-11-05 09:30:20" --demo-market
    python scripts/ov_picks_demo.py --sample meanings --at "2026-11-05 09:30:55" --demo-market

    python scripts/ov_picks_demo.py --sample meanings --at "2026-11-05 10:40" --demo-market

--spy-rv20 sets a made-up SPY RV20 for the top bar badge (red at 10.5 or lower, green above),
and a made-up 30-session history for the panel a click on it opens:

    python scripts/ov_picks_demo.py --sample frozen --at "2026-11-05 09:25:05" --spy-rv20 9.8

The rank badges and the score parts (the owner's layout "C1", 2026-11-08) show on:

    python scripts/ov_picks_demo.py --sample score_parts --at "2026-11-05 10:40" --demo-market
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

ET = ZoneInfo("America/New_York")
SAMPLES = _REPO_ROOT / "ov_picks" / "samples"


def _screen(market: bool = False) -> dict:
    """One saved screen: the card, the "OV picks" watchlist, and a Scanner window on "All";
    with `market`, a linked chart and a linked news window instead of the Scanner window."""
    base = {"link": "blue", "muted": False, "sound": {"tone": "off", "tts": False}}
    windows = {
        "ovp": {**base, "id": "ovp", "type": "ovpicks"},
        "wl": {**base, "id": "wl", "type": "watchlist", "watchlistId": "ov-picks",
               "columns": ["symbol", "price", "chg", "rvol"]},
        "sc": {**base, "id": "sc", "type": "scanner", "link": "none", "sources": [], "setups": [],
               "noSetups": False, "direction": "all", "minScore": 0, "symbolFilter": "",
               "columns": ["time", "symbol", "dir", "setup", "price"], "rowTint": True, "maxRows": 500},
    }
    layout = [
        {"i": "ovp", "x": 0, "y": 0, "w": 0.7, "h": 0.66, "z": 1},
        {"i": "wl", "x": 0.705, "y": 0, "w": 0.295, "h": 0.66, "z": 2},
        {"i": "sc", "x": 0, "y": 0.67, "w": 1, "h": 0.33, "z": 3},
    ]
    if market:
        del windows["sc"]
        windows["ch"] = {**base, "id": "ch", "type": "chart", "symbol": None, "timeframe": "5m", "extended": False,
                         "overlays": {"vwap": True, "ema9": True, "ema21": True, "volume": True, "sma50": False,
                                      "sma100": False, "sma200": False, "pdHL": True, "pmHL": True}}
        # "market" like a News window added from the top bar: a linked one follows its color in
        # either mode (src/windows/news/newsTarget.ts; Ash, 2026-11-12).
        windows["nw"] = {**base, "id": "nw", "type": "news", "mode": "market", "symbol": None, "hours": 24,
                         "limit": 20, "thumbnails": False}
        layout = [
            {"i": "ovp", "x": 0, "y": 0, "w": 0.6, "h": 0.5, "z": 1},
            {"i": "nw", "x": 0.605, "y": 0, "w": 0.395, "h": 0.5, "z": 2},
            {"i": "ch", "x": 0, "y": 0.51, "w": 0.75, "h": 0.49, "z": 3},
            {"i": "wl", "x": 0.755, "y": 0.51, "w": 0.245, "h": 0.49, "z": 4},
        ]
    stamp = "2026-11-05T00:00:00Z"
    return {"id": "ov-picks-demo", "name": "OV picks demo", "version": 1, "grid": 3, "createdAt": stamp,
            "updatedAt": stamp, "locked": False, "layout": layout, "windows": windows}


# ---------------------------------------------------------------- --demo-market: made-up data

# Intraday bars run 04:00-20:00 ET like Alpaca's, so the EXT toggle and the lighter
# extended hours have something to show; outside 09:30-16:00 they move and trade less.
_ET_OPEN, _ET_CLOSE = (9, 30), (16, 0)
_ET_FIRST, _ET_LAST = (4, 0), (20, 0)
_STEP = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "4Hour": 240}


class _DemoFeed:
    """Made-up bars for the chart route: a random walk per symbol, the same on every run."""

    def get_bars_range(self, symbol: str, tf: str, start: date, end: date):
        import pandas as pd
        if tf in _STEP and tf != "1Min":
            # Longer intraday frames are the 1-minute walk rolled up, so a 5m chart and
            # the chart's 1-minute ORB studies (dashboard-v2/src/lib/orbStudy.ts) agree.
            m = self.get_bars_range(symbol, "1Min", start, end)
            et = m.index.tz_convert(ET)
            first = et.normalize() + pd.Timedelta(hours=_ET_FIRST[0], minutes=_ET_FIRST[1])
            slot = ((et - first) // pd.Timedelta(minutes=_STEP[tf])) * pd.Timedelta(minutes=_STEP[tf]) + first
            g = m.groupby(slot.tz_convert(timezone.utc))
            out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                                "close": g["close"].last(), "volume": g["volume"].sum()})
            out.index.name = "timestamp"
            return out
        rng = random.Random(zlib.crc32(f"{symbol}|{tf}".encode()))
        price = base = 40 + rng.random() * 160
        stamps, rows = [], []
        d = start
        while d <= end:
            if d.weekday() < 5 and (tf != "1Week" or d.weekday() == 0):
                if tf == "1Min":
                    # Each day's minutes are seeded by the day, so every request that covers
                    # a day draws the same bars, however far back it starts.
                    rng = random.Random(zlib.crc32(f"{symbol}|{tf}|{d.isoformat()}".encode()))
                    price = base * (1 + rng.gauss(0, 0.03))
                if tf in _STEP:
                    t = datetime(d.year, d.month, d.day, *_ET_FIRST, tzinfo=ET)
                    last = datetime(d.year, d.month, d.day, *_ET_LAST, tzinfo=ET)
                    times = []
                    while t < last:
                        times.append(t)
                        t += timedelta(minutes=_STEP[tf])
                else:
                    times = [datetime(d.year, d.month, d.day, tzinfo=ET)]
                for t in times:
                    rth = tf not in _STEP or _ET_OPEN <= (t.hour, t.minute) < _ET_CLOSE
                    move = price * rng.gauss(0, (0.004 if rth else 0.0015) if tf in _STEP else 0.02)
                    o, c = price, max(1.0, price + move)
                    rows.append((o, max(o, c) * (1 + rng.random() * 0.002), min(o, c) * (1 - rng.random() * 0.002),
                                 c, rng.randint(5_000, 80_000) if rth else rng.randint(500, 8_000)))
                    stamps.append(t.astimezone(timezone.utc))
                    price = c
            d += timedelta(days=1)
        return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex(stamps, name="timestamp"))


class _DemoResp:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


def _demo_wire(url, headers, params, timeout):
    """Made-up headlines for the news route, a few hours apart, ending at the asked end."""
    end = (datetime.fromisoformat(params["end"].replace("Z", "+00:00")) if params.get("end")
           else datetime.now(timezone.utc))
    syms = (params.get("symbols") or "SPY").split(",")
    news = []
    for sym in syms:
        for i, hours in enumerate((1.5, 6, 20, 44, 66)):
            at = end - timedelta(hours=hours)
            news.append({"id": f"demo-{sym}-{i}-{at:%Y%m%d}", "symbols": [sym], "source": "demo",
                         "headline": f"Demo headline {i + 1} for {sym}, {at.astimezone(ET):%a %Y-%m-%d %H:%M} ET",
                         "summary": "Made up by scripts/ov_picks_demo.py --demo-market. Not real news.",
                         "url": "http://127.0.0.1/demo", "created_at": at.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return _DemoResp({"news": news})


def _demo_first_minute(out_dir: Path):
    """A made-up 09:30 ET bar per row, from its levels: the same on every run, and spread
    so the demo shows tight and wide first minutes, and opens above, inside and below the
    pre-market range."""
    from ov_picks import contract

    def fetch(day: date, tickers: list[str]) -> dict:
        view = contract.read_day(out_dir, day)
        if not view["has_picks"] and hasattr(contract, "read_history"):
            view = contract.read_history(out_dir, day)
        bars = {}
        for row in view["picks"]:
            lv = row.get("levels") or {}
            if row["ticker"] not in tickers or not lv.get("atr") or lv.get("pm_high") is None:
                continue
            rng = random.Random(zlib.crc32(f"{row['ticker']}|{day}".encode()))
            low_pm, high_pm = lv.get("pm_low", lv["pm_high"]), lv["pm_high"]
            where = rng.choice(("above", "inside", "inside", "below"))
            open_ = {"above": high_pm + 0.05 * lv["atr"], "below": low_pm - 0.05 * lv["atr"],
                     "inside": (high_pm + low_pm) / 2}[where]
            width = lv["atr"] * rng.choice((0.08, 0.18, 0.3, 0.42, 0.65, 0.9))
            up = rng.random() < 0.5
            high = open_ + (width if up else width * 0.2)
            low = high - width
            bars[row["ticker"]] = {"open": round(open_, 2), "high": round(high, 2), "low": round(low, 2),
                                   "close": round(low + width * (0.8 if up else 0.2), 2)}
        return bars

    return fetch


def _demo_so_far(bars, clock):
    """Alpaca's snapshot of the made-up 09:30 bar at the demo's time, in alpaca-py's shape
    (a day bar, a minute bar and the latest trade), read by the scanner's own rule
    (scanner/ov_picks_live.py so_far). During the minute the day bar is today's and grows from
    the open to the bar's range, with the latest trade at the demo's time, so the live cells
    change every few seconds. From 09:31:00 the minute bar is the 09:30 bar, stamped with its
    start (09:30:00 ET) as Alpaca stamps it, and the latest trade is from 09:31 (the opening
    recorder's file, 2026-11-13; brief 20)."""
    from scanner.ov_picks_live import so_far as read_snapshots

    def so_far(day: date, tickers: list[str]) -> dict:
        now = clock()
        start = datetime.combine(day, dtime(9, 30), tzinfo=ET)
        share = min(1.0, max(0.05, (now - start).total_seconds() / 60))
        snapshots = {}
        for ticker, bar in bars(day, tickers).items():
            o = bar["open"]
            high, low = round(o + (bar["high"] - o) * share, 2), round(o - (o - bar["low"]) * share, 2)
            close = round(low + (high - low) * 0.6, 2)
            # Alpaca stamps a bar with its start: the day bar at midnight ET, a minute bar at its minute.
            daily = SimpleNamespace(timestamp=datetime.combine(day, dtime(0), tzinfo=ET),
                                    open=o, high=high, low=low, close=close)
            if now >= start + timedelta(minutes=1):
                minute = SimpleNamespace(timestamp=start, **bar)
            else:
                minute = SimpleNamespace(timestamp=start - timedelta(minutes=1), open=o, high=o, low=o, close=o)
            snapshots[ticker] = SimpleNamespace(daily_bar=daily, minute_bar=minute,
                                                latest_trade=SimpleNamespace(timestamp=now, price=close))
        return read_snapshots(snapshots, day)

    return so_far


def _demo_rv_history(last: float | None, prior: date) -> list[dict]:
    """Made-up SPY RV20 for the last 30 weekdays, ending at --spy-rv20 on `prior`, for the top
    bar's history panel (Ash, 2026-11-13). It starts 4 points higher and wobbles down, so a
    calm last value crosses the 10.5 line inside the window."""
    if last is None:
        return []
    from scanner import spy_rv
    days, d = [], prior
    while len(days) < spy_rv.HISTORY:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    n = len(days)
    out = []
    for i, day in enumerate(days):
        v = last + 4.0 * (n - 1 - i) / (n - 1) + 0.6 * ((i * 7919) % 5 - 2) / 2 * (i < n - 1)
        r = spy_rv.reading(v, day.isoformat())
        out.append({"date": r["through"], "rv20": r["rv20"], "calm": r["calm"]})
    return out


def _demo_bars_late(fetch, clock, ready=dtime(9, 31, 6)):
    """The made-up bars request without today's 09:30 bars until `ready` ET: on 2026-11-13 the
    real one had the last of them at 09:31:06.5 (brief 20), so the snapshot's bar shows first."""
    def late(day: date, tickers: list[str]) -> dict:
        now = clock()
        if now.date() == day and now < datetime.combine(day, ready, tzinfo=ET):
            return {}
        return fetch(day, tickers)

    return late


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", default="frozen",
                    help="a folder name under ov_picks/samples, or the path of an out/ folder")
    ap.add_argument("--at", default="2026-11-05 09:25:05", help="the fake ET start time, YYYY-MM-DD HH:MM[:SS]")
    ap.add_argument("--not-scanned", default="",
                    help="comma-separated tickers the stub scanner does not scan (the card's universe note)")
    ap.add_argument("--unset", action="store_true", help="leave OV_PICKS_DIR unset (the 'not set up' card)")
    ap.add_argument("--demo-market", action="store_true",
                    help="add a linked chart and news window with made-up bars and headlines")
    ap.add_argument("--spy-rv20", type=float, default=None,
                    help="a made-up SPY RV20 for the top bar badge (scanner/spy_rv.py), through the prior weekday")
    ap.add_argument("--port", type=int, default=7787)
    ap.add_argument("--work", default="", help="scratch folder (default .logs/ov-picks-demo/<sample>)")
    args = ap.parse_args()

    sample = Path(args.sample) if Path(args.sample).is_dir() else SAMPLES / args.sample
    if not sample.is_dir():
        sys.exit(f"No sample folder {args.sample}. Pick one of: {', '.join(sorted(p.name for p in SAMPLES.iterdir()))}")
    work = Path(args.work) if args.work else _REPO_ROOT / ".logs" / "ov-picks-demo" / sample.name
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(sample, work / "out")
    (work / "data" / "layouts").mkdir(parents=True)
    (work / "data" / "layouts" / "ov-picks-demo.json").write_text(json.dumps(_screen(args.demo_market), indent=2))
    os.chdir(work)   # every data/ path in the app is relative to the working folder

    import uvicorn
    from scanner import ov_picks_route
    from scanner.api import AppState, create_app

    start = datetime.fromisoformat(args.at).replace(tzinfo=ET)
    t0 = time.monotonic()

    def clock() -> datetime:
        return datetime.fromtimestamp(start.timestamp() + (time.monotonic() - t0), ET)

    # create_app looks register up on the module when it runs (scanner/api.py, the fork
    # line), so the demo hands it the fake clock without a hook in the app itself.
    real_register = ov_picks_route.register
    made_up = _demo_first_minute(work / "out") if args.demo_market else None
    so_far = _demo_so_far(made_up, clock) if made_up else None
    first_minute = _demo_bars_late(made_up, clock) if made_up else None
    ov_picks_route.register = lambda app, app_state: real_register(app, app_state, clock=clock,
                                                                   first_minute_fetch=first_minute,
                                                                   first_minute_so_far=so_far)

    # Set after the imports: scanner/data/alpaca.py loads .env on import and would override it.
    if args.unset:
        os.environ.pop("OV_PICKS_DIR", None)
    else:
        os.environ["OV_PICKS_DIR"] = str(work / "out")

    # The stub scanner scans every name in the folder's days and previews, except --not-scanned.
    from ov_picks import contract
    folders = [d for d in (work / "out").iterdir() if d.is_dir()]
    tickers = {p["ticker"] for d in folders if (d / contract.PICKS_FILE).is_file()
               for p in contract.read_day(work / "out", d.name)["picks"]}
    tickers |= {p["ticker"] for d in folders if (d / contract.PREVIEW_FILE).is_file()
                for p in contract.read_preview(work / "out", d.name)["picks"]}
    skip = {s.strip().upper() for s in args.not_scanned.split(",") if s.strip()}
    scanned = sorted(tickers - skip) or ["SPY"]
    from scanner import spy_rv
    prior = start.date() - timedelta(days=1)
    while prior.weekday() >= 5:
        prior -= timedelta(days=1)
    stub = SimpleNamespace(_states={s: object() for s in scanned}, _spy_state=None, _regime=None, _sector_map={},
                           _spy_rv=spy_rv.reading(args.spy_rv20, prior.isoformat()),
                           _spy_rv_history=_demo_rv_history(args.spy_rv20, prior))
    feed = None
    if args.demo_market:
        from scanner import api_v2
        from scanner.news import NewsClient
        feed = _DemoFeed()
        # create_app builds the news client inside register_v2_routes; hand it the demo wire.
        api_v2.NewsClient = lambda *a, **k: NewsClient("demo", "demo", get_fn=_demo_wire)
    app = create_app(AppState(scanner=stub, feed=feed))  # type: ignore[arg-type]
    print(f"OV picks demo: {sample.name} at {start:%Y-%m-%d %H:%M:%S} ET, http://127.0.0.1:{args.port}/v2/ "
          f"(work folder {work})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
