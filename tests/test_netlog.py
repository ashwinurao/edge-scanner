"""scanner/netlog.py: the record of every outside call (brief 11, Ash 2026-11-13 14:05 UTC).

No network: answers come from a stub requests adapter mounted on the client's own session, and
the clocks are fakes. Expected values are worked by hand in the comments.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from requests.structures import CaseInsensitiveDict

from scanner import netlog

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
FAKE_KEY = "PKNETLOGFAKEKEY0001"
FAKE_SECRET = "NETLOGFAKESECRET0000000000000000002"
T0 = datetime(2026, 11, 13, 14, 31, 30, tzinfo=UTC)      # 09:31:30 ET


class Clock:
    """One fake clock behind netlog's monotonic and wall clocks and requests' `elapsed`."""

    def __init__(self, wall0: datetime = T0) -> None:
        self.t = 1000.0
        self.wall0 = wall0

    def mono(self) -> float:
        return self.t

    def wall(self) -> datetime:
        return self.wall0 + timedelta(seconds=self.t - 1000.0)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(netlog, "_mono", c.mono)
    monkeypatch.setattr(netlog, "_wall", c.wall)
    monkeypatch.setattr(requests.sessions, "preferred_clock", c.mono)
    return c


@pytest.fixture
def nl(tmp_path, monkeypatch):
    """netlog started on a temp folder, no heartbeat thread. Yields a reader of written lines."""
    monkeypatch.delenv(netlog.ENV, raising=False)
    monkeypatch.setattr(netlog, "dropped", 0)
    monkeypatch.setenv("ALPACA_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ALPACA_SECRET_KEY", FAKE_SECRET)
    folder = tmp_path / "netlog"
    assert netlog.start(folder, beat=False)

    def lines(kind=None):
        netlog.flush()
        out = []
        for f in sorted(folder.glob("*.jsonl")):
            out += [json.loads(x) for x in f.read_text().splitlines()]
        return [x for x in out if kind is None or x["kind"] == kind]

    lines.folder = folder
    yield lines
    netlog.flush()
    netlog.stop()


class StubAdapter(requests.adapters.BaseAdapter):
    """Answers from a list: (status, headers, body, wire seconds) or an exception to raise."""

    def __init__(self, answers, clock=None) -> None:
        super().__init__()
        self.answers = list(answers)
        self.clock = clock

    def send(self, request, **kwargs):
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        status, headers, body, wire = a
        if self.clock is not None:
            self.clock.t += wire
        r = requests.Response()
        r.status_code = status
        r.headers = CaseInsensitiveDict(headers)
        r._content = body.encode() if isinstance(body, str) else body
        r.encoding = "utf-8"
        r.request = request
        r.url = request.url
        r.reason = "stub"
        return r

    def close(self) -> None:
        pass


BARS = json.dumps({"bars": {
    "AAPL": [{"t": "2026-11-13T14:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "n": 3, "vw": 1.2}],
    "MSFT": [{"t": "2026-11-13T14:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "n": 3, "vw": 1.2},
             {"t": "2026-11-13T14:31:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "n": 3, "vw": 1.2}]},
    "next_page_token": None})
RL = {"X-RateLimit-Limit": "10000", "X-RateLimit-Remaining": "9998", "X-RateLimit-Reset": "1763044320",
      "Date": "Fri, 13 Nov 2026 14:31:30 GMT", "Content-Length": str(len(BARS))}


def _feed(tmp_path, answers, clock):
    from scanner.data.alpaca import AlpacaFeed
    feed = AlpacaFeed(cache_dir=tmp_path / "d", intraday_cache_dir=tmp_path / "5m")
    feed._client._session.mount("https://", StubAdapter(answers, clock))
    return feed


# ── answers and calls ────────────────────────────────────────────────────────

def test_an_alpaca_answer_is_one_answer_line_with_the_rate_limit_fields(nl, clock, tmp_path):
    feed = _feed(tmp_path, [(200, RL, BARS, 0.250)], clock)
    df = feed._fetch_recent_minute_bars(["AAPL", "MSFT"], T0 - timedelta(minutes=3), T0)
    assert len(df) == 3
    (ans,) = nl("answer")
    (call,) = nl("call")
    assert ans["app"] == "edge-scanner" and ans["vendor"] == "alpaca_data"
    assert (ans["method"], ans["path"], ans["http"], ans["wire_ms"]) == ("GET", "/v2/stocks/bars", 200, 250.0)
    assert (ans["rl_limit"], ans["rl_remaining"], ans["rl_reset"]) == (10000, 9998, 1763044320)
    assert ans["date"] == "Fri, 13 Nov 2026 14:31:30 GMT" and ans["bytes"] == len(BARS)
    assert (ans["call"], ans["attempt"]) == (call["id"], 1)
    assert (call["endpoint"], call["purpose"], call["items"], call["symbols_n"]) == ("bars_recent", "recent_bars", 3, 2)
    # Newest bar 14:31 ends 14:32:00; it arrived 0.25 s after 14:31:30, so it is 29.75 s from its end.
    assert call["data_ts"] == "2026-11-13T14:32:00.000+00:00"
    assert call["data_age_ms"] == -29750.0
    assert call["ok"] is True and call["error"] is None


def test_a_retried_call_counts_two_attempts_in_one_call_line(nl, clock, tmp_path, monkeypatch):
    # alpaca-py retries a 429 after time.sleep(3); the stub answers 429 (0.1 s) then 200 (0.2 s).
    monkeypatch.setattr("alpaca.common.rest.time.sleep", lambda s: setattr(clock, "t", clock.t + s))
    feed = _feed(tmp_path, [(429, {"Retry-After": "3"}, "too many requests", 0.100),
                            (200, RL, BARS, 0.200)], clock)
    feed._fetch_recent_minute_bars(["AAPL", "MSFT"], T0 - timedelta(minutes=3), T0)
    answers = nl("answer")
    (call,) = nl("call")
    assert [a["http"] for a in answers] == [429, 200] and [a["attempt"] for a in answers] == [1, 2]
    assert answers[0]["retry_after"] == "3"
    assert (call["attempts"], call["pages"], call["http"], call["ok"]) == (2, 1, 200, True)
    # wait 0 + wire 100+200 + between 3000 (the sleep) + after 0 = total 3300
    assert (call["wait_ms"], call["wire_ms"], call["between_ms"], call["after_ms"], call["total_ms"]) == \
        (0.0, 300.0, 3000.0, 0.0, 3300.0)


def test_wait_wire_between_and_after_add_up_to_the_total(nl, clock):
    s = requests.Session()
    assert netlog.attach(s, "alpaca_data")
    s.mount("https://", StubAdapter([(200, {}, "{}", 0.250)], clock))
    with netlog.call("alpaca_data", "bars", "test") as c:
        clock.t += 0.040          # our code before the send
        s.get("https://data.alpaca.markets/v2/stocks/bars?symbols=AAPL")
        clock.t += 0.015          # body, parse, DataFrame
        c.seen(items=1)
    (call,) = nl("call")
    assert (call["wait_ms"], call["wait_client_ms"], call["wire_ms"], call["between_ms"], call["after_ms"]) == \
        (40.0, 40.0, 250.0, 0.0, 15.0)
    assert call["total_ms"] == 305.0 == call["wait_ms"] + call["wire_ms"] + call["between_ms"] + call["after_ms"]
    assert "symbols=" not in json.dumps(nl())          # no query string reaches a line


@pytest.mark.parametrize("exc, http, want", [
    (requests.exceptions.ConnectTimeout("Connection to data.alpaca.markets timed out. (connect timeout=5)"), None, "timeout_connect"),
    (requests.exceptions.ReadTimeout("Read timed out. (read timeout=5)"), None, "timeout_read"),
    (requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), None, "tls"),
    (requests.exceptions.ConnectionError("Failed to resolve 'data.alpaca.markets' ([Errno -2] Name or service not known)"), None, "dns"),
    (requests.exceptions.ConnectionError("Failed to establish a new connection: [Errno 111] Connection refused"), None, "refused"),
    (requests.exceptions.ConnectionError(ConnectionResetError(104, "Connection reset by peer")), None, "reset"),
    (RuntimeError("news HTTP 429"), 429, "http_429"),
    (RuntimeError("news HTTP 404"), 404, "http_4xx"),
    (RuntimeError("news HTTP 503"), 503, "http_5xx"),
    (urllib.error.HTTPError("https://api.github.com/x", 403, "Forbidden", {}, None), None, "http_4xx"),
    (Exception("YFRateLimitError: Too Many Requests. Rate limited."), None, "http_429"),
    (ValueError("The data in response does not match any known keys."), None, "other"),
])
def test_each_error_class(exc, http, want):
    assert netlog.classify(exc, http) == want


def test_an_alpaca_api_error_is_classed_by_its_status(nl, clock, tmp_path):
    feed = _feed(tmp_path, [(503, {}, "upstream down", 0.05)], clock)
    with pytest.raises(Exception):
        feed._fetch_recent_minute_bars(["AAPL"], T0 - timedelta(minutes=3), T0)
    (call,) = nl("call")
    assert (call["ok"], call["error"], call["http"], call["attempts"]) == (False, "http_5xx", 503, 1)


def test_a_call_with_no_answer_has_its_error_and_no_answer_line(nl, clock, tmp_path):
    feed = _feed(tmp_path, [requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")], clock)
    with pytest.raises(requests.exceptions.ReadTimeout):
        feed._fetch_recent_minute_bars(["AAPL"], T0 - timedelta(minutes=3), T0)
    assert nl("answer") == []
    (call,) = nl("call")
    assert (call["error"], call["attempts"], call["wire_ms"], call["http"]) == ("timeout_read", 0, None, None)
    assert call["error_text"].startswith("ReadTimeout: Read timed out")


# ── calls that hang ──────────────────────────────────────────────────────────

def test_inflight_lines_at_2_and_5_s_for_a_call_that_hangs_and_none_for_a_fast_one(nl, clock):
    with netlog.call("alpaca_data", "snapshots", "ov_picks_live"):
        for step, now in ((1.9, 1001.9), (0.15, 1002.05), (0.45, 1002.5), (2.6, 1005.1)):
            clock.t += step
            netlog.check_inflight()
    with netlog.call("alpaca_data", "bars", "chart"):
        clock.t += 0.5
        netlog.check_inflight()
    netlog.check_inflight()
    lines = nl("inflight")
    assert [(x["endpoint"], x["at_s"], x["age_ms"]) for x in lines] == \
        [("snapshots", 2, 2050.0), ("snapshots", 5, 5100.0)]
    assert lines[0]["purpose"] == "ov_picks_live" and lines[0]["asked"] == "2026-11-13T14:31:30.000+00:00"


# ── bar lateness ─────────────────────────────────────────────────────────────

def _frame(rows):
    idx = pd.MultiIndex.from_tuples([(s, pd.Timestamp(t, tz="UTC")) for s, t in rows], names=["symbol", "timestamp"])
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


def test_bars_lines_worked_by_hand_three_symbols_two_minutes_one_without_a_bar(nl):
    at = lambda hhmmss: datetime.fromisoformat(f"2026-11-13T{hhmmss}+00:00")
    lat = netlog.BarLateness(expected=3, started=at("14:30:10"))
    # Minute 14:30 closed before the poll started: not measurable, so never counted.
    lat.seen(_frame([("A", "14:29"), ("A", "14:31")]), at("14:32:01.5"))      # A 14:31: +1500
    lat.seen(_frame([("A", "14:31"), ("B", "14:31")]), at("14:32:06.5"))      # B 14:31: +6500
    lat.seen(_frame([("A", "14:32")]), at("14:33:02"))                        # A 14:32: +2000
    lat.seen(_frame([("A", "14:32"), ("B", "14:32")]), at("14:33:07"))        # B 14:32: +7000
    lat.seen(_frame([("B", "14:32"), ("C", "14:32")]), at("14:33:12"))        # C 14:32: +12000
    lat.handed([{"symbol": "A", "timestamp": at("14:31:00"), "volume": 1.0, "close": 1.0},
                {"symbol": "B", "timestamp": at("14:31:00"), "volume": 1.0, "close": 1.0}], at("14:32:06.5"))
    lat.moved_past(at("14:32:30"))          # the poll asks from 14:32:30: 14:31 is done
    lat.moved_past(at("14:33:30"))          # 14:32 is done
    first, second = nl("bars")
    # 14:31: A 1500, B 6500, C none. Nearest rank: p50 = 1st of 2, p95 = 2nd of 2.
    assert (first["minute"], first["minute_et"]) == ("2026-11-13T14:31:00.000+00:00", "2026-11-13T09:31-05:00")
    assert (first["expected"], first["with_bar"]) == (3, 2)
    assert first["first_seen_ms_after_close"] == {"min": 1500.0, "p50": 1500.0, "p95": 6500.0, "max": 6500.0}
    assert (first["handed"], first["handed_ms_after_close"]) == (2, {"min": 6500.0, "p50": 6500.0, "p95": 6500.0, "max": 6500.0})
    assert (first["seen_before_close"], first["changed_after_handed"], first["max_volume_change_pct"]) == (0, 0, None)
    # 14:32: A 2000, B 7000, C 12000. p50 = 2nd of 3, p95 = 3rd of 3.
    assert second["with_bar"] == 3
    assert second["first_seen_ms_after_close"] == {"min": 2000.0, "p50": 7000.0, "p95": 12000.0, "max": 12000.0}
    assert (second["handed"], second["handed_ms_after_close"]) == (0, None)


def _frame_v(rows):
    """Bars with volume and close: (symbol, HH:MM, volume, close)."""
    idx = pd.MultiIndex.from_tuples([(s, pd.Timestamp(t, tz="UTC")) for s, t, _, _ in rows], names=["symbol", "timestamp"])
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [c for *_, c in rows],
                         "volume": [v for _, _, v, _ in rows]}, index=idx)


def test_bars_line_counts_bars_seen_before_close_and_bars_that_change_after_the_hand_over(nl):
    at = lambda hhmmss: datetime.fromisoformat(f"2026-11-13T{hhmmss}+00:00")
    lat = netlog.BarLateness(expected=3, started=at("14:30:10"))
    lat.seen(_frame_v([("C", "14:31", 50, 5.0)]), at("14:31:58"))                       # C: -2000, before close
    lat.seen(_frame_v([("A", "14:31", 100, 10.0), ("B", "14:31", 200, 20.0)]), at("14:32:03"))
    lat.handed([{"symbol": "A", "timestamp": at("14:31:00"), "volume": 100.0, "close": 10.0},
                {"symbol": "B", "timestamp": at("14:31:00"), "volume": 200.0, "close": 20.0}], at("14:32:03.5"))
    # Later answers: A grows to 130 (+30 %), then 125 (+25 %); B stays as handed.
    lat.seen(_frame_v([("A", "14:31", 130, 10.05), ("B", "14:31", 200, 20.0)]), at("14:32:08.5"))
    lat.seen(_frame_v([("A", "14:31", 125, 10.05), ("B", "14:31", 200, 20.0)]), at("14:32:13.5"))
    lat.moved_past(at("14:32:30"))
    (line,) = nl("bars")
    assert (line["with_bar"], line["seen_before_close"]) == (3, 1)
    # C -2000, A 3000, B 3000: p50 = 2nd of 3 = 3000
    assert line["first_seen_ms_after_close"] == {"min": -2000.0, "p50": 3000.0, "p95": 3000.0, "max": 3000.0}
    assert (line["changed_after_handed"], line["max_volume_change_pct"]) == (1, 30.0)


def _poll(fetch, passes, every=0.0, clock=lambda: T0, symbols=("AAPL", "MSFT")):
    from scanner.data import alpaca_poll
    got, stop, n = [], threading.Event(), [0]

    def counting_fetch(syms, start, end):
        n[0] += 1
        if n[0] >= passes:
            stop.set()
        return fetch(syms, start, end)

    alpaca_poll.poll_minute_bars(counting_fetch, list(symbols), got.append, stop, every=every, clock=clock)
    return got


def test_the_poll_writes_one_call_per_pass_and_flags_an_overrun(nl):
    frame = _frame([("AAPL", "14:30"), ("MSFT", "14:30"), ("MSFT", "14:31")])

    def slow(syms, start, end):
        time.sleep(0.03)      # a pass of at least 30 ms against `every` of 10 ms
        return frame

    got = _poll(slow, passes=1, every=0.01)
    (call,) = nl("call")
    assert (call["endpoint"], call["purpose"], call["symbols_n"], call["batches"]) == ("bars_poll", "poller", 2, 1)
    assert (call["items"], call["handed"], call["overrun"]) == (3, len(got), True)
    assert call["pass_ms"] >= 30
    assert call["data_ts"] == "2026-11-13T14:32:00.000+00:00"     # 14:31 bar's end
    (note,) = [x for x in nl("note") if x["text"] == "bar poll start"]
    assert (note["symbols_n"], note["batches"]) == (2, 1)


def test_the_callbacks_run_before_any_lateness_work_and_first_seen_uses_the_arrival_time(nl, monkeypatch):
    # Pass 1 (09:31:30 ET) gets nothing; pass 2 (09:32:30) gets AAPL's 09:31 bar, which arrives at
    # 14:32:30.5 UTC. The scanner's callback then takes 5 s. first_seen must be 30,500 ms, not 35,500.
    wall = [T0]
    monkeypatch.setattr(netlog, "_wall", lambda: wall[0])
    order, lats = [], []
    seen, handed = netlog.BarLateness.seen, netlog.BarLateness.handed

    def spy_seen(self, df, at=None):
        order.append("seen")
        lats.append(self)
        return seen(self, df, at)

    def spy_handed(self, bars, at=None):
        order.append("handed")
        return handed(self, bars, at)
    monkeypatch.setattr(netlog.BarLateness, "seen", spy_seen)
    monkeypatch.setattr(netlog.BarLateness, "handed", spy_handed)
    calls = [0]

    def fetch(syms, start, end):
        calls[0] += 1
        wall[0] = end + timedelta(seconds=0.5)
        return _frame([]) if calls[0] == 1 else _frame([("AAPL", "14:31")])

    def callback(bar):
        order.append("callback")
        wall[0] += timedelta(seconds=5)

    from scanner.data import alpaca_poll
    stop = threading.Event()
    clock = _seq(T0, T0, T0 + timedelta(minutes=1))
    alpaca_poll.poll_minute_bars(lambda *a: (stop.set() if calls[0] >= 1 else None) or fetch(*a),
                                 ["AAPL"], callback, stop, every=0.0, clock=clock)
    assert order == ["seen", "handed", "callback", "seen", "handed"]
    m = datetime(2026, 11, 13, 14, 31, tzinfo=UTC)
    assert lats[-1]._seen[m]["AAPL"] == 30500.0
    assert lats[-1]._handed[m] == [30500.0]      # handed at 14:32:30.5, before the callback's 5 s


def test_the_feeds_own_request_joins_the_poll_pass_so_there_is_one_call_line(nl, clock, tmp_path):
    feed = _feed(tmp_path, [(200, RL, BARS, 0.2)], clock)
    _poll(feed._fetch_recent_minute_bars, passes=1, every=10.0)
    (call,) = nl("call")
    (ans,) = nl("answer")
    assert call["endpoint"] == "bars_poll" and ans["call"] == call["id"] and call["attempts"] == 1
    assert call["overrun"] is False


def test_a_failed_poll_is_recorded_and_the_poll_goes_on(nl):
    def boom(syms, start, end):
        raise requests.exceptions.ConnectTimeout("Connection to data.alpaca.markets timed out. (connect timeout=None)")
    _poll(boom, passes=1, every=0.0)
    (call,) = nl("call")
    assert (call["ok"], call["error"], call["items"]) == (False, "timeout_connect", 0)


# ── routes ───────────────────────────────────────────────────────────────────

def test_a_route_line_with_the_two_headers_and_the_route_pattern(nl):
    app = FastAPI()
    app.add_middleware(netlog.RouteTimer)
    app.get("/api/v2/clock")(lambda: {"ok": 1})
    app.get("/api/bars/{symbol}/{timeframe}")(lambda symbol, timeframe: {"bars": []})
    app.get("/api/universe")(lambda: {"symbols": []})
    c = TestClient(app)
    assert c.get("/api/v2/clock", headers={"X-Edge-App": "edge-trader", "X-Edge-Req": "abc-123"}).json() == {"ok": 1}
    assert c.get("/api/bars/AAPL/1min").status_code == 200
    assert c.get("/api/universe").status_code == 200          # not a named route: no line
    first, second = nl("route")
    assert (first["route"], first["method"], first["http"], first["from"], first["req"]) == \
        ("/api/v2/clock", "GET", 200, "edge-trader", "abc-123")
    assert first["ms"] >= 0
    assert (second["route"], second["from"]) == ("/api/bars/{symbol}/{timeframe}", "other") and second["req"]
    assert len(nl("route")) == 2


def test_a_refused_request_is_timed_with_its_raw_path_capped_at_200_characters(nl):
    from starlette.responses import PlainTextResponse

    async def refuse(scope, receive, send):   # stands in for the local guard: no route matched
        await PlainTextResponse("refused", status_code=403)(scope, receive, send)
    timer = netlog.RouteTimer(refuse)
    c = TestClient(timer)
    long_path = "/api/bars/" + "X" * 500
    assert c.get(long_path).status_code == 403
    (line,) = nl("route")
    assert line["http"] == 403 and line["route"] == long_path[:200] and len(line["route"]) == 200


def test_a_call_made_inside_a_route_carries_the_routes_req_id(nl):
    app = FastAPI()
    app.add_middleware(netlog.RouteTimer)

    @app.get("/api/v2/ov-picks")
    def picks():  # a plain def runs in a worker thread, like the real route
        with netlog.call("alpaca_data", "snapshots", "ov_picks_live"):
            pass
        return {"ok": True}

    TestClient(app).get("/api/v2/ov-picks", headers={"X-Edge-Req": "r-77"})
    (call,) = nl("call")
    assert call["req"] == "r-77"


def _fake_scanner():
    return SimpleNamespace(_states={}, _spy_state=None, _regime=SimpleNamespace(value="neutral"), _sector_map={})


def test_the_real_app_times_the_clock_route_and_serves_diag_net(nl, monkeypatch, tmp_path):
    from scanner.api import AppState, create_app
    c = TestClient(create_app(AppState(scanner=_fake_scanner(), feed=None)))  # type: ignore[arg-type]
    assert c.get("/api/v2/clock", headers={"X-Edge-App": "edge-trader"}).status_code == 200
    body = c.get("/api/v2/diag/net").json()
    assert body["app"] == "edge-scanner" and body["active"] is True
    (route,) = nl("route")
    assert (route["route"], route["from"]) == ("/api/v2/clock", "edge-trader")


# ── diag rollup ──────────────────────────────────────────────────────────────

def test_diag_percentiles_worked_by_hand(nl, monkeypatch):
    now = datetime(2026, 11, 13, 15, 0, tzinfo=UTC)               # 10:00 ET
    def at(minutes_before):
        monkeypatch.setattr(netlog, "_wall", lambda: now - timedelta(minutes=minutes_before))
    rows = [  # (minutes before now, total, wire, error)
        (1, 100, 50, None), (2, 200, 60, None), (3, 300, 70, None), (4, 400, 80, "timeout_read"),
        (4.5, 1000, 900, None),
        (20, 5000, None, "timeout_connect"), (25, 50, 40, None),   # 09:40 and 09:35 ET
        (55, 99999, 99999, "http_5xx"),                            # 09:05 ET: before 09:25
    ]
    for before, total, wire, err in rows:
        at(before)
        netlog.emit("call", vendor="alpaca_data", endpoint="bars_poll", purpose="poller",
                    total_ms=total, wire_ms=wire, ok=err is None, error=err)
    at(20); netlog.emit("answer", vendor="alpaca_data", rl_remaining=150, rl_limit=10000)
    at(2); netlog.emit("answer", vendor="alpaca_data", rl_remaining=180, rl_limit=10000)
    d = netlog.diag(now)
    (row,) = d["calls"]
    five, day = row["last_5_min"], row["since_0925_et"]
    # last 5 min: totals 100,200,300,400,1000 -> p50 = 3rd = 300, p95 = ceil(4.75) = 5th = 1000
    assert (five["count"], five["errors"]) == (5, {"timeout_read": 1})
    assert five["total_ms"] == {"p50": 300.0, "p95": 1000.0, "max": 1000.0}
    assert five["wire_ms"] == {"p50": 70.0, "p95": 900.0, "max": 900.0}
    # since 09:25: 50,100,200,300,400,1000,5000 -> p50 = 4th = 300, p95 = ceil(6.65) = 7th = 5000
    assert (day["count"], day["errors"]) == (7, {"timeout_read": 1, "timeout_connect": 1})
    assert day["total_ms"] == {"p50": 300.0, "p95": 5000.0, "max": 5000.0}
    # wire (no value for the 5000 one): 40,50,60,70,80,900 -> p50 = 3rd = 60, p95 = 6th = 900
    assert day["wire_ms"] == {"p50": 60.0, "p95": 900.0, "max": 900.0}
    assert row["last_ok_age_s"] == 60.0 and five["complete"] and day["complete"]
    rl = d["rl_remaining_lowest"]["alpaca_data"]
    assert rl["last_5_min"]["rl_remaining"] == 180 and rl["since_0925_et"]["rl_remaining"] == 150
    assert d["since_0925_et_from"] == "2026-11-13T14:25:00+00:00"


def test_diag_lists_calls_in_flight(nl, clock):
    with netlog.call("alpaca_data", "snapshots", "ov_picks_live"):
        clock.t += 3.0
        (open_call,) = netlog.diag()["inflight"]
    assert (open_call["endpoint"], open_call["age_ms"]) == ("snapshots", 3000.0)
    assert netlog.diag()["inflight"] == []


def test_lag_line_carries_the_gauges(nl):
    netlog.gauge("feed_clients", lambda: 2)
    netlog._lag.add_thread(3.0)
    netlog._lag.add_thread(40.0)
    netlog._lag.add_loop(12.5)
    netlog.lag_line()
    (lag,) = nl("lag")
    assert (lag["thread_lag_max_ms"], lag["thread_lag_p95_ms"], lag["loop_lag_max_ms"], lag["feed_clients"]) == \
        (40.0, 40.0, 12.5, 2)
    assert lag["threads"] >= 1 and lag["dropped"] == 0
    assert netlog.diag()["last_lag"]["feed_clients"] == 2


# ── a failing netlog changes nothing ─────────────────────────────────────────

def _full(item):
    raise queue.Full


def _break(monkeypatch, tmp_path):
    # The queue object stays (the writer thread waits on it); only putting into it fails.
    monkeypatch.setattr(netlog._q, "put_nowait", _full)
    (tmp_path / "blocked").write_text("a file where the folder should be")
    monkeypatch.setattr(netlog, "_dir", tmp_path / "blocked")

    def bad_add(rec):
        raise RuntimeError("rollup broke")
    monkeypatch.setattr(netlog._rollup, "add", bad_add)


def _seq(*times):
    """A poll clock: start, then one reading per pass; the last one repeats."""
    it = iter(times)
    last = [times[-1]]
    return lambda: next(it, last[0])


def test_a_failing_netlog_leaves_the_polls_result_identical(nl, monkeypatch, tmp_path):
    # Start 09:31:30 ET; the second pass at 09:32:30 hands on the closed 09:31 bars.
    frame = _frame([("AAPL", "14:30"), ("AAPL", "14:31"), ("MSFT", "14:31")])
    netlog.stop()
    plain = _poll(lambda *a: frame, passes=2, clock=_seq(T0, T0, T0 + timedelta(minutes=1)))
    assert netlog.start(tmp_path / "again", beat=False)
    _break(monkeypatch, tmp_path)
    broken = _poll(lambda *a: frame, passes=2, clock=_seq(T0, T0, T0 + timedelta(minutes=1)))
    assert broken == plain and [b["symbol"] for b in plain] == ["AAPL", "MSFT"]
    assert netlog.dropped > 0


def test_a_dead_writer_leaves_the_polls_result_identical(nl, monkeypatch, tmp_path):
    frame = _frame([("AAPL", "14:31")])
    plain = _poll(lambda *a: frame, passes=2, clock=_seq(T0, T0, T0 + timedelta(minutes=1)))
    (tmp_path / "blocked").write_text("x")
    monkeypatch.setattr(netlog, "_dir", tmp_path / "blocked")      # every write fails
    monkeypatch.setattr(netlog, "_warned", False)
    assert _poll(lambda *a: frame, passes=2, clock=_seq(T0, T0, T0 + timedelta(minutes=1))) == plain
    assert len(plain) == 1
    netlog.flush()
    assert netlog._warned is True                                    # said once, never raised


def test_a_failing_netlog_leaves_the_picks_answer_and_the_snapshot_identical(nl, monkeypatch, tmp_path):
    from scanner import ov_picks_live
    from scanner import ov_picks_route as route
    samples = Path(__file__).resolve().parents[1] / "ov_picks" / "samples" / "frozen"
    monkeypatch.setenv(route.ENV_VAR, str(samples))
    monkeypatch.setenv("OPEN_RECORDER", "0")
    now = datetime(2026, 11, 5, 10, 40, tzinfo=ET)

    def answer():
        app = FastAPI()
        app.add_middleware(netlog.RouteTimer)
        route.register(app, SimpleNamespace(), clock=lambda: now)
        body = TestClient(app).get("/api/v2/ov-picks").json()
        return body

    trade = SimpleNamespace(timestamp=datetime(2026, 11, 5, 9, 30, 20, tzinfo=ET), price=10.0)
    day_bar = SimpleNamespace(timestamp=datetime(2026, 11, 5, 0, 0, tzinfo=ET), open=10.0, high=10.5, low=9.9, close=10.2)
    client = SimpleNamespace(get_stock_snapshot=lambda req: {"AAAA": SimpleNamespace(daily_bar=day_bar, latest_trade=trade)})
    forming = ov_picks_live.alpaca_forming(SimpleNamespace(_client=client))
    day = datetime(2026, 11, 5).date()

    good_answer, good_forming = answer(), forming(day, ["AAAA"])
    (snap,) = nl("call")
    assert (snap["endpoint"], snap["purpose"], snap["items"]) == ("snapshots", "ov_picks_live", 1)
    assert snap["data_ts"] == "2026-11-05T14:30:20.000+00:00"
    assert len(nl("route")) == 1
    _break(monkeypatch, tmp_path)
    assert answer() == good_answer
    assert forming(day, ["AAAA"]) == good_forming
    assert good_forming["AAAA"]["close"] == 10.2


# ── switch off, keys, other vendors ──────────────────────────────────────────

def test_off_writes_nothing_and_starts_no_thread(tmp_path, monkeypatch):
    monkeypatch.setenv(netlog.ENV, "0")
    before = set(threading.enumerate())
    assert netlog.start(tmp_path / "netlog") is False
    assert netlog.log_to_file(tmp_path / "logs") is False
    assert netlog.active() is False
    s = requests.Session()
    assert netlog.attach(s, "alpaca_data") is False and s.hooks["response"] == []
    assert netlog.request_hooks("yahoo") == {}
    with netlog.call("alpaca_data", "bars", "chart") as c:
        assert not c
    netlog.emit("note", text="x")
    assert set(threading.enumerate()) == before
    assert not (tmp_path / "netlog").exists() and not (tmp_path / "logs").exists()


def test_no_api_key_ever_reaches_a_line(nl, clock, tmp_path):
    echo = f'{{"message": "forbidden for key {FAKE_KEY} / {FAKE_SECRET}"}}'
    feed = _feed(tmp_path, [(403, {}, echo, 0.05), (200, RL, BARS, 0.05)], clock)
    with pytest.raises(Exception):
        feed._fetch_recent_minute_bars(["AAPL"], T0 - timedelta(minutes=3), T0)
    feed._fetch_recent_minute_bars(["AAPL"], T0 - timedelta(minutes=3), T0)
    assert len(nl("call")) == 2 and "{key}" in nl("call")[0]["error_text"]
    text = "".join(f.read_text() for f in nl.folder.glob("*.jsonl"))
    assert FAKE_KEY not in text and FAKE_SECRET not in text


def test_news_calls_and_the_real_getter_carries_the_hook(nl, monkeypatch):
    from scanner import news
    resp = SimpleNamespace(status_code=200, json=lambda: {"news": [
        {"id": 1, "headline": "H", "created_at": "2026-11-13T14:00:00Z"},
        {"id": 2, "headline": "I", "created_at": "2026-11-13T14:20:00Z"}]})
    client = news.NewsClient("k", "s", get_fn=lambda url, headers, params, timeout: resp, rss_sources=())
    assert len(client.fetch(["AAPL"])["items"]) == 2
    (call,) = nl("call")
    assert (call["vendor"], call["endpoint"], call["http"], call["items"]) == ("alpaca_news", "news", 200, 2)
    assert call["data_ts"] == "2026-11-13T14:20:00.000+00:00"
    seen = {}
    monkeypatch.setattr(requests, "get", lambda url, **kw: seen.update(kw) or resp)
    news._default_get(news.NEWS_URL, headers={}, params={}, timeout=8)
    assert set(seen) == {"headers", "params", "timeout", "hooks"}
    monkeypatch.setenv(netlog.ENV, "0")
    seen.clear()
    news._default_get(news.NEWS_URL, headers={}, params={}, timeout=8)
    assert set(seen) == {"headers", "params", "timeout"}      # off: exactly the old call


def test_yfinance_github_and_symbol_calls_say_what_is_not_visible(nl, tmp_path):
    from scanner import fundamentals, symbols, update_check

    def rate_limited(sym):
        raise Exception("YFRateLimitError: Too Many Requests. Rate limited. Try after a while.")
    cache = fundamentals.FundamentalsCache(tmp_path / "f.json", ticker_factory=rate_limited)
    cache.fetch_one("AAPL")
    update_check.check_once(fetch=lambda: {"tag_name": "v1.0.0"})
    replies = iter([SimpleNamespace(status_code=401), SimpleNamespace(status_code=200, json=lambda: [],
                                                                       raise_for_status=lambda: None)])
    symbols.fetch_alpaca(get=lambda url, **kw: next(replies))
    yahoo, github, live, paper = nl("call")
    assert (yahoo["vendor"], yahoo["error"], yahoo["attempts"], yahoo["attempts_visible"], yahoo["symbol"]) == \
        ("yahoo", "http_429", None, False, "AAPL")
    assert "wait_pause_ms" in yahoo
    assert (github["vendor"], github["ok"], github["attempts"]) == ("github", True, None)
    assert (live["vendor"], live["http"], live["error"]) == ("alpaca_trading", 401, "http_4xx")
    assert (paper["http"], paper["ok"]) == (200, True)


def test_warning_lines_go_to_a_daily_file(tmp_path, monkeypatch):
    monkeypatch.delenv(netlog.ENV, raising=False)
    assert netlog.log_to_file(tmp_path / "logs")
    try:
        logging.getLogger("scanner.test").warning("poll failed for %d symbols", 715)
        logging.getLogger("scanner.test").info("not kept")
    finally:
        netlog.stop_log_file()
    (f,) = (tmp_path / "logs").glob("edge-scanner-*.log")
    text = f.read_text()
    assert "WARNING" in text and "poll failed for 715 symbols" in text and "not kept" not in text


def test_a_line_json_cannot_write_is_skipped_and_counted_and_the_rest_of_the_batch_is_written(nl):
    import decimal
    import numpy as np
    netlog.emit("note", text="good")
    netlog.emit("note", text="decimal and numpy", price=decimal.Decimal("10.25"), n=np.int64(5), x=np.float64(1.5))
    netlog.emit("note", text="bad", weird={(1, 2): "a tuple key json refuses"})
    netlog.emit("note", text="after")
    texts = [x["text"] for x in nl("note") if x["text"] != "start"]
    assert texts == ["good", "decimal and numpy", "after"]
    odd = [x for x in nl("note") if x["text"] == "decimal and numpy"][0]
    assert (odd["price"], odd["n"], odd["x"]) == ("10.25", "5", 1.5)
    assert netlog.dropped == 1


def test_a_log_file_that_cannot_be_written_is_said_once_on_stderr(tmp_path, capsys):
    (tmp_path / "logs").write_text("a file where the folder should be")
    handler = netlog._DailyFile(tmp_path / "logs")
    rec = logging.LogRecord("scanner.test", logging.WARNING, __file__, 1, "poll failed", None, None)
    for _ in range(3):
        handler.emit(rec)                     # never raises
    err = capsys.readouterr().err
    assert err.count("could not write the log file") == 1 and "Traceback" not in err
    handler.close()


def test_start_writes_a_start_note(nl):
    (note,) = [x for x in nl("note") if x["text"] == "start"]
    assert note["app"] == "edge-scanner" and note["version"] and note["pid"]
    assert note["ts"].endswith("+00:00")
