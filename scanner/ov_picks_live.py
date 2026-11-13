"""The OV picks window's live columns: values Edge Scanner fills from the open.

Fork-owned. Ash chose the window's layout "C1 + E1" (project thread, 2026-11-07): the
research columns first, among them two that only exist after the open, and allowed one
cached Alpaca REST request per day shown for the 09:30 ET minute bars of the day's ranked
tickers ("One request per day shown", same thread). On 2026-11-08 05:10 UTC he asked for
the two to update every 2-3 seconds from 09:30, "so I can quickly make decision whether to
take or1 or o2 right after... I cannot wait every minute after market open".

The module marks such a column with a `live` object (ov_picks/CONTRACT.md, "Live
metrics"): its kind and its bands. Its value in the file is null. This file computes the
value from the day's 09:30 ET minute and the row's levels, then gives the cell the tone
and note of the first band the value falls in. It holds no research number: the bands, and
what they mean, come from the module.

- first_minute_range_atr: (high - low) / levels.atr, to 2 decimals.
- open_vs_pm_range: the minute's open against levels.pm_high and levels.pm_low: "above",
  "below" or "inside" (the edges count as inside).

From 09:30:00 ET, today only, the route asks Alpaca for a snapshot of the day's tickers,
about every LIVE_SECONDS while the window asks (never sooner than LIVE_SECONDS less
LIVE_SLACK_SECONDS after the last ask, so a refresh that lands a little early still asks). This is REST polling, never a websocket
(ALPACA_STREAM=0, fork PR #3). A snapshot fills the cells two ways (`so_far`):

- Its minute bar, once that is the 09:30 bar: the whole minute, as of its end (09:31:00).
  The first measured open (2026-11-13, the opening recorder's file, scanner/open_recorder.py)
  had it at 09:31:00.08 to 00.65 ET, while the bars request below had it only at 09:31:01.4
  to 06.5, so this fills the cells 2 to 5 seconds sooner (Ash's go, project chat,
  2026-11-13 15:38:32 UTC). Its high and low were the bars request's on that day.
- Its day bar, while the minute forms: the minute so far, as of the latest trade. Alpaca's
  day bar counts only regular-hours trades (its market data FAQ, "How are bars aggregated"),
  so before 09:31 its open, high and low would be the first minute's. On 2026-11-13 this
  showed nothing: the day bar stayed the prior day's until 09:31:00.65 ET for every symbol,
  and by then the latest trade was from 09:31. It stays because it costs no request and is
  checked against the day.

Both are marked not confirmed: the row says the time the values run to
(`first_minute_as_of`; Edge Trader reads it as "not final", so its order buttons wait for
the bar), and the cell's note says where they come from. The snapshot is asked on after
09:31:00 at the same pace, but only for the tickers still without their bar, and never
from GIVE_UP_ET. Not confirmed: whether the snapshot's minute bar can change after it first
shows the 09:30 bar, and whether other days look like 2026-11-13 (one day's file).

FirstMinuteBars keeps the 09:30 bar per day (the newest KEEP_DAYS days), which replaces
the snapshot's values and removes the mark; when the two differ by more than a cent it logs
a warning with both. For a day it asks Alpaca once, for every ranked ticker with a live
column, from DUE_ET (09:31:03, three seconds after the minute closes, like the scanner's
own poller, scanner/data/alpaca_poll.py SETTLE_SECONDS). A ticker with no bar yet is asked
again every FAST_RETRY_SECONDS until FAST_UNTIL_ET, then at most every RETRY_SECONDS until
09:35 ET (GIVE_UP_ET); after that, and at once for a past day, it has no 09:30 trade and
its cells say so. A failed request is asked again the same way. A day without a live
column, a past day and a preview (before 09:25 ET) never ask for a snapshot.

The bars are unadjusted (Alpaca's raw prices): the row's levels are that day's prices, so a
later split must not change the bar. The route adds the bar (or the snapshot's values) to each
row with a live column as `first_minute` (open, high, low, close, or null) for the details
card's level ladder, and asks the window to refresh every REFRESH_SECONDS while today's
cells are not final (`refresh_seconds`).
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

# The first minute of the regular session, when it closes, and when its bar is asked for:
# a few seconds later, so the last prints are in (scanner/data/alpaca_poll.py SETTLE_SECONDS).
BAR_ET = time(9, 30)
END_ET = time(9, 31)
DUE_ET = time(9, 31, 3)
# A ticker without its bar is asked again quickly at first: the bar is usually there within
# seconds, and Ash decides OR1 or OR2 right after 09:31 (2026-11-08 05:10 UTC).
FAST_RETRY_SECONDS = 2
FAST_UNTIL_ET = time(9, 31, 30)
# A ticker without a 09:30 bar by then had no trade in that minute (a halt, a late open).
GIVE_UP_ET = time(9, 35)
RETRY_SECONDS = 60
KEEP_DAYS = 30
# The minute so far: one snapshot request for the day's tickers about this often, and the
# window's refresh while the cells are not final ("every 2-3 secs", Ash, 2026-11-08).
LIVE_SECONDS = 2.0
# The window's 2 s refresh can land a few ms under 2 s after the last ask; without slack that
# refresh was skipped and the next ask came 4 s later (PR #45 review, 2026-11-13). So a snapshot
# is skipped only when the last ask was less than LIVE_SECONDS - LIVE_SLACK_SECONDS ago. One
# caller on a 2 s refresh still gets about 30 asks a minute. Two or more callers (two open
# windows, or a window and Edge Trader's Opening plan, which also asks the route every 2 s) can
# reach the bound: asks at 0, 1.75 ... 59.5 s, so at most 35 in any 60 s, against 30 without
# the slack. Only from 09:30 to at most 09:35 ET.
LIVE_SLACK_SECONDS = 0.25
REFRESH_SECONDS = 2
REFRESH_FROM_ET = time(9, 29, 45)

FIRST_MINUTE = "first_minute"   # the row field the route adds: the 09:30 bar, or null
AS_OF = "first_minute_as_of"    # added while the values are the snapshot's, not confirmed: "HH:MM:SS" ET
BAR_FIELDS = ("open", "high", "low", "close")
# The snapshot's 09:30 bar against the bar: a larger difference in a price is logged (brief 20).
CENT = 0.01

# Kept in step with ov_picks/contract.py (LIVE_FIRST_MINUTE_RANGE_ATR, LIVE_OPEN_VS_PM_RANGE,
# OPEN_*). Spelled here too so the scanner starts without the ov_picks folder.
KIND_RANGE_ATR = "first_minute_range_atr"
KIND_OPEN_VS_PM = "open_vs_pm_range"
KINDS = (KIND_RANGE_ATR, KIND_OPEN_VS_PM)
ABOVE, BELOW, INSIDE = "above", "below", "inside"

# (day, tickers) -> {ticker: {open, high, low, close}} for the 09:30 ET bar; a ticker
# without one is left out. Raises when the request fails.
Fetch = Callable[[date, list[str]], Mapping[str, Mapping[str, float]]]
# (day, tickers) -> {ticker: {open, high, low, close, as_of}} for the 09:30 ET minute from
# the snapshot, not confirmed: as_of is the aware time the values run to, the minute's end
# for the whole 09:30 bar, else the last trade's. A ticker without one is left out. Raises
# when the request fails.
Forming = Callable[[date, list[str]], Mapping[str, Mapping[str, Any]]]

NOTE_WAITING = ("Fills just after 09:31 ET: first from Alpaca's snapshot of the 09:30 bar, then "
                "confirmed by the 09:30 bar request within seconds.")
NOTE_NOT_IN_SNAPSHOT_YET = ("Alpaca's snapshot does not show this name's 09:30 minute yet. Edge "
                            "Scanner asks again every few seconds.")
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


def note_so_far(as_of: str, band_note: Optional[str]) -> str:
    lead = f"So far, to {as_of} ET. From Alpaca's snapshot; the 09:30 bar replaces it just after 09:31."
    return f"{lead} {band_note}" if band_note else lead


def note_unconfirmed(band_note: Optional[str]) -> str:
    lead = ("The 09:30 bar as Alpaca's snapshot shows it, not confirmed yet. The 09:30 bar request "
            "confirms it within seconds.")
    return f"{lead} {band_note}" if band_note else lead


@dataclass
class Lookup:
    """What the store knows about one day's bars."""
    bars: dict[str, dict[str, float]] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)    # no 09:30 trade
    problem: str = ""                              # the last request failed
    waiting: bool = False                          # before the bar is asked for (DUE_ET)
    no_feed: bool = False
    busy: bool = False                             # a request for the day is out
    # the snapshot's values, for tickers without their bar: {ticker: {open, high, low, close, as_of}}
    forming: dict[str, dict[str, Any]] = field(default_factory=dict)
    live: bool = False                             # the snapshot is being asked for


