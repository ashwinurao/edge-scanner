"""GET /api/v2/ov-picks: the OV picks module's latest trading day, read-only.

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
  NO_FOLDER (the folder is not there) or ERROR (the read failed); reason says what
  happened and what to do next. A day without picks is not a route problem: "day"
  says why (MISSING, UNREADABLE, INVALID).
- now_et, today_et: the clock this answer used, in ET.
- trading_date, is_today: the day served. That is today, unless the market is closed
  today; then it is the newest day folder up to today (or today, when there is none).
- market_closed, market_note: true, false, or null when the route can't tell, and why.
- next_scan_at, next_scan_et: the scan job's next_at from the status file, in UTC as
  written and in ET. Null when there is none, or when it is more than 10 minutes past
  (then a warning says the module's schedule is out of date).
- day: contract.read_day's view of trading_date, unchanged. Null on a route problem.
- status: contract.read_status's view, unchanged. Null on a route problem.
- preview: contract.read_preview's view of today, unchanged, only from 09:00 to 09:25 ET
  (PREVIEW_FROM to the contract's PREVIEW_BEFORE_ET) on a trading day whose picks are
  not frozen yet. Null otherwise, so the card shows a preview only in that window and
  never beside the frozen picks. The owner's every-minute previews (Ash, project thread,
  2026-11-06): the module's ranked list as of each minute, not frozen. The watchlist and
  the daily notice come from the frozen picks only (scanner/ov_picks_day.py).
- in_universe: each row of the day's ranked list mapped to whether the scanner scans it
  (it is in the startup universe, so it has live bars and setups); without picks, each
  row of the preview. Null with neither, or when the scanner has no universe to ask
  (ticket #20). The universe is built once
  at startup (scripts/run_live.py) and watchlists never add to it, so a name outside it
  gets no live bars and no setups.
- warnings: route-level warnings. The day's and the status file's own warnings stay
  inside those views.

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

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from scanner.ov_picks_day import register_morning

log = logging.getLogger(__name__)

ROUTE = "/api/v2/ov-picks"
ENV_VAR = "OV_PICKS_DIR"

NOT_SET_UP = "NOT_SET_UP"
NO_READER = "NO_READER"
NO_FOLDER = "NO_FOLDER"
ERROR = "ERROR"

ROUTE_FIELDS = ("set_up", "reason_code", "reason", "now_et", "today_et", "trading_date",
                "is_today", "market_closed", "market_note", "next_scan_at", "next_scan_et",
                "day", "status", "preview", "in_universe", "warnings")

_ET = ZoneInfo("America/New_York")
# Spec "Freeze and restart rules": a first scan may still run until 09:35 ET, ten minutes
# after its 09:25 slot, so a next-scan time inside that window is not yet overdue.
_LATE_WINDOW = timedelta(minutes=10)
# The module previews each minute from 09:00 ET until the 09:25 freeze (ov_picks/CONTRACT.md,
# "Preview file").
PREVIEW_FROM = time(9, 0)
_DAY_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")


def register(app: FastAPI, app_state: Any,
             clock: Optional[Callable[[], datetime]] = None) -> None:
    """Add GET /api/v2/ov-picks to `app`, and the day watchlist and notice (ticket #20).

    `clock` returns an aware datetime (tests and scripts/ov_picks_demo.py pass a fake).
    """
    now = clock or (lambda: datetime.now(_ET))
    register_morning(app, app_state, clock=clock)

    # A plain def: FastAPI runs it in a worker thread, so the file reads never block the
    # event loop that serves /ws/alerts.
    @app.get(ROUTE)
    def ov_picks() -> JSONResponse:
        at = now()
        setting = os.environ.get(ENV_VAR)
        try:
            body = build_answer(setting, at, universe=_universe(app_state))
        except Exception as exc:
            # Spec US19: one part failing never takes the scanner down, and the card says why.
            log.exception("ov_picks_route: reading the OV picks folder failed")
            body = _base(at)
            body.update(set_up=bool((setting or "").strip()), reason_code=ERROR,
                        reason=f"OV picks could not be read ({type(exc).__name__}: {exc}). "
                               "The scanner keeps running; check the OV picks module and "
                               f"{ENV_VAR}.")
        return JSONResponse(body)


def build_answer(setting: Optional[str], now: datetime,
                 universe: Optional[Container[str]] = None) -> dict:
    """The route's answer for OV_PICKS_DIR=`setting` at time `now`. Reads, never writes.

    `universe` holds the symbols the scanner scans, or is None when it can't be read.
    """
    body = _base(now)
    folder = (setting or "").strip()
    if not folder:
        body.update(reason_code=NOT_SET_UP,
                    reason=f"OV picks are not set up: {ENV_VAR} is not set. Set it to the OV picks "
                           "module's output folder, mounted read-only, and restart the scanner.")
        return body
    body["set_up"] = True
    contract = _load_contract()
    if contract is None:
        body.update(reason_code=NO_READER,
                    reason=f"{ENV_VAR} is set, but this install has no OV picks reader "
                           "(ov_picks/contract.py). Install the ov_picks folder next to scanner/ "
                           "and restart the scanner.")
        return body
    out_dir = Path(folder)
    if not out_dir.is_dir():
        body.update(reason_code=NO_FOLDER,
                    reason=f"{ENV_VAR} is {folder}, but that folder is not there. Check that the "
                           "OV picks output folder is mounted, then reload.")
        return body

    now_et = now.astimezone(_ET)
    today = now_et.date()
    status = contract.read_status(out_dir)
    jobs = status["jobs"]
    closed, note = _market_closed(contract, out_dir, jobs, today)
    served = _latest_day(out_dir, today) if closed else today
    next_at, next_et, warning = _next_scan(contract, jobs, now_et)
    body.update(trading_date=served.isoformat(), is_today=served == today,
                market_closed=closed, market_note=note,
                next_scan_at=next_at, next_scan_et=next_et,
                day=contract.read_day(out_dir, served), status=status)
    body["preview"] = _preview(contract, out_dir, body["day"], now_et, served == today and closed is not True)
    body["in_universe"] = _in_universe(body["day"], body["preview"], universe)
    if warning:
        body["warnings"].append(warning)
    return body


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

    Edge Scanner has no holiday calendar, and this route makes no network call. The
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
    best = None
    with os.scandir(out_dir) as entries:
        for entry in entries:
            if not _DAY_NAME.fullmatch(entry.name) or not entry.is_dir():
                continue
            try:
                day = date.fromisoformat(entry.name)
            except ValueError:
                continue
            if day <= today and (best is None or day > best):
                best = day
    return best or today


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
