"""GET /api/v2/ov-picks: the OV picks module's latest trading day, or a past one, read-only.

Fork-owned (ashwinurao/edge-scanner#12; spec #8, "Edge Scanner side"). scanner/api.py
wires it with one line, after the v2 routes. It is not registered through the plugin
hook (scanner/plugins.py): upstream reserves that hook for its private plugin package,
and SCANNER_NO_PLUGINS=1 would remove the route.

The module's output folder comes from the OV_PICKS_DIR environment variable, read on
each request. It is mounted read-only into the scanner. The route reads it only through
the contract readers in ov_picks/contract.py (read_day, read_preview, read_status) and
one folder listing, and never writes to it.

The answer always has every key in ROUTE_FIELDS:

- set_up: OV_PICKS_DIR is set.
- reason_code, reason: empty when the folder was read. Otherwise NOT_SET_UP (the
  variable is unset or blank), NO_READER (this install has no ov_picks/contract.py),
  NO_FOLDER (the folder is not there), BAD_DATE (the asked date can't be served) or
  ERROR (the read failed); reason says what happened and what to do next. A day without picks is not a route problem: "day"
  says why (MISSING, UNREADABLE, INVALID).
- now_et, today_et: the clock this answer used, in ET.
- asked_date: the ?date=YYYY-MM-DD the request asked for, as written, or null. A day
  that is not a real date, or is after today (ET), is refused with BAD_DATE. The OV
  picks window asks for past days (Ash, project thread, 2026-11-07).
- trading_date, is_today: the day served. That is the asked day when it is before
  today. Otherwise it is today, unless the market is closed today; then it is the
  newest day folder up to today (or today, when there is none).
- market_closed, market_note: true, false, or null when the route can't tell, and why.
  They are about today, also when a past day is served.
- next_scan_at, next_scan_et: the scan job's next_at from the status file, in UTC as
  written and in ET. Null when there is none, or when it is more than 10 minutes past
  (then a warning says the module's schedule is out of date).
- day: contract.read_day's view of trading_date, unchanged. Null on a route problem.
- history: for an asked past day without the module's own picks, contract.read_history's
  view of it, unchanged: OV's own ranked list for a day before the module ran, from OV's
  2026 rebuild or OV's live scan (its source says which; ov_picks/CONTRACT.md, "History
  file"). Null otherwise, and with a reader that predates history files. The module's
  frozen picks always win: a day with them never shows history.
- status: contract.read_status's view, unchanged. Null on a route problem.
- preview: contract.read_preview's view of today, unchanged, only from 09:00 to 09:25 ET
  (PREVIEW_FROM to the contract's PREVIEW_BEFORE_ET) on a trading day whose picks are
  not frozen yet. Null otherwise, so the card shows a preview only in that window and
  never beside the frozen picks. The owner's every-minute previews (Ash, project thread,
  2026-11-06): the module's ranked list as of each minute, not frozen. The watchlist and
  the daily notice come from the frozen picks only (scanner/ov_picks_day.py).
- in_universe: each row of the day's ranked list mapped to whether the scanner scans it
  (it is in the startup universe, so it has live bars and setups); without picks, each
  row of the preview. Null with neither, for an asked past day (live setups are about
  today), or when the scanner has no universe to ask (ticket #20). The universe is built once
  at startup (scripts/run_live.py) and watchlists never add to it, so a name outside it
  gets no live bars and no setups.
- warnings: route-level warnings. The day's and the status file's own warnings stay
  inside those views.
- refresh_seconds: how often the window should ask again while today's live columns are
  about to fill or still forming (from 09:29:45 ET until each row has its 09:30 bar, at most
  until 09:31:30 ET); null otherwise, when the window keeps its own pace.

The views are the contract readers' own, with one addition: the live columns. A metric
the module marks `live` (ov_picks/CONTRACT.md, "Live metrics") is null in the file; the
route fills its value, tone and note from the day's 09:30 ET minute, and adds that minute
to each such row as `first_minute` (null without one). During the minute the values are
the minute so far, and the row has `first_minute_as_of`, the ET time they run to; from
just after 09:31 they are the minute's bar. A row without a live column is served as read.
The minute is the route's only network call, both through the scanner's Alpaca data
client, REST only: one cached bars request per day shown, from 09:31:03 ET (Ash's go,
project thread, 2026-11-07), and while the minute forms, today only, one snapshot request
for the day's tickers at most every 2 seconds while the window asks (Ash, project thread,
2026-11-08 05:10 UTC: "can you make those two columns update live after 9:30 market
open?"; scanner/ov_picks_live.py). A failed request leaves the cells blank, or as they
were, with a note that says so; a failure while filling them adds a warning. The picks
show either way.

GET /api/v2/ov-picks/days lists the days that have picks, for the window's previous
and next-day buttons, oldest first: each day folder up to today with a picks file, and
each day up to today with a history file (contract.history_days). It checks only that
the file is there; the day's own answer says whether it can be used.
Its answer has every key in DAYS_FIELDS, with the same set_up and reason_code meanings.

register() also starts the day watchlist and the one daily notice
(scanner/ov_picks_day.py, ticket #20). Those are off unless OV_PICKS_DIR is set when the
scanner starts.
"""
from __future__ import annotations

