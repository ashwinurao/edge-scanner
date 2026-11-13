"""The OV picks window's live columns (scanner/ov_picks_live.py): Edge Scanner fills them
after the open from the day's 09:30 ET minute bar (Ash, project thread, 2026-11-07,
layout "C1 + E1" and "One request per day shown").

Hand-built bars and rows, a fake fetch and a fake monotonic clock. Nothing here reaches
the network.
"""
import socket
import threading
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ov_picks import contract as c
from scanner import ov_picks_live as live
from scanner import ov_picks_route as route

ET = ZoneInfo("America/New_York")
SAMPLES = route.Path(__file__).resolve().parents[1] / "ov_picks" / "samples"
DAY = date(2026, 11, 5)          # the samples' day, a Thursday
PAST = date(2026, 11, 4)         # the meanings sample's history day


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("an OV picks live test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(route.ENV_VAR, raising=False)


def _et(text):
    return datetime.fromisoformat(text).replace(tzinfo=ET)


def _bar(o, h, low, cl):
    return {"open": o, "high": h, "low": low, "close": cl}


class FakeFetch:
    """Answers from `bars` ({day: {ticker: bar}}) and records each call."""

    def __init__(self, bars=None, error=None):
        self.bars = bars or {}
        self.error = error
        self.calls = []

    def __call__(self, day, tickers):
        self.calls.append((day, list(tickers)))
        if self.error:
            raise self.error
        return {t: b for t, b in self.bars.get(day, {}).items() if t in tickers}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- bands

RANGE_BANDS = [{"max": 0.2, "tone": "good", "note": "tight"},
               {"min": 0.6, "tone": "bad", "note": "wide"},
               {"note": "ordinary"}]
OPEN_BANDS = [{"is": "above", "tone": "good", "note": "over"},
              {"is": "below", "note": "under"}]


@pytest.mark.parametrize("value, want", [
    (0.0, ("good", "tight")),
    (0.2, ("good", "tight")),          # max is inclusive
    (0.21, (None, "ordinary")),        # no tone, the default band's note
    (0.59, (None, "ordinary")),
    (0.6, ("bad", "wide")),            # min is inclusive
    (3.0, ("bad", "wide")),
])
def test_a_number_takes_the_first_band_it_falls_in(value, want):
    assert live.band(RANGE_BANDS, value) == want


def test_the_first_matching_band_wins_over_a_later_one():
    bands = [{"min": 0.5, "tone": "bad", "note": "first"}, {"min": 0.0, "tone": "good", "note": "second"}]
    assert live.band(bands, 0.7) == ("bad", "first")
    assert live.band(bands, 0.3) == ("good", "second")


def test_a_text_value_matches_only_its_is_band():
    assert live.band(OPEN_BANDS, "above") == ("good", "over")
    assert live.band(OPEN_BANDS, "below") == (None, "under")
    assert live.band(OPEN_BANDS, "inside") == (None, None)       # no band: no tone, no note


def test_a_text_value_never_falls_in_a_number_band_and_a_number_never_in_an_is_band():
    assert live.band([{"min": 0, "tone": "bad", "note": "n"}], "above") == (None, None)
    assert live.band([{"is": "1", "tone": "bad", "note": "n"}], 1) == (None, None)
    assert live.band([{"max": 1, "tone": "bad", "note": "n"}], True) == (None, None)


def test_no_bands_means_no_tone_and_no_note():
    assert live.band([], 0.4) == (None, None)


# ---------------------------------------------------------------- values

LEVELS = {"atr": 2.4, "pm_high": 42.82, "pm_low": 39.94}


@pytest.mark.parametrize("bar, want", [
    (_bar(42.0, 42.6, 42.0, 42.4), 0.25),      # 0.60 / 2.4, worked by hand
    (_bar(42.0, 42.0, 42.0, 42.0), 0.0),       # no range
    (_bar(42.0, 43.5, 41.1, 43.0), 1.0),       # 2.40 / 2.4
    (_bar(42.0, 42.11, 42.0, 42.1), 0.05),     # 0.11 / 2.4 = 0.0458, to 2 decimals
])
def test_the_first_minute_range_is_the_bar_range_in_atr(bar, want):
    assert live._value(live.KIND_RANGE_ATR, bar, LEVELS) == (want, "")


@pytest.mark.parametrize("open_, want", [
    (42.83, live.ABOVE),
    (42.82, live.INSIDE),     # at the pre-market high: inside, not above
    (41.00, live.INSIDE),
    (39.94, live.INSIDE),     # at the pre-market low: inside, not below
    (39.93, live.BELOW),
])
def test_the_open_is_placed_against_the_pre_market_range(open_, want):
    assert live._value(live.KIND_OPEN_VS_PM, _bar(open_, open_, open_, open_), LEVELS) == (want, "")


@pytest.mark.parametrize("levels", [{}, {"atr": None}, {"atr": 0}, {"atr": -1.0}])
def test_without_a_usable_atr_the_range_is_blank_and_says_why(levels):
    assert live._value(live.KIND_RANGE_ATR, _bar(1, 2, 1, 2), levels) == (None, live.NOTE_NO_ATR)


@pytest.mark.parametrize("levels", [{}, {"pm_high": 10.0}, {"pm_low": 9.0}])
def test_without_both_pre_market_levels_the_open_is_blank_and_says_why(levels):
    assert live._value(live.KIND_OPEN_VS_PM, _bar(1, 2, 1, 2), levels) == (None, live.NOTE_NO_PM)


def test_an_unknown_kind_is_blank_and_asks_for_a_newer_edge_scanner():
    value, note = live._value("vwap_hold", _bar(1, 2, 1, 2), LEVELS)
    assert value is None and "vwap_hold" in note and "Update Edge Scanner" in note


# ---------------------------------------------------------------- the store

def test_before_0931_et_nothing_is_asked():
    fetch = FakeFetch({DAY: {"AAAA": _bar(1, 2, 1, 2)}})
    store = live.FirstMinuteBars(fetch, clock=Clock())
    for now in ("2026-11-05 09:25:00", "2026-11-05 09:30:59"):
        assert store.get(DAY, ["AAAA"], _et(now)).waiting
    assert fetch.calls == []


def test_a_day_is_asked_once_for_all_its_tickers_and_then_served_from_the_cache():
    fetch = FakeFetch({DAY: {"AAAA": _bar(1, 2, 1, 2), "BBBB": _bar(3, 4, 3, 4)}})
    store = live.FirstMinuteBars(fetch, clock=Clock())
    first = store.get(DAY, ["BBBB", "AAAA", "AAAA"], _et("2026-11-05 09:31:05"))
    again = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 10:40"))
    assert fetch.calls == [(DAY, ["AAAA", "BBBB"])]
    assert first.bars == again.bars == {"AAAA": _bar(1, 2, 1, 2), "BBBB": _bar(3, 4, 3, 4)}
    assert not (first.waiting or first.no_feed or first.problem or first.gone)


def test_today_a_ticker_without_a_bar_is_asked_again_each_minute_until_0935_et_then_has_no_trade():
    fetch = FakeFetch({DAY: {"AAAA": _bar(1, 2, 1, 2)}})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock)

    got = store.get(DAY, ["AAAA", "HALT"], _et("2026-11-05 09:31:05"))
    assert "HALT" not in got.bars and "HALT" not in got.gone     # may still come

    clock.t += 30                                                 # within a minute: not asked
    store.get(DAY, ["AAAA", "HALT"], _et("2026-11-05 09:31:35"))
    assert len(fetch.calls) == 1

    clock.t += 31                                                 # a minute on: only HALT is asked
    got = store.get(DAY, ["AAAA", "HALT"], _et("2026-11-05 09:32:06"))
    assert fetch.calls[1] == (DAY, ["HALT"]) and "HALT" not in got.gone

    clock.t += 200
    got = store.get(DAY, ["AAAA", "HALT"], _et("2026-11-05 09:35:00"))
    assert fetch.calls[2] == (DAY, ["HALT"]) and got.gone == {"HALT"}

    clock.t += 600                                                # gone: never asked again
    store.get(DAY, ["AAAA", "HALT"], _et("2026-11-05 10:40"))
    assert len(fetch.calls) == 3


