"""The opening recorder: what Alpaca's REST data says around the open, saved for study.

Fork-owned. Step 1 of the OV entries plan: measure first, send nothing. Ash asked for it
in the project thread "Review OV picks desk architecture" on 2026-11-13 12:59 UTC: "let's
start with 0, 1 and 2 right away ... i want to have tracking in today so we have some
data to analyze".

It answers four questions about the first minutes of the session, for the day's OV top
picks and for SPY (REFERENCE: it trades all the time, so the age of its newest trade is
the data's own lateness, not a quiet stock):

1. How old is the newest trade when its snapshot arrives?
2. How does the first minute's range form, and when is it final?
3. When does the 09:30 ET bar first appear, and does it change afterwards?
4. How long does each request take?

It only reads and writes its own file. It never trades, never changes what the scanner
scans or shows, and never opens a websocket (ALPACA_STREAM=0, fork PR #3): every reading
is one REST request through the scanner's own Alpaca data client. They are the same two
requests the OV picks window makes (scanner/ov_picks_live.py), asked more often for seven
minutes.

When it runs: on weekdays from FROM_ET (09:29:00) to UNTIL_ET (09:36:00), and only on a
day with OV top picks (the contract reader's read_day, as scanner/ov_picks_day.py). A
weekend, a holiday or a day without picks makes no request and no file. A scanner that
starts inside the window joins it.

What it asks:

- Every POLL_SECONDS one snapshot request for the day's tickers (GET /v2/stocks/snapshots).
- From BARS_FROM_ET (09:31:00) the 09:30 ET minute bars, every BAR_SECONDS until each
  ticker's bar has been seen, at most until BARS_UNTIL_ET (09:31:30).
- Once from BARS_AGAIN_ET (09:35:30) the same bars again: the settled reading, to see
  whether late prints changed them.

That is about 870 requests in seven minutes, two a second at most.

What it writes: one JSON line per request to data/open_recorder/YYYY-MM-DD.jsonl (the
scanner's own data folder), with the wall time just before and just after the request
(`t0`, `t1`, UTC), how long it took (`ms`) and what came back. A day is about 2 MB. The
newest KEEP_DAYS files are kept. Line kinds:

- start:    the day's tickers and the settings, once per run.
- snapshot: per ticker the latest trade and quote with their own times, the latest minute
            bar and the day bar. Before 09:31 the day bar is the first minute so far (it
            counts only regular-hours trades; see scanner/ov_picks_live.py).
- bars:     the 09:30 ET bar of each ticker that has one, and the tickers still missing.
- stop:     the recorder stopped early for the day, and why.

A reading's age is `t1` minus the trade's own time. It is only as right as this machine's
clock, which the recorder does not check.

When something goes wrong it steps back. A failed request is written with its error, and
the next one waits ERROR_PAUSE_SECONDS. A rate-limit answer (HTTP 429) stops the recorder
until the next day, so it can never crowd out the scanner's own requests. It runs in its
own thread: the Alpaca client sets no time limit on a request, and a request that hangs
must hold up nothing but the recorder.

On when the scanner starts with OV_PICKS_DIR set (the picks are on) and an Alpaca feed.
OPEN_RECORDER=0 in .env turns it off. Without OV_PICKS_DIR it is off, so the test suite
and a developer's checkout never start it (tests/conftest.py clears the variable).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from scanner import ov_picks_day

log = logging.getLogger(__name__)

ENV_VAR = "OPEN_RECORDER"
OFF_VALUES = ("0", "off", "false", "no")
OUT_DIR = Path("data/open_recorder")
VERSION = 1     # of the line format; raise it when a field changes meaning

FROM_ET = time(9, 29)
UNTIL_ET = time(9, 36)
POLL_SECONDS = 0.5
BAR_ET = time(9, 30)            # the bar the recorder asks for
BARS_FROM_ET = time(9, 31)
BARS_UNTIL_ET = time(9, 31, 30)
BAR_SECONDS = 1.0
BARS_AGAIN_ET = time(9, 35, 30)
ERROR_PAUSE_SECONDS = 5.0
PICKS_RETRY_SECONDS = 5.0       # a late picks file is looked for this often
IDLE_SECONDS = 20.0             # the longest sleep outside the window
MIN_SLEEP_SECONDS = 0.05        # between two requests, however slow the last one was
REFERENCE = "SPY"
MAX_PICKS = 8                   # the module marks at most four as top; a bound all the same
KEEP_DAYS = 90
RATE_LIMITED = "rate_limited"

# What one step did. Tests assert on these.
NOT_A_WEEKDAY = "not_a_weekday"
OUTSIDE = "outside"             # a weekday, not between FROM_ET and UNTIL_ET
NO_PICKS = "no_picks"           # no top picks for today (yet)
RECORDED = "recorded"
FAILED = "failed"               # the request failed; its line says how
PAUSED = "paused"               # inside the pause after a failed request
STOPPED = "stopped"             # stopped for the day (a rate-limit answer)

_ET = ZoneInfo("America/New_York")
_FILE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl")
_BAR_KEYS = (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"),
             ("v", "volume"), ("n", "trade_count"), ("vw", "vwap"))

# tickers -> {ticker: an Alpaca Snapshot}. Raises when the request fails.
Snapshots = Callable[[list[str]], Mapping[str, Any]]
# (day, tickers) -> {ticker: {o, h, l, c, v, n, vw}} for the 09:30 ET bar; a ticker
# without one is left out. Raises when the request fails.
Bars = Callable[[date, list[str]], Mapping[str, Mapping[str, Any]]]


@dataclass
class _Day:
    day: date
    tickers: Optional[list[str]] = None         # the top picks and REFERENCE, once known
    picks_next_at: Optional[datetime] = None
    pause_until: Optional[datetime] = None      # after a failed request
    stopped: str = ""                           # why the day's recording ended early
    bars_seen: set[str] = field(default_factory=set)
    bars_next_at: Optional[datetime] = None
    bars_again: bool = False                    # the settled reading is done
    readings: int = 0
    failed: int = 0
    summed: bool = False


class OpenRecorder:
    """Records Alpaca's snapshot and first-minute bar answers around the open."""

    def __init__(self, picks_dir: Path, snapshots: Snapshots, bars: Optional[Bars] = None,
                 out_dir: Path = OUT_DIR, feed_name: str = "",
                 clock: Optional[Callable[[], datetime]] = None,
                 monotonic: Callable[[], float] = _time.monotonic,
                 load_contract: Optional[Callable[[], Any]] = None) -> None:
        self.picks_dir = Path(picks_dir)
        self.out_dir = Path(out_dir)
        self.feed_name = feed_name
        self._snapshots = snapshots
        self._bars = bars
        self._clock = clock or (lambda: datetime.now(_ET))
        self._monotonic = monotonic
        self._load_contract = load_contract or ov_picks_day._import_contract
        self._state: Optional[_Day] = None
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

    # ── one step ──

    def step(self) -> str:
        """Make at most one snapshot request and one bars request, and write their lines."""
        now = self._clock().astimezone(_ET)
        day = now.date()
        if now.weekday() >= 5 or not _at(day, FROM_ET) <= now < _at(day, UNTIL_ET):
            self._sum_up()
            return NOT_A_WEEKDAY if now.weekday() >= 5 else OUTSIDE
        state = self._state
        if state is None or state.day != day:
            state = self._state = _Day(day)
        if state.stopped:
            return STOPPED
        if state.pause_until is not None and now < state.pause_until:
            return PAUSED
        if state.tickers is None:
            if state.picks_next_at is not None and now < state.picks_next_at:
                return NO_PICKS
            picks = self._top_picks(day)
            if not picks:
                state.picks_next_at = now + timedelta(seconds=PICKS_RETRY_SECONDS)
                return NO_PICKS
            state.tickers = picks if REFERENCE in picks else picks + [REFERENCE]
            self._begin(state, now)
        if not self._read_snapshots(state):
            return FAILED
        if self._bars is not None and self._bars_due(state, now):
            self._read_bars(state, now)
        return RECORDED

    def wait(self) -> float:
        """Seconds until the next step is worth taking."""
        now = self._clock().astimezone(_ET)
        if now.weekday() >= 5:
            return IDLE_SECONDS
        start, end = _at(now.date(), FROM_ET), _at(now.date(), UNTIL_ET)
        if start <= now < end:
            return POLL_SECONDS
        if now < start:
            return min(IDLE_SECONDS, (start - now).total_seconds())
        return IDLE_SECONDS

    # ── the day's tickers ──

    def _top_picks(self, day: date) -> list[str]:
        contract = self._load_contract()
        if contract is None:
            return []
        view = contract.read_day(self.picks_dir, day.isoformat())
        if not view["has_picks"]:
            return []
        ranked = sorted(view["picks"], key=lambda p: p["rank"])
        return [p["ticker"] for p in ranked if p["top"]][:MAX_PICKS]

    def _begin(self, state: _Day, now: datetime) -> None:
        self._write(state.day, {
            "v": VERSION, "kind": "start", "t": _iso(now), "day": state.day.isoformat(),
            "tickers": list(state.tickers or []), "reference": REFERENCE, "feed": self.feed_name,
            "poll_seconds": POLL_SECONDS, "bar_seconds": BAR_SECONDS,
            "from_et": FROM_ET.isoformat(), "until_et": UNTIL_ET.isoformat(),
        })
        self._prune()
        log.info("opening recorder: recording %s until %s ET in %s", ", ".join(state.tickers or []),
                 UNTIL_ET.isoformat(), self._path(state.day))

    # ── the two requests ──

    def _read_snapshots(self, state: _Day) -> bool:
        tickers = list(state.tickers or [])
        rec, answer = self._timed("snapshot", state, lambda: self._snapshots(tickers))
        if rec["ok"]:
            found = answer if isinstance(answer, Mapping) else {}
            rec["symbols"] = {t: _snapshot(found[t]) for t in tickers if t in found}
            rec["missing"] = [t for t in tickers if t not in found]
        self._write(state.day, rec)
        self._note_stop(state)
        return bool(rec["ok"])

    def _bars_due(self, state: _Day, now: datetime) -> bool:
        if now >= _at(state.day, BARS_AGAIN_ET):
            return not state.bars_again
        if not _at(state.day, BARS_FROM_ET) <= now < _at(state.day, BARS_UNTIL_ET):
            return False
        waiting = any(t not in state.bars_seen for t in state.tickers or [])
        return waiting and (state.bars_next_at is None or now >= state.bars_next_at)

    def _read_bars(self, state: _Day, now: datetime) -> None:
        tickers = list(state.tickers or [])
        settled = now >= _at(state.day, BARS_AGAIN_ET)
        state.bars_next_at = now + timedelta(seconds=BAR_SECONDS)
        rec, answer = self._timed("bars", state, lambda: self._bars(state.day, tickers))
        if rec["ok"]:
            found = answer if isinstance(answer, Mapping) else {}
            rec["bars"] = {t: dict(found[t]) for t in tickers if t in found}
            rec["missing"] = [t for t in tickers if t not in found]
            rec["settled"] = settled
            state.bars_seen |= set(rec["bars"])
            state.bars_again = state.bars_again or settled
        self._write(state.day, rec)
        self._note_stop(state)

    def _timed(self, kind: str, state: _Day, ask: Callable[[], Any]) -> tuple[dict, Any]:
        """Run one request. Returns its line so far (times, ok, error) and its answer."""
        t0, m0 = self._clock(), self._monotonic()
        answer, problem = None, None
        try:
            answer = ask()
        except Exception as exc:    # a failed request is a reading too: its line says how
            problem = exc
        t1, ms = self._clock(), (self._monotonic() - m0) * 1000.0
        rec = {"v": VERSION, "kind": kind, "t0": _iso(t0), "t1": _iso(t1), "ms": round(ms, 1),
               "ok": problem is None}
        state.readings += 1
        if problem is not None:
            status = _status(problem)
            rec["error"] = f"{type(problem).__name__}: {problem}"[:300]
            rec["status"] = status
            state.failed += 1
            state.pause_until = t1.astimezone(_ET) + timedelta(seconds=ERROR_PAUSE_SECONDS)
            if status == 429:
                state.stopped = RATE_LIMITED
        return rec, answer

    def _note_stop(self, state: _Day) -> None:
        if not state.stopped or state.summed:
            return
        self._write(state.day, {"v": VERSION, "kind": "stop", "t": _iso(self._clock()), "why": state.stopped})
        log.warning("opening recorder: stopped until the next day (%s)", state.stopped)
        self._sum_up()

    # ── the file ──

    def _path(self, day: date) -> Path:
        return self.out_dir / f"{day.isoformat()}.jsonl"

    def _write(self, day: date, rec: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self._path(day), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def _prune(self) -> None:
        """Keep the newest KEEP_DAYS day files. Anything else in the folder is left alone."""
        files = sorted(p for p in self.out_dir.iterdir() if p.is_file() and _FILE_NAME.fullmatch(p.name))
        for old in files[:-KEEP_DAYS]:
            old.unlink(missing_ok=True)

    def _sum_up(self) -> None:
        state = self._state
        if state is None or state.summed or state.tickers is None:
            return
        state.summed = True
        log.info("opening recorder: %s done, %d requests, %d failed, file %s", state.day.isoformat(),
                 state.readings, state.failed, self._path(state.day))

    # ── the thread ──

    def run(self) -> None:
        last = None
        while not self._stop.is_set():
            began = self._monotonic()
            try:
                self.step()
                delay = max(MIN_SLEEP_SECONDS, self.wait() - (self._monotonic() - began))
                last = None
            except Exception as exc:
                # Nothing here may reach the scanner: log it once, wait, and carry on.
                problem = f"{type(exc).__name__}: {exc}"
                if problem != last:
                    log.exception("opening recorder: a step failed; it tries again in %gs", ERROR_PAUSE_SECONDS)
                last = problem
                delay = ERROR_PAUSE_SECONDS
            self._stop.wait(delay)

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="open-recorder", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------- reading Alpaca's answers

def _snapshot(snap: Any) -> dict:
    """The parts of one Alpaca Snapshot the study needs, with short keys."""
    out: dict[str, Any] = {}
    trade = getattr(snap, "latest_trade", None)
    if trade is not None:
        out["trade"] = {"t": _iso(getattr(trade, "timestamp", None)), "p": _num(getattr(trade, "price", None)),
                        "s": _num(getattr(trade, "size", None)), "x": _text(getattr(trade, "exchange", None)),
                        "c": _conditions(getattr(trade, "conditions", None))}
    quote = getattr(snap, "latest_quote", None)
    if quote is not None:
        out["quote"] = {"t": _iso(getattr(quote, "timestamp", None)),
                        "bp": _num(getattr(quote, "bid_price", None)), "bs": _num(getattr(quote, "bid_size", None)),
                        "ap": _num(getattr(quote, "ask_price", None)), "as": _num(getattr(quote, "ask_size", None))}
    for key, name in (("minute", "minute_bar"), ("day", "daily_bar")):
        bar = getattr(snap, name, None)
        if bar is not None:
            out[key] = {"t": _iso(getattr(bar, "timestamp", None)),
                        **{k: _num(getattr(bar, field_name, None)) for k, field_name in _BAR_KEYS}}
    return out


def frame_bars(df: Any, at: datetime) -> dict[str, dict[str, Any]]:
    """{ticker: {o, h, l, c, v, n, vw}} for the bars stamped `at` in an Alpaca bars frame
    (index symbol, timestamp)."""
    out: dict[str, dict[str, Any]] = {}
    if df is None or getattr(df, "empty", True):
        return out
    import pandas as pd
    frame = df.reset_index()
    if "symbol" not in frame.columns or "timestamp" not in frame.columns:
        return out
    stamps = pd.to_datetime(frame["timestamp"], utc=True)
    for _, row in frame[stamps == pd.Timestamp(at).tz_convert("UTC")].iterrows():
        out[str(row["symbol"])] = {k: _num(row.get(name)) for k, name in _BAR_KEYS}
    return out


def alpaca_snapshots(feed: Any) -> Optional[Snapshots]:
    """The snapshot request on the scanner's Alpaca feed, or None without one. One REST
    request (GET /v2/stocks/snapshots) on the configured feed, as scanner/ov_picks_live.py's
    alpaca_forming. Never a websocket."""
    client = getattr(feed, "_client", None)
    if client is None or not callable(getattr(client, "get_stock_snapshot", None)):
        return None

    def ask(tickers: list[str]) -> Mapping[str, Any]:
        from alpaca.data.requests import StockSnapshotRequest
        from scanner.data.alpaca import market_data_feed
        return client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=tickers, feed=market_data_feed()))

    return ask