import importlib
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Container, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from scanner import ov_picks_live, past_day
from scanner.ov_picks_day import register_morning

log = logging.getLogger(__name__)

ROUTE = "/api/v2/ov-picks"
DAYS_ROUTE = "/api/v2/ov-picks/days"
ENV_VAR = "OV_PICKS_DIR"

NOT_SET_UP = "NOT_SET_UP"
NO_READER = "NO_READER"
NO_FOLDER = "NO_FOLDER"
BAD_DATE = "BAD_DATE"
ERROR = "ERROR"

ROUTE_FIELDS = ("set_up", "reason_code", "reason", "now_et", "today_et", "asked_date",
                "trading_date", "is_today", "market_closed", "market_note", "next_scan_at",
                "next_scan_et", "day", "status", "preview", "history", "in_universe", "warnings",
                "refresh_seconds")
DAYS_FIELDS = ("set_up", "reason_code", "reason", "today_et", "days")

_ET = ZoneInfo("America/New_York")
# Spec "Freeze and restart rules": a first scan may still run until 09:35 ET, ten minutes
# after its 09:25 slot, so a next-scan time inside that window is not yet overdue.
_LATE_WINDOW = timedelta(minutes=10)
# The module previews each minute from 09:00 ET until the 09:25 freeze (ov_picks/CONTRACT.md,
# "Preview file").
PREVIEW_FROM = time(9, 0)
_DAY_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")


