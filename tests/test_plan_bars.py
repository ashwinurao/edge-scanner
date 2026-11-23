"""GET /api/v2/ov-picks/plan-bars (scanner/plan_bars.py): Edge Trader's Opening plan charts from
Alpaca, cached 2 s, with a back-off after a failure (Ash, project thread, 2026-11-23 20:18:45 UTC).

Driven with fake bars and trades requests, a fake clock and a fake monotonic clock. Tickers and
prices are made up. 2026-11-05 is a Thursday in EST, so 09:30 ET is 14:30 UTC. Nothing here
reaches the network or sleeps.
"""
import socket
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scanner import plan_bars as pb

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("a plan bars test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _et(text):
    return datetime.fromisoformat(f"2026-11-05 {text}").replace(tzinfo=ET)


def _df(rows):
    """rows: (symbol, ET minute "HH:MM", open, high, low, close, volume)."""
    idx = pd.MultiIndex.from_tuples([(s, pd.Timestamp(_et(m)).tz_convert("UTC")) for s, m, *_ in rows],
                                    names=["symbol", "timestamp"])
    return pd.DataFrame([dict(open=o, high=h, low=l, close=c, volume=v) for _s, _m, o, h, l, c, v in rows], index=idx)


class Rig:
    def __init__(self, now="09:45:10", rows=(), trades=None):
        self.now = _et(now)
        self.mono = 1000.0
        self.rows = list(rows)
        self.trades = trades or {}
        self.bar_calls, self.trade_calls = [], []
        self.fail = None
        self.plan = pb.PlanBars(self._bars, self._trades, clock=lambda: self.now, mono=lambda: self.mono)

    def _bars(self, names, start, end):
        self.bar_calls.append((tuple(names), start, end))
        if self.fail:
            raise self.fail
        return _df([r for r in self.rows if r[0] in names])

    def _trades(self, names):
        self.trade_calls.append(tuple(names))
        return {t: v for t, v in self.trades.items() if t in names}

    def step(self, seconds):
        self.mono += seconds
        self.now += timedelta(seconds=seconds)


def _t(et):
    return int(_et(et).timestamp())


def test_bars_from_0830_to_1100_only_and_bad_rows_left_out():
    rig = Rig(rows=[("AAA", "08:29", 10, 10.2, 9.9, 10.1, 100),      # before the window
                    ("AAA", "08:30", 10, 10.2, 9.9, 10.1, 100),
                    ("AAA", "08:31", 10.1, 10.0, 9.9, 10.05, 50),    # high below open: not a bar
                    ("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900),
                    ("BBB", "09:00", 20, 20.5, 19.5, 20.1, 10)])
    body = rig.plan.read(["aaa", "BBB"])
    assert body["error"] == ""
    assert [b["t"] for b in body["bars"]["AAA"]] == [_t("08:30"), _t("09:44")]
    assert body["bars"]["AAA"][0] == {"t": _t("08:30"), "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
                                      "volume": 100.0}
    assert [b["t"] for b in body["bars"]["BBB"]] == [_t("09:00")]
    names, start, end = rig.bar_calls[0]
    assert names == ("AAA", "BBB")
    assert start == _et("08:30:00") and end == _et("09:46:10")         # now + 1 minute


def test_forming_minute_from_the_latest_trade():
    rig = Rig(now="09:45:10", rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)],
              trades={"AAA": (10.4, _et("09:45:08"))})
    bars = rig.plan.read(["AAA"])["bars"]["AAA"]
    assert bars[-1] == {"t": _t("09:45"), "open": 10.4, "high": 10.4, "low": 10.4, "close": 10.4,
                        "volume": 0.0, "forming": True}
    assert "forming" not in bars[0]


def test_alpacas_forming_bar_is_widened_by_the_trade():
    rig = Rig(now="09:45:10", rows=[("AAA", "09:45", 10.2, 10.3, 10.1, 10.25, 40)],
              trades={"AAA": (10.05, _et("09:45:09"))})
    bar = rig.plan.read(["AAA"])["bars"]["AAA"][-1]
    assert bar == {"t": _t("09:45"), "open": 10.2, "high": 10.3, "low": 10.05, "close": 10.05,
                   "volume": 40.0, "forming": True}


