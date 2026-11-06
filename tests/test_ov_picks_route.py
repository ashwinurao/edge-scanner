"""GET /api/v2/ov-picks (ashwinurao/edge-scanner#12): Edge Scanner serves the OV picks
module's latest trading day, read-only, from the folder named by OV_PICKS_DIR.

Read through FastAPI's test client against the ticket #9 sample folders
(ov_picks/samples/) and against small folders built here with the contract's own
writers. A fake clock sets the ET time. Nothing here reaches the network, and nothing
needs the module's pinned libraries.
"""
import importlib
import json
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ov_picks import contract as c
from scanner import ov_picks_route as route

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "ov_picks" / "samples"
ET = ZoneInfo("America/New_York")
DAY = "2026-11-05"                      # the samples' day, a Thursday
NEXT_SCAN = "2026-11-06T14:25:00Z"      # the samples' scan next_at: Friday 09:25 ET
URL = "/api/v2/ov-picks"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("an OV picks route test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    # A developer's own OV_PICKS_DIR must not leak into these tests.
    monkeypatch.delenv(route.ENV_VAR, raising=False)


def _et(text):
    """An ET wall-clock time, e.g. "2026-11-05 10:40", as an aware datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=ET)


def _get(monkeypatch, out_dir, now):
    """The route's answer for OV_PICKS_DIR=out_dir (None leaves it unset) at ET time `now`."""
    if out_dir is not None:
        monkeypatch.setenv(route.ENV_VAR, str(out_dir))
    app = FastAPI()
    route.register(app, SimpleNamespace(), clock=lambda: _et(now))
    resp = TestClient(app).get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == set(route.ROUTE_FIELDS)
    return body


def _record(day, frozen_at, **over):
    rec = {
        "trading_date": day,
        "frozen_at": frozen_at,
        "warmup_state": c.READY,
        "warmup_reason": "",
        "late": False,
        "shadow": False,
        "earnings_status": c.EARNINGS_AVAILABLE,
        "picks": [{"rank": i + 1, "ticker": t, "score": 90.0 - i, "earnings_label": None,
                   "traded": True, "weight": 0.25} for i, t in enumerate(["AAAA", "BBBB", "CCCC", "DDDD"])],
        "reference_state": c.REF_OK,
        "reference_promoted": [],
    }
    rec.update(over)
    return rec


def _scan(out_dir, state, *, at, day, next_at, message=""):
    c.record_job_state(out_dir, c.JOB_SCAN, state, at=at, trading_date=day, message=message,
                       next_at=next_at)


def _snapshot(folder):
    """Every entry under `folder` with its bytes and mtime, and every folder's mtime, so a
    file written, replaced, created or deleted (even a temp file) shows as a change."""
    snap = {}
    for path in sorted([folder, *folder.rglob("*")]):
        st = path.stat()
        snap[str(path.relative_to(folder))] = (
            path.is_dir(), st.st_mtime_ns, st.st_size, None if path.is_dir() else path.read_bytes())
    return snap


# ---------------------------------------------------------------- wiring and "not set up"

def _real_app():
    from scanner.api import AppState, create_app
    scanner = SimpleNamespace(_states={}, _spy_state=None, _regime=None, _sector_map={})
    return create_app(AppState(scanner=scanner, feed=None))  # type: ignore[arg-type]


def test_create_app_serves_the_route_and_says_not_set_up_without_the_env_var():
    body = TestClient(_real_app()).get(URL).json()
    assert body["set_up"] is False
    assert body["reason_code"] == route.NOT_SET_UP
    assert route.ENV_VAR in body["reason"]
    assert body["day"] is None and body["status"] is None
    assert set(body) == set(route.ROUTE_FIELDS)


def test_create_app_reads_the_folder_named_by_the_env_var(monkeypatch):
    monkeypatch.setenv(route.ENV_VAR, str(SAMPLES / "frozen"))
    body = TestClient(_real_app()).get(URL).json()
    assert (body["set_up"], body["reason_code"]) == (True, "")
    assert body["status"]["has_status"] is True


def test_the_route_stays_when_plugins_are_switched_off(monkeypatch):
    # Not wired through the plugin hook (scanner/plugins.py): SCANNER_NO_PLUGINS=1 would remove it.
    import scanner.plugins as plugins
    monkeypatch.setenv("SCANNER_NO_PLUGINS", "1")
    importlib.reload(plugins)
    try:
        assert TestClient(_real_app()).get(URL).status_code == 200
    finally:
        monkeypatch.delenv("SCANNER_NO_PLUGINS")
        importlib.reload(plugins)


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_env_var_is_not_set_up(monkeypatch, value):
    monkeypatch.setenv(route.ENV_VAR, value)
    body = _get(monkeypatch, None, "2026-11-05 10:40")
    assert (body["set_up"], body["reason_code"]) == (False, route.NOT_SET_UP)


def test_a_folder_that_is_not_there_says_so(monkeypatch, tmp_path):
    missing = tmp_path / "not-mounted"
    body = _get(monkeypatch, missing, "2026-11-05 10:40")
    assert (body["set_up"], body["reason_code"]) == (True, route.NO_FOLDER)
    assert str(missing) in body["reason"]
    assert body["day"] is None and body["status"] is None
    assert not missing.exists()


def test_a_scanner_without_the_reader_says_so_and_keeps_running(monkeypatch):
    # The scanner image copies scanner/ and scripts/ only (Dockerfile), so ov_picks/ may be absent.
    monkeypatch.setitem(sys.modules, "ov_picks.contract", None)
    body = _get(monkeypatch, SAMPLES / "frozen", "2026-11-05 10:40")
    assert (body["set_up"], body["reason_code"]) == (True, route.NO_READER)
    assert "ov_picks" in body["reason"]


def test_a_reader_failure_is_a_clear_state_not_a_500(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(c, "read_day", _boom)
    body = _get(monkeypatch, SAMPLES / "frozen", "2026-11-05 10:40")
    assert (body["set_up"], body["reason_code"]) == (True, route.ERROR)
    assert "disk on fire" in body["reason"]


# ---------------------------------------------------------------- the ticket #9 samples

# case: (ET clock, has picks, shadow, late, warmup, earnings, traded, settled, parity, next scan)
ROUTE_CASES = {
    "frozen": ("2026-11-05 09:25:30", True, False, False, c.READY, c.EARNINGS_AVAILABLE,
               ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING, NEXT_SCAN),
    "shadow": ("2026-11-05 09:30", True, True, False, c.READY, c.EARNINGS_AVAILABLE,
               ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_SAME, c.PENDING, NEXT_SCAN),
    "late": ("2026-11-05 09:32", True, False, True, c.READY, c.EARNINGS_AVAILABLE,
             ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING, NEXT_SCAN),
    "not_ready": ("2026-11-05 09:30", True, False, False, c.NOT_READY, c.EARNINGS_AVAILABLE,
                  ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING, NEXT_SCAN),
    "earnings_unavailable": ("2026-11-05 09:30", True, False, False, c.READY, c.EARNINGS_UNAVAILABLE,
                             [], c.PENDING, c.PENDING, NEXT_SCAN),
    "parity_match": ("2026-11-05 10:40", True, True, False, c.READY, c.EARNINGS_AVAILABLE,
                     ["BBBB", "CCCC"], c.SETTLED_SAME, c.PARITY_MATCH, NEXT_SCAN),
    "parity_diff": ("2026-11-05 10:40", True, True, False, c.READY, c.EARNINGS_AVAILABLE,
                    ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_SAME, c.PARITY_DIFF, NEXT_SCAN),
    "no_ov_run": ("2026-11-05 10:40", True, False, False, c.READY, c.EARNINGS_AVAILABLE,
                  ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_SAME, c.PARITY_NO_OV_RUN, NEXT_SCAN),
    "settled_diff": ("2026-11-05 09:30", True, True, False, c.READY, c.EARNINGS_AVAILABLE,
                     ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_DIFF, c.PENDING, NEXT_SCAN),
    # No status file: the next scan is unknown.
    "older_schema": ("2026-11-05 09:30", True, False, False, c.READY, c.EARNINGS_AVAILABLE,
                     ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING, None),
    # 08:50 ET, warming, no picks file yet: today's scan is still to come.
    "warming": ("2026-11-05 08:50", False, None, None, None, None, [], None, None,
                "2026-11-05T14:25:00Z"),
}


def test_every_sample_folder_has_a_route_case():
    assert sorted(p.name for p in SAMPLES.iterdir() if p.is_dir()) == sorted(ROUTE_CASES)


@pytest.mark.parametrize("case", sorted(ROUTE_CASES))
def test_the_route_serves_each_sample_day(monkeypatch, case):
    now, has, shadow, late, warmup, earnings, traded, settled, parity, next_scan = ROUTE_CASES[case]
    folder = SAMPLES / case

    body = _get(monkeypatch, folder, now)

    assert (body["set_up"], body["reason_code"], body["warnings"]) == (True, "", [])
    assert (body["today_et"], body["trading_date"], body["is_today"]) == (DAY, DAY, True)
    assert body["market_closed"] is False, body["market_note"]
    assert body["next_scan_at"] == next_scan
    # The day and the status are the contract readers' views, unchanged.
    assert body["day"] == c.read_day(folder, DAY)
    assert body["status"] == c.read_status(folder)
    day = body["day"]
    assert day["has_picks"] is has, day["reason"]
    if not has:
        assert day["reason_code"] == c.MISSING and "09:25 ET" in day["reason"]
        assert body["status"]["jobs"][c.JOB_WARMUP]["state"] == c.WARMING
        return
    assert (day["shadow"], day["late"], day["warmup_state"]) == (shadow, late, warmup)
    assert day["earnings_status"] == earnings
    assert [p["ticker"] for p in day["picks"] if p["traded"]] == traded
    assert (day["settled_state"], day["parity_status"]) == (settled, parity)


def test_not_ready_shows_the_reason(monkeypatch):
    day = _get(monkeypatch, SAMPLES / "not_ready", "2026-11-05 09:30")["day"]
    assert day["warmup_state"] == c.NOT_READY and day["warmup_reason"].strip()


def test_earnings_unavailable_shows_no_traded_set_and_no_labels(monkeypatch):
    day = _get(monkeypatch, SAMPLES / "earnings_unavailable", "2026-11-05 09:30")["day"]
    assert len(day["picks"]) == 4
    assert [(p["traded"], p["weight"], p["earnings_label"]) for p in day["picks"]] == [(False, 0, None)] * 4


@pytest.mark.parametrize("case, status, has_difference", [
    ("parity_match", c.PARITY_MATCH, False),
    ("parity_diff", c.PARITY_DIFF, True),
    ("no_ov_run", c.PARITY_NO_OV_RUN, False),
])
def test_the_parity_line(monkeypatch, case, status, has_difference):
    day = _get(monkeypatch, SAMPLES / case, "2026-11-05 10:40")["day"]
    assert day["parity_status"] == status
    assert bool(day["parity_first_difference"]) is has_difference
    assert day["parity_at"] == "2026-11-05T15:35:02Z"


def test_an_older_schema_version_still_serves(monkeypatch):
    # A later ticket raises SCHEMA_VERSION; the frozen version 1 sample must keep loading.
    monkeypatch.setattr(c, "SCHEMA_VERSION", 2)
    day = _get(monkeypatch, SAMPLES / "older_schema", "2026-11-05 09:30")["day"]
    assert day["has_picks"] is True, day["reason"]
    assert day["schema_version"] == 1
    assert [p["ticker"] for p in day["picks"]] == ["AAAA", "BBBB", "CCCC", "DDDD"]


def test_an_empty_folder_is_no_picks_and_an_unknown_market(monkeypatch, tmp_path):
    body = _get(monkeypatch, tmp_path, "2026-11-05 10:40")
    assert (body["set_up"], body["reason_code"]) == (True, "")
    assert body["day"]["has_picks"] is False and body["day"]["reason_code"] == c.MISSING
    assert body["status"]["has_status"] is False
    # Edge Scanner has no holiday calendar: with no status records it cannot tell.
    assert body["market_closed"] is None and body["market_note"]
    assert (body["next_scan_at"], body["next_scan_et"]) == (None, None)


# ---------------------------------------------------------------- market closed and next scan

def _friday_then_monday(out_dir):
    # Friday 2026-11-06 frozen, next scan Monday 2026-11-09 09:25 ET.
    c.write_picks(out_dir, _record("2026-11-06", "2026-11-06T14:25:00Z"))
    _scan(out_dir, c.PICKS_FROZEN, at="2026-11-06T14:25:00Z", day="2026-11-06",
          next_at="2026-11-09T14:25:00Z")


def test_a_weekend_is_market_closed_and_serves_the_last_trading_day(monkeypatch, tmp_path):
    _friday_then_monday(tmp_path)

    body = _get(monkeypatch, tmp_path, "2026-11-07 11:00")

    assert body["market_closed"] is True and "Saturday" in body["market_note"]
    assert (body["today_et"], body["trading_date"], body["is_today"]) == ("2026-11-07", "2026-11-06", False)
    assert body["day"]["has_picks"] is True
    assert body["next_scan_at"] == "2026-11-09T14:25:00Z"
    assert body["next_scan_et"] == "2026-11-09T09:25:00-05:00"
    assert body["warnings"] == []


def test_a_weekend_ignores_folders_after_today(monkeypatch, tmp_path):
    _friday_then_monday(tmp_path)
    # A folder from a clock that ran ahead must not be served as the latest day.
    c.write_picks(tmp_path, _record("2026-11-09", "2026-11-09T14:25:00Z"))
    body = _get(monkeypatch, tmp_path, "2026-11-08 11:00")
    assert body["trading_date"] == "2026-11-06"


def test_a_closed_day_with_no_saved_day_serves_today_as_no_picks(monkeypatch, tmp_path):
    # A status file but no day folder at all, e.g. the first weekend after setup.
    _scan(tmp_path, c.PICKS_FROZEN, at="2026-11-06T14:25:00Z", day="2026-11-06",
          next_at="2026-11-09T14:25:00Z")
    body = _get(monkeypatch, tmp_path, "2026-11-07 11:00")
    assert body["market_closed"] is True
    assert body["trading_date"] == "2026-11-07" and body["day"]["has_picks"] is False


def test_the_next_scan_is_in_et_across_the_fall_dst_change(monkeypatch, tmp_path):
    # Friday 2026-10-30 is EDT (UTC-4); Monday 2026-11-02 is EST (UTC-5). zoneinfo, never a fixed offset.
    c.write_picks(tmp_path, _record("2026-10-30", "2026-10-30T13:25:00Z"))
    _scan(tmp_path, c.PICKS_FROZEN, at="2026-10-30T13:25:00Z", day="2026-10-30",
          next_at="2026-11-02T14:25:00Z")

    body = _get(monkeypatch, tmp_path, "2026-10-31 10:00")

    assert body["now_et"] == "2026-10-31T10:00:00-04:00"
    assert body["market_closed"] is True and body["trading_date"] == "2026-10-30"
    assert body["next_scan_et"] == "2026-11-02T09:25:00-05:00"


def _wednesday_before_thanksgiving(out_dir):
    # Thanksgiving, Thursday 2026-11-26: the runner's calendar puts the next scan on Friday.
    c.write_picks(out_dir, _record("2026-11-25", "2026-11-25T14:25:00Z"))
    _scan(out_dir, c.PICKS_FROZEN, at="2026-11-25T14:25:00Z", day="2026-11-25",
          next_at="2026-11-27T14:25:00Z")


def test_a_holiday_is_market_closed_from_the_modules_schedule(monkeypatch, tmp_path):
    _wednesday_before_thanksgiving(tmp_path)

    body = _get(monkeypatch, tmp_path, "2026-11-26 10:00")

    assert body["market_closed"] is True, body["market_note"]
    assert "2026-11-27" in body["market_note"]
    assert (body["trading_date"], body["is_today"]) == ("2026-11-25", False)
    assert body["day"]["has_picks"] is True
    assert body["next_scan_et"] == "2026-11-27T09:25:00-05:00"


def test_the_day_before_the_holiday_is_open(monkeypatch, tmp_path):
    _wednesday_before_thanksgiving(tmp_path)
    body = _get(monkeypatch, tmp_path, "2026-11-25 16:30")
    assert body["market_closed"] is False
    assert (body["trading_date"], body["is_today"]) == ("2026-11-25", True)


def test_a_morning_with_todays_scan_still_to_come_is_open(monkeypatch, tmp_path):
    # Monday 08:00 ET: Friday's scan entry says the next scan is today at 09:25.
    _friday_then_monday(tmp_path)
    body = _get(monkeypatch, tmp_path, "2026-11-09 08:00")
    assert body["market_closed"] is False
    assert (body["trading_date"], body["is_today"]) == ("2026-11-09", True)
    assert body["day"]["has_picks"] is False and body["day"]["reason_code"] == c.MISSING
    assert body["next_scan_at"] == "2026-11-09T14:25:00Z"


def test_a_job_that_ran_today_means_open_even_without_a_scan_schedule(monkeypatch, tmp_path):
    # The runner skips weekends and holidays for every job but the Saturday reference job.
    c.record_job_state(tmp_path, c.JOB_NIGHTLY, c.NIGHTLY_OK, at="2026-11-05T08:30:41Z",
                       trading_date=DAY, message="ok", next_at="2026-11-06T08:30:00Z")
    body = _get(monkeypatch, tmp_path, "2026-11-05 08:00")
    assert body["market_closed"] is False
    assert body["next_scan_at"] is None


def test_the_reference_job_running_on_a_closed_day_does_not_make_it_open(monkeypatch, tmp_path):
    # The reference job is the one job that runs on a closed day (spec "Runner"), so an entry
    # of its for today says nothing about the session. A catch-up run on the holiday:
    _wednesday_before_thanksgiving(tmp_path)
    c.record_job_state(tmp_path, c.JOB_REFERENCE, c.REF_OK, at="2026-11-26T15:06:12Z",
                       trading_date="2026-11-26", message="SEC and Yahoo sectors updated.",
                       next_at="2026-11-28T15:00:00Z")
    assert _get(monkeypatch, tmp_path, "2026-11-26 11:00")["market_closed"] is True


def test_a_scan_skipped_today_is_not_called_a_closed_market(monkeypatch, tmp_path):
    # A late start (after 09:35) and a holiday can both skip the scan; the route can't tell
    # them apart, so it says it can't tell and passes on the runner's message.
    c.write_picks(tmp_path, _record("2026-11-09", "2026-11-09T14:25:00Z"))
    _scan(tmp_path, c.SKIPPED, at="2026-11-10T15:02:00Z", day="2026-11-10",
          message="Started at 10:02 ET, after 09:35. No scan today.", next_at="2026-11-11T14:25:00Z")

    body = _get(monkeypatch, tmp_path, "2026-11-10 10:05")

    assert body["market_closed"] is None
    assert "after 09:35" in body["market_note"]
    assert (body["trading_date"], body["is_today"]) == ("2026-11-10", True)
    assert body["day"]["has_picks"] is False
    assert body["next_scan_at"] == "2026-11-11T14:25:00Z"


def test_an_out_of_date_schedule_is_flagged_not_shown_as_the_next_scan(monkeypatch, tmp_path):
    # The module stopped after Monday's scan; it is Thursday.
    c.write_picks(tmp_path, _record("2026-11-02", "2026-11-02T14:25:00Z"))
    _scan(tmp_path, c.PICKS_FROZEN, at="2026-11-02T14:25:00Z", day="2026-11-02",
          next_at="2026-11-03T14:25:00Z")

    body = _get(monkeypatch, tmp_path, "2026-11-05 10:00")

    assert body["market_closed"] is None and "out of date" in body["market_note"]
    assert (body["next_scan_at"], body["next_scan_et"]) == (None, None)
    assert len(body["warnings"]) == 1 and "2026-11-03 09:25 ET" in body["warnings"][0]
    assert (body["trading_date"], body["day"]["has_picks"]) == ("2026-11-05", False)


@pytest.mark.parametrize("now, stale", [
    ("2026-11-09 09:35", False),   # the LATE window (09:25 to 09:35) is still open
    ("2026-11-09 09:35:01", True),
])
def test_a_next_scan_that_just_passed_is_not_flagged_inside_the_late_window(monkeypatch, tmp_path,
                                                                             now, stale):
    _friday_then_monday(tmp_path)
    body = _get(monkeypatch, tmp_path, now)
    assert (body["next_scan_at"] is None) is stale
    assert bool(body["warnings"]) is stale


# ---------------------------------------------------------------- read-only

@pytest.mark.parametrize("case", sorted(ROUTE_CASES))
def test_the_route_never_writes_into_the_output_folder(monkeypatch, tmp_path, case):
    folder = tmp_path / case
    shutil.copytree(SAMPLES / case, folder)
    before = _snapshot(folder)

    for now in ("2026-11-05 08:50", "2026-11-05 10:40", "2026-11-07 11:00", "2026-11-26 10:00"):
        _get(monkeypatch, folder, now)

    assert _snapshot(folder) == before


def test_the_answer_is_plain_json(monkeypatch):
    body = _get(monkeypatch, SAMPLES / "frozen", "2026-11-05 10:40")
    # allow_nan=False: the dashboard's JSON.parse refuses NaN and Infinity.
    json.dumps(body, allow_nan=False)


# ---------------------------------------------------------------- names the scanner does not scan

def _get_with_universe(monkeypatch, out_dir, now, states):
    """Like _get, with a scanner whose universe (LiveScanner._states) is `states`."""
    monkeypatch.setenv(route.ENV_VAR, str(out_dir))
    app = FastAPI()
    app_state = SimpleNamespace(scanner=SimpleNamespace(_states=states))
    route.register(app, app_state, clock=lambda: _et(now))
    body = TestClient(app).get(URL).json()
    assert set(body) == set(route.ROUTE_FIELDS)
    return body


def test_a_pick_outside_the_scanner_universe_is_marked(monkeypatch):
    # ticket #20: the universe is built once at startup and watchlists never add to it, so
    # DDDD gets no live bars and no setups. The card says so for that name only.
    states = {s: object() for s in ("AAAA", "BBBB", "CCCC", "SPYX")}
    body = _get_with_universe(monkeypatch, SAMPLES / "frozen", "2026-11-05 09:30", states)
    assert body["in_universe"] == {"AAAA": True, "BBBB": True, "CCCC": True, "DDDD": False}
    assert body["day"] == c.read_day(SAMPLES / "frozen", DAY)      # the day view stays unchanged


def test_picks_all_in_the_universe_are_all_marked_scanned(monkeypatch):
    states = {s: object() for s in ("AAAA", "BBBB", "CCCC", "DDDD")}
    body = _get_with_universe(monkeypatch, SAMPLES / "frozen", "2026-11-05 09:30", states)
    assert body["in_universe"] == dict.fromkeys(["AAAA", "BBBB", "CCCC", "DDDD"], True)


@pytest.mark.parametrize("states", [{}, None, "not a dict"])
def test_a_scanner_with_no_universe_to_ask_never_marks_a_name_unscanned(monkeypatch, states):
    # Empty or missing is "can't tell", never "not scanned".
    body = _get_with_universe(monkeypatch, SAMPLES / "frozen", "2026-11-05 09:30", states)
    assert body["in_universe"] is None
    assert body["day"]["has_picks"] is True


def test_no_universe_answer_without_picks(monkeypatch):
    body = _get_with_universe(monkeypatch, SAMPLES / "warming", "2026-11-05 08:50", {"AAAA": object()})
    assert body["day"]["has_picks"] is False and body["in_universe"] is None


def test_no_universe_answer_when_not_set_up(monkeypatch):
    body = _get(monkeypatch, None, "2026-11-05 09:30")
    assert body["reason_code"] == route.NOT_SET_UP and body["in_universe"] is None
