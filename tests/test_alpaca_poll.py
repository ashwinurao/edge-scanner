"""ALPACA_STREAM=0: closed 1-minute bars polled from REST, no websocket."""
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scanner.data import alpaca_poll

T0 = datetime(2026, 11, 3, 15, 0, 30, tzinfo=timezone.utc)      # 10:00:30 ET


def _bars(rows):
    """rows: (symbol, minute offset from 15:00 UTC, close)."""
    idx = pd.MultiIndex.from_tuples(
        [(s, pd.Timestamp("2026-11-03 15:00", tz="UTC") + pd.Timedelta(minutes=m)) for s, m, _ in rows],
        names=["symbol", "timestamp"])
    return pd.DataFrame({"open": [c for *_, c in rows], "high": [c + 1 for *_, c in rows],
                         "low": [c - 1 for *_, c in rows], "close": [c for *_, c in rows],
                         "volume": [100.0] * len(rows)}, index=idx)


def test_only_closed_bars_newer_than_the_last_are_handed_on():
    last, floor = {}, T0.replace(second=0) - timedelta(minutes=1)          # 14:59
    df = _bars([("AAPL", -1, 1.0), ("AAPL", 0, 2.0), ("MSFT", 0, 3.0), ("AAPL", 1, 4.0)])
    # 15:01:02: the 15:00 bar closed 2 s ago, inside the settle time; nothing yet.
    assert alpaca_poll.closed_bars(df, last, floor, T0.replace(minute=1, second=2)) == []
    # 15:01:04: the 15:00 bars are final. 14:59 is before the start, 15:01 is still forming.
    got = alpaca_poll.closed_bars(df, last, floor, T0.replace(minute=1, second=4))
    assert [(b["symbol"], b["close"]) for b in got] == [("AAPL", 2.0), ("MSFT", 3.0)]
    assert got[0]["timestamp"] == datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    assert (got[0]["high"], got[0]["low"], got[0]["volume"]) == (3.0, 1.0, 100.0)
    # The next poll sees the same bars again and hands on only the new one.
    got = alpaca_poll.closed_bars(df, last, floor, T0.replace(minute=2, second=5))
    assert [(b["symbol"], b["close"]) for b in got] == [("AAPL", 4.0)]


def test_bars_come_oldest_first_across_symbols():
    df = _bars([("ZZZ", 0, 1.0), ("AAA", 1, 2.0), ("AAA", 0, 3.0)])
    got = alpaca_poll.closed_bars(df, {}, T0 - timedelta(minutes=5), T0 + timedelta(minutes=5))
    assert [(b["symbol"], b["close"]) for b in got] == [("AAA", 3.0), ("ZZZ", 1.0), ("AAA", 2.0)]


class _Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def test_poller_survives_a_failed_poll_and_catches_up(monkeypatch):
    clock, calls, got = _Clock(T0), [], []
    data = _bars([("AAPL", 0, 2.0), ("AAPL", 1, 4.0)])
    fail = {"on": False}
    stop = threading.Event()
    ticks = iter([T0.replace(minute=1, second=4), T0.replace(minute=2, second=4), None])

    def fetch(symbols, start, end):
        calls.append((list(symbols), start, end))
        if fail["on"]:
            raise RuntimeError("HTTP 500")
        return data

    def wait(_secs):
        nxt = next(ticks)
        if nxt is None:
            return True
        clock.t = nxt
        fail["on"] = nxt.minute == 1           # the poll at 15:01:04 fails
        return False

    monkeypatch.setattr(stop, "wait", wait)
    alpaca_poll.poll_minute_bars(fetch, ["AAPL"], got.append, stop, clock=clock)
    # 15:00:30 nothing closed; 15:01:04 failed; 15:02:04 hands on both bars, oldest first.
    assert [b["close"] for b in got] == [2.0, 4.0]
    assert calls[-1][1] <= datetime(2026, 11, 3, 14, 59, tzinfo=timezone.utc)   # window reaches back


def test_universe_is_requested_in_batches(monkeypatch):
    monkeypatch.setattr(alpaca_poll, "BATCH", 2)
    sizes = []
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda _s: True)
    alpaca_poll.poll_minute_bars(lambda s, a, b: sizes.append(len(s)) or pd.DataFrame(),
                                 ["A", "B", "C", "D", "E"], lambda bar: None, stop, clock=lambda: T0)
    assert sizes == [2, 2, 1]


def test_feed_with_stream_off_never_opens_a_websocket(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_STREAM", "0")
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    import alpaca.data.live
    from scanner.data.alpaca import AlpacaFeed

    monkeypatch.setattr(alpaca.data.live, "StockDataStream",
                        lambda *a, **k: pytest.fail("opened an Alpaca websocket"))
    seen = {}

    def fake_poll(fetch, symbols, callback, stop, **_):
        seen.update(symbols=symbols, stop=stop)

    monkeypatch.setattr("scanner.data.alpaca_poll.poll_minute_bars", fake_poll)
    feed = AlpacaFeed(cache_dir=tmp_path / "d", intraday_cache_dir=tmp_path / "i")
    feed.subscribe_minute_bars(["SPY", "AAPL"], lambda bar: None)
    assert seen["symbols"] == ["SPY", "AAPL"]
    feed.stop_stream()
    assert seen["stop"].is_set()


def test_streaming_stays_the_default(monkeypatch):
    monkeypatch.delenv("ALPACA_STREAM", raising=False)
    assert alpaca_poll.alpaca_streaming()
    monkeypatch.setenv("ALPACA_STREAM", "0")
    assert not alpaca_poll.alpaca_streaming()