@dataclass
class _Day:
    bars: dict[str, dict[str, float]] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)
    problem: str = ""
    tried_at: Optional[float] = None
    forming: dict[str, dict[str, Any]] = field(default_factory=dict)
    formed_at: Optional[float] = None


def _at(day: date, when: time) -> datetime:
    return datetime.combine(day, when, tzinfo=_ET)


class FirstMinuteBars:
    """The 09:30 ET minute of the days shown: from the snapshot first, then its bar, kept.

    `fetch` asks for the bars (alpaca_fetch, or a fake in tests and the demo); None when the
    scanner has no Alpaca feed. `forming` asks for the snapshot's values (alpaca_forming); None
    leaves the cells blank until the bar. `clock` is a monotonic clock for the spacing.
    Safe to call from the route's worker threads: one request of each kind per day is out
    at a time, and a caller never waits for another's request.
    """

    def __init__(self, fetch: Optional[Fetch], clock: Callable[[], float] = _time.monotonic,
                 forming: Optional[Forming] = None) -> None:
        self._fetch = fetch
        self._forming = forming
        self._clock = clock
        self._lock = threading.Lock()
        self._days: dict[date, _Day] = {}
        self._busy: set[date] = set()
        self._forming_busy: set[date] = set()

    def get(self, day: date, tickers: Iterable[str], now_et: datetime) -> Lookup:
        # Before the bar is due the cells wait; without a feed they will stay blank.
        no_feed = self._fetch is None and self._forming is None
        if now_et < _at(day, BAR_ET):
            return Lookup(waiting=True, no_feed=no_feed)
        wanted = sorted(set(tickers))
        if now_et < _at(day, DUE_ET):
            # From 09:31:00 the snapshot's minute bar is the 09:30 bar (brief 20, 2026-11-13).
            self._ask_forming(day, wanted)
            with self._lock:
                entry = self._days.get(day) or _Day()
                return Lookup(waiting=True, no_feed=no_feed, forming=_copy(entry.forming),
                              live=self._forming is not None)
        if self._fetch is None:
            return Lookup(no_feed=True)
        self._ask_bars(day, wanted, now_et)
        # Until its bar is in, the snapshot keeps a ticker's cells filled sooner (brief 20):
        # only for the tickers still missing one, and never from 09:35 (nor for a past day).
        if now_et < _at(day, GIVE_UP_ET):
            with self._lock:
                entry = self._days.get(day) or _Day()
                unconfirmed = [t for t in wanted if t not in entry.bars and t not in entry.gone]
            self._ask_forming(day, unconfirmed)
        with self._lock:
            return self._lookup(self._days.get(day) or _Day(), busy=day in self._busy)

    def _ask_bars(self, day: date, wanted: list[str], now_et: datetime) -> None:
        """Ask for the 09:30 bars of the tickers without one, unless a request for the day is
        out or the last one was too recent."""
        with self._lock:
            entry = self._days.get(day) or _Day()
            missing = [t for t in wanted if t not in entry.bars and t not in entry.gone]
            if not missing or day in self._busy:
                return
            spacing = FAST_RETRY_SECONDS if now_et < _at(day, FAST_UNTIL_ET) else RETRY_SECONDS
            if entry.tried_at is not None and self._clock() - entry.tried_at < spacing:
                return
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
                    _log_against_so_far(ticker, bar, entry.forming.get(ticker))
            entry.problem = problem
            # A ticker still without a bar after 09:35 ET had no trade in that minute; before
            # then the bar may come late, so it is asked again.
            if not problem and now_et >= _at(day, GIVE_UP_ET):
                entry.gone.update(t for t in missing if t not in entry.bars)
            self._days[day] = entry
            for old in sorted(self._days)[:-KEEP_DAYS]:
                del self._days[old]

    def _ask_forming(self, day: date, wanted: list[str]) -> None:
        """Ask the snapshot for `wanted`, unless it was asked within LIVE_SECONDS (less the
        slack, LIVE_SLACK_SECONDS) or is out."""
        if self._forming is None or not wanted:
            return
        with self._lock:
            entry = self._days.setdefault(day, _Day())
            if day in self._forming_busy:
                return
            if entry.formed_at is not None and self._clock() - entry.formed_at < LIVE_SECONDS - LIVE_SLACK_SECONDS:
                return
            entry.formed_at = self._clock()
            self._forming_busy.add(day)
        got: Mapping[str, Mapping[str, Any]] = {}
        try:
            got = self._forming(day, wanted) or {}
        except Exception as exc:
            # The cells keep the last values; the next ask, a few seconds on, tries again.
            log.warning("ov_picks_live: the 09:30 ET minute so far for %s could not be fetched: %s", day, exc)
        with self._lock:
            self._forming_busy.discard(day)
            for ticker in wanted:
                raw = got.get(ticker)
                bar = _bar(raw)
                as_of = raw.get("as_of") if isinstance(raw, Mapping) else None
                if bar is None or not isinstance(as_of, datetime):
                    continue
                old = entry.forming.get(ticker)
                # A slower answer never takes an older minute over a newer one.
                if old is None or as_of >= old["as_of"]:
                    entry.forming[ticker] = {**bar, "as_of": as_of}

    def _lookup(self, entry: _Day, busy: bool = False) -> Lookup:
        forming = {t: v for t, v in entry.forming.items() if t not in entry.bars}
        return Lookup(bars=dict(entry.bars), gone=set(entry.gone), problem=entry.problem, busy=busy,
                      forming=_copy(forming), live=self._forming is not None)