def alpaca_bars(feed: Any) -> Optional[Bars]:
    """The 09:30 ET bar request on the scanner's Alpaca feed, or None without one: the
    feed's multi-symbol minute-bar request (unadjusted), as scanner/ov_picks_live.py's
    alpaca_fetch."""
    request = getattr(feed, "_fetch_recent_minute_bars", None)
    if request is None:
        return None

    def ask(day: date, tickers: list[str]) -> dict[str, dict[str, Any]]:
        start = _at(day, BAR_ET)
        return frame_bars(request(tickers, start, start + timedelta(minutes=1)), start)

    return ask


def _status(problem: Exception) -> Optional[int]:
    """The HTTP status of a failed Alpaca request (alpaca-py's APIError.status_code), if any."""
    try:
        status = getattr(problem, "status_code", None)
    except Exception:
        return None
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _at(day: date, when: time) -> datetime:
    return datetime.combine(day, when, tzinfo=_ET)


def _iso(stamp: Any) -> Optional[str]:
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _conditions(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    return [value] if isinstance(value, str) else [str(v) for v in value]


# ---------------------------------------------------------------- starting it with the app

def from_env(app_state: Any, clock: Optional[Callable[[], datetime]] = None,
             out_dir: Path = OUT_DIR) -> Optional[OpenRecorder]:
    """An OpenRecorder for this scanner, or None when it is off (see the module text)."""
    folder = (os.environ.get(ov_picks_day.ENV_VAR) or "").strip()
    if not folder:
        return None     # scanner/ov_picks_day.py already logs that the picks are off
    if (os.environ.get(ENV_VAR) or "").strip().lower() in OFF_VALUES:
        log.info("opening recorder: off (%s is set to off).", ENV_VAR)
        return None
    feed = getattr(app_state, "feed", None)
    snapshots = alpaca_snapshots(feed)
    if snapshots is None:
        log.info("opening recorder: off (this scanner has no Alpaca market data feed).")
        return None
    return OpenRecorder(Path(folder), snapshots, alpaca_bars(feed), out_dir=out_dir,
                        feed_name=_feed_name(), clock=clock)


def _feed_name() -> str:
    try:
        from scanner.data.alpaca import market_data_feed
        return str(market_data_feed().value)
    except Exception:
        return ""


def register(app: Any, app_state: Any, clock: Optional[Callable[[], datetime]] = None,
             out_dir: Path = OUT_DIR) -> None:
    """Start the recorder's thread when the app starts, if it is on then, and stop it when
    the app stops."""

    async def _start_open_recorder() -> None:
        try:
            recorder = from_env(app_state, clock=clock, out_dir=out_dir)
        except Exception:
            # The recorder is a measuring tool: it must never stop the scanner from starting.
            log.exception("opening recorder: could not be set up, so it is off.")
            return
        if recorder is None:
            return
        app.state.open_recorder = recorder
        recorder.start()
        log.info("opening recorder: on, weekdays %s to %s ET, writing to %s", FROM_ET.isoformat(),
                 UNTIL_ET.isoformat(), recorder.out_dir)

    async def _stop_open_recorder() -> None:
        recorder = getattr(app.state, "open_recorder", None)
        if recorder is not None:
            recorder.stop()

    # The same startup list scanner/ov_picks_day.py's register_morning uses.
    app.router.add_event_handler("startup", _start_open_recorder)
    app.router.add_event_handler("shutdown", _stop_open_recorder)
