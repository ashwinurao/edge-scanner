"""A past ET day for the OV picks, chart and news routes: the OV picks window's past days.

Fork-owned. The OV picks window can show a past day's ranked list (Ash, project thread,
2026-11-07). A click on a ticker there sends the ticker and that day to the window's
linked charts and news, so they show the stock as it was then:

- GET /api/bars/{symbol}/{timeframe}?date=YYYY-MM-DD ends the bars on that day
  (scanner/api.py). The frame keeps its usual length of history before it.
- GET /api/v2/news?date=YYYY-MM-DD ends the news window at that day's close, 16:00 ET
  (scanner/api_v2.py, scanner/news.py).
- GET /api/v2/ov-picks?date=YYYY-MM-DD serves that day's picks (scanner/ov_picks_route.py).

Without `date`, each route is unchanged: it ends today. The same Alpaca calls serve
both cases, with an earlier end; there is no new source.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# The regular session's close: a past day's news runs up to it.
CLOSE_ET = time(16, 0)


def today_et(clock: Optional[Callable[[], datetime]] = None) -> date:
    return (clock() if clock else datetime.now(_ET)).astimezone(_ET).date()


def parse_day(text: Optional[str], today: date) -> Optional[date]:
    """The day in `text` (YYYY-MM-DD), or None when `text` is empty.

    Raises ValueError, with a message for the caller, for anything else or a day after
    `today` (ET): there are no bars or news from the future to show.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        if not _DAY.fullmatch(text):
            raise ValueError
        day = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"The date {text!r} is not a day as YYYY-MM-DD. Pick a day in the "
                         "calendar.") from None
    if day > today:
        raise ValueError(f"{day} is after today ({today} ET), so there is nothing to show yet. "
                         "Pick today or an earlier day.")
    return day


def close_of(day: date) -> datetime:
    """That day's close, 16:00 ET, as an aware datetime."""
    return datetime.combine(day, CLOSE_ET, tzinfo=_ET)
