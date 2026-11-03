"""Daily context rolls forward in a running scanner (DAILY_REFRESH_ET), without a restart."""
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scanner import daily_refresh
from scanner.state import SymbolState
from tests.helpers import _daily

ET = ZoneInfo("America/New_York")


def _with_day(df, o, h, l, c, v):
    """`df` plus one more business day with these values."""
    ts = df.index[-1] + pd.offsets.BDay(1)
    row = pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c], "volume": [v]}, index=[ts])
    return pd.concat([df, row])


class _Profiles:
    def __init__(self): self.invalidated = 0
    def invalidate_members(self): self.invalidated += 1


class _Series:
    def __init__(self): self.seeded = []
    def seed_daily(self, d): self.seeded.append(len(d))


def _scanner():
    old = _daily(n=60, base=100.0, step=0.0, vol=1_000_000.0)       # flat: close 100 every day
    spy = _daily(n=60, base=400.0, step=0.0)
    st = SymbolState.from_history("AAPL", old, spy)
    st._cum_vol = 123.0                                              # intraday state of the session
    sc = SimpleNamespace(_states={"AAPL": st}, _spy_state=SymbolState.from_history("SPY", spy, spy),
                         _series={"AAPL": _Series()}, _profiles=_Profiles(), _sector_map={})
    return sc, old, spy


def test_apply_rolls_the_daily_context_forward_and_leaves_intraday_alone():
    sc, old, spy = _scanner()
    assert sc._states["AAPL"].prior_close == 100.0
    new = _with_day(old, o=101.0, h=110.0, l=99.0, c=108.0, v=3_000_000.0)
    new_spy = _with_day(spy, o=400.0, h=405.0, l=398.0, c=404.0, v=1.0)

    n = daily_refresh.apply(sc, new_spy, {"AAPL": new}, {}, {})

    st = sc._states["AAPL"]
    assert n == 1
    # The new day is the prior session now: hand-worked from the row added above.
    assert (st.prior_open, st.prior_high, st.prior_low, st.prior_close) == (101.0, 110.0, 99.0, 108.0)
    # 20-day ADV: 19 days of 1,000,000 and the new 3,000,000 -> 22,000,000 / 20.
    assert st.adv20 == pytest.approx(1_100_000.0)
    assert st.daily_highs_60d.iloc[-1] == 110.0 and len(st.daily_highs_60d) == 60
    assert sc._spy_state.prior_close == 404.0
    assert st._cum_vol == 123.0                                      # untouched
    assert sc._series["AAPL"].seeded == [61]                         # daily ring reseeded
    assert sc._profiles.invalidated == 1                             # membership re-resolved


def test_apply_keeps_old_values_for_a_symbol_without_new_history():
    sc, old, spy = _scanner()
    assert daily_refresh.apply(sc, spy, {}, {}, {}) == 0
    assert sc._states["AAPL"].prior_close == 100.0


def _history(close):
    old = _daily(n=60, base=100.0, step=0.0, vol=1_000_000.0)
    spy = _daily(n=60, base=400.0, step=0.0)
    return (_with_day(spy, 400.0, 401.0, 399.0, 400.0, 1.0),
            {"AAPL": _with_day(old, close, close, close, close, 1_000_000.0)}, {}, {})


def test_scheduled_refresh_runs_once_at_the_time():
    sc, _, _ = _scanner()
    now = [datetime(2026, 11, 2, 20, 57, tzinfo=ET)]                 # started Monday evening
    fetched = []

    def fetch():
        fetched.append(now[0])
        return _history(close=105.0)

    def wait(secs):
        now[0] += timedelta(seconds=secs)
        if now[0] > datetime(2026, 11, 3, 12, 0, tzinfo=ET):
            raise SystemExit                                          # end the test's timer thread
        return False

    r = daily_refresh.DailyRefresher(sc, fetch, threading.Lock(), (3, 30), clock=lambda: now[0], wait=wait)
    assert r.refreshed_for == date(2026, 11, 2)
    with pytest.raises(SystemExit):
        r._schedule()
    assert fetched == [datetime(2026, 11, 3, 3, 30, tzinfo=ET)]
    assert r.refreshed_for == date(2026, 11, 3)
    assert sc._states["AAPL"].prior_close == 105.0


def test_failed_fetch_keeps_old_values_and_is_retried():
    sc, _, _ = _scanner()
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit("Could not load SPY daily history")     # what the startup fetch does
        return _history(close=107.0)

    r = daily_refresh.DailyRefresher(sc, fetch, threading.Lock(), (3, 30),
                                     clock=lambda: datetime(2026, 11, 3, 3, 30, tzinfo=ET))
    r.refreshed_for = date(2026, 11, 2)
    assert r.refresh_now(date(2026, 11, 3)) is False
    assert sc._states["AAPL"].prior_close == 100.0
    assert r.refresh_now(date(2026, 11, 3)) is True
    assert sc._states["AAPL"].prior_close == 107.0
    assert r.refresh_now(date(2026, 11, 3)) is True and len(calls) == 2   # done for the day


def test_first_bar_of_an_unrefreshed_date_catches_up():
    sc, _, _ = _scanner()
    r = daily_refresh.DailyRefresher(sc, lambda: _history(close=109.0), threading.Lock(), (3, 30),
                                     clock=lambda: datetime(2026, 11, 2, 20, 57, tzinfo=ET))
    r.note_bar({"timestamp": pd.Timestamp("2026-11-02 20:59", tz=ET)})   # same date: nothing
    assert r.refreshed_for == date(2026, 11, 2)
    r.note_bar({"timestamp": pd.Timestamp("2026-11-03 04:00", tz=ET).tz_convert("UTC")})
    deadline = time.monotonic() + 5
    while r.refreshed_for != date(2026, 11, 3) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert r.refreshed_for == date(2026, 11, 3)
    assert sc._states["AAPL"].prior_close == 109.0


def test_catch_up_does_not_refetch_on_every_bar_while_failing():
    sc, _, _ = _scanner()
    calls = []

    def fetch():
        calls.append(1)
        raise RuntimeError("HTTP 500")

    r = daily_refresh.DailyRefresher(sc, fetch, threading.Lock(), (3, 30),
                                     clock=lambda: datetime(2026, 11, 2, 20, 57, tzinfo=ET))
    for m in range(50):
        r.note_bar({"timestamp": pd.Timestamp("2026-11-03 04:00", tz=ET) + pd.Timedelta(minutes=m)})
    time.sleep(0.2)
    assert len(calls) == 1


def test_parse_and_off():
    assert daily_refresh.parse_hhmm(None) == (3, 30)                  # on by default
    assert daily_refresh.parse_hhmm("04:15") == (4, 15)
    assert daily_refresh.parse_hhmm("off") is None
    with pytest.raises(ValueError):
        daily_refresh.parse_hhmm("25:00")
    sc, _, _ = _scanner()
    r = daily_refresh.DailyRefresher(sc, lambda: pytest.fail("fetched"), threading.Lock(), None)
    assert r.start() is None
    r.note_bar({"timestamp": pd.Timestamp("2030-01-01 04:00", tz=ET)})