def test_a_past_day_marks_a_ticker_without_a_bar_at_once():
    fetch = FakeFetch({PAST: {"AAAA": _bar(1, 2, 1, 2)}})
    store = live.FirstMinuteBars(fetch, clock=Clock())
    got = store.get(PAST, ["AAAA", "HALT"], _et("2026-11-05 09:20"))
    assert got.gone == {"HALT"} and set(got.bars) == {"AAAA"}
    assert fetch.calls == [(PAST, ["AAAA", "HALT"])]


def test_a_failed_request_says_why_and_is_asked_again_at_most_once_a_minute():
    fetch = FakeFetch(error=RuntimeError("429 too many requests"))
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock)
    got = store.get(PAST, ["AAAA"], _et("2026-11-05 10:40"))
    assert "RuntimeError" in got.problem and "429" in got.problem
    assert got.gone == set()                                      # a failure is not "no trade"
    clock.t += 59
    assert store.get(PAST, ["AAAA"], _et("2026-11-05 10:41")).problem
    assert len(fetch.calls) == 1
    clock.t += 2
    fetch.error, fetch.bars = None, {PAST: {"AAAA": _bar(1, 2, 1, 2)}}
    got = store.get(PAST, ["AAAA"], _et("2026-11-05 10:42"))
    assert (len(fetch.calls), got.problem, set(got.bars)) == (2, "", {"AAAA"})


def test_without_a_feed_nothing_is_asked_and_the_lookup_says_so():
    store = live.FirstMinuteBars(None, clock=Clock())
    assert store.get(DAY, ["AAAA"], _et("2026-11-05 10:40")).no_feed


def test_a_bad_bar_from_the_fetch_is_left_out():
    fetch = FakeFetch({PAST: {"AAAA": {"open": 1, "high": 2}, "BBBB": _bar(1, float("nan"), 1, 1),
                              "CCCC": _bar(1, 2, 1, 2)}})
    got = live.FirstMinuteBars(fetch, clock=Clock()).get(PAST, ["AAAA", "BBBB", "CCCC"], _et("2026-11-05 10:40"))
    assert set(got.bars) == {"CCCC"} and got.gone == {"AAAA", "BBBB"}


def test_the_store_keeps_the_newest_days_only():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(live.KEEP_DAYS + 2)]
    fetch = FakeFetch({d: {"AAAA": _bar(1, 2, 1, 2)} for d in days})
    store = live.FirstMinuteBars(fetch, clock=Clock())
    for d in days:
        store.get(d, ["AAAA"], _et("2026-11-05 10:40"))
    assert sorted(store._days) == days[-live.KEEP_DAYS:]


def test_a_second_caller_does_not_wait_for_a_request_that_is_out():
    started, release = threading.Event(), threading.Event()

    def slow(day, tickers):
        started.set()
        release.wait(5)
        return {"AAAA": _bar(1, 2, 1, 2)}

    store = live.FirstMinuteBars(slow, clock=Clock())
    worker = threading.Thread(target=store.get, args=(PAST, ["AAAA"], _et("2026-11-05 10:40")))
    worker.start()
    assert started.wait(5)
    got = store.get(PAST, ["AAAA"], _et("2026-11-05 10:40"))
    assert got.busy and got.bars == {}
    release.set()
    worker.join(5)
    assert store.get(PAST, ["AAAA"], _et("2026-11-05 10:40")).bars == {"AAAA": _bar(1, 2, 1, 2)}


# ---------------------------------------------------------------- the Alpaca request

