"""The open tape: what the opening recorder just read from Alpaca, served from memory.

Fork-owned. Brief 15 of the OV entries build ("the open tape"). Ash's go: "Build 6,7,8,9 in
parallel branches right now, build, test and then wait to merge those branches" (project thread
"Review OV picks desk architecture", 2026-11-13 15:21:55 UTC); this is the base Step 6 needs.

Why: an armed plan in Edge Trader needs a price at most about a second old and the first
minute's range as soon as it exists. GET /api/v2/ov-picks refreshes every 2 s and has no price
after 09:31 ET. The opening recorder (scanner/open_recorder.py) already asks Alpaca for the
picks' snapshots twice a second from 09:29 to 09:36 ET. The tape keeps what it just read, in
memory, and GET /api/v2/ov-picks/open-tape (scanner/ov_picks_route.py) serves it. No new
Alpaca request, no websocket, no key in Edge Trader.

What it keeps, under its own lock:
- the day, the tickers in rank order and the reference (from the recorder's `start` line);
- the newest ok `snapshot` line (t0, t1, ms, symbols, missing), as the recorder wrote it;
- every 09:30 ET bar seen so far per ticker, in the order seen, each with the `t1` it was first
  seen (`seen`) and where it came from: `snapshot` (the snapshot's minute bar, when it is
  stamped 09:30 ET of the day) or `bars` (the recorder's 09:30 bars request). A version is kept
  only when it differs from the last one from the same source, at most MAX_VERSIONS a ticker;
- the recorder's state: on or off (and why), stopped for the day (and why), the last failed
  request;
- a sequence number that rises with each change.

What the first measured open showed (2026-11-13, project memory open-2026-11-13-measurements):
the snapshot's `day` bar stayed the PRIOR day's until 09:31:00.65 ET, so it is never a range;
the snapshot's `minute` bar became the finished 09:30 bar at 09:31:00.08-0.65 ET; the bars
request had the same bar at 09:31:01.4-06.5 ET with the same high and low. Whether the
snapshot's minute bar can change after it first shows the 09:30 bar is not confirmed (one
day's file says no); each change would show here as a new version.

`publish(rec)` never raises: the recorder calls it with each line it writes, and nothing here may
change what the recorder asks, writes or does. `view(now)` copies what the route serves.
"""
from __future__ import annotations

import copy
import logging
import threading
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

VERSION = 1             # of the answer's format; raise it when a field changes meaning
MAX_VERSIONS = 20       # 09:30 bar versions kept per ticker; the newest replaces the last past it

# state.code values.
OFF = "off"                     # no recorder runs on this scanner; `why` says why
OUTSIDE = "outside"             # a weekend, or outside the recorder's window
WAITING = "waiting"             # inside the window, no start line for today yet (no picks yet)
RECORDING = "recording"         # inside the window, recording today's picks
STOPPED = "stopped"             # the recorder stopped for the day; `why` says why
ERROR = "error"                 # the route could not read the tape (scanner/ov_picks_route.py)

_ET = ZoneInfo("America/New_York")
_BAR_FIELDS = ("o", "h", "l", "c", "v", "n", "vw")