def _copy(forming: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t: dict(v) for t, v in forming.items()}


def _log_against_so_far(ticker: str, bar: Mapping[str, float], so_far: Optional[Mapping[str, Any]]) -> None:
    """Log the snapshot's last values against the bar, to check the snapshot on real mornings
    (Ash's request, 2026-11-08).

    The snapshot's 09:30 bar should be the bar: a difference of more than a cent in open,
    high, low or close is a WARNING with both (brief 20; the server keeps WARNING and up, so
    this is the line a morning leaves). The day bar's minute so far misses the minute's last
    seconds, so it only gets an INFO line."""
    if not so_far:
        return
    if _whole_minute(so_far["as_of"]):
        if any(round(abs(bar[k] - so_far[k]), 6) > CENT for k in BAR_FIELDS):
            log.warning("ov_picks_live: %s 09:30 bar o/h/l/c %.4f/%.4f/%.4f/%.4f differs by more than a cent "
                        "from the snapshot's 09:30 bar %.4f/%.4f/%.4f/%.4f; the bar wins",
                        ticker, *(bar[k] for k in BAR_FIELDS), *(so_far[k] for k in BAR_FIELDS))
        return
    log.info("ov_picks_live: %s 09:30 bar o/h/l %.4f/%.4f/%.4f; so far to %s: %.4f/%.4f/%.4f",
             ticker, bar["open"], bar["high"], bar["low"], so_far["as_of"].strftime("%H:%M:%S"),
             so_far["open"], so_far["high"], so_far["low"])