def _frame(rows):
    df = pd.DataFrame(rows, columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index(["symbol", "timestamp"])


def test_the_alpaca_request_asks_for_the_0930_minute_and_keeps_only_that_bar():
    asked = []

    def request(symbols, start, end):
        asked.append((symbols, start, end))
        # 2026-11-05 09:30 ET is 14:30 UTC (EST); the 09:31 bar must be ignored.
        return _frame([("AAAA", "2026-11-05T14:30:00Z", 42.0, 42.6, 42.0, 42.4, 9000),
                       ("AAAA", "2026-11-05T14:31:00Z", 42.4, 44.0, 42.3, 43.9, 8000),
                       ("BBBB", "2026-11-05T14:31:00Z", 18.0, 18.2, 17.9, 18.1, 500)])

    fetch = live.alpaca_fetch(SimpleNamespace(_fetch_recent_minute_bars=request))
    got = fetch(DAY, ["AAAA", "BBBB"])
    assert got == {"AAAA": _bar(42.0, 42.6, 42.0, 42.4)}
    symbols, start, end = asked[0]
    assert symbols == ["AAAA", "BBBB"]
    assert start == _et("2026-11-05 09:30") and end == _et("2026-11-05 09:31")


def test_the_0930_bar_is_found_on_a_summer_day_too():
    # 2026-07-01 09:30 ET is 13:30 UTC (EDT): the time comes from the time-zone library.
    frame = _frame([("AAAA", "2026-07-01T13:30:00Z", 1.0, 2.0, 0.5, 1.5, 10),
                    ("AAAA", "2026-07-01T14:30:00Z", 9.0, 9.0, 9.0, 9.0, 10)])
    assert live.bars_at(frame, _et("2026-07-01 09:30")) == {"AAAA": _bar(1.0, 2.0, 0.5, 1.5)}


def test_an_empty_answer_has_no_bars():
    assert live.bars_at(pd.DataFrame(), _et("2026-11-05 09:30")) == {}
    assert live.bars_at(None, _et("2026-11-05 09:30")) == {}


def test_a_feed_without_the_minute_request_has_no_fetch():
    assert live.alpaca_fetch(None) is None
    assert live.alpaca_fetch(SimpleNamespace(get_bars_range=lambda *a: None)) is None


# ---------------------------------------------------------------- filling rows

def _row(ticker, levels=None, kinds=(live.KIND_RANGE_ATR, live.KIND_OPEN_VS_PM)):
    metrics = [{"label": "Rank", "value": 1}]
    for kind in kinds:
        metrics.append({"label": kind, "value": None,
                        "live": {"kind": kind, "bands": RANGE_BANDS if kind == live.KIND_RANGE_ATR else OPEN_BANDS}})
    return {"ticker": ticker, "metrics": metrics, "levels": LEVELS if levels is None else levels}


def test_fill_sets_value_tone_and_note_from_the_bar_and_adds_the_bar():
    row = _row("AAAA")
    lookup = live.Lookup(bars={"AAAA": _bar(42.9, 43.0, 42.9, 43.0)})    # 0.1/2.4 = 0.04, above PM
    live.fill([row], lookup)
    rank, rng, opn = row["metrics"]
    assert rank == {"label": "Rank", "value": 1}                         # not a live column: untouched
    assert (rng["value"], rng["tone"], rng["note"]) == (0.04, "good", "tight")
    assert (opn["value"], opn["tone"], opn["note"]) == ("above", "good", "over")
    assert row[live.FIRST_MINUTE] == _bar(42.9, 43.0, 42.9, 43.0)


def test_a_row_without_a_live_column_is_left_as_read():
    row = {"ticker": "AAAA", "metrics": [{"label": "Rank", "value": 1}], "levels": LEVELS}
    live.fill([row], live.Lookup(bars={"AAAA": _bar(1, 2, 1, 2)}))
    assert row == {"ticker": "AAAA", "metrics": [{"label": "Rank", "value": 1}], "levels": LEVELS}


@pytest.mark.parametrize("lookup, note", [
    (None, live.NOTE_WAITING),
    (live.Lookup(waiting=True), live.NOTE_WAITING),
    (live.Lookup(no_feed=True), live.NOTE_NO_FEED),
    (live.Lookup(gone={"AAAA"}), live.NOTE_NO_TRADE),
    (live.Lookup(problem="RuntimeError: boom"), live.note_failed("RuntimeError: boom")),
    (live.Lookup(busy=True), live.NOTE_LOADING),
    (live.Lookup(), live.NOTE_NOT_YET),
])
def test_a_blank_live_cell_says_why(lookup, note):
    row = _row("AAAA")
    live.fill([row], lookup)
    for metric in row["metrics"][1:]:
        assert (metric["value"], metric["tone"], metric["note"]) == (None, None, note)
    assert row[live.FIRST_MINUTE] is None


def test_a_row_without_levels_says_which_level_is_missing():
    row = _row("AAAA", levels={})
    live.fill([row], live.Lookup(bars={"AAAA": _bar(1, 2, 1, 2)}))
    assert [m["note"] for m in row["metrics"][1:]] == [live.NOTE_NO_ATR, live.NOTE_NO_PM]
    assert row[live.FIRST_MINUTE] == _bar(1, 2, 1, 2)


def test_live_tickers_are_the_rows_with_a_column_this_scanner_can_fill():
    rows = [_row("AAAA"), _row("BBBB", kinds=("vwap_hold",)),
            {"ticker": "CCCC", "metrics": [{"label": "Rank", "value": 3}]}]
    assert live.live_tickers(rows) == ["AAAA"]


# ---------------------------------------------------------------- through the route

def _get(monkeypatch, now, fetch, asked="", app_state=None):
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "meanings"))
    app = FastAPI()
    route.register(app, app_state or SimpleNamespace(), clock=lambda: _et(now), first_minute_fetch=fetch)
    resp = TestClient(app).get(route.ROUTE, params={"date": asked} if asked else {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _cells(rows, label):
    return {p["ticker"]: next(m for m in p["metrics"] if m["label"] == label) for p in rows}


def _sample_bars(day):
    """A 09:30 bar for each meanings row, from its levels: AAAA wide and above the PM high,
    BBBB tight and inside."""
    view = c.read_day(SAMPLES / "meanings", day) if day == DAY else c.read_history(SAMPLES / "meanings", day)
    bars = {}
    for p in view["picks"]:
        lv = p["levels"]
        bars[p["ticker"]] = _bar(lv["pm_high"] + 0.01, lv["pm_high"] + 0.01 + 0.7 * lv["atr"],
                                 lv["pm_high"] + 0.01, lv["pm_high"] + 0.5 * lv["atr"])
    bars["BBBB"] = _bar(18.30, 18.35, 18.25, 18.30)     # 0.10 / 0.95 = 0.11: tight; inside 18.06-18.58
    return bars


def test_the_route_fills_todays_live_columns_after_0931_et_with_one_request(monkeypatch):
    fetch = FakeFetch({DAY: _sample_bars(DAY)})
    body = _get(monkeypatch, "2026-11-05 10:40", fetch)
    rows = body["day"]["picks"]
    assert fetch.calls == [(DAY, sorted(p["ticker"] for p in rows))]
    rng, opn = _cells(rows, "1st min (ATR)"), _cells(rows, "Open vs PM")
    assert (rng["AAAA"]["value"], rng["AAAA"]["tone"]) == (0.7, "bad")
    assert rng["AAAA"]["note"].startswith("Sample note: a wide first minute")
    assert (rng["BBBB"]["value"], rng["BBBB"]["tone"]) == (0.11, "good")
    assert (opn["AAAA"]["value"], opn["BBBB"]["value"]) == ("above", "inside")
    assert rows[1][live.FIRST_MINUTE] == _bar(18.30, 18.35, 18.25, 18.30)
    assert body["warnings"] == []
    # Everything but the live cells and the added bar is the reader's view.
    read = c.read_day(SAMPLES / "meanings", DAY)
    for got, want in zip(rows, read["picks"]):
        assert {k: v for k, v in got.items() if k != live.FIRST_MINUTE} | {"metrics": None} == want | {"metrics": None}
        assert [m for m in got["metrics"] if "live" not in m] == [m for m in want["metrics"] if "live" not in m]


def test_the_route_asks_nothing_before_0931_et_and_says_when_the_cells_fill(monkeypatch):
    fetch = FakeFetch({DAY: _sample_bars(DAY)})
    body = _get(monkeypatch, "2026-11-05 09:28", fetch)
    assert fetch.calls == []
    cells = _cells(body["day"]["picks"], "1st min (ATR)")
    assert {m["note"] for m in cells.values()} == {live.NOTE_WAITING}
    assert all(m["value"] is None for m in cells.values())


def test_a_past_days_history_rows_are_filled_from_that_days_bars(monkeypatch):
    fetch = FakeFetch({PAST: _sample_bars(PAST)})
    body = _get(monkeypatch, "2026-11-05 10:40", fetch, asked=PAST.isoformat())
    assert body["day"]["has_picks"] is False and body["history"]["has_history"] is True
    assert [d for d, _ in fetch.calls] == [PAST]
    cells = _cells(body["history"]["picks"], "1st min (ATR)")
    assert cells["CCCC"]["value"] == 0.7 and cells["CCCC"]["tone"] == "bad"


def test_without_a_feed_the_route_shows_the_picks_with_blank_live_cells(monkeypatch):
    body = _get(monkeypatch, "2026-11-05 10:40", None)
    assert body["day"]["has_picks"] and body["warnings"] == []
    cells = _cells(body["day"]["picks"], "Open vs PM")
    assert {m["note"] for m in cells.values()} == {live.NOTE_NO_FEED}


def test_a_failed_request_leaves_blank_cells_that_say_so(monkeypatch):
    body = _get(monkeypatch, "2026-11-05 10:40", FakeFetch(error=TimeoutError("read timed out")))
    assert body["day"]["has_picks"] and body["warnings"] == []
    notes = {m["note"] for m in _cells(body["day"]["picks"], "1st min (ATR)").values()}
    assert notes == {live.note_failed("TimeoutError: read timed out")}


def test_a_failure_while_filling_is_a_warning_and_the_picks_still_show(monkeypatch):
    def boom(*_a, **_k):
        raise KeyError("ticker")
    monkeypatch.setattr(live, "fill_answer", boom)
    body = _get(monkeypatch, "2026-11-05 10:40", FakeFetch())
    assert body["day"]["has_picks"] and len(body["day"]["picks"]) == 8
    assert len(body["warnings"]) == 1 and "could not be filled" in body["warnings"][0]


def test_a_module_file_without_live_columns_makes_no_request_and_is_served_as_read(monkeypatch):
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "frozen"))
    fetch = FakeFetch()
    app = FastAPI()
    route.register(app, SimpleNamespace(), clock=lambda: _et("2026-11-05 10:40"), first_minute_fetch=fetch)
    body = TestClient(app).get(route.ROUTE).json()
    assert fetch.calls == []
    assert body["day"] == c.read_day(SAMPLES / "frozen", DAY)