def register(app: FastAPI, app_state: Any,
             clock: Optional[Callable[[], datetime]] = None,
             first_minute_fetch: Optional[ov_picks_live.Fetch] = None,
             first_minute_so_far: Optional[ov_picks_live.Forming] = None) -> None:
    """Add GET /api/v2/ov-picks and its days list to `app`, and the day watchlist and
    notice (ticket #20).

    `clock` returns an aware datetime (tests and scripts/ov_picks_demo.py pass a fake).
    `first_minute_fetch` asks for the live columns' 09:30 ET bars, `first_minute_so_far`
    for the minute while it forms; by default the scanner's Alpaca feed does both
    (scanner/ov_picks_live.py). Tests and the demo pass fakes; a fake bars request alone
    runs without the minute so far.
    """
    now = clock or (lambda: datetime.now(_ET))
    register_morning(app, app_state, clock=clock)
    feed = getattr(app_state, "feed", None)
    if first_minute_fetch is None:
        first_minute_fetch = ov_picks_live.alpaca_fetch(feed)
        first_minute_so_far = first_minute_so_far or ov_picks_live.alpaca_forming(feed)
    bars = ov_picks_live.FirstMinuteBars(first_minute_fetch, forming=first_minute_so_far)

    # A plain def: FastAPI runs it in a worker thread, so the file reads never block the
    # event loop that serves /ws/alerts.
    @app.get(ROUTE)
    def ov_picks(asked: str = Query("", alias="date")) -> JSONResponse:
        at = now()
        setting = os.environ.get(ENV_VAR)
        try:
            body = build_answer(setting, at, universe=_universe(app_state), asked=asked)
        except Exception as exc:
            # Spec US19: one part failing never takes the scanner down, and the card says why.
            log.exception("ov_picks_route: reading the OV picks folder failed")
            body = _base(at)
            body.update(set_up=bool((setting or "").strip()), reason_code=ERROR,
                        asked_date=asked.strip() or None, reason=_error_reason(exc))
        fill_live(body, bars, at)
        return JSONResponse(body)

    @app.get(DAYS_ROUTE)
    def ov_picks_days() -> JSONResponse:
        at = now()
        setting = os.environ.get(ENV_VAR)
        try:
            body = list_days(setting, at)
        except Exception as exc:
            log.exception("ov_picks_route: listing the OV picks days failed")
            body = dict(set_up=bool((setting or "").strip()), reason_code=ERROR,
                        reason=_error_reason(exc), today_et=at.astimezone(_ET).date().isoformat(),
                        days=[])
        return JSONResponse(body)


def fill_live(body: dict, bars: ov_picks_live.FirstMinuteBars, now: datetime) -> None:
    """Fill the answer's live columns in place (scanner/ov_picks_live.py). A failure leaves
    the picks as read and says so in a warning."""
    try:
        ov_picks_live.fill_answer(body, bars, now.astimezone(_ET))
    except Exception as exc:
        log.exception("ov_picks_route: filling the live columns failed")
        body["warnings"].append(f"The columns that fill after the open could not be filled "
                                f"({type(exc).__name__}: {exc}). The picks show as the module wrote them.")


def _error_reason(exc: Exception) -> str:
    return (f"OV picks could not be read ({type(exc).__name__}: {exc}). The scanner keeps "
            f"running; check the OV picks module and {ENV_VAR}.")


def build_answer(setting: Optional[str], now: datetime,
                 universe: Optional[Container[str]] = None, asked: Optional[str] = None) -> dict:
    """The route's answer for OV_PICKS_DIR=`setting` at time `now`. Reads, never writes.

    `universe` holds the symbols the scanner scans, or is None when it can't be read.
    `asked` is the ?date=YYYY-MM-DD asked for, or empty for the latest day.
    """
    body = _base(now)
    body["asked_date"] = (asked or "").strip() or None
    out_dir, contract = _open_folder(setting, body)
    if out_dir is None:
        return body

    now_et = now.astimezone(_ET)
    today = now_et.date()
    try:
        past = _asked_day(body["asked_date"], today)
    except ValueError as exc:
        body.update(reason_code=BAD_DATE, reason=str(exc))
        return body
    status = contract.read_status(out_dir)
    jobs = status["jobs"]
    closed, note = _market_closed(contract, out_dir, jobs, today)
    served = past or (_latest_day(out_dir, today) if closed else today)
    next_at, next_et, warning = _next_scan(contract, jobs, now_et)
    body.update(trading_date=served.isoformat(), is_today=served == today,
                market_closed=closed, market_note=note,
                next_scan_at=next_at, next_scan_et=next_et,
                day=contract.read_day(out_dir, served), status=status)
    body["preview"] = _preview(contract, out_dir, body["day"], now_et, served == today and closed is not True)
    body["history"] = _history(contract, out_dir, body["day"]) if past else None
    # Whether a name gets live setups is about today, not about a past day's list.
    body["in_universe"] = None if past else _in_universe(body["day"], body["preview"], universe)
    if warning:
        body["warnings"].append(warning)
    return body