def _whole_minute(as_of: datetime) -> bool:
    """Whether snapshot values run to the minute's end, so they are the whole 09:30 bar from
    the snapshot's minute bar (`so_far`). The minute so far always runs to a trade before it."""
    return as_of.astimezone(_ET).time() == END_ET


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


def alpaca_forming(feed: Any) -> Optional[Forming]:
    """The snapshot request on the scanner's Alpaca feed, or None without one.

    One snapshot request (REST, GET /v2/stocks/snapshots) for the day's tickers, on the
    configured feed, through the feed's own data client. Never a websocket."""
    client = getattr(feed, "_client", None)
    if client is None or not callable(getattr(client, "get_stock_snapshot", None)):
        return None

    def forming(day: date, tickers: list[str]) -> dict[str, dict[str, Any]]:
        from alpaca.data.requests import StockSnapshotRequest
        from scanner.data.alpaca import market_data_feed
        from scanner import netlog  # fork: the call's time and data age (scanner/netlog.py, brief 11)
        with netlog.call("alpaca_data", "snapshots", "ov_picks_live", symbols_n=len(tickers)) as c:
            snaps = client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=tickers, feed=market_data_feed()))
            if c:
                c.seen(items=len(snaps), data_ts=netlog.newest_trade(snaps))
        return so_far(snaps, day)

    return forming


