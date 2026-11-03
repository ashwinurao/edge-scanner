"""Roll the daily context forward inside a running scanner, before each session.

The daily context of every symbol (prior close/high/low/open, 20-day ADV, daily ATR, SMAs, daily
EMA, RRS vs SPY and sector, chart quality, the 60-day highs/lows, the RVOL volume profile and the
daily candle ring) used to be built once, at startup. A scanner left running overnight then
measured the new session against old numbers: on Unraid on 2026-11-03 the pre-market lists showed
change against Friday's close on a Tuesday.

DailyRefresher re-runs the startup's own history fetches (cache-aware, so only the new day is
downloaded) at DAILY_REFRESH_ET (03:30 New York time by default, before the 04:00 pre-market)
and, as a safety net, when the first bar of a date it has not refreshed for arrives. The fetch runs
outside the bar lock; only the swap of the new values into the live states holds it, so bars keep
flowing. Intraday state is not touched here: the session rollover in scanner/live_scanner.py
resets it. DAILY_REFRESH_ET=off turns it off.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from scanner.state import SymbolState

log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
RETRY_SECONDS = 600.0      # after a failed refresh
MAX_ATTEMPTS = 6           # per day: an hour of retries, then wait for the next day

# Everything SymbolState.from_history derives from history. Intraday fields are left alone.
DAILY_FIELDS = ("prior_high", "prior_low", "prior_close", "prior_open", "adv20", "atr_d1", "rrs_d1",
                "rrs_sector_d1", "sma_50", "sma_100", "sma_200", "ema_8_d1", "chart_quality",
                "volume_profile", "daily_highs_60d", "daily_lows_60d")

# (spy_daily, symbol_daily, sector_daily, bars_5m), as the startup fetches them
History = tuple[pd.DataFrame, dict, dict, dict]


def parse_hhmm(raw: Optional[str], default: str = "03:30") -> Optional[tuple[int, int]]:
    """'03:30' -> (3, 30); 'off', '0' -> None; unset -> the default. ValueError on anything else."""
    v = (default if raw is None else raw).strip().lower()
    if v in ("", "0", "off", "no", "false"):
        return None
    hh, _, mm = v.partition(":")
    h, m = int(hh), int(mm)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"DAILY_REFRESH_ET={raw!r}: expected HH:MM or off")
    return h, m


def next_time(now: datetime, at: tuple[int, int]) -> datetime:
    """The next HH:MM New York time strictly after `now`."""
    local = now.astimezone(_ET)
    for add in range(3):              # three days covers a DST change on the way
        d = local.date() + timedelta(days=add)
        cand = datetime(d.year, d.month, d.day, at[0], at[1], tzinfo=_ET)
        if cand > local:
            return cand
    raise AssertionError("unreachable")


def apply(scanner, spy_daily: pd.DataFrame, symbol_daily: dict, sector_daily: dict, bars_5m: dict) -> int:
    """Copy freshly built daily context onto the scanner's live states. Returns how many symbols
    were updated. A symbol whose new history fails keeps its old values, as warmup skips it."""
    sector_map = getattr(scanner, "_sector_map", {}) or {}
    updated = 0
    spy_state = getattr(scanner, "_spy_state", None)
    if spy_state is not None and spy_daily is not None and not spy_daily.empty:
        _copy(SymbolState.from_history("SPY", spy_daily, spy_daily), spy_state)
    for sym, live in scanner._states.items():
        daily = symbol_daily.get(sym)
        if daily is None or daily.empty:
            continue
        sec = sector_map.get(sym)
        try:
            fresh = SymbolState.from_history(sym, daily, spy_daily,
                                             sector_daily=(sector_daily or {}).get(sec) if sec else None,
                                             bars_5m_history=(bars_5m or {}).get(sym))
        except Exception as exc:
            log.warning("Daily refresh failed for %s (%s); keeping its old values", sym, exc)
            continue
        _copy(fresh, live)
        series = getattr(scanner, "_series", {}).get(sym)
        if series is not None:
            series.seed_daily(daily)      # daily EMAs and 52-week levels
        updated += 1
    # Universe-filter membership was resolved against the old ADV / ATR / prior close.
    profiles = getattr(scanner, "_profiles", None)
    if profiles is not None:
        profiles.invalidate_members()
    return updated


def _copy(src: SymbolState, dst: SymbolState) -> None:
    for f in DAILY_FIELDS:
        setattr(dst, f, getattr(src, f))


def _et_day(ts) -> Optional[date]:
    try:
        t = pd.Timestamp(ts)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        return t.tz_convert(_ET).date()
    except Exception:
        return None


class DailyRefresher:
    """Refreshes the daily context once per New York date: on schedule, or on the first bar of a
    date not yet refreshed for. `fetch` returns History; `lock` is held by the bar handler."""

    def __init__(self, scanner, fetch: Callable[[], History], lock: threading.Lock,
                 at: Optional[tuple[int, int]], refreshed_for: Optional[date] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(_ET),
                 wait: Optional[Callable[[float], bool]] = None) -> None:
        self.scanner, self.fetch, self.lock, self.at = scanner, fetch, lock, at
        self.refreshed_for = refreshed_for or clock().astimezone(_ET).date()   # the startup's warmup
        self.clock = clock
        self.wait = wait or threading.Event().wait
        self._busy = threading.Lock()
        self._attempts: dict[date, int] = {}
        self._mono = time.monotonic
        self._last_try = float("-inf")

    # ── triggers ────────────────────────────────────────────────────────────

    def start(self) -> Optional[threading.Thread]:
        if self.at is None:
            return None
        print(f"       Daily refresh: prior closes and daily levels roll forward at "
              f"{self.at[0]:02d}:{self.at[1]:02d} ET each day (DAILY_REFRESH_ET)", flush=True)
        t = threading.Thread(target=self._schedule, daemon=True, name="daily-refresh")
        t.start()
        return t

    def note_bar(self, bar: dict) -> None:
        """Called for every bar: a bar of a date not refreshed for starts a refresh in the
        background (the scheduled one was missed, for example the server was asleep)."""
        if self.at is None:
            return
        day = _et_day(bar.get("timestamp"))
        if day is None or day <= self.refreshed_for or self._busy.locked():
            return
        if self._attempts.get(day, 0) >= MAX_ATTEMPTS or self._mono() - self._last_try < RETRY_SECONDS:
            return                            # failing today: do not refetch on every bar
        self._last_try = self._mono()
        threading.Thread(target=self.refresh_now, args=(day,), daemon=True,
                         name="daily-refresh-catchup").start()

    def _schedule(self) -> None:
        while True:
            target = next_time(self.clock(), self.at)
            while (left := (target - self.clock()).total_seconds()) > 0:
                self.wait(min(left, 300.0))      # wake at least every 5 min, so clock changes are noticed
            day = target.date()
            while not self.refresh_now(day) and self._attempts.get(day, 0) < MAX_ATTEMPTS:
                self.wait(RETRY_SECONDS)

    # ── the refresh ─────────────────────────────────────────────────────────

    def refresh_now(self, day: date) -> bool:
        """Fetch and apply for `day`. True when done (or already done for that day)."""
        if not self._busy.acquire(blocking=False):
            return False
        try:
            if self.refreshed_for >= day:
                return True
            self._attempts[day] = self._attempts.get(day, 0) + 1
            try:
                spy_daily, symbol_daily, sector_daily, bars_5m = self.fetch()
            except BaseException as exc:      # the startup fetch calls sys.exit when SPY is missing
                log.warning("Daily refresh for %s: history fetch failed (%s)", day, exc)
                return False
            if spy_daily is None or spy_daily.empty:
                log.warning("Daily refresh for %s: no SPY history; keeping the old values", day)
                return False
            with self.lock:
                n = apply(self.scanner, spy_daily, symbol_daily, sector_daily, bars_5m)
            self.refreshed_for = day
            print(f"       Daily refresh {day}: {n} symbols rolled forward "
                  f"(prior session {_et_day(spy_daily.index[-1])})", flush=True)
            log.info("Daily refresh for %s: %d symbols", day, n)
            return True
        finally:
            self._busy.release()