def list_days(setting: Optional[str], now: datetime) -> dict:
    """The days answer: each day folder up to today (ET) with a picks file, oldest first."""
    today = now.astimezone(_ET).date()
    body = dict.fromkeys(DAYS_FIELDS)
    body.update(set_up=False, reason_code="", reason="", today_et=today.isoformat(), days=[])
    out_dir, contract = _open_folder(setting, body)
    if out_dir is None:
        return body
    days = {d.isoformat() for d in _day_folders(out_dir, today)
            if (contract.day_dir(out_dir, d) / contract.PICKS_FILE).is_file()}
    listed = getattr(contract, "history_days", None)   # an older reader has no history files
    if listed is not None:
        days.update(d for d in listed(out_dir) if d <= today.isoformat())
    body["days"] = sorted(days)
    return body


def _open_folder(setting: Optional[str], body: dict) -> tuple[Optional[Path], Any]:
    """(the output folder, the contract module), or (None, None) with the reason in `body`."""
    folder = (setting or "").strip()
    if not folder:
        body.update(reason_code=NOT_SET_UP,
                    reason=f"OV picks are not set up: {ENV_VAR} is not set. Set it to the OV picks "
                           "module's output folder, mounted read-only, and restart the scanner.")
        return None, None
    body["set_up"] = True
    contract = _load_contract()
    if contract is None:
        body.update(reason_code=NO_READER,
                    reason=f"{ENV_VAR} is set, but this install has no OV picks reader "
                           "(ov_picks/contract.py). Install the ov_picks folder next to scanner/ "
                           "and restart the scanner.")
        return None, None
    out_dir = Path(folder)
    if not out_dir.is_dir():
        body.update(reason_code=NO_FOLDER,
                    reason=f"{ENV_VAR} is {folder}, but that folder is not there. Check that the "
                           "OV picks output folder is mounted, then reload.")
        return None, None
    return out_dir, contract


def _asked_day(text: Optional[str], today: date) -> Optional[date]:
    """The asked day when it is before today; None for no date or today itself.

    Raises ValueError, with the card's text, for a date that is not real or is after today.
    """
    day = past_day.parse_day(text, today)
    return day if day is not None and day < today else None


def _base(now: datetime) -> dict:
    now_et = now.astimezone(_ET)
    body = dict.fromkeys(ROUTE_FIELDS)
    body.update(set_up=False, reason_code="", reason="", now_et=now_et.isoformat(timespec="seconds"),
                today_et=now_et.date().isoformat(), is_today=False, market_note="", warnings=[])
    return body


def _universe(app_state: Any) -> Optional[Container[str]]:
    # LiveScanner._states: one entry per scanned symbol, built at startup
    # (scanner/live_scanner.py warmup). Empty or missing means "can't tell", never "not
    # scanned": refuse rather than guess.
    states = getattr(getattr(app_state, "scanner", None), "_states", None)
    return states if isinstance(states, dict) and states else None


def _preview(contract, out_dir: Path, day: dict, now_et: datetime, trading_today: bool) -> Optional[dict]:
    """Today's preview view, inside the preview window and before the freeze; else None."""
    before = getattr(contract, "PREVIEW_BEFORE_ET", None)
    if before is None or not trading_today or day.get("has_picks"):
        return None   # an older reader has no previews; frozen picks always win
    if not PREVIEW_FROM <= now_et.time() < before:
        return None
    return contract.read_preview(out_dir, now_et.date())


def _history(contract, out_dir: Path, day: dict) -> Optional[dict]:
    """OV's own list for a past day without the module's picks; None with them or an older reader."""
    read = getattr(contract, "read_history", None)
    if read is None or day.get("has_picks"):
        return None
    return read(out_dir, day["trading_date"])


def _in_universe(day: Optional[dict], preview: Optional[dict],
                 universe: Optional[Container[str]]) -> Optional[dict]:
    if universe is None:
        return None
    if day and day.get("has_picks"):
        rows = day["picks"]
    elif preview and preview.get("has_preview"):
        rows = preview["picks"]
    else:
        return None
    return {p["ticker"]: p["ticker"] in universe for p in rows}


