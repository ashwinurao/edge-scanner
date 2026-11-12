"""SPY RV20: how much SPY has moved day to day over the last 20 sessions, for the top bar.

Fork-owned (Ash, project thread, 2026-11-12). Display only: no setup, gate or alert reads it.

RV20 is the sample standard deviation of SPY's last 20 close-to-close simple returns, annualized
with sqrt(252) and shown in percent. That is the same definition as the "SPY RV20" in
OpenVector's header, and the one the realized-volatility study used. It needs 21 completed daily
closes. The closes come from the scanner's own SPY daily history: loaded at startup, and rolled
forward by scanner/daily_refresh.py before each session (03:30 ET), so the value is always "through the last completed session".

CALM_MAX comes from the study "Realized volatility and opening drives (2026)" (2026-11-11) and its
out-of-sample check on 2023-2025 (both in the project's research files, not in this repo):
on days whose prior-session SPY RV20 was at or below 10.5, OV's top 4 lost their OR1/OR2 edge.
The same data shows no middle band: 2026 days with RV20 10.5-12 did as well as 12-15, so the badge
has two colours, not three.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")

WINDOW = 20           # returns, so WINDOW + 1 closes
ANNUALIZE = 252       # trading days a year
CALM_MAX = 10.5       # at or below: a calm SPY day (see the module docstring)


def rv20(closes: Sequence[float]) -> Optional[float]:
    """Annualized RV of the last 20 close-to-close returns, in percent. None with fewer than 21
    usable closes, or a close that is not a positive number (a bad bar must not look calm)."""
    tail = [float(c) for c in list(closes)[-(WINDOW + 1):]]
    if len(tail) < WINDOW + 1 or any(not math.isfinite(c) or c <= 0 for c in tail):
        return None
    rets = [b / a - 1.0 for a, b in zip(tail, tail[1:])]
    mean = sum(rets) / WINDOW
    var = sum((r - mean) ** 2 for r in rets) / (WINDOW - 1)    # sample variance, as pandas .std()
    return math.sqrt(var) * math.sqrt(ANNUALIZE) * 100.0


def reading(value: Optional[float], through: Optional[str]) -> Optional[dict]:
    """The top bar's payload: the value, the last session it includes, and calm or not."""
    if value is None:
        return None
    return {"rv20": round(value, 2), "through": through, "calm": value <= CALM_MAX,
            "calm_max": CALM_MAX}


def from_daily(spy_daily: Optional[pd.DataFrame]) -> Optional[dict]:
    """The reading from SPY's daily bars (UTC index, one row per session, as the feeds return
    them). None when there is not enough history."""
    if spy_daily is None or spy_daily.empty or "close" not in spy_daily:
        return None
    closes = spy_daily["close"].dropna()
    if closes.empty:
        return None
    return reading(rv20(closes.tolist()), _et_date(closes.index[-1]))


def _et_date(ts) -> Optional[str]:
    try:
        t = pd.Timestamp(ts)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        return t.tz_convert(_ET).date().isoformat()
    except Exception:
        return None