def test_the_preview_never_asks_and_says_when_it_fills(tmp_path, monkeypatch):
    # A preview day built from the meanings rows, at 09:14 ET.
    day = c.read_day(SAMPLES / "meanings", DAY)
    rows = [{k: v for k, v in p.items()} for p in day["picks"]]
    c.write_preview(tmp_path, {"trading_date": DAY.isoformat(), "as_of": "2026-11-05T14:14:00Z",
                               "previewed_at": "2026-11-05T14:14:20Z", "shadow": True,
                               "earnings_status": c.EARNINGS_AVAILABLE, "metric_labels": day["metric_labels"],
                               "picks": rows})
    monkeypatch.setenv(route.ENV_VAR, str(tmp_path))
    fetch = FakeFetch()
    app = FastAPI()
    route.register(app, SimpleNamespace(), clock=lambda: _et("2026-11-05 09:14:30"), first_minute_fetch=fetch)
    body = TestClient(app).get(route.ROUTE).json()
    assert body["preview"]["has_preview"] and fetch.calls == []
    assert {m["note"] for m in _cells(body["preview"]["picks"], "Open vs PM").values()} == {live.NOTE_WAITING}


def test_create_app_uses_the_feeds_minute_request(monkeypatch):
    from scanner.api import AppState, create_app
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "meanings"))
    asked = []

    def request(symbols, start, end):
        asked.append((list(symbols), start))
        return pd.DataFrame()

    feed = SimpleNamespace(_fetch_recent_minute_bars=request)
    scanner = SimpleNamespace(_states={}, _spy_state=None, _regime=None, _sector_map={})
    app = create_app(AppState(scanner=scanner, feed=feed))  # type: ignore[arg-type]
    body = TestClient(app).get(route.ROUTE, params={"date": PAST.isoformat()}).json()
    assert asked and asked[0][1] == _et("2026-11-04 09:30")
    assert body["history"]["has_history"]


# ---------------------------------------------------------------- the minute so far (2026-11-08)
# Ash, project thread, 2026-11-08 05:10 UTC: "the 1st min atr and open vs pm should update
# every 2-3 secs along with live data so I can quickly make decision whether to take or1 or o2
# right after". During 09:30-09:31 ET the cells show the minute so far, from Alpaca's
# snapshot; the 09:30 bar replaces it from 09:31:03 ET.

class FakeSoFar:
    """Answers the minute so far from `minute` ({ticker: bar with as_of}) and records each call."""

    def __init__(self, minute=None, error=None):
        self.minute = minute or {}
        self.error = error
        self.calls = []

    def __call__(self, day, tickers):
        self.calls.append((day, list(tickers)))
        if self.error:
            raise self.error
        return {t: dict(b) for t, b in self.minute.items() if t in tickers}


def _so_far(o, h, low, cl, at):
    return {**_bar(o, h, low, cl), "as_of": _et(at)}


def _snap(bar_day, o, h, low, cl, last):
    """An Alpaca snapshot as alpaca-py gives it: a day bar stamped midnight ET of `bar_day`
    (Alpaca stamps a day bar at the start of its New York day) and the latest trade."""
    stamp = datetime.fromisoformat(bar_day).replace(tzinfo=ET).astimezone(ZoneInfo("UTC"))
    daily = SimpleNamespace(timestamp=stamp, open=o, high=h, low=low, close=cl, volume=1000)
    trade = SimpleNamespace(timestamp=_et(last).astimezone(ZoneInfo("UTC")), price=cl, size=100)
    return SimpleNamespace(daily_bar=daily, latest_trade=trade)


