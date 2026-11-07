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
end to end (the calendar, Ash, 2026-11-07). It also makes up each row's 09:30 ET bar
from the row's levels, so the columns that fill after the open (scanner/ov_picks_live.py)
show values and tones. Without it the demo has no data feed and those cells stay blank.

    python scripts/ov_picks_demo.py --sample meanings --at "2026-11-05 10:40" --demo-market
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
        windows["nw"] = {**base, "id": "nw", "type": "news", "mode": "linked", "symbol": None, "hours": 24,
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

_ET_OPEN, _ET_CLOSE = (9, 30), (16, 0)
_STEP = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "4Hour": 240}


class _DemoFeed:
    """Made-up bars for the chart route: a random walk per symbol, the same on every run."""

    def get_bars_range(self, symbol: str, tf: str, start: date, end: date):
        import pandas as pd
        rng = random.Random(zlib.crc32(f"{symbol}|{tf}".encode()))
        price = 40 + rng.random() * 160
        stamps, rows = [], []
        d = start
        while d <= end:
            if d.weekday() < 5 and (tf != "1Week" or d.weekday() == 0):
                if tf in _STEP:
                    t = datetime(d.year, d.month, d.day, *_ET_OPEN, tzinfo=ET)
                    close = datetime(d.year, d.month, d.day, *_ET_CLOSE, tzinfo=ET)
                    times = []
                    while t < close:
                        times.append(t)
                        t += timedelta(minutes=_STEP[tf])
                else:
                    times = [datetime(d.year, d.month, d.day, tzinfo=ET)]
                for t in times:
                    move = price * rng.gauss(0, 0.004 if tf in _STEP else 0.02)
                    o, c = price, max(1.0, price + move)
                    rows.append((o, max(o, c) * (1 + rng.random() * 0.002), min(o, c) * (1 - rng.random() * 0.002),
                                 c, rng.randint(5_000, 80_000)))
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
    first_minute = _demo_first_minute(work / "out") if args.demo_market else None
    ov_picks_route.register = lambda app, app_state: real_register(app, app_state, clock=clock,
                                                                   first_minute_fetch=first_minute)

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
    stub = SimpleNamespace(_states={s: object() for s in scanned}, _spy_state=None, _regime=None, _sector_map={})
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
