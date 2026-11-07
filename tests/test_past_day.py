"""Past days for the OV picks window's linked charts and news (Ash, 2026-11-07).

A click on a ticker in a past day's OV picks list sends that day to the linked windows:
GET /api/bars/{symbol}/{tf}?date=YYYY-MM-DD ends the bars on that day, and
GET /api/v2/news?date=YYYY-MM-DD ends the news window at that day's 16:00 ET close
(scanner/past_day.py). Without `date` both routes are unchanged.

All offline: the feed and the news getters are fakes that record what they were asked.
"""
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scanner import api as api_mod
from scanner import past_day
from scanner.api import AppState, create_app
from scanner.api_v2 import register_v2_routes
from scanner.news import NewsClient

TODAY = date(2026, 11, 7)       # a Saturday, ET


# ---------------------------------------------------------------- parse_day and close_of

def test_an_empty_date_means_today_and_changes_nothing():
    assert past_day.parse_day("", TODAY) is None
    assert past_day.parse_day(None, TODAY) is None
    assert past_day.parse_day("   ", TODAY) is None


def test_a_past_day_or_today_is_read():
    assert past_day.parse_day("2026-10-30", TODAY) == date(2026, 10, 30)
    assert past_day.parse_day(" 2026-01-02 ", TODAY) == date(2026, 1, 2)
    assert past_day.parse_day("2026-11-07", TODAY) == TODAY


@pytest.mark.parametrize("text", ["2026-13-01", "2026-02-30", "11/04/2026", "2026-11-4", "yesterday",
                                  "2026-11-04T10:00"])
def test_anything_but_a_day_is_refused_with_what_to_do(text):
    with pytest.raises(ValueError) as err:
        past_day.parse_day(text, TODAY)
    assert "not a day as YYYY-MM-DD" in str(err.value) and "calendar" in str(err.value)


def test_a_day_after_today_is_refused():
    with pytest.raises(ValueError) as err:
        past_day.parse_day("2026-11-08", TODAY)
    assert "after today (2026-11-07 ET)" in str(err.value)


def test_the_close_is_16_00_new_york_time_in_winter_and_summer():
    # EST after 2026-11-01, EDT in July: the zone, never a fixed offset.
    assert past_day.close_of(date(2026, 11, 6)).astimezone(timezone.utc) == \
        datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)
    assert past_day.close_of(date(2026, 7, 15)).astimezone(timezone.utc) == \
        datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)


def test_today_in_et_uses_new_york_not_utc():
    # 02:00 UTC on Saturday is still Friday evening in New York.
    clock = lambda: datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    assert past_day.today_et(clock) == date(2026, 11, 6)


# ---------------------------------------------------------------- bars