def test_a_snapshot_inside_the_minute_is_the_minute_so_far():
    got = live.so_far({"AAAA": _snap("2026-11-05", 42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")}, DAY)
    assert got == {"AAAA": {**_bar(42.9, 43.4, 42.7, 43.1), "as_of": _et("2026-11-05 09:30:41")}}


@pytest.mark.parametrize("snap", [
    _snap("2026-11-04", 42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41"),   # the prior day's bar
    _snap("2026-11-05", 42.9, 43.4, 42.7, 43.1, "2026-11-05 09:29:59"),   # last trade before the open
    _snap("2026-11-05", 42.9, 43.9, 42.7, 43.8, "2026-11-05 09:31:00"),   # the day bar has 09:31 trades
    _snap("2026-11-05", 42.9, float("nan"), 42.7, 43.1, "2026-11-05 09:30:41"),
    SimpleNamespace(daily_bar=None, latest_trade=None),
])
def test_a_snapshot_that_is_not_the_minute_so_far_is_left_out(snap):
    assert live.so_far({"AAAA": snap}, DAY) == {}


def test_a_day_bar_stamped_at_midnight_utc_is_that_days_too():
    snap = _snap("2026-11-05", 42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")
    snap.daily_bar.timestamp = datetime(2026, 11, 5, tzinfo=ZoneInfo("UTC"))      # 19:00 ET the day before
    assert set(live.so_far({"AAAA": snap}, DAY)) == {"AAAA"}
    snap.daily_bar.timestamp = datetime(2026, 11, 4, tzinfo=ZoneInfo("UTC"))      # the prior day's bar
    assert live.so_far({"AAAA": snap}, DAY) == {}


def test_the_minute_so_far_is_found_on_a_summer_day_too():
    # 2026-07-01: New York is on EDT, so midnight ET and 09:30 ET come from the time-zone library.
    got = live.so_far({"AAAA": _snap("2026-07-01", 1.0, 2.0, 0.5, 1.5, "2026-07-01 09:30:05")}, date(2026, 7, 1))
    assert got["AAAA"]["as_of"] == _et("2026-07-01 09:30:05")
    assert live.so_far(None, DAY) == {}


def test_the_alpaca_minute_so_far_is_one_snapshot_request_through_the_feeds_data_client():
    asked = []

    def get_stock_snapshot(request):
        asked.append(request)
        return {"AAAA": _snap("2026-11-05", 42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")}

    forming = live.alpaca_forming(SimpleNamespace(_client=SimpleNamespace(get_stock_snapshot=get_stock_snapshot)))
    assert forming(DAY, ["AAAA", "BBBB"]) == {"AAAA": {**_bar(42.9, 43.4, 42.7, 43.1), "as_of": _et("2026-11-05 09:30:41")}}
    assert len(asked) == 1 and asked[0].symbol_or_symbols == ["AAAA", "BBBB"]
    assert live.alpaca_forming(None) is None
    assert live.alpaca_forming(SimpleNamespace(_client=SimpleNamespace())) is None


def test_during_the_minute_the_store_asks_for_the_minute_so_far_every_two_seconds_and_not_for_the_bar():
    fetch = FakeFetch({DAY: {"AAAA": _bar(42.9, 43.6, 42.6, 43.3)}})
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)

    assert store.get(DAY, ["AAAA"], _et("2026-11-05 09:29:59")).forming == {}     # before the open: nothing
    got = store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:42"))
    assert got.waiting and got.live and got.forming == {"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")}
    clock.t += 1.7
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:44"))
    assert len(so_far.calls) == 1                                                   # within 1.75 s: served as known
    clock.t += 0.1
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:44"))
    assert so_far.calls == [(DAY, ["AAAA"])] * 2 and fetch.calls == []


def test_a_slower_answer_never_puts_an_older_minute_over_a_newer_one():
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")})
    clock = Clock()
    store = live.FirstMinuteBars(FakeFetch(), clock=clock, forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:42"))
    so_far.minute = {"AAAA": _so_far(42.9, 43.0, 42.8, 43.0, "2026-11-05 09:30:20")}
    clock.t += 3
    got = store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:45"))
    assert got.forming["AAAA"]["high"] == 43.4


def test_a_failed_minute_so_far_keeps_the_last_values_and_is_asked_again_two_seconds_on():
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")})
    clock = Clock()
    store = live.FirstMinuteBars(FakeFetch(), clock=clock, forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:42"))
    so_far.error = RuntimeError("429 too many requests")
    clock.t += 2
    got = store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:44"))
    assert got.forming["AAAA"]["high"] == 43.4 and len(so_far.calls) == 2


def test_the_minute_so_far_is_never_asked_for_a_past_day():
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")})
    store = live.FirstMinuteBars(FakeFetch(), clock=Clock(), forming=so_far)
    for now in ("2026-11-05 09:30:42", "2026-11-05 09:31:01", "2026-11-05 09:31:05"):
        store.get(PAST, ["AAAA"], _et(now))
    assert so_far.calls == []


def test_from_0931_03_the_bar_replaces_the_minute_so_far_and_is_asked_again_every_two_seconds_until_0931_30():
    fetch = FakeFetch({DAY: {"AAAA": _bar(42.9, 43.6, 42.6, 43.3)}})
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:58"),
                        "BBBB": _so_far(18.3, 18.4, 18.2, 18.3, "2026-11-05 09:30:57")})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)
    store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:30:58"))

    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:02"))
    assert fetch.calls == [] and set(got.forming) == {"AAAA", "BBBB"}            # not due yet
    clock.t += 4
    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:03"))
    assert fetch.calls == [(DAY, ["AAAA", "BBBB"])]
    assert got.bars == {"AAAA": _bar(42.9, 43.6, 42.6, 43.3)} and set(got.forming) == {"BBBB"}

    clock.t += 1.9
    store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:05"))
    assert len(fetch.calls) == 1
    clock.t += 0.1
    fetch.bars[DAY]["BBBB"] = _bar(18.3, 18.45, 18.2, 18.4)
    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:05"))
    assert fetch.calls[1] == (DAY, ["BBBB"]) and got.forming == {} and set(got.bars) == {"AAAA", "BBBB"}
    # While BBBB had no bar, the snapshot was asked for it alone (brief 20), 1.9 s apart (the
    # slack, LIVE_SLACK_SECONDS); with both bars, never.
    assert so_far.calls[1:] == [(DAY, ["BBBB"])] * 2


def test_after_0931_30_a_missing_bar_is_asked_again_once_a_minute():
    fetch = FakeFetch({DAY: {}})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock)
    store.get(DAY, ["HALT"], _et("2026-11-05 09:31:29"))
    clock.t += 2
    store.get(DAY, ["HALT"], _et("2026-11-05 09:31:31"))
    assert len(fetch.calls) == 1
    clock.t += 58
    store.get(DAY, ["HALT"], _et("2026-11-05 09:32:29"))
    assert len(fetch.calls) == 2


def test_fill_shows_the_minute_so_far_says_until_when_and_adds_it_as_the_first_minute():
    row = _row("AAAA")
    so_far = _so_far(42.9, 43.0, 42.9, 43.0, "2026-11-05 09:30:41")
    live.fill([row], live.Lookup(waiting=True, live=True, forming={"AAAA": so_far}))
    _, rng, opn = row["metrics"]
    assert (rng["value"], rng["tone"]) == (0.04, "good")
    assert rng["note"] == ("So far, to 09:30:41 ET. From Alpaca's snapshot; the 09:30 bar replaces it "
                           "just after 09:31. tight")
    assert (opn["value"], opn["tone"]) == ("above", "good")
    assert row[live.FIRST_MINUTE] == _bar(42.9, 43.0, 42.9, 43.0)
    assert row[live.AS_OF] == "09:30:41"


def test_with_its_bar_a_row_has_no_time_so_far():
    row = _row("AAAA")
    live.fill([row], live.Lookup(bars={"AAAA": _bar(42.9, 43.0, 42.9, 43.0)}))
    assert live.AS_OF not in row and row["metrics"][1]["note"] == "tight"


def test_before_its_bar_a_ticker_the_snapshot_does_not_show_yet_says_so():
    row = _row("AAAA")
    live.fill([row], live.Lookup(waiting=True, live=True))
    assert {m["note"] for m in row["metrics"][1:]} == {live.NOTE_NOT_IN_SNAPSHOT_YET}


@pytest.mark.parametrize("now, lookup, want", [
    ("2026-11-05 09:29:44", live.Lookup(waiting=True), False),
    ("2026-11-05 09:29:45", live.Lookup(waiting=True), True),
    ("2026-11-05 09:30:30", live.Lookup(waiting=True, live=True), True),
    ("2026-11-05 09:31:10", live.Lookup(bars={"AAAA": _bar(1, 2, 1, 2)}), True),           # BBBB still to come
    ("2026-11-05 09:31:10", live.Lookup(bars={"AAAA": _bar(1, 2, 1, 2)}, gone={"BBBB"}), False),
    ("2026-11-05 09:31:30", live.Lookup(), False),
    ("2026-11-05 09:31:10", live.Lookup(no_feed=True), False),
])
def test_the_window_refreshes_quickly_only_while_todays_cells_are_about_to_fill_or_forming(now, lookup, want):
    assert live.fast(DAY, ["AAAA", "BBBB"], lookup, _et(now)) is want


def _get_live(monkeypatch, now, fetch, so_far, asked=""):
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "meanings"))
    app = FastAPI()
    route.register(app, SimpleNamespace(), clock=lambda: _et(now), first_minute_fetch=fetch,
                   first_minute_so_far=so_far)
    resp = TestClient(app).get(route.ROUTE, params={"date": asked} if asked else {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_route_fills_the_minute_so_far_and_asks_the_window_to_refresh_every_two_seconds(monkeypatch):
    bars = _sample_bars(DAY)
    so_far = FakeSoFar({t: {**b, "as_of": _et("2026-11-05 09:30:41")} for t, b in bars.items() if t != "BBBB"})
    fetch = FakeFetch({DAY: bars})
    body = _get_live(monkeypatch, "2026-11-05 09:30:42", fetch, so_far)
    rows = {p["ticker"]: p for p in body["day"]["picks"]}
    assert fetch.calls == [] and len(so_far.calls) == 1
    assert body["refresh_seconds"] == live.REFRESH_SECONDS
    rng = _cells(body["day"]["picks"], "1st min (ATR)")
    assert (rng["AAAA"]["value"], rng["AAAA"]["tone"]) == (0.7, "bad")
    assert rng["AAAA"]["note"].startswith("So far, to 09:30:41 ET.")
    assert rows["AAAA"][live.AS_OF] == "09:30:41"
    assert rng["BBBB"]["value"] is None and rng["BBBB"]["note"] == live.NOTE_NOT_IN_SNAPSHOT_YET


def test_the_route_does_not_ask_the_window_to_hurry_outside_the_minute_or_for_a_past_day(monkeypatch):
    fetch, so_far = FakeFetch({DAY: _sample_bars(DAY), PAST: _sample_bars(PAST)}), FakeSoFar()
    for now in ("2026-11-05 09:29:30", "2026-11-05 10:40"):
        assert _get_live(monkeypatch, now, fetch, so_far)["refresh_seconds"] is None
    assert _get_live(monkeypatch, "2026-11-05 09:30:42", fetch, so_far, asked=PAST.isoformat())["refresh_seconds"] is None
    assert _get_live(monkeypatch, "2026-11-05 09:30:42", None, None)["refresh_seconds"] is None
    assert so_far.calls == []


def test_the_route_asks_the_scanners_feed_for_the_minute_so_far_by_default(monkeypatch):
    asked = []

    def get_stock_snapshot(request):
        asked.append(list(request.symbol_or_symbols))
        return {}

    feed = SimpleNamespace(_fetch_recent_minute_bars=lambda *a: pd.DataFrame(),
                           _client=SimpleNamespace(get_stock_snapshot=get_stock_snapshot))
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "meanings"))
    app = FastAPI()
    route.register(app, SimpleNamespace(feed=feed), clock=lambda: _et("2026-11-05 09:30:10"))
    body = TestClient(app).get(route.ROUTE).json()
    assert asked == [sorted(p["ticker"] for p in body["day"]["picks"])]
    assert body["refresh_seconds"] == live.REFRESH_SECONDS


# ---------------------------------------------------------------- the snapshot's 09:30 bar (brief 20, 2026-11-13)
# The first measured open (2026-11-13, the opening recorder's file): the snapshot's day bar
# stayed the prior day's until 09:31:00.65 ET, so the minute so far showed nothing, while
# the snapshot's minute bar became the finished 09:30 bar at 09:31:00.08-0.65 and the bars
# request had it only at 09:31:01.4-06.5. Ash's go, project chat, 2026-11-13 15:38:32 UTC:
# fill the "1st min" columns from that minute bar, 2 to 5 s sooner, marked not confirmed.

def _snap_min(minute_at, o, h, low, cl, bar_day="2026-11-04", last="2026-11-05 09:31:00.40"):
    """An Alpaca snapshot as seen just after 09:31 on 2026-11-13: the minute bar stamped
    `minute_at` (ET, None for no minute bar), the prior day's day bar, a trade from 09:31."""
    snap = _snap(bar_day, 40.0, 41.0, 39.0, 40.5, last)
    snap.minute_bar = None if minute_at is None else SimpleNamespace(
        timestamp=_et(minute_at).astimezone(ZoneInfo("UTC")), open=o, high=h, low=low, close=cl, volume=5000)
    return snap


def _whole(o, h, low, cl, day="2026-11-05"):
    """The snapshot's 09:30 bar as the store keeps it: the whole minute, as of its end."""
    return {**_bar(o, h, low, cl), "as_of": _et(f"{day} 09:31:00")}


def test_a_snapshot_minute_bar_stamped_0930_is_the_whole_minute_as_of_0931():
    got = live.so_far({"AAAA": _snap_min("2026-11-05 09:30", 42.9, 43.4, 42.7, 43.1)}, DAY)
    assert got == {"AAAA": _whole(42.9, 43.4, 42.7, 43.1)}


@pytest.mark.parametrize("minute_at", [
    "2026-11-05 09:29",       # the pre-market's last minute: the 09:30 bar is not out yet
    "2026-11-05 09:31",       # a later minute: the 09:30 bar is gone from the snapshot
    "2026-11-04 09:30",       # another day's 09:30 bar
    "2026-11-05 09:30:01",    # not on the minute
    None,                     # no minute bar
])
def test_a_snapshot_minute_bar_not_stamped_0930_of_the_day_fills_nothing(minute_at):
    assert live.so_far({"AAAA": _snap_min(minute_at, 42.9, 43.4, 42.7, 43.1)}, DAY) == {}


def test_a_minute_bar_with_a_bad_price_fills_nothing():
    assert live.so_far({"AAAA": _snap_min("2026-11-05 09:30", 42.9, float("nan"), 42.7, 43.1)}, DAY) == {}


def test_the_snapshots_0930_bar_wins_over_the_day_bar_so_far():
    # Both in one snapshot: the minute bar is the whole minute, the day bar only part of it.
    snap = _snap_min("2026-11-05 09:30", 42.9, 43.4, 42.7, 43.1, bar_day="2026-11-05", last="2026-11-05 09:30:59")
    assert live.so_far({"AAAA": snap}, DAY) == {"AAAA": _whole(42.9, 43.4, 42.7, 43.1)}


def test_the_snapshots_0930_bar_is_found_on_a_summer_day_too():
    # 2026-07-01 09:30 ET is 13:30 UTC (EDT): the time comes from the time-zone library.
    snap = _snap_min("2026-07-01 09:30", 1.0, 2.0, 0.5, 1.5, bar_day="2026-06-30", last="2026-07-01 09:31:00.40")
    assert live.so_far({"AAAA": snap}, date(2026, 7, 1)) == {"AAAA": _whole(1.0, 2.0, 0.5, 1.5, day="2026-07-01")}


def test_fill_shows_the_snapshots_0930_bar_marked_not_confirmed():
    row = _row("AAAA")
    live.fill([row], live.Lookup(waiting=True, live=True, forming={"AAAA": _whole(42.9, 43.0, 42.9, 43.0)}))
    _, rng, opn = row["metrics"]
    assert (rng["value"], rng["tone"]) == (0.04, "good")
    assert (opn["value"], opn["tone"]) == ("above", "good")
    assert rng["note"] == ("The 09:30 bar as Alpaca's snapshot shows it, not confirmed yet. The 09:30 bar "
                           "request confirms it within seconds. tight")
    assert opn["note"] == live.note_unconfirmed("over")
    assert row[live.FIRST_MINUTE] == _bar(42.9, 43.0, 42.9, 43.0)
    # Edge Trader reads first_minute_as_of as "not final"; its order buttons wait for the bar.
    assert row[live.AS_OF] == "09:31:00"


def test_after_0931_the_snapshot_is_asked_every_two_seconds_but_only_for_tickers_without_their_bar():
    fetch = FakeFetch({DAY: {}})
    so_far = FakeSoFar({"AAAA": _whole(42.9, 43.4, 42.7, 43.1), "BBBB": _whole(18.3, 18.4, 18.2, 18.3)})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)

    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:00.5"))     # before the bar is due
    assert got.waiting and got.forming == so_far.minute and fetch.calls == []
    clock.t += 1.7
    store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:02.2"))
    assert len(so_far.calls) == 1                                            # within 1.75 s: served as known
    clock.t += 0.1
    store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:02.3"))
    assert len(so_far.calls) == 2

    clock.t += 0.7                                                           # 09:31:03: the bars request, no bar yet
    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:03"))
    assert fetch.calls == [(DAY, ["AAAA", "BBBB"])] and len(so_far.calls) == 2
    assert got.forming == so_far.minute and not got.waiting                  # the cells keep the snapshot's bar

    clock.t += 2
    fetch.bars[DAY]["AAAA"] = _bar(42.9, 43.4, 42.7, 43.1)
    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:05"))
    assert fetch.calls[-1] == (DAY, ["AAAA", "BBBB"]) and so_far.calls[-1] == (DAY, ["BBBB"])
    assert set(got.bars) == {"AAAA"} and set(got.forming) == {"BBBB"}

    clock.t += 2
    fetch.bars[DAY]["BBBB"] = _bar(18.3, 18.4, 18.2, 18.3)
    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:07"))
    assert set(got.bars) == {"AAAA", "BBBB"} and got.forming == {}
    asked = len(so_far.calls)
    clock.t += 30
    store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:37"))
    assert len(so_far.calls) == asked == 3                                   # every bar is in: never again


