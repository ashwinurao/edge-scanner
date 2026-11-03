"""Alpaca without a websocket: ALPACA_STREAM=0 polls REST for closed 1-minute bars.

Alpaca allows one market-data websocket per account. On Ash's server another app (OpenVector)
holds it for live trading on the same keys, so the scanner must not open a second one. Instead it
asks the REST bars endpoint every POLL_SECONDS for the last few minutes of 1-minute bars for the
whole universe (one request per BATCH symbols) and hands each bar to the scanner once its minute
has closed. These are Alpaca's own bars, the same ones the stream sends, only a few seconds later.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

log = logging.getLogger(__name__)

POLL_SECONDS = 5.0      # one pass over the universe
SETTLE_SECONDS = 3.0    # a minute's bar is handed on this long after the minute closes
LOOKBACK_MINUTES = 3    # each request covers this much, so one failed poll loses nothing
MAX_GAP_MINUTES = 30    # after a longer outage, older missed bars are skipped
BATCH = 1000            # symbols per request: 1,000 x 3 bars stays inside one 10,000-bar page
_MINUTE = timedelta(minutes=1)


def alpaca_streaming() -> bool:
    """False when ALPACA_STREAM=0: the scanner then never opens an Alpaca websocket."""
    return os.environ.get("ALPACA_STREAM", "1").strip().lower() not in ("0", "false", "no", "off")


def closed_bars(df: pd.DataFrame, last: dict[str, datetime], floor: datetime,
                now: datetime, settle: float = SETTLE_SECONDS) -> list[dict]:
    """Bars in `df` (index symbol, timestamp) that closed at least `settle` seconds before `now`
    and are newer than the last one handed on for their symbol (or than `floor`). Updates `last`.
    Oldest first, so the scanner sees time move forward."""
    if df is None or df.empty or not isinstance(df.index, pd.MultiIndex):
        return []
    out = []
    cutoff = now - timedelta(seconds=settle)
    for (sym, ts), row in df.iterrows():
        ts = pd.Timestamp(ts)
        ts = (ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")).to_pydatetime()
        sym = str(sym)
        if ts + _MINUTE > cutoff or ts <= last.get(sym, floor):
            continue                      # still forming, or already handed on
        if any(pd.isna(row.get(c)) for c in ("open", "high", "low", "close", "volume")):
            continue
        out.append({"symbol": sym, "timestamp": ts, "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]), "volume": float(row["volume"])})
    out.sort(key=lambda b: (b["timestamp"], b["symbol"]))
    for b in out:
        last[b["symbol"]] = b["timestamp"]
    return out


def poll_minute_bars(fetch: Callable[[list[str], datetime, datetime], pd.DataFrame],
                     symbols: list[str], callback: Callable[[dict], None], stop: threading.Event,
                     every: float = POLL_SECONDS,
                     clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
    """Hand closed 1-minute bars to `callback` until `stop` is set. Blocks, like the stream.

    `fetch(symbols, start, end)` returns 1-minute bars indexed (symbol, timestamp). The first bar
    handed on is the one for the minute running at start, as the stream does: earlier bars come
    from the scanner's warmup."""
    start_now = clock()
    floor = start_now.replace(second=0, microsecond=0) - _MINUTE
    last: dict[str, datetime] = {}
    last_ok = start_now
    batches = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]
    log.info("Alpaca polling (ALPACA_STREAM=0): %d symbols in %d request(s) every %gs",
             len(symbols), len(batches), every)
    print(f"       Alpaca websocket off (ALPACA_STREAM=0): {len(symbols):,} symbols on 1-minute bars "
          f"polled from REST every {every:g}s.", flush=True)
    while True:
        now = clock()
        start = max(now - timedelta(minutes=MAX_GAP_MINUTES),
                    min(now - timedelta(minutes=LOOKBACK_MINUTES), last_ok - 2 * _MINUTE))
        frames, ok = [], True
        for group in batches:
            try:
                frames.append(fetch(group, start, now))
            except Exception as exc:
                ok = False
                log.warning("Alpaca bar poll failed for %d symbols: %s", len(group), str(exc)[:200])
        if ok:
            last_ok = now
        df = pd.concat([f for f in frames if f is not None and not f.empty]) if any(
            f is not None and not f.empty for f in frames) else pd.DataFrame()
        for bar in closed_bars(df, last, floor, now):
            try:
                callback(bar)
            except Exception as exc:
                log.error("Poll callback error for %s: %s", bar["symbol"], exc, exc_info=True)
        if stop.wait(every):
            return
