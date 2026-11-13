"""The opening recorder (scanner/open_recorder.py): what it asks Alpaca, when, and what it
writes. Step 1 of the OV entries plan (Ash, project thread, 2026-11-13 12:59 UTC).

Driven through OpenRecorder.step with a fake ET clock and fake requests that hand back real
alpaca-py Snapshot objects and a bars frame shaped like Alpaca's. Every file lives in
tmp_path. Nothing here reaches the network or sleeps.
"""
import json
import shutil
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

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "ov_picks" / "samples"
ET = ZoneInfo("America/New_York")
DAY = "2026-11-05"                      # the samples' day, a Thursday
PICKS = ["AAAA", "BBBB", "CCCC", "DDDD"]
TICKERS = PICKS + ["SPY"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("an opening recorder test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _et(text):
    """An ET wall-clock time, e.g. "2026-11-05 09:30:00.250", as an aware datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=ET)


class Clock:
    """A wall clock and a monotonic clock that move only when the test moves them."""

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

    def set(self, at):
        self.move((_et(at) - self.now).total_seconds())


def _snapshot(symbol, trade_at, price=100.5):
    """A real alpaca-py Snapshot, built from Alpaca's own wire fields."""
    bar = {"o": 100.0, "h": 101.25, "l": 99.5, "c": price, "v": 1200, "n": 14, "vw": 100.31}
    return Snapshot(symbol, {
        "latestTrade": {"t": trade_at, "x": "V", "p": price, "s": 100, "c": ["@"], "i": 7, "z": "C"},
        "latestQuote": {"t": trade_at, "ax": "V", "ap": price + 0.02, "as": 2, "bx": "Q",
                        "bp": price - 0.02, "bs": 3, "c": ["R"], "z": "C"},
        "minuteBar": {"t": "2026-11-05T14:29:00Z", **bar},
        "dailyBar": {"t": "2026-11-05T05:00:00Z", **bar},
        "prevDailyBar": {"t": "2026-11-04T05:00:00Z", **bar},
    })


def _frame(symbols, at="2026-11-05 09:30:00"):
    """A bars frame as alpaca-py's BarSet.df gives it: index (symbol, timestamp)."""
    stamp = pd.Timestamp(_et(at)).tz_convert("UTC")
    rows = [{"symbol": s, "timestamp": stamp, "open": 100.0, "high": 101.25, "low": 99.5, "close": 100.75,
             "volume": 52000.0, "trade_count": 410.0, "vwap": 100.4} for s in symbols]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index(["symbol", "timestamp"])


class Setup:
    """A recorder on fake requests. `took` is how long each fake request takes."""

    def __init__(self, tmp_path, at, picks=PICKS, took=0.08, with_bars=True):
        self.clock = Clock(at)
        self.out = tmp_path / "data" / "open_recorder"
        self.picks = picks
        self.took = took
        self.snapshot_calls = []
        self.bar_calls = []
        self.snapshot_problem = None
        self.bars_ready = []            # the tickers whose 09:30 bar Alpaca has
        self.recorder = rec.OpenRecorder(
            tmp_path / "picks", self._snapshots, self._bars if with_bars else None, out_dir=self.out,
            feed_name="sip", clock=self.clock, monotonic=self.clock.monotonic,
            load_contract=lambda: SimpleNamespace(read_day=self._read_day))

    def _read_day(self, _folder, day):
        names = self.picks if day == DAY else None
        if not names:
            return {"has_picks": False, "picks": []}
        # Out of rank order on purpose, with two names past the top picks.
        rows = [{"rank": i + 1, "ticker": t, "top": True} for i, t in enumerate(names)]
        rows += [{"rank": len(names) + 1, "ticker": "EEEE", "top": False},
                 {"rank": len(names) + 2, "ticker": "FFFF", "top": False}]
        return {"has_picks": True, "picks": list(reversed(rows))}

    def _snapshots(self, tickers):
        self.snapshot_calls.append((self.clock.now, list(tickers)))
        self.clock.move(self.took)
        if self.snapshot_problem is not None:
            raise self.snapshot_problem
        # Each newest trade is 150 ms older than the moment the request went out.
        trade_at = (self.clock.now - timedelta(seconds=self.took + 0.15)).astimezone(ZoneInfo("UTC"))
        return {t: _snapshot(t, trade_at.isoformat().replace("+00:00", "Z")) for t in tickers}

    def _bars(self, day, tickers):
        self.bar_calls.append((self.clock.now, day.isoformat(), list(tickers)))
        self.clock.move(self.took)
        return rec.frame_bars(_frame([t for t in tickers if t in self.bars_ready]), _et(f"{DAY} 09:30:00"))

    def step(self, at=None):
        if at:
            self.clock.set(at)
        return self.recorder.step()

    def lines(self, day=DAY):
        path = self.out / f"{day}.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def kinds(self):
        return [line["kind"] for line in self.lines()]


class RateLimited(Exception):
    """Shaped like alpaca-py's APIError: the HTTP status is on status_code."""
    status_code = 429


# ---------------------------------------------------------------- when it must not run

@pytest.mark.parametrize("at, outcome", [
    ("2026-11-05 09:28:59.900", rec.OUTSIDE),       # a tenth of a second before the window
    ("2026-11-05 09:36:00", rec.OUTSIDE),           # the window's end is not inside it
    ("2026-11-05 04:00:00", rec.OUTSIDE),
    ("2026-11-05 15:59:00", rec.OUTSIDE),
    ("2026-11-07 09:30:30", rec.NOT_A_WEEKDAY),     # a Saturday
    ("2026-11-08 09:30:30", rec.NOT_A_WEEKDAY),
])
def test_outside_the_window_it_asks_nothing_and_writes_nothing(tmp_path, at, outcome):
    s = Setup(tmp_path, at)
    assert s.step() == outcome
    assert s.snapshot_calls == [] and s.bar_calls == []
    assert not s.out.exists()


def test_a_day_without_top_picks_makes_no_request_and_no_file(tmp_path):
    # A holiday or a skipped scan: the module wrote no picks for the day.
    s = Setup(tmp_path, "2026-11-05 09:29:00", picks=[])
    for _ in range(30):
        assert s.step() == rec.NO_PICKS
        s.clock.move(rec.POLL_SECONDS)
    assert s.snapshot_calls == [] and s.bar_calls == []
    assert not s.out.exists()


def test_a_late_picks_file_is_looked_for_every_few_seconds_and_then_recorded(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:29:00", picks=[])
    reads = []
    real = s._read_day
    s.recorder._load_contract = lambda: SimpleNamespace(
        read_day=lambda folder, day: (reads.append(s.clock.now), real(folder, day))[1])
    for _ in range(20):                 # ten seconds of steps
        s.step()
        s.clock.move(rec.POLL_SECONDS)
    assert len(reads) == 2              # at 09:29:00 and 09:29:05, not on every step
    s.picks = PICKS
    assert s.step("2026-11-05 09:29:10") == rec.RECORDED
    assert s.snapshot_calls[0][1] == TICKERS


def test_without_the_contract_reader_it_does_nothing(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00")
    s.recorder._load_contract = lambda: None
    assert s.step() == rec.NO_PICKS
    assert s.snapshot_calls == [] and not s.out.exists()


# ---------------------------------------------------------------- what a reading holds

def test_a_reading_has_the_request_times_and_each_tickers_trade_quote_and_bars(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:10")
    assert s.step() == rec.RECORDED
    start, reading = s.lines()
    # The start line: the top picks in rank order, then SPY, and the settings.
    assert start == {"v": 1, "kind": "start", "t": "2026-11-05T14:30:10.000000Z", "day": DAY,
                     "tickers": TICKERS, "reference": "SPY", "feed": "sip", "poll_seconds": 0.5,
                     "bar_seconds": 1.0, "from_et": "09:29:00", "until_et": "09:36:00"}
    # The request left at 09:30:10.000 ET and its answer came 80 ms later.
    assert reading["kind"] == "snapshot" and reading["ok"] is True
    assert reading["t0"] == "2026-11-05T14:30:10.000000Z"
    assert reading["t1"] == "2026-11-05T14:30:10.080000Z"
    assert reading["ms"] == 80.0
    assert list(reading["symbols"]) == TICKERS and reading["missing"] == []
    bar = {"o": 100.0, "h": 101.25, "l": 99.5, "c": 100.5, "v": 1200.0, "n": 14.0, "vw": 100.31}
    assert reading["symbols"]["AAAA"] == {
        # The newest trade is stamped 150 ms before the request left: 230 ms old on arrival.
        "trade": {"t": "2026-11-05T14:30:09.850000Z", "p": 100.5, "s": 100.0, "x": "V", "c": ["@"]},
        "quote": {"t": "2026-11-05T14:30:09.850000Z", "bp": 100.48, "bs": 3.0, "ap": 100.52, "as": 2.0},
        "minute": {"t": "2026-11-05T14:29:00.000000Z", **bar},
        "day": {"t": "2026-11-05T05:00:00.000000Z", **bar},
    }


def test_a_ticker_alpaca_leaves_out_is_listed_as_missing(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:10")
    full = s._snapshots
    s.recorder._snapshots = lambda tickers: {t: v for t, v in full(tickers).items() if t != "CCCC"}
    assert s.step() == rec.RECORDED
    reading = s.lines()[1]
    assert list(reading["symbols"]) == ["AAAA", "BBBB", "DDDD", "SPY"]
    assert reading["missing"] == ["CCCC"]


def test_spy_is_not_added_twice_when_it_is_a_pick(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:10", picks=["AAAA", "SPY"])
    s.step()
    assert s.snapshot_calls[0][1] == ["AAAA", "SPY"]


def test_it_reads_the_real_contract_files(tmp_path):
    shutil.copytree(SAMPLES / "frozen", tmp_path / "picks")
    clock = Clock("2026-11-05 09:29:30")
    asked = []
    recorder = rec.OpenRecorder(tmp_path / "picks", lambda tickers: asked.append(list(tickers)) or {},
                                out_dir=tmp_path / "rec", clock=clock, monotonic=clock.monotonic)
    assert recorder.step() == rec.RECORDED
    assert asked == [["AAAA", "BBBB", "CCCC", "DDDD", "SPY"]]     # the sample's four top picks


def test_a_scanner_that_starts_inside_the_window_joins_it(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:33:20")
    assert s.step() == rec.RECORDED
    assert s.kinds() == ["start", "snapshot"]


# ---------------------------------------------------------------- the 09:30 bar

def test_the_bar_is_not_asked_for_before_0931(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:29:00")
    while s.clock.now < _et("2026-11-05 09:31:00"):
        s.step()
        s.clock.set((s.clock.now + timedelta(seconds=0.5 - s.took)).replace(tzinfo=None).isoformat(" "))
    assert len(s.snapshot_calls) == 240          # two a second for two minutes
    assert s.bar_calls == []


def test_the_bar_is_asked_for_each_second_from_0931_until_every_ticker_has_one(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:31:00")
    assert s.step() == rec.RECORDED              # 09:31:00.0: asked, no bar yet
    assert s.step("2026-11-05 09:31:00.500") == rec.RECORDED     # too soon to ask again
    assert len(s.bar_calls) == 1
    s.bars_ready = ["AAAA", "SPY"]
    s.step("2026-11-05 09:31:01.000")
    s.bars_ready = TICKERS
    s.step("2026-11-05 09:31:02.000")
    for at in ("09:31:03.000", "09:31:04.000", "09:31:10.000"):  # every bar is in: no more asks
        s.step(f"2026-11-05 {at}")
    assert [(at.strftime("%H:%M:%S.%f")[:-3], day, names) for at, day, names in s.bar_calls] == [
        ("09:31:00.080", DAY, TICKERS), ("09:31:01.080", DAY, TICKERS), ("09:31:02.080", DAY, TICKERS)]
    bars = [line for line in s.lines() if line["kind"] == "bars"]
    assert [(sorted(b["bars"]), b["missing"], b["settled"]) for b in bars] == [
        ([], TICKERS, False),
        (["AAAA", "SPY"], ["BBBB", "CCCC", "DDDD"], False),
        (sorted(TICKERS), [], False)]
    assert bars[2]["bars"]["AAAA"] == {"o": 100.0, "h": 101.25, "l": 99.5, "c": 100.75,
                                       "v": 52000.0, "n": 410.0, "vw": 100.4}
    assert bars[2]["t0"] == "2026-11-05T14:31:02.080000Z" and bars[2]["ms"] == 80.0


def test_a_bar_that_never_comes_is_given_up_on_at_093130(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:31:00")
    s.bars_ready = PICKS                          # SPY's bar never comes
    while s.clock.now < _et("2026-11-05 09:35:00"):
        s.step()
        s.clock.move(1.0 - 2 * s.took)
    assert len(s.bar_calls) == 30                 # one a second from 09:31:00 to 09:31:29
    assert max(at for at, _d, _n in s.bar_calls) < _et("2026-11-05 09:31:30")


def test_the_settled_bar_is_asked_for_once_from_093530(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:31:00")
    s.bars_ready = TICKERS
    s.step()
    assert len(s.bar_calls) == 1
    s.step("2026-11-05 09:35:29.900")
    assert len(s.bar_calls) == 1
    for at in ("09:35:30.000", "09:35:30.500", "09:35:59.000"):
        s.step(f"2026-11-05 {at}")
    assert len(s.bar_calls) == 2
    assert [b["settled"] for b in s.lines() if b["kind"] == "bars"] == [False, True]


def test_bars_stamped_at_another_minute_are_not_the_0930_bar():
    at = _et("2026-11-05 09:30:00")
    assert rec.frame_bars(_frame(["AAAA"], at="2026-11-05 09:31:00"), at) == {}
    assert rec.frame_bars(_frame([]), at) == {}
    assert rec.frame_bars(None, at) == {}
    assert list(rec.frame_bars(_frame(["AAAA", "BBBB"]), at)) == ["AAAA", "BBBB"]


def test_without_a_bars_request_only_snapshots_are_recorded(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:31:00", with_bars=False)
    assert s.step() == rec.RECORDED
    assert s.kinds() == ["start", "snapshot"]


# ---------------------------------------------------------------- when a request fails

def test_a_failed_request_is_written_and_the_next_one_waits_five_seconds(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00")
    s.snapshot_problem = TimeoutError("read timed out")
    assert s.step() == rec.FAILED
    failed = s.lines()[1]
    assert failed["ok"] is False and failed["error"] == "TimeoutError: read timed out"
    assert failed["status"] is None and "symbols" not in failed
    s.snapshot_problem = None
    for at in ("09:30:00.500", "09:30:03.000", "09:30:05.000"):
        assert s.step(f"2026-11-05 {at}") == rec.PAUSED
    assert len(s.snapshot_calls) == 1
    assert s.step("2026-11-05 09:30:05.080") == rec.RECORDED      # five seconds after the answer
    assert s.kinds() == ["start", "snapshot", "snapshot"]


def test_a_failed_snapshot_asks_for_no_bars_in_the_same_step(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:31:00")
    s.snapshot_problem = ConnectionError("reset")
    assert s.step() == rec.FAILED
    assert s.bar_calls == []


def test_a_rate_limit_answer_stops_the_recorder_until_the_next_day(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00")
    s.snapshot_problem = RateLimited("too many requests")
    assert s.step() == rec.FAILED
    s.snapshot_problem = None
    for at in ("09:30:01", "09:30:10", "09:33:00", "09:35:59"):
        assert s.step(f"2026-11-05 {at}") == rec.STOPPED
    assert len(s.snapshot_calls) == 1
    lines = s.lines()
    assert [line["kind"] for line in lines] == ["start", "snapshot", "stop"]
    assert lines[1]["status"] == 429 and lines[2]["why"] == "rate_limited"
    # The next trading day starts clean. No picks file for it here, so it asks nothing yet.
    assert s.step("2026-11-06 09:30:00") == rec.NO_PICKS


def test_a_long_error_text_is_cut(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00")
    s.snapshot_problem = RuntimeError("x" * 5000)
    s.step()
    assert len(s.lines()[1]["error"]) == 300


# ---------------------------------------------------------------- the files it keeps

def test_it_keeps_the_newest_90_day_files_and_leaves_other_files_alone(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00")
    s.out.mkdir(parents=True)
    old = [f"2026-{m:02d}-{d:02d}.jsonl" for m in (5, 6, 7, 8) for d in range(1, 26)]   # 100 days
    for name in old:
        (s.out / name).write_text("{}\n")
    (s.out / "notes.txt").write_text("mine")
    (s.out / "2026-01-01.jsonl.bak").write_text("mine")
    s.step()
    kept = sorted(p.name for p in s.out.iterdir())
    assert "notes.txt" in kept and "2026-01-01.jsonl.bak" in kept
    days = [n for n in kept if n.endswith(".jsonl")]
    assert len(days) == 90
    assert days[-1] == f"{DAY}.jsonl" and days[0] == old[11]      # the 11 oldest are gone


# ---------------------------------------------------------------- pacing and the thread

@pytest.mark.parametrize("at, seconds", [
    ("2026-11-05 09:29:00", 0.5),
    ("2026-11-05 09:35:59.900", 0.5),
    ("2026-11-05 09:28:57", 3.0),        # wakes exactly at the window's start
    ("2026-11-05 04:00:00", 20.0),
    ("2026-11-05 09:36:00", 20.0),
    ("2026-11-07 09:28:57", 20.0),       # a Saturday
])
def test_how_long_it_waits_before_the_next_step(tmp_path, at, seconds):
    assert Setup(tmp_path, at).recorder.wait() == seconds


def test_the_loop_survives_a_step_that_raises_and_stops_when_told(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00")
    waits, steps = [], []

    def step():
        steps.append(1)
        if len(steps) == 1:
            raise OSError("disk full")
        return rec.RECORDED

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            s.recorder.stop()
        return False

    s.recorder.step = step
    s.recorder._stop.wait = wait
    s.recorder.run()                     # returns: the loop ended on stop()
    assert len(steps) == 3
    assert waits == [rec.ERROR_PAUSE_SECONDS, rec.POLL_SECONDS, rec.POLL_SECONDS]


def test_a_slow_request_shortens_the_wait_but_never_to_nothing(tmp_path):
    s = Setup(tmp_path, "2026-11-05 09:30:00", took=0.2)
    waits = []
    s.recorder._stop.wait = lambda seconds: (waits.append(round(seconds, 3)), s.recorder.stop())[1]
    s.recorder.run()
    assert waits == [0.3]                # 0.5 s from one request's start to the next
    s2 = Setup(tmp_path / "slow", "2026-11-05 09:30:00", took=4.0)
    waits2 = []
    s2.recorder._stop.wait = lambda seconds: (waits2.append(seconds), s2.recorder.stop())[1]
    s2.recorder.run()
    assert waits2 == [rec.MIN_SLEEP_SECONDS]


# ---------------------------------------------------------------- on and off

class _Feed:
    """Shaped like scanner/data/alpaca.py's AlpacaFeed for the two requests."""

    def __init__(self):
        self._client = SimpleNamespace(get_stock_snapshot=lambda _request: {})

    def _fetch_recent_minute_bars(self, _symbols, _start, _end):
        return pd.DataFrame()


def test_it_is_off_without_ov_picks_dir(monkeypatch):
    # tests/conftest.py clears OV_PICKS_DIR for every test, as a developer's checkout has it.
    assert rec.from_env(SimpleNamespace(feed=_Feed())) is None
    monkeypatch.setenv("OV_PICKS_DIR", "   ")
    assert rec.from_env(SimpleNamespace(feed=_Feed())) is None


@pytest.mark.parametrize("value", ["0", "off", "OFF", "false", "no", " 0 "])
def test_open_recorder_0_turns_it_off(monkeypatch, tmp_path, value):
    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    monkeypatch.setenv("OPEN_RECORDER", value)
    assert rec.from_env(SimpleNamespace(feed=_Feed())) is None


def test_it_is_off_without_an_alpaca_feed(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    assert rec.from_env(SimpleNamespace(feed=None)) is None
    assert rec.from_env(SimpleNamespace()) is None
    assert rec.from_env(SimpleNamespace(feed=SimpleNamespace())) is None      # a feed that is not Alpaca's


def test_it_is_on_with_the_picks_and_an_alpaca_feed(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    for value in (None, "1", ""):
        if value is None:
            monkeypatch.delenv("OPEN_RECORDER", raising=False)
        else:
            monkeypatch.setenv("OPEN_RECORDER", value)
        recorder = rec.from_env(SimpleNamespace(feed=_Feed()), out_dir=tmp_path / "rec")
        assert recorder is not None and recorder.picks_dir == tmp_path
        assert recorder._bars is not None and recorder.out_dir == tmp_path / "rec"


def test_the_app_starts_its_thread_only_when_it_is_on_and_stops_it_on_shutdown(monkeypatch, tmp_path):
    def app_with(feed):
        app = FastAPI()
        # 03:00 ET on the samples' day: the thread starts and only waits.
        rec.register(app, SimpleNamespace(feed=feed), clock=lambda: _et("2026-11-05 03:00:00"),
                     out_dir=tmp_path / "rec")
        return app

    off = app_with(_Feed())                      # OV_PICKS_DIR is not set
    with TestClient(off):
        assert not hasattr(off.state, "open_recorder")

    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    on = app_with(_Feed())
    with TestClient(on):
        recorder = on.state.open_recorder
        assert recorder.thread.is_alive() and recorder.thread.daemon
        assert recorder.thread.name == "open-recorder"
    recorder.thread.join(timeout=5)
    assert not recorder.thread.is_alive()
    assert not (tmp_path / "rec").exists()       # outside the window it wrote nothing


def test_a_setup_that_raises_never_stops_the_app_from_starting(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_PICKS_DIR", str(tmp_path))
    monkeypatch.setattr(rec, "from_env", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    app = FastAPI()
    rec.register(app, SimpleNamespace(feed=_Feed()))
    with TestClient(app):
        assert not hasattr(app.state, "open_recorder")


def test_the_requests_go_to_the_feeds_own_client_with_the_configured_feed():
    asked = []
    feed = _Feed()
    feed._client = SimpleNamespace(get_stock_snapshot=lambda request: asked.append(request) or {"AAAA": 1})
    assert rec.alpaca_snapshots(feed)(["AAAA", "SPY"]) == {"AAAA": 1}
    assert asked[0].symbol_or_symbols == ["AAAA", "SPY"]
    assert str(asked[0].feed.value) == "sip"      # ALPACA_FEED's default (scanner/data/alpaca.py)

    spans = []
    feed._fetch_recent_minute_bars = lambda symbols, start, end: spans.append((symbols, start, end)) or _frame(symbols)
    bars = rec.alpaca_bars(feed)(datetime(2026, 11, 5).date(), ["AAAA", "SPY"])
    assert sorted(bars) == ["AAAA", "SPY"]
    assert spans == [(["AAAA", "SPY"], _et("2026-11-05 09:30:00"), _et("2026-11-05 09:31:00"))]
