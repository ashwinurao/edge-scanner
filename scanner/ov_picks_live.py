"""The OV picks window's live columns: values Edge Scanner fills after the open.

Fork-owned. Ash chose the window's layout "C1 + E1" (project thread, 2026-11-07): the
research columns first, among them two that only exist after the open, and allowed one
cached Alpaca REST request per day shown for the 09:30 ET minute bars of the day's ranked
tickers ("One request per day shown", same thread).

The module marks such a column with a `live` object (ov_picks/CONTRACT.md, "Live
metrics"): its kind and its bands. Its value in the file is null. This file computes the
value from the day's 09:30 ET minute bar and the row's levels, then gives the cell the tone
and note of the first band the value falls in. It holds no research number: the bands, and
what they mean, come from the module.

- first_minute_range_atr: (bar high - bar low) / levels.atr, to 2 decimals.
- open_vs_pm_range: the bar's open against levels.pm_high and levels.pm_low: "above",
  "below" or "inside" (the edges count as inside).

FirstMinuteBars keeps the bars per day (the newest KEEP_DAYS days). For a day it asks
Alpaca once, for every ranked ticker with a live column, after 09:31 ET. A ticker with no
bar yet is asked again, at most every RETRY_SECONDS, until 09:35 ET (GIVE_UP_ET); after
that, and at once for a past day, it has no 09:30 trade and its cells say so. A failed
request is asked again at most every RETRY_SECONDS. A day without a live column, a
preview (before 09:25 ET) and a day before 09:31 ET make no request.

The bars are unadjusted (Alpaca's raw prices): the row's levels are that day's prices, so a
later split must not change the bar. The route adds the bar to each row with a live column as
`first_minute` (open, high, low, close, or null) for the details card's level ladder.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# The first minute of the regular session, and when its bar is complete.
BAR_ET = time(9, 30)
DUE_ET = time(9, 31)
# A ticker without a 09:30 bar by then had no trade in that minute (a halt, a late open).
GIVE_UP_ET = time(9, 35)
RETRY_SECONDS = 60
KEEP_DAYS = 30

FIRST_MINUTE = "first_minute"   # the row field the route adds: the 09:30 bar, or null
BAR_FIELDS = ("open", "high", "low", "close")

# Kept in step with ov_picks/contract.py (LIVE_FIRST_MINUTE_RANGE_ATR, LIVE_OPEN_VS_PM_RANGE,
# OPEN_*). Spelled here too so the scanner starts without the ov_picks folder.
KIND_RANGE_ATR = "first_minute_range_atr"
KIND_OPEN_VS_PM = "open_vs_pm_range"
KINDS = (KIND_RANGE_ATR, KIND_OPEN_VS_PM)
ABOVE, BELOW, INSIDE = "above", "below", "inside"

# (day, tickers) -> {ticker: {open, high, low, close}} for the 09:30 ET bar; a ticker
# without one is left out. Raises when the request fails.
Fetch = Callable[[date, list[str]], Mapping[str, Mapping[str, float]]]

NOTE_WAITING = "Fills at 09:31 ET, from the first minute's bar."
NOTE_NO_FEED = ("This scanner has no Alpaca market data feed, so this column stays blank. "
                "Run the scanner with Alpaca to fill it.")
NOTE_LOADING = "Fetching the 09:30 ET bars. They show on the next refresh."
NOTE_NOT_YET = "No 09:30 ET bar yet. Edge Scanner asks again within a minute, until 09:35 ET."
NOTE_NO_TRADE = "No trade in the 09:30 ET minute, so there is nothing to show."
NOTE_NO_ATR = "The module wrote no ATR for this row, so the first-minute range can't be worked out."
NOTE_NO_PM = "The module wrote no pre-market high and low for this row, so the open can't be placed."


def note_failed(problem: str) -> str:
    return (f"The 09:30 ET bars could not be fetched ({problem}). Edge Scanner asks again "
            "within a minute; press Refresh after that.")


def note_unknown_kind(kind: Any) -> str:
    return f"This Edge Scanner can't fill this column ({kind!r} is new to it). Update Edge Scanner."


@dataclass
class Lookup:
    """What the store knows about one day's bars."""
    bars: dict[str, dict[str, float]] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)    # no 09:30 trade
    problem: str = ""                              # the last request failed
    waiting: bool = False                          # before 09:31 ET
    no_feed: bool = False
    busy: bool = False                             # a request for the day is out


