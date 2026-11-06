"""The OV picks day watchlist and the one "OV picks ready" notice a day
(ashwinurao/edge-scanner#20; scanner/ov_picks_day.py).

Driven through MorningPicks.check_once with a fake ET clock, against copies of the
ticket #9 sample folders (ov_picks/samples/) and small folders built with the contract's
own writers. Every store lives in tmp_path. Nothing here reaches the network, and
nothing needs the module's pinned libraries.
"""
import json
import shutil
import socket
import time as _time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ov_picks import contract as c
from scanner import ov_picks_day as morning
from scanner import ov_picks_route as route
from scanner.api import AppState
from scanner.api_v2 import register_v2_routes
from scanner.feed_hub import FeedHub, Subscription
from scanner.json_store import WatchlistStore
from scanner.news import NewsClient

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "ov_picks" / "samples"
ET = ZoneInfo("America/New_York")
DAY = "2026-11-05"                      # the samples' day, a Thursday
NAMES = ["AAAA", "BBBB", "CCCC", "DDDD"]
# The fields every alert on the feed has (dashboard-v2/src/types.ts, Alert). The notice
# keeps each one, with its existing meaning.
ALERT_FIELDS = {"symbol", "direction", "timestamp", "price", "trigger", "triggers_fired", "score",
                "market_regime", "conditions", "vwap", "ema3", "ema9"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("an OV picks test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _et(text):
    """An ET wall-clock time, e.g. "2026-11-05 09:25:05", as an aware datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=ET)


class Day:
    """One scanner's stores in tmp_path, plus an OV picks output folder."""

    def __init__(self, tmp_path, sample=None):
        self.out = tmp_path / "out"
        if sample:
            shutil.copytree(SAMPLES / sample, self.out)
        else:
            self.out.mkdir()
        self.wl_path = tmp_path / "watchlists.json"
        self.marker = tmp_path / "data" / "ov_picks" / "notified.json"
        self.alerts_dir = tmp_path / "alerts"
        self.restart()

    def restart(self):
        """A new scanner process: new feed, new stores, same files on disk."""
        self.hub = FeedHub(store_dir=self.alerts_dir, keep_days=30)
        self.watchlists = WatchlistStore(self.wl_path)
        self.job = morning.MorningPicks(self.out, self.watchlists, self.hub, marker_path=self.marker)

    def check(self, now):
        return self.job.check_once(_et(now))

    def notices(self):
        return self.hub.recent_for(Subscription())

    def ov_list(self):
        lists = [w for w in self.watchlists.load_all() if w["id"] == morning.WATCHLIST_ID]
        return lists[0] if lists else None

    def archived(self):
        return [json.loads(line) for p in sorted(self.alerts_dir.glob("*.jsonl"))
                for line in p.read_text().splitlines()]


def _record(day, frozen_at, picks=None, top=None, **over):
    """A picks record whose ranked list is `picks` (NAMES by default), the first `top` of
    them marked top (all of them by default)."""
    names = NAMES if picks is None else picks
    top = len(names) if top is None else top
    rec = {
        "trading_date": day, "frozen_at": frozen_at, "warmup_state": c.READY, "warmup_reason": "",
        "late": False, "shadow": False, "earnings_status": c.EARNINGS_AVAILABLE,
        "metric_labels": ["Score"],
        "picks": [{"rank": i + 1, "ticker": t, "score": 90.0 - i, "status": "PICK" if i < top else "WATCH",
                   "top": i < top, "earnings_label": None, "metrics": [{"label": "Score", "value": 90.0 - i}],
                   "details": [{"label": "Ticker", "value": t}]} for i, t in enumerate(names)],
        "reference_state": c.REF_OK, "reference_promoted": [],
    }
    rec.update(over)
    return rec


# ---------------------------------------------------------------- the watchlist upsert

def test_the_watchlist_gets_the_days_four_top_picks_in_rank_order(tmp_path):
    d = Day(tmp_path, "frozen")
    assert len(c.read_day(d.out, DAY)["picks"]) == 10     # four top picks, six more ranked rows

    assert d.check("2026-11-05 09:25:05") == morning.SENT

    wl = d.ov_list()
    assert (wl["id"], wl["name"], wl["symbols"]) == ("ov-picks", "OV picks", NAMES)
    assert wl["updatedAt"] == "2026-11-05T14:25:05Z"
    # Through the dashboard's own store and file shape: GET /api/v2/watchlists serves it.
    assert json.loads(d.wl_path.read_text()) == {"watchlists": [wl]}


def test_the_upsert_replaces_yesterdays_names_and_leaves_other_lists_alone(tmp_path):
    d = Day(tmp_path, "frozen")
    d.watchlists.save({"id": "wl_mine", "name": "Mine", "symbols": ["MSFT"], "updatedAt": "x"})
    d.watchlists.save({"id": "ov-picks", "name": "OV morning", "symbols": ["OLD1", "OLD2", "OLD3", "OLD4"],
                       "updatedAt": "x"})

    d.check("2026-11-05 09:25:05")

    lists = {w["id"]: w for w in d.watchlists.load_all()}
    assert lists["wl_mine"] == {"id": "wl_mine", "name": "Mine", "symbols": ["MSFT"], "updatedAt": "x"}
    # The day's names replace yesterday's; a name the trader gave the list is kept.
    assert (lists["ov-picks"]["name"], lists["ov-picks"]["symbols"]) == ("OV morning", NAMES)


def test_the_rows_below_the_top_picks_stay_off_the_watchlist_and_the_notice(tmp_path):
    # The owner picks and sizes from the ranked list by hand (Ash, 2026-11-06); the
    # watchlist and the notice carry the top picks only.
    d = Day(tmp_path)
    c.write_picks(d.out, _record(DAY, "2026-11-05T14:25:00Z", picks=["EEEE", "AAAA", "FFFF", "BBBB"], top=2))

    assert d.check("2026-11-05 09:25:05") == morning.SENT

    assert d.ov_list()["symbols"] == ["EEEE", "AAAA"]
    [n] = d.notices()
    assert (n["setup_label"], n["symbol"], n["picks"]) == ("OV picks ready: EEEE, AAAA", "EEEE", ["EEEE", "AAAA"])
    assert json.loads(d.marker.read_text())["symbols"] == ["EEEE", "AAAA"]


def test_a_ranked_list_with_no_top_picks_clears_the_list_and_sends_no_notice(tmp_path):
    d = Day(tmp_path)
    c.write_picks(d.out, _record(DAY, "2026-11-05T14:25:00Z", picks=["EEEE", "FFFF"], top=0))
    d.watchlists.save({"id": "ov-picks", "name": "OV picks", "symbols": ["OLD1"], "updatedAt": "x"})

    assert d.check("2026-11-05 09:25:05") == morning.WATCHLIST_ONLY

    assert d.ov_list()["symbols"] == []
    assert d.notices() == []


def test_a_day_with_no_names_clears_the_list_and_sends_no_notice(tmp_path):
    # The contract allows an empty list: no name passed OV's gates.
    d = Day(tmp_path)
    c.write_picks(d.out, _record(DAY, "2026-11-05T14:25:00Z", picks=[]))
    d.watchlists.save({"id": "ov-picks", "name": "OV picks", "symbols": ["OLD1"], "updatedAt": "x"})

    assert d.check("2026-11-05 09:25:05") == morning.WATCHLIST_ONLY

    assert d.ov_list()["symbols"] == []     # yesterday's names never look like today's
    assert d.notices() == []
    assert json.loads(d.marker.read_text())["notice_sent"] is False


# ---------------------------------------------------------------- the notice

def test_one_notice_with_the_four_names_on_the_existing_alert_feed(tmp_path):
    d = Day(tmp_path, "frozen")

    d.check("2026-11-05 09:25:05")

    [n] = d.notices()
    assert n["setup_label"] == "OV picks ready: AAAA, BBBB, CCCC, DDDD"
    assert n["source"] == "custom"            # the dashboard drops any other source
    assert "custom" not in n                  # not a custom setup's alert
    assert (n["symbol"], n["direction"], n["trigger"], n["setup"], n["notice"]) == (
        "AAAA", "neutral", "ov_picks_ready", "ov_picks", "ov_picks")
    # Existing alert fields keep their meaning: timestamp is the bar's start, so the Scanner
    # window (bar close = start + 1 min) shows the 09:25 freeze.
    assert ALERT_FIELDS <= set(n)
    assert n["timestamp"] == "2026-11-05T09:24:00-05:00"
    assert (n["price"], n["score"], n["triggers_fired"], n["conditions"]) == (None, 0, [], {})
    assert (n["trading_date"], n["frozen_at"], n["picks"]) == (DAY, "2026-11-05T14:25:00Z", NAMES)
    # Archived like every alert, and plain JSON for the dashboard.
    assert [a["setup_label"] for a in d.archived()] == [n["setup_label"]]
    json.dumps(n, allow_nan=False)
    assert json.loads(d.marker.read_text()) == {"date": DAY, "symbols": NAMES, "notice_sent": True,
                                                "at": "2026-11-05T14:25:05Z"}


def test_the_notice_is_sent_once_a_day(tmp_path):
    d = Day(tmp_path, "frozen")

    outcomes = [d.check(t) for t in ("2026-11-05 09:25:05", "2026-11-05 09:25:10", "2026-11-05 10:40",
                                     "2026-11-05 15:00")]

    assert outcomes == [morning.SENT] + [morning.DONE_TODAY] * 3
    assert len(d.notices()) == 1 and len(d.archived()) == 1


def test_no_notice_and_no_upsert_after_a_restart_the_same_day(tmp_path):
    d = Day(tmp_path, "frozen")
    assert d.check("2026-11-05 09:25:05") == morning.SENT
    # The trader edits the list after the upsert; a restart must not undo that.
    d.watchlists.save({**d.ov_list(), "symbols": ["AAAA", "BBBB", "ZZZZ"]})

    d.restart()
    assert d.check("2026-11-05 09:40") == morning.DONE_TODAY

    assert d.notices() == []                          # the new process's feed stays empty
    assert len(d.archived()) == 1                     # one notice on disk for the day
    assert d.ov_list()["symbols"] == ["AAAA", "BBBB", "ZZZZ"]


def test_the_next_trading_day_gets_its_own_notice(tmp_path):
    d = Day(tmp_path, "frozen")
    d.check("2026-11-05 09:25:05")
    c.write_picks(d.out, _record("2026-11-06", "2026-11-06T14:25:00Z", picks=["EEEE", "AAAA"]))

    d.restart()
    assert d.check("2026-11-06 09:25:04") == morning.SENT

    [n] = d.notices()
    assert n["setup_label"] == "OV picks ready: EEEE, AAAA"
    assert d.ov_list()["symbols"] == ["EEEE", "AAAA"]
    assert json.loads(d.marker.read_text())["date"] == "2026-11-06"


def test_an_unreadable_marker_counts_as_no_marker(tmp_path):
    # One repeated notice beats a missed one.
    d = Day(tmp_path, "frozen")
    d.marker.parent.mkdir(parents=True)
    d.marker.write_text("{not json")
    assert d.check("2026-11-05 09:30") == morning.SENT
    assert len(d.notices()) == 1


def test_a_late_day_says_late_and_shows_its_freeze_time(tmp_path):
    d = Day(tmp_path, "late")          # first scan frozen at 09:31:12 ET
    assert d.check("2026-11-05 09:31:15") == morning.SENT
    [n] = d.notices()
    assert n["setup_label"] == "OV picks ready: AAAA, BBBB, CCCC, DDDD (LATE)"
    assert n["timestamp"] == "2026-11-05T09:30:12-05:00"


def test_earnings_unavailable_still_lists_the_top_picks_and_says_so(tmp_path):
    d = Day(tmp_path, "earnings_unavailable")
    assert d.check("2026-11-05 09:25:05") == morning.SENT
    [n] = d.notices()
    assert n["setup_label"] == "OV picks ready: AAAA, BBBB, CCCC, DDDD (earnings unavailable)"
    assert n["picks"] == NAMES
    assert d.ov_list()["symbols"] == NAMES


@pytest.mark.parametrize("sample, label", [
    ("shadow", "OV picks ready: AAAA, BBBB, CCCC, DDDD (shadow: checking against OV)"),
    ("not_ready", "OV picks ready: AAAA, BBBB, CCCC, DDDD (warmup not ready)"),
    # An earnings day: the notice names the four top picks; the labels show in the window.
    ("parity_match", "OV picks ready: AAAA, BBBB, CCCC, DDDD (shadow: checking against OV)"),
])
def test_the_notice_carries_the_days_flags(tmp_path, sample, label):
    d = Day(tmp_path, sample)
    d.check("2026-11-05 09:25:05")
    assert [n["setup_label"] for n in d.notices()] == [label]


@pytest.mark.parametrize("now, outcome, notices", [
    ("2026-11-05 15:59:59", morning.SENT, 1),
    ("2026-11-05 16:00", morning.WATCHLIST_ONLY, 0),   # the close: "ready" would be noise
])
def test_a_scanner_that_first_sees_the_picks_after_the_close_sends_no_notice(tmp_path, now, outcome, notices):
    d = Day(tmp_path, "frozen")
    assert d.check(now) == outcome
    assert len(d.notices()) == notices
    assert d.ov_list()["symbols"] == NAMES
    assert d.check("2026-11-05 16:30") == morning.DONE_TODAY


# ---------------------------------------------------------------- days with nothing to send

def _assert_nothing_written(d):
    assert d.notices() == [] and d.archived() == []
    assert not d.wl_path.exists() and not d.marker.exists()


def test_while_the_module_warms_up_nothing_is_written_until_the_picks_freeze(tmp_path):
    d = Day(tmp_path, "warming")       # 08:50 ET, warmup running, no picks file yet

    assert d.check("2026-11-05 08:50") == morning.NO_PICKS
    assert d.check("2026-11-05 09:24:59") == morning.NO_PICKS
    _assert_nothing_written(d)

    c.write_picks(d.out, _record(DAY, "2026-11-05T14:25:00Z"))
    assert d.check("2026-11-05 09:25:03") == morning.SENT
    assert len(d.notices()) == 1


def test_the_previews_never_fill_the_watchlist_or_send_the_notice(tmp_path):
    # The owner's previews (Ash, 2026-11-06): the watchlist and the notice wait for the
    # frozen picks at 09:25 ET; a preview each minute before then writes nothing here.
    d = Day(tmp_path, "preview")       # 09:14 ET, the preview as of 09:14, no picks file yet
    for now in ("2026-11-05 09:00", "2026-11-05 09:14:30", "2026-11-05 09:24:59"):
        assert d.check(now) == morning.NO_PICKS
    _assert_nothing_written(d)

    c.write_picks(d.out, _record(DAY, "2026-11-05T14:25:00Z", picks=["EEEE", "AAAA"]))
    assert d.check("2026-11-05 09:25:03") == morning.SENT
    assert d.ov_list()["symbols"] == ["EEEE", "AAAA"]     # the frozen picks, not the preview's


def test_a_holiday_sends_nothing(tmp_path):
    # Thanksgiving, Thursday 2026-11-26: Wednesday's picks are there, and the module's
    # calendar puts the next scan on Friday. Nothing is for today.
    d = Day(tmp_path)
    c.write_picks(d.out, _record("2026-11-25", "2026-11-25T14:25:00Z"))
    c.record_job_state(d.out, c.JOB_SCAN, c.PICKS_FROZEN, at="2026-11-25T14:25:00Z",
                       trading_date="2026-11-25", next_at="2026-11-27T14:25:00Z")

    for now in ("2026-11-26 09:25:05", "2026-11-26 12:00"):
        assert d.check(now) == morning.NO_PICKS
    _assert_nothing_written(d)


def test_a_skipped_scan_sends_nothing(tmp_path):
    # SKIPPED can be a holiday or a start after 09:35; either way there are no picks today.
    d = Day(tmp_path)
    c.write_picks(d.out, _record("2026-11-09", "2026-11-09T14:25:00Z"))
    c.record_job_state(d.out, c.JOB_SCAN, c.SKIPPED, at="2026-11-10T15:02:00Z", trading_date="2026-11-10",
                       message="Started at 10:02 ET, after 09:35. No scan today.",
                       next_at="2026-11-11T14:25:00Z")
    assert d.check("2026-11-10 10:05") == morning.NO_PICKS
    _assert_nothing_written(d)


def test_a_weekend_sends_nothing_even_with_fridays_picks(tmp_path):
    d = Day(tmp_path)
    c.write_picks(d.out, _record("2026-11-06", "2026-11-06T14:25:00Z"))
    for now in ("2026-11-07 09:25:05", "2026-11-08 09:25:05"):
        assert d.check(now) == morning.NOT_A_WEEKDAY
    _assert_nothing_written(d)


def test_a_picks_file_that_breaks_the_contract_sends_nothing(tmp_path):
    d = Day(tmp_path)
    (d.out / DAY).mkdir()
    bad = _record(DAY, "2026-11-05T14:25:00Z")
    bad["picks"][0]["top"] = False                     # the top picks no longer come first
    (d.out / DAY / c.PICKS_FILE).write_text(json.dumps({"schema_version": 1, "kind": c.KIND_PICKS, **bad}))
    assert c.read_day(d.out, DAY)["reason_code"] == c.INVALID
    assert d.check("2026-11-05 09:25:05") == morning.NO_PICKS
    _assert_nothing_written(d)


def test_a_missing_output_folder_sends_nothing(tmp_path):
    d = Day(tmp_path)
    d.out.rmdir()
    assert d.check("2026-11-05 09:25:05") == morning.NO_PICKS
    _assert_nothing_written(d)


def test_without_the_reader_nothing_is_written(tmp_path):
    d = Day(tmp_path, "frozen")
    d.job = morning.MorningPicks(d.out, d.watchlists, d.hub, marker_path=d.marker, load_contract=lambda: None)
    assert d.check("2026-11-05 09:25:05") == morning.NO_READER
    _assert_nothing_written(d)


def test_the_check_never_writes_into_the_output_folder(tmp_path):
    d = Day(tmp_path, "parity_match")
    before = sorted((p.relative_to(d.out), p.stat().st_mtime_ns) for p in d.out.rglob("*"))
    d.check("2026-11-05 09:25:05")
    d.check("2026-11-05 10:40")
    assert sorted((p.relative_to(d.out), p.stat().st_mtime_ns) for p in d.out.rglob("*")) == before


# ---------------------------------------------------------------- off unless OV_PICKS_DIR is set

def _bare_app(tmp_path, monkeypatch, now):
    """The v2 routes and the OV picks route on tmp_path stores, in tmp_path as the working
    folder, so the marker's data/ path is under tmp_path too."""
    monkeypatch.chdir(tmp_path)
    hub = FeedHub(store_dir=tmp_path / "alerts", keep_days=30)
    scanner = SimpleNamespace(_states={}, _spy_state=None, _regime=None, _sector_map={})
    app_state = AppState(scanner=scanner, feed=None, hub=hub)  # type: ignore[arg-type]
    app = FastAPI()
    register_v2_routes(app, app_state, layouts_dir=tmp_path / "layouts", watchlists_path=tmp_path / "wl.json",
                       fundamentals_path=tmp_path / "fund.json", news_client=NewsClient("k", "s"),
                       universe_csv=tmp_path / "nope.csv", sector_csv=tmp_path / "nope2.csv",
                       setups_dir=tmp_path / "setups")
    route.register(app, app_state, clock=lambda: _et(now))
    return app, app_state


def test_both_modules_read_the_same_variable():
    assert morning.ENV_VAR == route.ENV_VAR == "OV_PICKS_DIR"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_the_env_var_the_watcher_never_starts_and_writes_nothing(tmp_path, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(morning.ENV_VAR, value)
    app, app_state = _bare_app(tmp_path, monkeypatch, "2026-11-05 09:25:05")

    with TestClient(app) as client:
        assert client.get("/api/v2/ov-picks").json()["reason_code"] == route.NOT_SET_UP
        _time.sleep(0.2)

    assert not hasattr(app.state, "ov_picks_morning")
    assert not (tmp_path / "wl.json").exists()
    assert not (tmp_path / "data" / "ov_picks").exists()
    assert app_state.hub.recent_for(Subscription()) == []


def test_with_the_env_var_the_startup_task_fills_the_watchlist_and_sends_once(tmp_path, monkeypatch):
    out = tmp_path / "out"
    shutil.copytree(SAMPLES / "frozen", out)
    monkeypatch.setenv(morning.ENV_VAR, str(out))
    app, app_state = _bare_app(tmp_path, monkeypatch, "2026-11-05 09:25:05")

    with TestClient(app) as client:
        deadline = _time.monotonic() + 10
        while not app_state.hub.recent_for(Subscription()) and _time.monotonic() < deadline:
            _time.sleep(0.05)
        lists = client.get("/api/v2/watchlists").json()["watchlists"]

    [n] = app_state.hub.recent_for(Subscription())
    assert n["setup_label"] == "OV picks ready: AAAA, BBBB, CCCC, DDDD"
    assert [(w["id"], w["symbols"]) for w in lists] == [("ov-picks", NAMES)]
    assert json.loads((tmp_path / "data" / "ov_picks" / "notified.json").read_text())["date"] == DAY
    assert app.state.ov_picks_morning.out_dir == out


def test_the_marker_defaults_to_the_scanners_own_data_folder():
    # Never the module's output folder: that is mounted read-only (CONTRACT.md, Layout).
    assert morning.MARKER_PATH == Path("data/ov_picks/notified.json")
