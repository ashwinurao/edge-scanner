"""DAILY_RESTART_ET: the scanner stops once a day so it starts fresh before the pre-market."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scanner import daily_restart

ET = ZoneInfo("America/New_York")


def test_parse():
    assert daily_restart.parse_hhmm("03:40") == (3, 40)
    for off in (None, "", "0", "off"):
        assert daily_restart.parse_hhmm(off) is None
    for bad in ("3", "25:00", "03:60", "x:y"):
        with pytest.raises(ValueError):
            daily_restart.parse_hhmm(bad)


def test_next_restart_is_today_or_tomorrow_new_york_time():
    evening = datetime(2026, 11, 2, 20, 57, tzinfo=ET)          # Monday evening start
    assert daily_restart.next_restart(evening, (3, 40)) == datetime(2026, 11, 3, 3, 40, tzinfo=ET)
    early = datetime(2026, 11, 3, 3, 0, tzinfo=ET)
    assert daily_restart.next_restart(early, (3, 40)) == datetime(2026, 11, 3, 3, 40, tzinfo=ET)
    exactly = datetime(2026, 11, 3, 3, 40, tzinfo=ET)            # strictly after: not a restart loop
    assert daily_restart.next_restart(exactly, (3, 40)) == datetime(2026, 11, 4, 3, 40, tzinfo=ET)
    utc = datetime(2026, 11, 3, 8, 0, tzinfo=ZoneInfo("UTC"))    # 03:00 ET
    assert daily_restart.next_restart(utc, (3, 40)) == datetime(2026, 11, 3, 3, 40, tzinfo=ET)


def test_next_restart_across_the_dst_change():
    sat = datetime(2026, 10, 31, 12, 0, tzinfo=ET)              # clocks go back Sunday 2026-11-01
    nxt = daily_restart.next_restart(sat, (3, 40))
    assert (nxt.date(), nxt.hour, nxt.minute) == (datetime(2026, 11, 1).date(), 3, 40)


def test_timer_stops_the_process_at_the_time_and_only_once():
    now = [datetime(2026, 11, 2, 20, 57, tzinfo=ET)]
    stops, waits = [], []

    def wait(secs):
        waits.append(secs)
        now[0] += timedelta(seconds=secs)
        return False

    t = daily_restart.start("03:40", stop=lambda: stops.append(now[0]), clock=lambda: now[0], wait=wait)
    t.join(timeout=5)
    assert stops == [datetime(2026, 11, 3, 3, 40, tzinfo=ET)]
    assert max(waits) <= 300


def test_off_by_default():
    assert daily_restart.start(None, stop=lambda: pytest.fail("stopped")) is None