@dataclass
class _Day:
    bars: dict[str, dict[str, float]] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)
    problem: str = ""
    tried_at: Optional[float] = None


class FirstMinuteBars:
    """The 09:30 ET minute bars of the days shown, fetched once per day and kept.

    `fetch` is the request (alpaca_fetch, or a fake in tests and the demo); None when the
    scanner has no Alpaca feed. `clock` is a monotonic clock for the retry spacing.
    Safe to call from the route's worker threads: one request per day is out at a time, and
    a caller never waits for another's request.
    """

    def __init__(self, fetch: Optional[Fetch], clock: Callable[[], float] = _time.monotonic) -> None:
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._days: dict[date, _Day] = {}
        self._busy: set[date] = set()

    def get(self, day: date, tickers: Iterable[str], now_et: datetime) -> Lookup:
        if now_et < datetime.combine(day, DUE_ET, tzinfo=_ET):
            return Lookup(waiting=True)
        if self._fetch is None:
            return Lookup(no_feed=True)
        wanted = sorted(set(tickers))
        with self._lock:
            entry = self._days.get(day) or _Day()
            missing = [t for t in wanted if t not in entry.bars and t not in entry.gone]
            if not missing or day in self._busy:
                return self._lookup(entry, busy=day in self._busy)
            if entry.tried_at is not None and self._clock() - entry.tried_at < RETRY_SECONDS:
                return self._lookup(entry)
            entry.tried_at = self._clock()
            self._busy.add(day)
            self._days[day] = entry
        got: Mapping[str, Mapping[str, float]] = {}
        problem = ""
        try:
            got = self._fetch(day, missing) or {}
        except Exception as exc:
            log.warning("ov_picks_live: the 09:30 ET bars for %s could not be fetched: %s", day, exc)
            problem = f"{type(exc).__name__}: {str(exc)[:120]}"
        with self._lock:
            self._busy.discard(day)
            for ticker in missing:
                bar = _bar(got.get(ticker))
                if bar is not None:
                    entry.bars[ticker] = bar
            entry.problem = problem
            # A ticker still without a bar after 09:35 ET had no trade in that minute; before
            # then the bar may come late, so it is asked again.
            if not problem and now_et >= datetime.combine(day, GIVE_UP_ET, tzinfo=_ET):
                entry.gone.update(t for t in missing if t not in entry.bars)
            self._days[day] = entry
            for old in sorted(self._days)[:-KEEP_DAYS]:
                del self._days[old]
            return self._lookup(entry)

    @staticmethod
    def _lookup(entry: _Day, busy: bool = False) -> Lookup:
        return Lookup(bars=dict(entry.bars), gone=set(entry.gone), problem=entry.problem, busy=busy)


