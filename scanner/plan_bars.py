"""GET /api/v2/ov-picks/plan-bars: 1-minute bars for Edge Trader's Opening plan charts, from Alpaca.

Fork-owned. Ash, project thread "Fix stale pick list blocking Arm", 2026-11-23 20:18:45 UTC: "so
let's move charts over to alpaca, that's for certain, i want charts to update every 2 secs". Until
then Edge Trader drew these charts from Schwab's price history on its own Schwab connection. On
2026-11-23 from 09:27 ET that call answered HTTP 500 after about 5 s, Edge Trader asked again every
15 s per name, and its order-list read queued behind those calls for 10 to 16 s, so the Opening
plan refused to send ("Schwab's order list is old") and Arm stayed off.

What it serves, for at most MAX_TICKERS names (`?tickers=AAA,BBB`):
- today's (ET) 1-minute bars from FROM_ET (08:30) to TO_ET (11:00), oldest first, the window the
  A1 charts show (Ash, 2026-11-18 14:07:45 UTC: "the chart view is fixed 8:30 - 11:00am ET");
- the minute that is still forming, marked `forming: true`, so the last candle moves between
  minutes: Alpaca's own bar for it when the bars answer has one, widened by the latest trade.

How it reads Alpaca: REST only, through the scanner's own Alpaca data client, never a websocket
(CONTRIBUTING.md "One market-data connection"; on the server ALPACA_STREAM=0). Only when a request
asks, never in the background. Per read one multi-symbol bars request and one snapshot request for
the names asked. Answers are cached: a request inside LIVE_TTL_S (2 s) of the last read, or inside
IDLE_TTL_S outside the window, gets the cached answer, so several open pages share one read. While
a read is running, other requests get the cached answer at once instead of waiting.

After a failed read the next one waits BACKOFF_S (5 s, then 10, 20, 40, 60) and the answer keeps
the last good bars with `error` and their age, so a failing Alpaca call is never asked again
every 2 s.

Times: each bar's `t` is its minute's start in seconds since 1970 (UTC). The answer's `at` is when
those bars were read (UTC, ISO) and `age_s` how old they are.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

ROUTE = "/api/v2/ov-picks/plan-bars"
VERSION = 1
_ET = ZoneInfo("America/New_York")
FROM_ET = time(8, 30)
TO_ET = time(11, 0)
LIVE_TTL_S = 2.0        # Ash, 2026-11-23 20:18:45 UTC: "i want charts to update every 2 secs"
IDLE_TTL_S = 60.0       # outside 08:30-11:00 ET the window's bars no longer change
BACKOFF_S = (5.0, 10.0, 20.0, 40.0, 60.0)
MAX_TICKERS = 8
_MINUTE = timedelta(minutes=1)

# (tickers, start, end) -> Alpaca's bars DataFrame, index (symbol, timestamp).
BarsFetch = Callable[[list, datetime, datetime], Any]
# tickers -> {ticker: (price, trade time)} from the latest trade.
TradesFetch = Callable[[list], Mapping[str, tuple]]


def _utc(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    try:
        import pandas as pd
        t = pd.Timestamp(ts)
        t = t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
        return t.to_pydatetime()
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None            # NaN is not a number here


def parse_bars(df: Any, day: date) -> dict[str, list[dict]]:
    """{ticker: [bar]} for `day`'s minutes from FROM_ET to TO_ET, oldest first. A row that is not a
    bar (a missing price, high below low) is left out."""
    out: dict[str, list[dict]] = {}
    if df is None or getattr(df, "empty", True):
        return out
    start = datetime.combine(day, FROM_ET, tzinfo=_ET)
    end = datetime.combine(day, TO_ET, tzinfo=_ET)
    for idx, row in df.iterrows():
        if not isinstance(idx, tuple) or len(idx) != 2:
            continue
        sym, ts = str(idx[0]), _utc(idx[1])
        if ts is None or not start <= ts <= end:
            continue
        o, h, l, c = (_num(row.get(k)) for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c) or l <= 0 or h < max(o, c) or l > min(o, c):
            continue
        out.setdefault(sym, []).append({"t": int(ts.timestamp()), "open": o, "high": h, "low": l,
                                        "close": c, "volume": _num(row.get("volume")) or 0.0})
    for bars in out.values():
        bars.sort(key=lambda b: b["t"])
    return out


def with_forming(bars: list[dict], trade: Optional[tuple], now: datetime) -> list[dict]:
    """`bars` with the forming minute marked, and moved by the latest trade.

    The minute that holds `now` is forming. Alpaca's bar for it, when the bars answer has one, is
    widened to the trade's price and closes there. Without one, a trade inside that minute starts
    it (open, high, low and close at the trade's price; volume 0, as it is not known). A trade from
    an earlier minute adds nothing: it is already inside that minute's own bar."""
    out = [dict(b) for b in bars]
    minute = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    t0 = int(minute.timestamp())
    last = out[-1] if out else None
    if last is not None and last["t"] == t0:
        last["forming"] = True
    price, at = (trade or (None, None))
    price, at = _num(price), _utc(at)
    if price is None or price <= 0 or at is None or not minute <= at < minute + _MINUTE:
        return out
    if minute.astimezone(_ET).time() < FROM_ET or minute.astimezone(_ET).time() > TO_ET:
        return out
    if last is not None and last["t"] == t0:
        last["high"], last["low"], last["close"] = max(last["high"], price), min(last["low"], price), price
    else:
        out.append({"t": t0, "open": price, "high": price, "low": price, "close": price,
                    "volume": 0.0, "forming": True})
    return out


class PlanBars:
    """The cache, the backoff and the reads. `bars` and `trades` are the two Alpaca requests; tests
    pass fakes. `clock` returns an aware datetime, `mono` a monotonic second count."""

    def __init__(self, bars: Optional[BarsFetch], trades: Optional[TradesFetch],
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 mono: Callable[[], float] = _time.monotonic) -> None:
        self._bars, self._trades, self._clock, self._mono = bars, trades, clock, mono
        self._lock = threading.Lock()           # the cache only; never held across a request
        self._busy = False
        self._cache: dict[tuple, dict] = {}     # tickers -> {"body", "ok_mono", "tried_mono", "fails"}

    def in_window(self, now: datetime) -> bool:
        t = now.astimezone(_ET).time()
        return FROM_ET <= t <= (datetime.combine(date.min, TO_ET) + _MINUTE).time()

    def _due(self, c: Optional[dict], now: datetime) -> bool:
        if c is None:
            return True
        m = self._mono()
        if c["fails"]:
            return m - c["tried_mono"] >= BACKOFF_S[min(c["fails"], len(BACKOFF_S)) - 1]
        return m - c["tried_mono"] >= (LIVE_TTL_S if self.in_window(now) else IDLE_TTL_S)

    def read(self, tickers: Iterable[str]) -> dict:
        names = tuple(dict.fromkeys(str(t).strip().upper() for t in tickers if str(t).strip()))[:MAX_TICKERS]
        now = self._clock()
        day = now.astimezone(_ET).date()
        key = (day, names)
        with self._lock:
            c = self._cache.get(key)
            go = bool(names) and not self._busy and self._due(c, now)
            if go:
                self._busy = True
        if go:
            try:
                self._read(key, names, now, day)
            finally:
                with self._lock:
                    self._busy = False
        with self._lock:
            c = self._cache.get(key)
            body = dict(c["body"]) if c else self._empty(names, "No bars yet." if names else "")
        ok_at = body.get("at")
        body["age_s"] = round((self._clock() - datetime.fromisoformat(ok_at)).total_seconds(), 1) if ok_at else None
        body["now"] = self._clock().astimezone(timezone.utc).isoformat()
        return body

    def _empty(self, names: tuple, why: str) -> dict:
        return {"v": VERSION, "at": None, "age_s": None, "ttl_s": LIVE_TTL_S, "error": why,
                "bars": {t: [] for t in names}}

    def _read(self, key: tuple, names: tuple, now: datetime, day: date) -> None:
        with self._lock:
            c = self._cache.setdefault(key, {"body": None, "tried_mono": 0.0, "fails": 0})
            c["tried_mono"] = self._mono()
        why = None
        try:
            if self._bars is None:
                raise RuntimeError("this scanner has no Alpaca data client")
            start = datetime.combine(day, FROM_ET, tzinfo=_ET)
            end = min(now, datetime.combine(day, TO_ET, tzinfo=_ET)) + _MINUTE
            got = parse_bars(self._bars(list(names), start, end), day) if now >= start else {}
            trades: Mapping[str, tuple] = {}
            if self._trades is not None and self.in_window(now):
                try:
                    trades = self._trades(list(names)) or {}
                except Exception as exc:          # the bars still show; the forming minute waits
                    log.warning("plan_bars: latest trades failed: %r", exc)
            body = {"v": VERSION, "at": now.astimezone(timezone.utc).isoformat(), "age_s": 0.0,
                    "ttl_s": LIVE_TTL_S, "error": "",
                    "bars": {t: with_forming(got.get(t, []), trades.get(t), now) for t in names}}
        except Exception as exc:
            log.warning("plan_bars: Alpaca bars failed: %r", exc)
            why = f"Alpaca's bars request failed ({type(exc).__name__}). The chart keeps its last bars."
        with self._lock:
            if why is None:
                c["body"], c["fails"] = body, 0
            else:
                c["fails"] += 1
                old = c["body"] or self._empty(names, why)
                c["body"] = {**old, "error": why}


def alpaca_bars(feed: Any) -> Optional[BarsFetch]:
    """The feed's multi-symbol 1-minute bars request (scanner/data/alpaca.py, the one ALPACA_STREAM=0
    polls with: unadjusted, the configured feed, one netlog line), or None without one."""
    request = getattr(feed, "_fetch_recent_minute_bars", None)
    return request if callable(request) else None


def alpaca_trades(feed: Any) -> Optional[TradesFetch]:
    """One snapshot request (REST, GET /v2/stocks/snapshots) for the latest trades, through the
    feed's own data client, or None without one."""
    client = getattr(feed, "_client", None)
    if client is None or not callable(getattr(client, "get_stock_snapshot", None)):
        return None

    def trades(tickers: list) -> dict[str, tuple]:
        from alpaca.data.requests import StockSnapshotRequest
        from scanner import netlog
        from scanner.data.alpaca import market_data_feed
        with netlog.call("alpaca_data", "snapshots", "plan_bars", symbols_n=len(tickers)) as c:
            snaps = client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=tickers, feed=market_data_feed()))
            if c:
                c.seen(items=len(snaps), data_ts=netlog.newest_trade(snaps))
        out = {}
        for sym, snap in (snaps or {}).items():
            tr = getattr(snap, "latest_trade", None)
            if tr is not None:
                out[str(sym)] = (getattr(tr, "price", None), getattr(tr, "timestamp", None))
        return out

    return trades


def register(app: FastAPI, app_state: Any, source: Optional[PlanBars] = None) -> PlanBars:
    """Add ROUTE to `app` on the scanner's Alpaca feed (or `source`, for tests and the demo)."""
    feed = getattr(app_state, "feed", None)
    plan = source or PlanBars(alpaca_bars(feed), alpaca_trades(feed))

    # A plain def: FastAPI runs it in a worker thread, so an Alpaca request never blocks the event
    # loop that serves /ws/alerts and the open tape.
    @app.get(ROUTE)
    def ov_picks_plan_bars(tickers: str = Query("")) -> JSONResponse:
        try:
            body = plan.read(tickers.split(","))
        except Exception as exc:
            log.exception("plan_bars: serving the plan bars failed")
            body = {"v": VERSION, "at": None, "age_s": None, "ttl_s": LIVE_TTL_S, "bars": {},
                    "error": f"The plan bars could not be read ({type(exc).__name__})."}
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    return plan