def test_the_snapshot_is_never_asked_from_0935_et():
    fetch = FakeFetch({DAY: {}})
    so_far = FakeSoFar()
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)
    store.get(DAY, ["HALT"], _et("2026-11-05 09:34:59"))
    assert len(so_far.calls) == 1                                            # still missing, before 09:35
    clock.t += 61
    got = store.get(DAY, ["HALT"], _et("2026-11-05 09:35:00"))
    assert got.gone == {"HALT"} and len(so_far.calls) == 1
    # A failed bars request after 09:35 leaves the ticker missing, but the snapshot stays quiet.
    fetch.error = RuntimeError("429 too many requests")
    clock.t += 61
    store.get(DAY, ["LATE"], _et("2026-11-05 09:36:01"))
    assert len(so_far.calls) == 1


def test_after_0931_a_failed_snapshot_keeps_the_last_values_and_is_asked_again():
    fetch = FakeFetch({DAY: {}})
    so_far = FakeSoFar({"AAAA": _whole(42.9, 43.4, 42.7, 43.1)})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:31:01"))
    so_far.error = RuntimeError("429 too many requests")
    clock.t += 2
    got = store.get(DAY, ["AAAA"], _et("2026-11-05 09:31:03"))
    assert got.forming == {"AAAA": _whole(42.9, 43.4, 42.7, 43.1)} and len(so_far.calls) == 2
    clock.t += 2
    so_far.error = None
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:31:05"))
    assert len(so_far.calls) == 3