def _bar(raw: Any) -> Optional[dict[str, float]]:
    if not isinstance(raw, Mapping):
        return None
    try:
        bar = {k: float(raw[k]) for k in BAR_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None
    return bar if all(v == v for v in bar.values()) else None   # no NaN


def alpaca_fetch(feed: Any) -> Optional[Fetch]:
    """The 09:30 ET bar request on the scanner's Alpaca feed, or None without one.

    It uses the feed's multi-symbol minute-bar request (scanner/data/alpaca.py, the one
    ALPACA_STREAM=0 polls with), which asks for unadjusted bars on the configured feed.
    """
    request = getattr(feed, "_fetch_recent_minute_bars", None)
    if request is None:
        return None

    def fetch(day: date, tickers: list[str]) -> dict[str, dict[str, float]]:
        start = datetime.combine(day, BAR_ET, tzinfo=_ET)
        return bars_at(request(tickers, start, start + timedelta(minutes=1)), start)

    return fetch


def bars_at(df: Any, at: datetime) -> dict[str, dict[str, float]]:
    """{ticker: bar} for the bars stamped `at` in an Alpaca bars frame (symbol, timestamp)."""
    out: dict[str, dict[str, float]] = {}
    if df is None or getattr(df, "empty", True):
        return out
    import pandas as pd
    frame = df.reset_index()
    if "symbol" not in frame.columns or "timestamp" not in frame.columns:
        return out
    stamps = pd.to_datetime(frame["timestamp"], utc=True)
    for _, row in frame[stamps == pd.Timestamp(at).tz_convert("UTC")].iterrows():
        bar = _bar({k: row.get(k) for k in BAR_FIELDS})
        if bar is not None:
            out[str(row["symbol"])] = bar
    return out


# ---------------------------------------------------------------- filling the rows

def live_tickers(rows: Iterable[Mapping]) -> list[str]:
    """The tickers of the rows with a live column this scanner can fill."""
    return [r["ticker"] for r in rows if any(_kind(m) in KINDS for m in r.get("metrics") or [])]


def _kind(metric: Mapping) -> Any:
    live = metric.get("live")
    return live.get("kind") if isinstance(live, Mapping) else None


def fill(rows: list[dict], lookup: Optional[Lookup]) -> None:
    """Fill each row's live columns in place, and add its 09:30 bar as `first_minute`.

    A row without a live column is left as read, so a module file without them is served
    unchanged. `lookup` is None for rows that never fill (a preview, before the freeze).
    """
    for row in rows:
        live = [m for m in row.get("metrics") or [] if isinstance(m.get("live"), Mapping)]
        if not live:
            continue
        bar = lookup.bars.get(row["ticker"]) if lookup else None
        row[FIRST_MINUTE] = dict(bar) if bar else None
        for metric in live:
            value, note = _value(metric["live"].get("kind"), bar, row.get("levels") or {})
            if value is None:
                metric["value"], metric["tone"] = None, None
                metric["note"] = note or _blank_note(row["ticker"], lookup)
                continue
            tone, band_note = band(metric["live"].get("bands") or [], value)
            metric["value"], metric["tone"], metric["note"] = value, tone, band_note


def _blank_note(ticker: str, lookup: Optional[Lookup]) -> str:
    if lookup is None or lookup.waiting:
        return NOTE_WAITING
    if lookup.no_feed:
        return NOTE_NO_FEED
    if ticker in lookup.gone:
        return NOTE_NO_TRADE
    if lookup.problem:
        return note_failed(lookup.problem)
    return NOTE_LOADING if lookup.busy else NOTE_NOT_YET


def _value(kind: Any, bar: Optional[Mapping[str, float]], levels: Mapping) -> tuple[Any, str]:
    """(the value, or None with a note that says why when the bar can't explain it)."""
    if kind not in KINDS:
        return None, note_unknown_kind(kind)
    if bar is None:
        return None, ""
    if kind == KIND_RANGE_ATR:
        atr = levels.get("atr")
        if not isinstance(atr, (int, float)) or atr <= 0:
            return None, NOTE_NO_ATR
        return round((bar["high"] - bar["low"]) / atr, 2), ""
    high, low = levels.get("pm_high"), levels.get("pm_low")
    if not isinstance(high, (int, float)) or not isinstance(low, (int, float)):
        return None, NOTE_NO_PM
    if bar["open"] > high:
        return ABOVE, ""
    if bar["open"] < low:
        return BELOW, ""
    return INSIDE, ""


def band(bands: Iterable[Mapping], value: Any) -> tuple[Optional[str], Optional[str]]:
    """(tone, note) of the first band `value` falls in, or (None, None) when none does.

    A text value matches a band whose `is` equals it. A number matches a band without
    `is` when it is at least `min` and at most `max` (a missing bound is open). A band
    with no bound at all takes any value (ov_picks/CONTRACT.md, "Live metrics").
    """
    for b in bands:
        text, low, high = b.get("is"), b.get("min"), b.get("max")
        if text is not None:
            hit = value == text
        elif low is None and high is None:
            hit = True
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            hit = (low is None or value >= low) and (high is None or value <= high)
        else:
            hit = False
        if hit:
            return b.get("tone"), b.get("note")
    return None, None


def fill_answer(body: dict, store: FirstMinuteBars, now_et: datetime) -> None:
    """Fill the live columns of the route's day, history and preview views in place."""
    for key in ("day", "history"):
        view = body.get(key)
        rows = (view or {}).get("picks") or []
        if not rows:
            continue
        tickers = live_tickers(rows)
        lookup = store.get(date.fromisoformat(view["trading_date"]), tickers, now_et) if tickers else Lookup()
        fill(rows, lookup)
    preview = body.get("preview")
    if preview and preview.get("picks"):
        fill(preview["picks"], None)
