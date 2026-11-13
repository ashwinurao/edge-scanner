"""Replay today's 1-min bars into the scanner at startup (scripts/run_live.py step 5c).

A start after the open needs each stock's VWAP, high/low, volume, 5-min bars and
relative strength vs SPY as if it had run all day. This replays the day's bars
into every state and candle ring, the same as live, but without the setups.

Speed matters here: the dashboard only starts after this. Rebuilding the 5-min
RRS in pandas on every replayed bar made a restart late in the day take 12-16
minutes on the home server (deploy logs, 2026-11-12). The states now compute it
once at the end (SymbolState.begin_seed / end_seed), with the same result.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

_OHLCV = ("open", "high", "low", "close", "volume")


def bar_dicts(symbol: str, bars: pd.DataFrame) -> list[dict]:
    """One bar dict per row, in the shape the live feed delivers."""
    if bars is None or bars.empty:
        return []
    cols = bars[list(_OHLCV)]
    # itertuples is ~10x faster than iterrows over a day of bars for 700 stocks.
    return [
        {"symbol": symbol, "timestamp": ts, "open": float(o), "high": float(h),
         "low": float(lo), "close": float(c), "volume": float(v)}
        for ts, o, h, lo, c, v in cols.itertuples(index=True, name=None)
    ]


def spy_bar_map(today_spy: Optional[pd.DataFrame]) -> dict:
    """{timestamp: SPY bar dict}, so each stock bar gets the SPY bar of its minute."""
    return {b["timestamp"]: b for b in bar_dicts("SPY", today_spy)}


def replay_today(scanner, today_bars: dict[str, pd.DataFrame], spy_by_ts: dict,
                 seeded: Optional[set[str]] = None) -> set[str]:
    """Replay today's bars into every loaded stock. Returns the symbols seeded.

    Pass `seeded` to have it filled as stocks finish: if one raises, the caller
    still knows which were seeded (the quote catch-up then skips them).

    Each state's intraday values are cleared first, then every bar goes to the
    state and to the candle rings in live order. No setup sees these bars, so
    nothing that happened before the start can alert.
    """
    if seeded is None:
        seeded = set()
    for sym, sym_bars in today_bars.items():
        state = scanner._states.get(sym)
        if state is None or sym_bars is None or sym_bars.empty:
            continue
        state._reset_intraday()  # clear any partial state from warmup
        state.begin_seed()
        try:
            for bar in bar_dicts(sym, sym_bars):
                state.on_bar(bar, spy_by_ts.get(bar["timestamp"]))
                # The candle rings too, in the same order as live. They hold the
                # day's high and low, the opening-range candle and today's candles
                # for every composed trigger. Left empty, a start after the open
                # made each new local high a "new high of day" for the rest of the
                # session and left the opening range undefined.
                scanner._advance_series(state, bar)
        finally:
            state.end_seed()
        seeded.add(sym)
    return seeded