def test_a_trade_from_an_earlier_minute_adds_no_bar():
    # Where it must NOT fire: a quiet name's last trade is a minute old.
    rig = Rig(now="09:45:10", rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)],
              trades={"AAA": (10.5, _et("09:44:59"))})
    bars = rig.plan.read(["AAA"])["bars"]["AAA"]
    assert [b["t"] for b in bars] == [_t("09:44")] and bars[0]["high"] == 10.3


def test_cached_for_two_seconds_then_read_again():
    rig = Rig(rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)])
    rig.plan.read(["AAA"])
    rig.step(1.9)
    body = rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == 1 and body["age_s"] == 1.9
    rig.step(0.1)
    rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == 2 and len(rig.trade_calls) == 2


def test_after_1100_reads_once_a_minute_and_asks_no_trades():
    rig = Rig(now="11:05:00", rows=[("AAA", "10:59", 10.1, 10.3, 10.0, 10.2, 900)])
    rig.plan.read(["AAA"])
    rig.step(30)
    rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == 1 and rig.trade_calls == []
    rig.step(30)
    rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == 2


def test_failure_backs_off_and_keeps_the_last_bars():
    rig = Rig(rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)])
    rig.plan.read(["AAA"])
    rig.fail = RuntimeError("HTTP 500")
    rig.step(2)
    body = rig.plan.read(["AAA"])
    assert "Alpaca's bars request failed (RuntimeError)" in body["error"]
    assert [b["t"] for b in body["bars"]["AAA"]] == [_t("09:44")] and body["age_s"] == 2.0
    calls = len(rig.bar_calls)
    for wait in (4.9, ):                       # 5 s after the first failure, not 2
        rig.step(wait)
        rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == calls
    rig.step(0.1)
    rig.plan.read(["AAA"])                     # second failure: the next wait is 10 s
    assert len(rig.bar_calls) == calls + 1
    rig.step(9.9)
    rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == calls + 1
    rig.fail = None
    rig.step(0.1)
    body = rig.plan.read(["AAA"])
    assert body["error"] == "" and body["age_s"] == 0.0 and len(rig.bar_calls) == calls + 2
    rig.step(2)
    rig.plan.read(["AAA"])                     # healthy again: back to 2 s
    assert len(rig.bar_calls) == calls + 3


def test_failing_trades_still_serve_the_bars():
    rig = Rig(rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)])
    rig.plan._trades = lambda names: (_ for _ in ()).throw(TimeoutError("slow"))
    body = rig.plan.read(["AAA"])
    assert body["error"] == "" and [b["t"] for b in body["bars"]["AAA"]] == [_t("09:44")]


def test_a_read_in_flight_serves_the_cache_without_waiting():
    rig = Rig(rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)])
    rig.plan.read(["AAA"])
    rig.step(2)
    rig.plan._busy = True                      # another request is reading Alpaca now
    body = rig.plan.read(["AAA"])
    assert len(rig.bar_calls) == 1 and body["age_s"] == 2.0


def test_no_names_no_request_and_at_most_eight():
    rig = Rig()
    body = rig.plan.read([""])
    assert rig.bar_calls == [] and body["bars"] == {}
    rig.plan.read([f"T{i}" for i in range(12)])
    assert len(rig.bar_calls[0][0]) == pb.MAX_TICKERS


def test_without_an_alpaca_client_it_says_so():
    plan = pb.PlanBars(None, None, clock=lambda: _et("09:45:00"))
    body = plan.read(["AAA"])
    assert "Alpaca's bars request failed" in body["error"] and body["bars"] == {"AAA": []}


def test_route_answers_no_store():
    rig = Rig(rows=[("AAA", "09:44", 10.1, 10.3, 10.0, 10.2, 900)])
    app = FastAPI()
    pb.register(app, object(), source=rig.plan)
    r = TestClient(app).get(pb.ROUTE, params={"tickers": "AAA"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["bars"]["AAA"][0]["t"] == _t("09:44")


def test_alpaca_trades_reads_latest_trade_from_snapshots():
    class Trade:
        price, timestamp = 10.4, datetime(2026, 11, 5, 14, 45, 8, tzinfo=timezone.utc)

    class Snap:
        latest_trade = Trade()

    class Client:
        def get_stock_snapshot(self, req):
            return {"AAA": Snap(), "BBB": type("S", (), {"latest_trade": None})()}

    class Feed:
        _client = Client()

    got = pb.alpaca_trades(Feed())(["AAA", "BBB"])
    assert got == {"AAA": (10.4, Trade.timestamp)}
    assert pb.alpaca_trades(object()) is None and pb.alpaca_bars(object()) is None
