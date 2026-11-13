"""Startup replay of today's bars (scanner/startup_seed.py) and the M5 RRS it seeds.

A restart late in the day took 12-16 minutes on the home server because the
5-min RRS was rebuilt in pandas on every replayed 1-min bar. These tests pin
that it is now computed only when its inputs change, once per stock during the
startup replay, and that the numbers are the same as before.
"""
import pandas as pd
import pytest

import scanner.state as state_mod
from scanner import startup_seed
from scanner.indicators.rrs import M5_LENGTH, rrs_raw
from scanner.live_scanner import LiveScanner
from scanner.state import SymbolState, _deque_to_df


# ── Hand-built bars ───────────────────────────────────────────────────────────

def _daily(base: float, n: int = 260) -> pd.DataFrame:
    idx = pd.date_range("2025-10-01", periods=n, freq="B", tz="UTC")
    c = pd.Series([base + (i % 7) * 0.3 + i * 0.02 for i in range(n)], index=idx)
    return pd.DataFrame({"open": c, "high": c + 1.0, "low": c - 1.0, "close": c,
                         "volume": 1_000_000.0}, index=idx)


def _minutes(start_et: str, end_et: str) -> pd.DatetimeIndex:
    """1-min bar times from start to end inclusive, ET wall clock, as UTC."""
    day = "2026-11-12 "
    return pd.date_range(pd.Timestamp(day + start_et, tz="America/New_York"),
                         pd.Timestamp(day + end_et, tz="America/New_York"),
                         freq="1min").tz_convert("UTC")


def _bars(idx: pd.DatetimeIndex, base: float, step: float) -> pd.DataFrame:
    # A saw-tooth with drift: 5-min closes move both ways, so the RRS is not trivial.
    close = [base + step * i + (0.4 if i % 3 == 0 else -0.25) for i in range(len(idx))]
    return pd.DataFrame({"open": close, "high": [c + 0.1 for c in close],
                         "low": [c - 0.12 for c in close], "close": close,
                         "volume": [1000.0 + 10 * (i % 11) for i in range(len(idx))]}, index=idx)


def _state() -> SymbolState:
    return SymbolState.from_history("AAA", _daily(50.0), _daily(400.0))


def _count_rrs(monkeypatch) -> list:
    calls = []
    real = state_mod.compute_rrs_raw

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(state_mod, "compute_rrs_raw", counting)
    return calls


def _expected_rrs(st: SymbolState):
    """The M5 RRS straight from the state's completed 5-min bars (the old per-bar result)."""
    if len(st._stock_5m) < M5_LENGTH or len(st._spy_5m) < M5_LENGTH:
        return None
    series = rrs_raw(_deque_to_df(st._stock_5m), _deque_to_df(st._spy_5m), M5_LENGTH)
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


# ── SymbolState: when the M5 RRS is computed ──────────────────────────────────

def test_live_rrs_m5_recomputed_only_when_a_5m_bar_completes(monkeypatch):
    idx = _minutes("09:30", "12:29")             # 180 bars, 36 five-min slots
    stock, spy = startup_seed.bar_dicts("AAA", _bars(idx, 50.0, 0.01)), \
        startup_seed.bar_dicts("SPY", _bars(idx, 400.0, -0.02))
    calls = _count_rrs(monkeypatch)
    st = _state()
    for b, s in zip(stock, spy):
        st.on_bar(b, s)
        # Same value as rebuilding it after every bar, which is what it did before.
        assert st.rrs_m5 == _expected_rrs(st)
    assert st.rrs_m5 is not None
    # 35 completed slots, the first 11 too few for the RRS: 24 computes, not one per bar.
    assert len(calls) == 35 - (M5_LENGTH - 1)


def test_seed_computes_rrs_m5_once_with_the_live_result(monkeypatch):
    idx = _minutes("04:00", "17:15")             # pre-market, the session and after hours
    stock, spy = startup_seed.bar_dicts("AAA", _bars(idx, 50.0, 0.003)), \
        startup_seed.bar_dicts("SPY", _bars(idx, 400.0, -0.004))

    live = _state()
    for b, s in zip(stock, spy):
        live.on_bar(b, s)

    calls = _count_rrs(monkeypatch)
    seeded = _state()
    seeded.begin_seed()
    for b, s in zip(stock, spy):
        seeded.on_bar(b, s)
    assert calls == []                           # nothing during the replay
    assert seeded.rrs_m5 is None
    seeded.end_seed()
    assert len(calls) == 1

    assert live.rrs_m5 is not None
    assert seeded.rrs_m5 == live.rrs_m5
    assert seeded.rrs_m5_tail == live.rrs_m5_tail
    assert seeded.vwap == live.vwap and seeded.ema_9 == live.ema_9
    assert seeded.open_10am == live.open_10am == stock[idx.get_loc(
        pd.Timestamp("2026-11-12 10:00", tz="America/New_York"))]["open"]