def so_far(snapshots: Any, day: date) -> dict[str, dict[str, Any]]:
    """{ticker: {open, high, low, close, as_of}} for `day`'s 09:30 ET minute from Alpaca
    snapshots ({symbol: Snapshot}), not confirmed. A snapshot without either is left out.

    - The whole minute, as of its end (09:31:00): the snapshot's minute bar, when it is
      stamped 09:30:00 ET of `day`. Seen from 09:31:00.08 to 00.65 ET on 2026-11-13 (brief 20).
    - Else the minute so far, as of the latest trade: the day bar, when it is `day`'s and the
      latest trade is inside the minute. Before the first regular trade the day bar is the
      prior day's, and from 09:31 on it holds later trades too. On 2026-11-13 the day bar
      stayed the prior day's until 09:31:00.65 ET, so this showed nothing that day.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(snapshots, Mapping):
        return out
    start, end = _at(day, BAR_ET), _at(day, END_ET)
    for symbol, snap in snapshots.items():
        minute = getattr(snap, "minute_bar", None)
        if _et_time(getattr(minute, "timestamp", None)) == start:
            bar = _bar({k: getattr(minute, k, None) for k in BAR_FIELDS})
            if bar is not None:
                out[str(symbol)] = {**bar, "as_of": end}
                continue
        daily, trade = getattr(snap, "daily_bar", None), getattr(snap, "latest_trade", None)
        bar_time, last = _et_time(getattr(daily, "timestamp", None)), _et_time(getattr(trade, "timestamp", None))
        if bar_time is None or last is None or not _bar_of(bar_time, day) or not start <= last < end:
            continue
        bar = _bar({k: getattr(daily, k, None) for k in BAR_FIELDS})
        if bar is not None:
            out[str(symbol)] = {**bar, "as_of": last}
    return out


def _bar_of(stamp: datetime, day: date) -> bool:
    """Whether a day bar stamped `stamp` is `day`'s. Alpaca stamps a day bar at the start of
    its New York day (05:00 UTC in winter); a stamp at 00:00 UTC of the day counts too, so a
    change of stamp would not hide the minute so far. A prior day's bar is neither."""
    return stamp.date() == day or stamp.astimezone(ZoneInfo("UTC")).date() == day


def _et_time(stamp: Any) -> Optional[datetime]:
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return None
    return stamp.astimezone(_ET)


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

    Without the bar yet, the snapshot's values fill them, and the row says the time they run
    to (`first_minute_as_of`: "09:31:00" for the snapshot's 09:30 bar), which marks them not
    confirmed. A row without a live column is left as read, so a module file without them is
    served unchanged. `lookup` is None for rows that never fill (a preview, before the freeze).
    """
    for row in rows:
        live = [m for m in row.get("metrics") or [] if isinstance(m.get("live"), Mapping)]
        if not live:
            continue
        bar = lookup.bars.get(row["ticker"]) if lookup else None
        as_of = None
        whole = False
        if bar is None and lookup is not None and row["ticker"] in lookup.forming:
            forming = lookup.forming[row["ticker"]]
            bar = {k: forming[k] for k in BAR_FIELDS}
            as_of = forming["as_of"].astimezone(_ET).strftime("%H:%M:%S")
            whole = _whole_minute(forming["as_of"])
            row[AS_OF] = as_of
        row[FIRST_MINUTE] = dict(bar) if bar else None
        for metric in live:
            value, note = _value(metric["live"].get("kind"), bar, row.get("levels") or {})
            if value is None:
                metric["value"], metric["tone"] = None, None
                metric["note"] = note or _blank_note(row["ticker"], lookup)
                continue
            tone, band_note = band(metric["live"].get("bands") or [], value)
            metric["value"], metric["tone"] = value, tone
            if as_of is None:
                metric["note"] = band_note
            else:
                metric["note"] = note_unconfirmed(band_note) if whole else note_so_far(as_of, band_note)


def _blank_note(ticker: str, lookup: Optional[Lookup]) -> str:
    if lookup is not None and lookup.waiting and lookup.live:
        return NOTE_NOT_IN_SNAPSHOT_YET   # before the bar is due: the snapshot shows no 09:30 minute yet
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
    """Fill the live columns of the route's day, history and preview views in place, and
    set `refresh_seconds` while today's cells are about to fill or still forming."""
    body["refresh_seconds"] = None
    for key in ("day", "history"):
        view = body.get(key)
        rows = (view or {}).get("picks") or []
        if not rows:
            continue
        tickers = live_tickers(rows)
        day = date.fromisoformat(view["trading_date"])
        lookup = store.get(day, tickers, now_et) if tickers else Lookup()
        fill(rows, lookup)
        if tickers and fast(day, tickers, lookup, now_et):
            body["refresh_seconds"] = REFRESH_SECONDS
    preview = body.get("preview")
    if preview and preview.get("picks"):
        fill(preview["picks"], None)


def fast(day: date, tickers: Iterable[str], lookup: Lookup, now_et: datetime) -> bool:
    """Whether the window should refresh every REFRESH_SECONDS: from just before 09:30 ET
    until each ticker has its 09:30 bar, at most until FAST_UNTIL_ET, and only with a feed."""
    if lookup.no_feed or not _at(day, REFRESH_FROM_ET) <= now_et < _at(day, FAST_UNTIL_ET):
        return False
    return any(t not in lookup.bars and t not in lookup.gone for t in tickers)