class _Feed:
    """Records each get_bars_range call and returns one bar on the asked end day."""

    def __init__(self):
        self.calls = []

    def get_bars_range(self, symbol, tf, start, end):
        self.calls.append((symbol, tf, start, end))
        ts = pd.Timestamp(datetime.combine(end, datetime.min.time()), tz="America/New_York")
        return pd.DataFrame({"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10.0]},
                            index=pd.DatetimeIndex([ts]))


@pytest.fixture
def bars(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(past_day, "today_et", lambda clock=None: TODAY)
    feed = _Feed()
    state = AppState(scanner=SimpleNamespace(_states={}, _spy_state=None, _regime=None, _sector_map={}),
                     feed=feed)  # type: ignore[arg-type]
    return SimpleNamespace(client=TestClient(create_app(state)), feed=feed, state=state)


@pytest.mark.parametrize("tf, feed_tf, days", [
    ("1min", "1Min", 5), ("5min", "5Min", 10), ("1day", "Day", 600), ("1week", "1Week", 1095),
])
def test_a_date_ends_the_bars_on_that_day_with_the_usual_history(bars, tf, feed_tf, days):
    r = bars.client.get(f"/api/bars/nvda/{tf}", params={"date": "2026-10-30"})
    assert r.status_code == 200
    assert bars.feed.calls == [("NVDA", feed_tf, date(2026, 10, 30) - timedelta(days=days), date(2026, 10, 30))]
    assert r.json()["bars"][0]["t"].startswith("2026-10-30")


def test_without_a_date_the_bars_end_today_as_before(bars):
    bars.client.get("/api/bars/NVDA/5min")
    (_, _, start, end), = bars.feed.calls
    assert end == date.today() and start == end - timedelta(days=10)


@pytest.mark.parametrize("bad, words", [("10/30/2026", "not a day"), ("2026-11-08", "after today")])
def test_a_bad_or_future_date_is_a_400_and_asks_the_feed_nothing(bars, bad, words):
    r = bars.client.get("/api/bars/NVDA/5min", params={"date": bad})
    assert r.status_code == 400 and words in r.json()["error"]
    assert bars.feed.calls == []


def test_each_day_has_its_own_cache_entry(bars):
    for day in ("2026-10-30", "2026-10-29", "2026-10-30", ""):
        bars.client.get("/api/bars/NVDA/5min", params={"date": day} if day else None)
    # 10-30 twice is served from memory; today is a separate entry.
    assert [c[3] for c in bars.feed.calls] == [date(2026, 10, 30), date(2026, 10, 29), date.today()]


def test_expired_bar_entries_are_dropped_when_a_new_one_is_stored(bars):
    import time
    old = time.monotonic() - api_mod._BAR_CACHE_TTL - 1
    bars.state._bar_cache[("NVDA", "5min", date(2026, 10, 1))] = (old, [])
    bars.state._bar_cache[("AMD", "1day", None)] = (old, [])
    bars.client.get("/api/bars/NVDA/5min", params={"date": "2026-10-29"})
    assert list(bars.state._bar_cache) == [("NVDA", "5min", date(2026, 10, 29))]


# ---------------------------------------------------------------- news

class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def _rss(*stamps):
    body = "".join(f"<item><title>Story {i}</title><link>http://x/{i}</link><guid>{i}</guid>"
                   f"<pubDate>{format_datetime(t)}</pubDate></item>" for i, t in enumerate(stamps))
    return _Resp(200, text=f"<rss><channel>{body}</channel></rss>")


CLOSE = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)      # 16:00 EDT, Friday 2026-10-30


def test_a_past_day_asks_the_wire_for_that_span_only():
    calls = []
    def wire(url, headers, params, timeout):
        calls.append(params)
        return _Resp(200, {"news": [{"id": 1, "headline": "H", "url": "u", "source": "s", "symbols": ["A"],
                                     "created_at": "2026-10-30T15:00:00Z"}]})
    nc = NewsClient("k", "s", get_fn=wire, rss_sources=())
    r = nc.fetch(["A"], limit=10, hours=72, until=CLOSE)
    assert (calls[0]["start"], calls[0]["end"]) == ("2026-10-27T20:00:00Z", "2026-10-30T20:00:00Z")
    assert [i["headline"] for i in r["items"]] == ["H"]


def test_without_until_the_wire_gets_no_end_as_before():
    calls = []
    nc = NewsClient("k", "s", get_fn=lambda url, headers, params, timeout: calls.append(params) or _Resp(200, {}),
                    rss_sources=())
    nc.fetch(["A"], hours=6)
    assert "end" not in calls[0] and "start" in calls[0]


def test_rss_items_after_the_close_or_before_the_window_are_cut():
    feed = _rss(CLOSE + timedelta(hours=5), CLOSE - timedelta(hours=1), CLOSE, CLOSE - timedelta(hours=80))
    nc = NewsClient(None, None, rss_sources=("yahoo",), rss_get_fn=lambda *a, **k: feed)
    r = nc.fetch(["A"], limit=10, hours=72, until=CLOSE)
    # Story 0 is after the close, story 3 before the 72-hour window: neither is shown.
    assert sorted(i["headline"] for i in r["items"]) == ["Story 1", "Story 2"]


def test_a_past_day_and_today_are_cached_apart():
    calls = []
    def wire(url, headers, params, timeout):
        calls.append(params.get("end"))
        return _Resp(200, {"news": [{"id": 1, "headline": "H", "url": "u", "source": "s", "symbols": ["A"],
                                     "created_at": "2026-10-30T15:00:00Z"}]})
    nc = NewsClient("k", "s", get_fn=wire, rss_sources=())
    nc.fetch(["A"], hours=72, until=CLOSE)
    nc.fetch(["A"], hours=72, until=CLOSE)        # from memory
    nc.fetch(["A"], hours=72)                     # today: its own entry
    assert calls == ["2026-10-30T20:00:00Z", None]


class _SpyNews:
    def __init__(self):
        self.calls = []

    def fetch(self, symbols=None, limit=50, hours=24, until=None):
        self.calls.append((symbols, limit, hours, until))
        return {"items": [], "stale": False}


@pytest.fixture
def news(tmp_path, monkeypatch):
    monkeypatch.setattr(past_day, "today_et", lambda clock=None: TODAY)
    spy = _SpyNews()
    app = FastAPI()
    state = AppState(scanner=SimpleNamespace(_states={}, _spy_state=None, _regime=None, _sector_map={}),
                     feed=None)  # type: ignore[arg-type]
    register_v2_routes(app, state, layouts_dir=tmp_path / "layouts", watchlists_path=tmp_path / "wl.json",
                       fundamentals_path=tmp_path / "fund.json", news_client=spy,
                       universe_csv=tmp_path / "nope.csv", sector_csv=tmp_path / "nope2.csv")
    return SimpleNamespace(client=TestClient(app), spy=spy)


def test_the_news_route_ends_a_past_day_at_its_close(news):
    r = news.client.get("/api/v2/news", params={"symbols": "a", "hours": 72, "date": "2026-11-06"})
    assert r.status_code == 200
    (syms, _, hours, until), = news.spy.calls
    assert (syms, hours) == (["A"], 72)
    assert until == past_day.close_of(date(2026, 11, 6))
    assert until.astimezone(timezone.utc) == datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)


def test_the_news_route_without_a_date_ends_now_as_before(news):
    news.client.get("/api/v2/news", params={"symbols": "A"})
    assert news.spy.calls == [(["A"], 50, 24, None)]


def test_the_news_route_refuses_a_bad_date_and_fetches_nothing(news):
    r = news.client.get("/api/v2/news", params={"symbols": "A", "date": "2026-11-09"})
    assert r.status_code == 400 and "after today" in r.json()["error"]
    assert news.spy.calls == []
