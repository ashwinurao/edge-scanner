"""Stop the scanner once a day so that a supervisor starts it fresh before the pre-market.

The daily context (prior close, ADV, levels, the volume profile) is loaded only at startup; a
process left running overnight keeps yesterday's (scanner/live_scanner.py _roll_session_if_new_day
says "restart to refresh it"). On the desktop that restart is the user starting the scanner each
morning. On a server nobody does, so the pre-market lists measured change against a close two
sessions old (Edge Scanner on Unraid, 2026-11-03).

DAILY_RESTART_ET=HH:MM sends this process SIGTERM at that time, New York time, every day. The
scanner then exits the way Ctrl+C does, and the container's restart policy starts it again with a
fresh warmup. Only set it where something restarts the process (compose.yaml does).
"""
from __future__ import annotations

import logging
import os
import signal
import threading
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


def parse_hhmm(raw: str | None) -> tuple[int, int] | None:
    """'03:40' -> (3, 40); empty, '0' or 'off' -> None. Raises ValueError on anything else."""
    v = (raw or "").strip().lower()
    if v in ("", "0", "off", "no", "false"):
        return None
    hh, _, mm = v.partition(":")
    h, m = int(hh), int(mm)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"DAILY_RESTART_ET={raw!r}: expected HH:MM")
    return h, m


def next_restart(now: datetime, at: tuple[int, int]) -> datetime:
    """The next HH:MM New York time strictly after `now` (any timezone-aware datetime)."""
    local = now.astimezone(_ET)
    day = local.date()
    for add in range(3):              # three days covers a DST change on the way
        d = day + timedelta(days=add)
        cand = datetime(d.year, d.month, d.day, at[0], at[1], tzinfo=_ET)
        if cand > local:
            return cand
    raise AssertionError("unreachable")


def start(raw: str | None, stop: Callable[[], None] | None = None,
          clock: Callable[[], datetime] = lambda: datetime.now(_ET),
          wait: Callable[[float], bool] | None = None) -> threading.Thread | None:
    """Start the daily-restart timer. None when DAILY_RESTART_ET is not set."""
    at = parse_hhmm(raw)
    if at is None:
        return None
    stop = stop or (lambda: os.kill(os.getpid(), signal.SIGTERM))
    wait = wait or threading.Event().wait
    first = next_restart(clock(), at)
    print(f"       Daily restart: {first:%Y-%m-%d %H:%M} ET, then every day at {at[0]:02d}:{at[1]:02d} ET "
          f"(DAILY_RESTART_ET), so prior closes and levels are fresh each session.", flush=True)

    def _run() -> None:
        target = first
        while True:
            left = (target - clock()).total_seconds()
            if left <= 0:
                log.warning("Daily restart (DAILY_RESTART_ET): stopping so the scanner starts fresh")
                print("\n  Daily restart: stopping so the scanner starts fresh for the new session.", flush=True)
                stop()
                return
            wait(min(left, 300.0))    # wake every 5 minutes at most, so a clock change is noticed

    t = threading.Thread(target=_run, daemon=True, name="daily-restart")
    t.start()
    return t
