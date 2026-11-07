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