def test_the_bar_replaces_the_snapshots_bar_and_a_difference_over_a_cent_logs_one_warning(caplog):
    caplog.set_level("INFO", logger=live.__name__)
    so_far = FakeSoFar({"AAAA": _whole(42.90, 43.40, 42.70, 43.10), "BBBB": _whole(18.30, 18.40, 18.20, 18.30)})
    fetch = FakeFetch({DAY: {"AAAA": _bar(42.90, 43.42, 42.70, 43.10),      # high 2 cents higher
                             "BBBB": _bar(18.31, 18.41, 18.19, 18.31)}})    # each one cent off: no warning
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)
    store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:01"))
    clock.t += 2
    got = store.get(DAY, ["AAAA", "BBBB"], _et("2026-11-05 09:31:03"))
    assert got.bars == {"AAAA": _bar(42.90, 43.42, 42.70, 43.10), "BBBB": _bar(18.31, 18.41, 18.19, 18.31)}
    assert got.forming == {}
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "AAAA" in text and "43.42" in text and "43.40" in text and "BBBB" not in text
    row = _row("AAAA")
    live.fill([row], got)
    assert live.AS_OF not in row and row[live.FIRST_MINUTE] == _bar(42.90, 43.42, 42.70, 43.10)
    assert row["metrics"][1]["note"] == "ordinary"                          # 0.72 / 2.4 = 0.3: the band's note alone