class OpenTape:
    """The recorder's newest readings, for GET /api/v2/ov-picks/open-tape."""

    def __init__(self, from_et: time, until_et: time, bar_et: time = time(9, 30)) -> None:
        self.from_et, self.until_et, self.bar_et = from_et, until_et, bar_et
        self._lock = threading.Lock()
        self._on = False
        self._off_why = "the opening recorder has not started yet"
        self._seq = 0
        self._failed_once = False
        self._clear(None)

    def _clear(self, day: Optional[date]) -> None:
        self._day = day
        self._tickers: list[str] = []
        self._reference: Optional[str] = None
        self._poll_seconds: Optional[float] = None
        self._snapshot: Optional[dict] = None
        self._bars: dict[str, list[dict]] = {}
        self._stopped = ""
        self._error: Optional[dict] = None

    # ── what the recorder tells it ──

    def recorder_on(self) -> None:
        with self._lock:
            self._on, self._off_why = True, ""
            self._seq += 1

    def recorder_off(self, why: str) -> None:
        with self._lock:
            self._on, self._off_why = False, str(why)
            self._seq += 1

    def publish(self, rec: Mapping[str, Any]) -> None:
        """Take one recorder line. Never raises: a tape failure must not reach the recorder."""
        try:
            with self._lock:
                if self._take(rec):
                    self._seq += 1
        except Exception:
            if not self._failed_once:
                self._failed_once = True
                log.exception("open tape: a recorder line could not be taken; the recorder carries on")

    def _take(self, rec: Mapping[str, Any]) -> bool:
        kind = rec.get("kind")
        if kind == "start":
            self._clear(date.fromisoformat(str(rec["day"])))
            self._tickers = [str(t) for t in rec.get("tickers") or []]
            self._reference = rec.get("reference")
            self._poll_seconds = rec.get("poll_seconds")
            return True
        if self._day is None:
            return False                # a line before any start: nothing to file it under
        if kind == "stop":
            self._stopped = str(rec.get("why") or "stopped")
            return True
        if kind not in ("snapshot", "bars"):
            return False
        if not rec.get("ok"):
            self._error = {"kind": kind, "t1": rec.get("t1"), "error": rec.get("error"),
                           "status": rec.get("status")}
            return True
        if kind == "snapshot":
            symbols = rec.get("symbols") or {}
            self._snapshot = {"t0": rec.get("t0"), "t1": rec.get("t1"), "ms": rec.get("ms"),
                              "symbols": copy.deepcopy(dict(symbols)), "missing": list(rec.get("missing") or [])}
            for ticker, snap in symbols.items():
                minute = snap.get("minute") if isinstance(snap, Mapping) else None
                if isinstance(minute, Mapping) and self._is_bar_time(minute.get("t")):
                    self._add_bar(ticker, "snapshot", rec.get("t1"), minute.get("t"), minute)
        else:
            stamp = _iso(datetime.combine(self._day, self.bar_et, tzinfo=_ET))
            for ticker, bar in (rec.get("bars") or {}).items():
                if isinstance(bar, Mapping):
                    self._add_bar(ticker, "bars", rec.get("t1"), stamp, bar, settled=bool(rec.get("settled")))
        return True

    def _is_bar_time(self, text: Any) -> bool:
        """True for a bar stamped exactly 09:30:00 ET of the tape's day."""
        stamp = _parse(text)
        return stamp is not None and stamp == datetime.combine(self._day, self.bar_et, tzinfo=_ET)

    def _add_bar(self, ticker: str, source: str, seen: Any, stamp: Any, bar: Mapping, settled: Optional[bool] = None) -> None:
        entry = {"from": source, "seen": seen, "t": stamp, **{k: bar.get(k) for k in _BAR_FIELDS}}
        if settled is not None:
            entry["settled"] = settled
        versions = self._bars.setdefault(str(ticker), [])
        last = next((v for v in reversed(versions) if v["from"] == source), None)
        if last is not None and _same(last, entry):
            return
        if len(versions) >= MAX_VERSIONS:
            versions[-1] = entry
        else:
            versions.append(entry)

    # ── what the route serves ──

    def view(self, now: datetime) -> dict:
        """The route's answer at `now` (an aware time). Reads memory only."""
        now_et = now.astimezone(_ET)
        today = now_et.date()
        with self._lock:
            current = self._day == today
            body = {
                "v": VERSION, "seq": self._seq, "now": _iso(now),
                "day": today.isoformat() if current else None,
                "tickers": list(self._tickers) if current else [],
                "reference": self._reference if current else None,
                "state": self._state(now_et, current),
                "snapshot": copy.deepcopy(self._snapshot) if current else None,
                "bars": copy.deepcopy(self._bars) if current else {},
                "poll_seconds": self._poll_seconds,
            }
        return body

    def _state(self, now_et: datetime, current: bool) -> dict:
        error = copy.deepcopy(self._error) if current else None
        if not self._on:
            return {"code": OFF, "why": self._off_why, "error": None}
        start = datetime.combine(now_et.date(), self.from_et, tzinfo=_ET)
        end = datetime.combine(now_et.date(), self.until_et, tzinfo=_ET)
        if now_et.weekday() >= 5:
            return {"code": OUTSIDE, "why": f"the recorder runs on weekdays only (today is {now_et:%A})",
                    "error": None}
        if current and self._stopped:
            return {"code": STOPPED, "why": f"the recorder stopped for the day ({self._stopped})", "error": error}
        if not start <= now_et < end:
            return {"code": OUTSIDE, "why": f"the recorder runs from {self.from_et:%H:%M} to "
                                            f"{self.until_et:%H:%M} ET", "error": error}
        if not current:
            return {"code": WAITING, "why": "no top picks for today yet", "error": None}
        return {"code": RECORDING, "why": "", "error": error}


def _same(a: Mapping, b: Mapping) -> bool:
    return all(a.get(k) == b.get(k) for k in ("t", "settled", *_BAR_FIELDS))


def _parse(text: Any) -> Optional[datetime]:
    try:
        stamp = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.utcoffset() is not None else None


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