def test_seed_leaves_rrs_m5_empty_before_an_hour_of_5m_bars(monkeypatch):
    # 09:30-10:24 completes 10 five-min bars: too few for M5 RRS, so none after seeding.
    idx = _minutes("09:30", "10:24")
    calls = _count_rrs(monkeypatch)
    st = _state()
    st.begin_seed()
    for b, s in zip(startup_seed.bar_dicts("AAA", _bars(idx, 50.0, 0.01)),
                    startup_seed.bar_dicts("SPY", _bars(idx, 400.0, 0.01))):
        st.on_bar(b, s)
    st.end_seed()
    assert st.rrs_m5 is None and st.rrs_m5_tail == []
    assert calls == []


def test_seed_without_spy_bars_never_computes_rrs_m5(monkeypatch):
    idx = _minutes("09:30", "12:29")
    calls = _count_rrs(monkeypatch)
    st = _state()
    st.begin_seed()
    for b in startup_seed.bar_dicts("AAA", _bars(idx, 50.0, 0.01)):
        st.on_bar(b, None)
    st.end_seed()
    assert st.rrs_m5 is None
    assert calls == []


def test_reset_intraday_ends_seeding():
    st = _state()
    st.begin_seed()
    st._reset_intraday()
    assert st._seeding is False and st._rrs_due is False


# ── replay_today: the startup loop in scripts/run_live.py ─────────────────────

def _scanner(symbols):
    sc = LiveScanner(symbols, feed=None)
    sc.warmup(_daily(400.0), {s: _daily(50.0 + i) for i, s in enumerate(symbols)})
    return sc


def _old_replay(sc, today, spy_df):
    """The loop scripts/run_live.py ran before this change, verbatim in effect."""
    spy_map = {ts: {"symbol": "SPY", "timestamp": ts, **{k: float(r[k]) for k in
               ("open", "high", "low", "close", "volume")}} for ts, r in spy_df.iterrows()}
    for sym, sym_bars in today.items():
        st = sc._states.get(sym)
        if st is None or sym_bars.empty:
            continue
        st._reset_intraday()
        for ts, row in sym_bars.iterrows():
            bar = {"symbol": sym, "timestamp": ts, **{k: float(row[k]) for k in
                   ("open", "high", "low", "close", "volume")}}
            st.on_bar(bar, spy_map.get(ts))
            sc._advance_series(st, bar)


def test_replay_today_gives_the_same_states_and_candles_as_the_old_loop():
    idx = _minutes("04:00", "13:45")
    spy_df = _bars(idx, 400.0, -0.004)
    today = {"AAA": _bars(idx, 50.0, 0.004), "BBB": _bars(idx[300:], 70.0, -0.006),
             "CCC": _bars(idx[:0], 1.0, 0.0),            # no bars today: not seeded
             "ZZZ": _bars(idx, 9.0, 0.0)}                # not in the universe: ignored
    old, new = _scanner(["AAA", "BBB", "CCC"]), _scanner(["AAA", "BBB", "CCC"])
    _old_replay(old, today, spy_df)
    seeded = startup_seed.replay_today(new, today, startup_seed.spy_bar_map(spy_df))

    assert seeded == {"AAA", "BBB"}
    for sym in ("AAA", "BBB"):
        a, b = old._states[sym], new._states[sym]
        assert a.rrs_m5 is not None
        for prop in ("rrs_m5", "rrs_m5_tail", "vwap", "session_volume", "high_of_day", "low_of_day",
                     "ema_3", "ema_9", "ema_8", "ema_21", "pm_high", "pm_low", "pm_vol",
                     "session_open", "open_10am", "last_1m", "mom_15m_pct"):
            assert getattr(a, prop) == getattr(b, prop), (sym, prop)
        assert list(a._stock_5m) == list(b._stock_5m) and list(a._spy_5m) == list(b._spy_5m)
        sa, sb = old._series[sym], new._series[sym]
        assert (sa.session_date, sa.day_high, sa.day_low, sa.ext_high, sa.ext_low) == \
               (sb.session_date, sb.day_high, sb.day_low, sb.ext_high, sb.ext_low)
        assert new._states[sym]._seeding is False


def test_replay_today_keeps_progress_when_a_stock_fails(monkeypatch):
    idx = _minutes("09:30", "10:30")
    today = {"AAA": _bars(idx, 50.0, 0.01), "BBB": _bars(idx, 60.0, 0.01)}
    sc = _scanner(["AAA", "BBB"])
    bad = sc._states["BBB"]

    def boom(bar, spy_bar=None):
        raise RuntimeError("bad bar")

    monkeypatch.setattr(bad, "on_bar", boom)
    seeded: set[str] = set()
    with pytest.raises(RuntimeError):
        startup_seed.replay_today(sc, today, startup_seed.spy_bar_map(_bars(idx, 400.0, 0.0)), seeded)
    # The caller (run_live's quote catch-up) must not overwrite the stock that did seed.
    assert seeded == {"AAA"}
    assert bad._seeding is False


def test_spy_bar_map_handles_no_spy_bars():
    assert startup_seed.spy_bar_map(pd.DataFrame()) == {}
    assert startup_seed.spy_bar_map(None) == {}