def test_a_minute_so_far_that_differs_from_the_bar_logs_no_warning(caplog):
    # The day bar's minute so far misses the minute's last trades, so a difference is expected.
    caplog.set_level("INFO", logger=live.__name__)
    so_far = FakeSoFar({"AAAA": _so_far(42.90, 43.20, 42.80, 43.00, "2026-11-05 09:30:50")})
    store = live.FirstMinuteBars(FakeFetch({DAY: {"AAAA": _bar(42.90, 43.60, 42.60, 43.30)}}), clock=Clock(), forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:51"))
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:31:03"))
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_a_preview_never_asks_for_the_snapshot_or_the_bars():
    so_far, fetch = FakeSoFar(), FakeFetch()
    store = live.FirstMinuteBars(fetch, clock=Clock(), forming=so_far)
    body = {"preview": {"picks": [_row("AAAA")]}}
    live.fill_answer(body, store, _et("2026-11-05 09:31:05"))
    assert so_far.calls == [] and fetch.calls == []
    assert {m["note"] for m in body["preview"]["picks"][0]["metrics"][1:]} == {live.NOTE_WAITING}


def test_the_route_fills_the_columns_from_the_snapshots_0930_bar_before_the_bars_request_has_it(monkeypatch):
    bars = _sample_bars(DAY)
    snaps = {t: _snap_min("2026-11-05 09:30", b["open"], b["high"], b["low"], b["close"]) for t, b in bars.items()}
    asked = []

    def get_stock_snapshot(request):
        asked.append(list(request.symbol_or_symbols))
        return {t: s for t, s in snaps.items() if t in request.symbol_or_symbols}

    # The bars request has no 09:30 bar yet (2026-11-13: it had them at 09:31:01.4 to 06.5).
    feed = SimpleNamespace(_fetch_recent_minute_bars=lambda *a: pd.DataFrame(),
                           _client=SimpleNamespace(get_stock_snapshot=get_stock_snapshot))
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "meanings"))
    app = FastAPI()
    route.register(app, SimpleNamespace(feed=feed), clock=lambda: _et("2026-11-05 09:31:04"))
    body = TestClient(app).get(route.ROUTE).json()
    rows = {p["ticker"]: p for p in body["day"]["picks"]}
    assert asked == [sorted(rows)]
    rng, opn = _cells(body["day"]["picks"], "1st min (ATR)"), _cells(body["day"]["picks"], "Open vs PM")
    assert (rng["AAAA"]["value"], rng["AAAA"]["tone"]) == (0.7, "bad")
    assert (rng["BBBB"]["value"], opn["BBBB"]["value"]) == (0.11, "inside")
    assert rng["AAAA"]["note"].startswith("The 09:30 bar as Alpaca's snapshot shows it, not confirmed yet.")
    assert rows["BBBB"][live.FIRST_MINUTE] == _bar(18.30, 18.35, 18.25, 18.30)
    assert {p[live.AS_OF] for p in rows.values()} == {"09:31:00"}
    assert body["refresh_seconds"] == live.REFRESH_SECONDS


# ---------------------------------------------------------------- the snapshot's pace (PR #45 review, 2026-11-13)
# The window refreshes on a 2 s setInterval, so a refresh can land a few ms under 2 s after the
# last snapshot ask. Without slack that refresh was skipped and the next ask came 4 s later.
# A snapshot is now skipped only when the last ask was less than LIVE_SECONDS -
# LIVE_SLACK_SECONDS (1.75 s) ago.

@pytest.mark.parametrize("gap, asked", [(1.99, True), (1.8, True), (1.75, True), (1.7, False)])
def test_during_the_minute_a_refresh_a_little_under_two_seconds_on_still_asks(gap, asked):
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:41")})
    clock = Clock()
    store = live.FirstMinuteBars(FakeFetch(), clock=clock, forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:42"))
    clock.t += gap
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:44"))
    assert len(so_far.calls) == (2 if asked else 1)


@pytest.mark.parametrize("gap, asked", [(1.99, True), (1.8, True), (1.7, False)])
def test_after_0931_the_snapshot_keeps_the_same_pace(gap, asked):
    fetch = FakeFetch({DAY: {}})
    so_far = FakeSoFar({"AAAA": _whole(42.9, 43.4, 42.7, 43.1)})
    clock = Clock()
    store = live.FirstMinuteBars(fetch, clock=clock, forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:31:10"))      # the bars request (none yet), then the snapshot
    clock.t += gap
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:31:12"))
    assert len(so_far.calls) == (2 if asked else 1)


@pytest.mark.parametrize("gap, asked", [(1.99, True), (1.8, True), (1.7, False)])
def test_a_failed_snapshot_is_asked_again_at_the_same_pace(gap, asked):
    so_far = FakeSoFar(error=RuntimeError("429 too many requests"))
    clock = Clock()
    store = live.FirstMinuteBars(FakeFetch(), clock=clock, forming=so_far)
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:42"))
    clock.t += gap
    store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:44"))
    assert len(so_far.calls) == (2 if asked else 1)


@pytest.mark.parametrize("every, asks", [(0.25, 35), (2.0, 30)])
def test_in_one_minute_callers_get_at_most_35_snapshots_and_one_2s_refresh_gets_30(every, asks):
    # Asks as often as callers come (two or more windows, or a window and Edge Trader's Opening
    # plan): the snapshot goes out at 0, 1.75 ... 59.5 s, 35 times. One caller on a 2 s refresh:
    # 0, 2 ... 58 s, 30 times, as before the slack. Steps of 0.25 s are exact in binary.
    assert (live.LIVE_SECONDS, live.LIVE_SLACK_SECONDS) == (2.0, 0.25)
    so_far = FakeSoFar({"AAAA": _so_far(42.9, 43.4, 42.7, 43.1, "2026-11-05 09:30:00")})
    clock = Clock()
    store = live.FirstMinuteBars(FakeFetch(), clock=clock, forming=so_far)
    start, step = clock.t, 0
    while step * every < 60:
        clock.t = start + step * every
        store.get(DAY, ["AAAA"], _et("2026-11-05 09:30:00") + timedelta(seconds=step * every))
        step += 1
    assert len(so_far.calls) == asks