def _load_contract():
    # Imported on each request, not at import time: an install without ov_picks/contract.py
    # (the Docker image copies it since ticket #21) must never stop create_app. A failed
    # import is not cached, so installing the folder needs no new code.
    try:
        return importlib.import_module("ov_picks.contract")
    except ImportError:
        return None


def _et_time(text: Optional[str]) -> Optional[datetime]:
    # read_status has already checked the time; this only turns it into ET.
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return dt.astimezone(_ET) if dt.utcoffset() is not None else None


def _market_closed(contract, out_dir: Path, jobs: dict, today: date) -> tuple[Optional[bool], str]:
    """Whether today (ET) has no session, from the clock and the module's records only.

    Edge Scanner has no holiday calendar, and this route asks the network for nothing
    but the live columns' bars (scanner/ov_picks_live.py). The
    module's runner skips weekends and Alpaca-calendar holidays, except the Saturday
    reference job, and sets each job's next_at from that calendar (spec "Runner"). So a
    day job that ran today means a trading day, and a scan scheduled past today with no
    record for today means a holiday. A scan skipped today could be a holiday or a start
    after 09:35, so that case is "can't tell" (spec: refuse rather than guess).
    """
    if today.weekday() >= 5:
        return True, f"The market is closed today ({today:%A})."
    day_text = today.isoformat()
    if (out_dir / day_text).is_dir():
        return False, "Today is a trading day: the OV picks module has a folder for today."
    for job in contract.JOBS:
        entry = jobs.get(job)
        if job == contract.JOB_REFERENCE or entry is None:
            continue
        if entry["trading_date"] == day_text and entry["state"] != contract.SKIPPED:
            return False, f"Today is a trading day: the module's {job} job ran today ({entry['state']})."
    scan = jobs.get(contract.JOB_SCAN)
    if scan is None:
        return None, ("Can't tell whether the market is open today: the module has not "
                      "recorded a scan yet.")
    if scan["trading_date"] == day_text:
        return None, f"The scan was skipped today: {scan['message'] or 'no reason given.'}"
    nxt = _et_time(scan["next_at"])
    if nxt is None:
        return None, ("Can't tell whether the market is open today: the module's scan "
                      "entry has no next run time.")
    if nxt.date() > today:
        return True, (f"The market is closed today: the module's next scan is "
                      f"{nxt:%A %Y-%m-%d %H:%M} ET.")
    if nxt.date() == today:
        return False, f"Today is a trading day: the module's scan is due at {nxt:%H:%M} ET."
    return None, ("Can't tell whether the market is open today: the module's schedule is "
                  f"out of date (its scan was due {nxt:%Y-%m-%d %H:%M} ET). "
                  "Check the OV picks module.")


def _latest_day(out_dir: Path, today: date) -> date:
    """The newest day folder up to today, or today when there is none."""
    days = _day_folders(out_dir, today)
    return days[-1] if days else today


def _day_folders(out_dir: Path, today: date) -> list[date]:
    """Every day folder up to today, oldest first."""
    found = []
    with os.scandir(out_dir) as entries:
        for entry in entries:
            if not _DAY_NAME.fullmatch(entry.name) or not entry.is_dir():
                continue
            try:
                day = date.fromisoformat(entry.name)
            except ValueError:
                continue
            if day <= today:
                found.append(day)
    return sorted(found)


def _next_scan(contract, jobs: dict, now_et: datetime) -> tuple[Optional[str], Optional[str], str]:
    """(next_at as written, the same time in ET, warning) from the scan job's entry."""
    scan = jobs.get(contract.JOB_SCAN)
    nxt = _et_time(scan["next_at"]) if scan else None
    if nxt is None:
        return None, None, ""
    if now_et > nxt + _LATE_WINDOW:
        return None, None, (f"The module's next scan was due {nxt:%Y-%m-%d %H:%M} ET and has "
                            "not run. Check the OV picks module.")
    return scan["next_at"], nxt.isoformat(timespec="seconds"), ""
