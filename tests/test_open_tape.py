"""The open tape (scanner/open_tape.py) and GET /api/v2/ov-picks/open-tape. Brief 15 of the OV
entries build (Ash's go, project thread "Review OV picks desk architecture", 2026-11-13 15:21 UTC).

The tape keeps, in memory, what the opening recorder (scanner/open_recorder.py) just read, and the
route serves it to Edge Trader. These tests drive it with hand-written recorder lines and with the
real recorder on fake requests. Tickers and prices are made up. The days are 2026-11-05 (a
Thursday, the contract samples' day) and 2026-11-13 (a Friday); both are EST, so 09:30 ET is
14:30 UTC. Nothing here reaches the network or sleeps.
"""
import builtins
import json
import socket
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from alpaca.data.models import Snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scanner import open_recorder as rec
from scanner import open_tape as ot
from scanner import ov_picks_route as route

ET = ZoneInfo("America/New_York")
URL = route.TAPE_ROUTE
DAY = "2026-11-05"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("an open tape test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _et(text):
    return datetime.fromisoformat(text).replace(tzinfo=ET)


def _utc(text):
    """ET wall time -> the recorder's UTC text, e.g. "2026-11-05 09:31:00.1" -> "...T14:31:00.100000Z"."""
    return rec._iso(_et(text))


def _tape():
    return ot.OpenTape(rec.FROM_ET, rec.UNTIL_ET, rec.BAR_ET)


def _start(day=DAY, tickers=("AAA", "BBB", "SPY")):
    return {"v": 1, "kind": "start", "t": _utc(f"{day} 09:29:00"), "day": day, "tickers": list(tickers),
            "reference": "SPY", "feed": "sip", "poll_seconds": 0.5, "bar_seconds": 1.0,
            "from_et": "09:29:00", "until_et": "09:36:00"}


def _sym(price, trade_et, minute_et, minute_hl=(10.40, 10.00), day_bar_day="2026-11-04", quote_et=None, day=DAY):
    """One ticker in a snapshot line, as the recorder writes it."""
    h, low = minute_hl
    return {"trade": {"t": _utc(f"{day} {trade_et}"), "p": price, "s": 100, "x": "V", "c": ["@"]},
            "quote": {"t": _utc(f"{day} {quote_et or trade_et}"), "bp": price - 0.01, "bs": 1,
                      "ap": price + 0.01, "as": 2},
            "minute": {"t": _utc(f"{day} {minute_et}"), "o": 10.1, "h": h, "l": low, "c": 10.2, "v": 900,
                       "n": 9, "vw": 10.2},
            # The day bar is the PRIOR day's until after 09:31 (2026-11-13's file).
            "day": {"t": f"{day_bar_day}T05:00:00.000000Z", "o": 9.0, "h": 99.0, "l": 1.0, "c": 9.5,
                    "v": 10, "n": 1, "vw": 9.5}}


def _snap(t1_et, symbols, ok=True, day=DAY):
    line = {"v": 1, "kind": "snapshot", "t0": _utc(f"{day} {t1_et}"), "t1": _utc(f"{day} {t1_et}"), "ms": 50.0,
            "ok": ok}
    if ok:
        line.update(symbols=symbols, missing=[])
    else:
        line.update(error="ConnectionError: boom", status=None)
    return line


def _bars(t1_et, bars, settled=False, day=DAY):
    return {"v": 1, "kind": "bars", "t0": _utc(f"{day} {t1_et}"), "t1": _utc(f"{day} {t1_et}"), "ms": 80.0,
            "ok": True, "bars": bars, "missing": [], "settled": settled}


def _bar(h, low):
    return {"o": 10.1, "h": h, "l": low, "c": 10.2, "v": 52000.0, "n": 410.0, "vw": 10.2}


# ---------------------------------------------------------------- what the tape keeps

def test_before_the_recorder_starts_it_says_so():
    body = _tape().view(_et(f"{DAY} 09:30:00"))
    assert body["state"]["code"] == ot.OFF and "not started" in body["state"]["why"]
    assert (body["seq"], body["day"], body["snapshot"], body["bars"], body["tickers"]) == (0, None, None, {}, [])


def test_after_start_snapshot_and_bars_lines_it_serves_the_newest_reading_and_every_0930_bar():
    tape = _tape()
    tape.recorder_on()
    seqs = [tape.view(_et(f"{DAY} 09:30:00"))["seq"]]
    lines = [
        _start(),
        # 09:30:30: the minute bar is 09:29's, the day bar yesterday's: no 09:30 bar yet.
        _snap("09:30:30.100", {"AAA": _sym(10.2, "09:30:30.000", "09:29:00"), "SPY": _sym(600, "09:30:30.050", "09:29:00")}),
        # 09:31:00.1: the minute bar turns into the 09:30 bar.
        _snap("09:31:00.100", {"AAA": _sym(10.3, "09:31:00.050", "09:30:00", (10.40, 10.00)),
                               "SPY": _sym(600, "09:31:00.060", "09:30:00", (601.0, 599.0))}),
        # The same 09:30 bar again: not a new version.
        _snap("09:31:00.600", {"AAA": _sym(10.35, "09:31:00.500", "09:30:00", (10.40, 10.00)),
                               "SPY": _sym(600.1, "09:31:00.550", "09:30:00", (601.0, 599.0))}),
        _bars("09:31:01.400", {"AAA": _bar(10.42, 10.00)}),
    ]
    for line in lines:
        tape.publish(line)
        seqs.append(tape.view(_et(f"{DAY} 09:31:02"))["seq"])
    body = tape.view(_et(f"{DAY} 09:31:02"))
    assert seqs == sorted(set(seqs)), "the sequence rises with each line"
    assert body["v"] == 1 and body["day"] == DAY and body["now"] == _utc(f"{DAY} 09:31:02")
    assert body["tickers"] == ["AAA", "BBB", "SPY"] and body["reference"] == "SPY"
    assert body["state"] == {"code": ot.RECORDING, "why": "", "error": None}
    assert body["poll_seconds"] == 0.5
    assert body["snapshot"]["t1"] == _utc(f"{DAY} 09:31:00.600")
    assert body["snapshot"]["symbols"]["AAA"]["trade"]["p"] == 10.35
    assert body["bars"]["AAA"] == [
        {"from": "snapshot", "seen": _utc(f"{DAY} 09:31:00.100"), "t": _utc(f"{DAY} 09:30:00"),
         "o": 10.1, "h": 10.40, "l": 10.00, "c": 10.2, "v": 900, "n": 9, "vw": 10.2},
        {"from": "bars", "seen": _utc(f"{DAY} 09:31:01.400"), "t": _utc(f"{DAY} 09:30:00"), "settled": False,
         **_bar(10.42, 10.00)},
    ]
    assert [b["from"] for b in body["bars"]["SPY"]] == ["snapshot"]
    assert "BBB" not in body["bars"]


@pytest.mark.parametrize("minute_et, minute_day", [
    ("09:29:00", DAY),              # the minute before the open
    ("09:31:00", DAY),              # the minute after
    ("09:30:00", "2026-11-04"),     # yesterday's 09:30 bar
])
def test_a_minute_bar_not_stamped_0930_of_the_day_is_never_a_0930_bar(minute_et, minute_day):
    tape = _tape()
    tape.recorder_on()
    tape.publish(_start())
    sym = _sym(10.2, "09:31:00.050", "09:30:00")
    sym["minute"]["t"] = _utc(f"{minute_day} {minute_et}")
    tape.publish(_snap("09:31:00.100", {"AAA": sym}))
    assert tape.view(_et(f"{DAY} 09:31:01"))["bars"] == {}


def test_the_day_bar_is_never_taken_as_a_bar():
    # Even a day bar stamped today (as after 09:31:00.65) is not the 09:30 minute.
    tape = _tape()
    tape.publish(_start())
    sym = _sym(10.2, "09:31:00.700", "09:29:00", day_bar_day=DAY)
    tape.publish(_snap("09:31:00.700", {"AAA": sym}))
    assert tape.view(_et(f"{DAY} 09:31:01"))["bars"] == {}


def test_a_changed_snapshot_bar_is_kept_as_a_new_version_with_when_it_was_seen():
    tape = _tape()
    tape.publish(_start())
    tape.publish(_snap("09:31:00.100", {"AAA": _sym(10.3, "09:31:00.050", "09:30:00", (10.40, 10.00))}))
    tape.publish(_snap("09:31:00.600", {"AAA": _sym(10.3, "09:31:00.550", "09:30:00", (10.45, 10.00))}))
    got = tape.view(_et(f"{DAY} 09:31:01"))["bars"]["AAA"]
    assert [(b["from"], b["h"], b["seen"]) for b in got] == [
        ("snapshot", 10.40, _utc(f"{DAY} 09:31:00.100")), ("snapshot", 10.45, _utc(f"{DAY} 09:31:00.600"))]


def test_the_settled_bars_reading_is_kept_even_when_it_matches():
    tape = _tape()
    tape.publish(_start())
    tape.publish(_bars("09:31:01.400", {"AAA": _bar(10.42, 10.00)}))
    tape.publish(_bars("09:31:02.400", {"AAA": _bar(10.42, 10.00)}))           # the same: not kept
    tape.publish(_bars("09:35:30.100", {"AAA": _bar(10.42, 10.00)}, settled=True))
    got = tape.view(_et(f"{DAY} 09:35:31"))["bars"]["AAA"]
    assert [(b["seen"], b["settled"]) for b in got] == [(_utc(f"{DAY} 09:31:01.400"), False),
                                                        (_utc(f"{DAY} 09:35:30.100"), True)]


def test_versions_are_capped_per_ticker():
    tape = _tape()
    tape.publish(_start())
    for i in range(ot.MAX_VERSIONS + 5):
        tape.publish(_snap(f"09:31:{i:02d}.100", {"AAA": _sym(10.3, f"09:31:{i:02d}.000", "09:30:00",
                                                                  (10.40 + i / 100, 10.00))}))
    got = tape.view(_et(f"{DAY} 09:31:30"))["bars"]["AAA"]
    assert len(got) == ot.MAX_VERSIONS
    assert got[0]["h"] == 10.40 and got[-1]["h"] == pytest.approx(10.40 + (ot.MAX_VERSIONS + 4) / 100)


def test_a_failed_request_keeps_the_last_good_snapshot_and_says_so():
    tape = _tape()
    tape.recorder_on()
    tape.publish(_start())
    tape.publish(_snap("09:30:10.000", {"AAA": _sym(10.2, "09:30:09.900", "09:29:00")}))
    tape.publish(_snap("09:30:10.500", {}, ok=False))
    body = tape.view(_et(f"{DAY} 09:30:11"))
    assert body["snapshot"]["t1"] == _utc(f"{DAY} 09:30:10.000")
    assert body["state"]["code"] == ot.RECORDING
    assert body["state"]["error"] == {"kind": "snapshot", "t1": _utc(f"{DAY} 09:30:10.500"),
                                      "error": "ConnectionError: boom", "status": None}


def test_a_stop_line_says_the_recorder_stopped_for_the_day_and_why():
    tape = _tape()
    tape.recorder_on()
    tape.publish(_start())
    tape.publish({"v": 1, "kind": "stop", "t": _utc(f"{DAY} 09:30:12"), "why": rec.RATE_LIMITED})
    state = tape.view(_et(f"{DAY} 09:30:13"))["state"]
    assert state["code"] == ot.STOPPED and rec.RATE_LIMITED in state["why"]


@pytest.mark.parametrize("now, code", [
    (f"{DAY} 09:28:59", ot.OUTSIDE),
    (f"{DAY} 09:29:00", ot.WAITING),
    (f"{DAY} 09:35:59", ot.WAITING),
    (f"{DAY} 09:36:00", ot.OUTSIDE),
    ("2026-11-07 09:31:00", ot.OUTSIDE),         # a Saturday
])
def test_outside_the_window_or_before_the_days_start_it_says_so(now, code):
    tape = _tape()
    tape.recorder_on()
    state = tape.view(_et(now))["state"]
    assert state["code"] == code and state["why"]


def test_a_recorder_that_is_off_says_why():
    tape = _tape()
    tape.recorder_off("the opening recorder is off (OPEN_RECORDER is set to off)")
    assert tape.view(_et(f"{DAY} 09:31:00"))["state"] == {
        "code": ot.OFF, "why": "the opening recorder is off (OPEN_RECORDER is set to off)", "error": None}


def test_yesterdays_tape_is_never_served_today_and_a_new_start_clears_it():
    tape = _tape()
    tape.recorder_on()
    tape.publish(_start())
    tape.publish(_snap("09:31:00.100", {"AAA": _sym(10.3, "09:31:00.050", "09:30:00")}))
    body = tape.view(_et("2026-11-06 09:29:30"))
    assert (body["day"], body["tickers"], body["snapshot"], body["bars"], body["reference"]) == (None, [], None, {}, None)
    assert body["state"]["code"] == ot.WAITING
    tape.publish(_start(day="2026-11-06", tickers=["CCC", "SPY"]))
    body = tape.view(_et("2026-11-06 09:29:31"))
    assert (body["day"], body["tickers"], body["snapshot"], body["bars"]) == ("2026-11-06", ["CCC", "SPY"], None, {})


def test_publish_never_raises():
    tape = _tape()
    for junk in ({"kind": "start", "day": "not a day"}, {"kind": "snapshot", "ok": True, "symbols": 5},
                 {}, {"kind": "bars", "ok": True, "bars": {"AAA": 3}}):
        tape.publish(junk)
    tape.publish(_start())
    tape.publish({"kind": "snapshot", "ok": True, "t1": "x", "symbols": {"AAA": "not a dict"}})
    tape.view(_et(f"{DAY} 09:31:00"))


def test_the_view_is_a_copy():
    tape = _tape()
    tape.publish(_start())
    tape.publish(_snap("09:31:00.100", {"AAA": _sym(10.3, "09:31:00.050", "09:30:00")}))
    body = tape.view(_et(f"{DAY} 09:31:01"))
    body["snapshot"]["symbols"]["AAA"]["trade"]["p"] = 0
    body["bars"]["AAA"].clear()
    again = tape.view(_et(f"{DAY} 09:31:01"))
    assert again["snapshot"]["symbols"]["AAA"]["trade"]["p"] == 10.3 and len(again["bars"]["AAA"]) == 1


# ---------------------------------------------------------------- the recorder feeds it

class _Clock:
    def __init__(self, at):
        self.now = _et(at)
        self.mono = 100.0

    def __call__(self):
        return self.now

    def monotonic(self):
        return self.mono

    def move(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


def _alpaca_snapshot(symbol, trade_at, minute_at):
    bar = {"o": 10.0, "h": 10.4, "l": 10.0, "c": 10.2, "v": 1200, "n": 14, "vw": 10.2}
    return Snapshot(symbol, {
        "latestTrade": {"t": trade_at, "x": "V", "p": 10.2, "s": 100, "c": ["@"], "i": 7, "z": "C"},
        "latestQuote": {"t": trade_at, "ax": "V", "ap": 10.21, "as": 2, "bx": "Q", "bp": 10.19, "bs": 3,
                        "c": ["R"], "z": "C"},
        "minuteBar": {"t": minute_at, **bar},
        "dailyBar": {"t": "2026-11-04T05:00:00Z", **bar},
        "prevDailyBar": {"t": "2026-11-03T05:00:00Z", **bar},
    })


def _recorder(tmp_path, clock, tape, calls):
    def snapshots(tickers):
        calls.append(("snapshot", list(tickers)))
        clock.move(0.05)
        minute = "2026-11-05T14:30:00Z" if clock.now >= _et(f"{DAY} 09:31:00") else "2026-11-05T14:29:00Z"
        trade_at = rec._iso(clock.now - timedelta(seconds=0.1))
        return {t: _alpaca_snapshot(t, trade_at, minute) for t in tickers}

    def bars(day, tickers):
        calls.append(("bars", list(tickers)))
        clock.move(0.08)
        stamp = pd.Timestamp(_et(f"{DAY} 09:30:00")).tz_convert("UTC")
        frame = pd.DataFrame([{"symbol": t, "timestamp": stamp, "open": 10.0, "high": 10.4, "low": 10.0,
                               "close": 10.2, "volume": 5.0, "trade_count": 4.0, "vwap": 10.2} for t in tickers])
        return rec.frame_bars(frame.set_index(["symbol", "timestamp"]), _et(f"{DAY} 09:30:00"))

    def read_day(_folder, day):
        if day != DAY:
            return {"has_picks": False, "picks": []}
        return {"has_picks": True, "picks": [{"rank": 2, "ticker": "BBB", "top": True},
                                             {"rank": 1, "ticker": "AAA", "top": True}]}

    return rec.OpenRecorder(tmp_path / "picks", snapshots, bars, out_dir=tmp_path / "out", feed_name="sip",
                            clock=clock, monotonic=clock.monotonic,
                            load_contract=lambda: SimpleNamespace(read_day=read_day), tape=tape)


def _run_open(recorder, clock):
    for at in ("09:30:59.000", "09:31:00.100", "09:31:01.000"):
        clock.now = _et(f"{DAY} {at}")
        recorder.step()


def test_the_recorder_publishes_each_line_it_writes(tmp_path):
    clock, calls = _Clock(f"{DAY} 09:30:59"), []
    tape = _tape()
    tape.recorder_on()
    recorder = _recorder(tmp_path, clock, tape, calls)
    _run_open(recorder, clock)
    body = tape.view(clock.now)
    assert body["tickers"] == ["AAA", "BBB", "SPY"]
    written = [json.loads(x) for x in (tmp_path / "out" / f"{DAY}.jsonl").read_text().splitlines()]
    snaps = [x for x in written if x["kind"] == "snapshot"]
    assert body["snapshot"] == {k: snaps[-1][k] for k in ("t0", "t1", "ms", "symbols", "missing")}
    assert [b["from"] for b in body["bars"]["AAA"]] == ["snapshot", "bars"]


class _Raising:
    def publish(self, _rec):
        raise RuntimeError("tape broke")


def test_a_tape_that_raises_changes_nothing_the_recorder_asks_or_writes(tmp_path):
    files, asked = [], []
    for name, tape in (("plain", None), ("raising", _Raising())):
        clock, calls = _Clock(f"{DAY} 09:30:59"), []
        recorder = _recorder(tmp_path / name, clock, tape, calls)
        _run_open(recorder, clock)
        files.append((tmp_path / name / "out" / f"{DAY}.jsonl").read_text())
        asked.append(calls)
    assert files[0] == files[1] and asked[0] == asked[1]


# ---------------------------------------------------------------- the route

class _CountingFeed:
    """Shaped like scanner/data/alpaca.py's AlpacaFeed; counts every request."""

    def __init__(self):
        self.calls = 0
        self._client = SimpleNamespace(get_stock_snapshot=self._snap)

    def _snap(self, _request):
        self.calls += 1
        return {}

    def _fetch_recent_minute_bars(self, *_a):
        self.calls += 1
        return pd.DataFrame()


def _app(clock, feed=None):
    app = FastAPI()
    route.register(app, SimpleNamespace(feed=feed), clock=clock, first_minute_fetch=lambda *_a: {})
    return app


def test_the_route_serves_the_tape_from_memory_with_no_store(monkeypatch, tmp_path):
    now = _et(f"{DAY} 09:31:02")
    feed = _CountingFeed()
    app = _app(lambda: now, feed)
    tape = app.state.open_tape
    tape.recorder_on()
    tape.publish(_start())
    tape.publish(_snap("09:31:00.100", {"AAA": _sym(10.3, "09:31:00.050", "09:30:00")}))
    real_open = builtins.open

    def no_files(*a, **k):
        raise AssertionError(f"the open tape route opened a file: {a[:1]}")

    client = TestClient(app)
    monkeypatch.setattr(builtins, "open", no_files)
    try:
        answers = [client.get(URL) for _ in range(5)]
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert {r.status_code for r in answers} == {200}
    assert answers[0].headers["cache-control"] == "no-store"
    body = answers[-1].json()
    assert set(body) == {"v", "seq", "now", "day", "tickers", "reference", "state", "snapshot", "bars",
                         "poll_seconds"}
    assert body["day"] == DAY and body["snapshot"]["t1"] == _utc(f"{DAY} 09:31:00.100")
    assert feed.calls == 0, "the route asked Alpaca"


def test_the_route_says_the_recorder_is_off_without_the_picks(monkeypatch):
    app = _app(lambda: _et(f"{DAY} 09:31:00"), _CountingFeed())
    with TestClient(app) as client:          # runs the startup handlers
        body = client.get(URL).json()
    assert body["state"]["code"] == ot.OFF and "OV_PICKS_DIR" in body["state"]["why"]
    assert body["snapshot"] is None


def test_the_route_says_when_open_recorder_0_turns_it_off(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    monkeypatch.setenv("OPEN_RECORDER", "0")
    app = _app(lambda: _et(f"{DAY} 09:31:00"), _CountingFeed())
    with TestClient(app) as client:
        body = client.get(URL).json()
    assert body["state"]["code"] == ot.OFF and "OPEN_RECORDER" in body["state"]["why"]


def test_the_route_says_waiting_when_the_recorder_is_on_and_has_no_line_yet(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    started = []
    monkeypatch.setattr(rec.OpenRecorder, "start", lambda self: started.append(self))
    app = _app(lambda: _et(f"{DAY} 09:29:30"), _CountingFeed())
    with TestClient(app) as client:
        body = client.get(URL).json()
    assert len(started) == 1 and started[0]._tape is app.state.open_tape
    assert body["state"]["code"] == ot.WAITING


def test_a_tape_that_cannot_be_read_is_a_clear_state_not_a_500():
    app = _app(lambda: _et(f"{DAY} 09:31:00"))
    app.state.open_tape.view = lambda _now: (_ for _ in ()).throw(RuntimeError("boom"))
    resp = TestClient(app).get(URL)
    assert resp.status_code == 200
    assert resp.json()["state"]["code"] == ot.ERROR and "boom" in resp.json()["state"]["why"]


def test_the_route_is_behind_the_scanners_local_only_guard(monkeypatch):
    from scanner.api import AppState, create_app
    scanner = SimpleNamespace(_states={}, _spy_state=None, _regime=None, _sector_map={})
    app = create_app(AppState(scanner=scanner, feed=None))  # type: ignore[arg-type]
    client = TestClient(app)
    assert client.get(URL).status_code == 200
    assert client.get(URL, headers={"host": "evil.example"}).status_code == 400
    monkeypatch.setenv("SCANNER_ALLOWED_HOSTS", "testserver,192.168.1.10")
    assert client.get(URL, headers={"host": "192.168.1.10:8772"}).status_code == 200
