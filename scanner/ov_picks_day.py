"""The OV picks day watchlist and the one "OV picks ready" notice a day.

Fork-owned (ashwinurao/edge-scanner#20; spec #8, "Edge Scanner side", US6 and US7).
scanner/ov_picks_route.py's register() calls register_morning(), so scanner/api.py keeps
its one fork line.

Off unless OV_PICKS_DIR is set when the scanner starts. The main test suite and a
developer's checkout build the real app on the real data/ paths, so a watcher that ran
by default could write into someone's own data/watchlists.json and mark their day's
notice as sent (tests/conftest.py also clears the variable for every test).

When it is on, a background task calls MorningPicks.check_once() every POLL_SECONDS.
One check does this, in order:

1. Stop if today (ET) already has a marker in data/ov_picks/notified.json.
2. Read today's picks with the contract reader (ov_picks/contract.py read_day). No valid
   picks file for today means nothing to do: the module is still warming up, the market
   is closed (a holiday or a weekend), the scan was skipped, or the file breaks the
   contract. The card says which.
3. Upsert the "OV picks" watchlist (id WATCHLIST_ID) with the day's top picks (the rows
   marked `top`) in rank order, through the dashboard's own WatchlistStore
   (app_state.v2.watchlists), so its lock covers both writers. The rest of the ranked
   list stays in the OV picks window: the owner picks and sizes from it by hand (Ash,
   2026-11-06), so it is not a watchlist.
4. Publish one notice on the alert feed (FeedHub.publish, source "custom") that lists the
   top picks. Not on a day with no top picks, and not after NOTICE_UNTIL_ET: a "ready"
   notice after the close is noise.
5. Write today's marker. A crash between 4 and 5 repeats the notice once after the
   restart, which is safer than missing it.

The marker lives in the scanner's own data/ folder. The module's output folder is mounted
read-only and only the module writes it.

The notice uses the existing alert fields with their existing meaning and adds
`notice`, `trading_date`, `frozen_at` and `picks` (the top picks). Nothing reads it as a setup alert:
it has no price, score 0 and direction "neutral".
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from scanner.json_store import AtomicJsonStore

log = logging.getLogger(__name__)

ENV_VAR = "OV_PICKS_DIR"
MARKER_PATH = Path("data/ov_picks/notified.json")
WATCHLIST_ID = "ov-picks"
WATCHLIST_NAME = "OV picks"
# Set on the watchlist while it holds a past day's top picks, from the OV picks window's
# calendar (Ash, 2026-11-07; dashboard-v2/src/windows/ovpicks/api.ts). The morning
# upsert drops it: the list is today's again.
PAST_DAY_FIELD = "ov_picks_day"
NOTICE_KIND = "ov_picks"            # the notice's `notice` and `setup` fields
NOTICE_TRIGGER = "ov_picks_ready"
NOTICE_SOURCE = "custom"            # FeedHub.SOURCES; the dashboard drops any other source
# A file that changes once a day: a few seconds of delay after 09:25:00 ET is plenty.
POLL_SECONDS = 5.0
# The regular session closes at 16:00 ET. A scanner that first sees the day's picks after
# that (it was down all day) still fills the watchlist but sends no "ready" notice.
NOTICE_UNTIL_ET = time(16, 0)

# What one check did. Tests assert on these; the loop logs them when they change.
DONE_TODAY = "done_today"           # today's marker is there
NOT_A_WEEKDAY = "not_a_weekday"     # Saturday or Sunday: the module never makes picks then
NO_READER = "no_reader"             # this install has no ov_picks/contract.py
NO_PICKS = "no_picks"               # no valid picks file for today (yet)
SENT = "sent"                       # watchlist upserted, notice published, marker written
WATCHLIST_ONLY = "watchlist_only"   # watchlist upserted and marker written, no notice

_ET = ZoneInfo("America/New_York")


class MorningPicks:
    """Puts the day's OV picks in the "OV picks" watchlist and on the alert feed, once a day."""

    def __init__(self, out_dir: Path, watchlists: Any, hub: Any,
                 marker_path: Path = MARKER_PATH,
                 clock: Optional[Callable[[], datetime]] = None,
                 load_contract: Optional[Callable[[], Any]] = None) -> None:
        self.out_dir = Path(out_dir)
        self.watchlists = watchlists        # scanner.json_store.WatchlistStore
        self.hub = hub                      # scanner.feed_hub.FeedHub
        self.marker_path = Path(marker_path)
        self._marker: Optional[AtomicJsonStore] = None
        self._clock = clock or (lambda: datetime.now(_ET))
        self._load_contract = load_contract or _import_contract
        self._done_for: Optional[str] = None    # saves re-reading the marker every few seconds
        self.task: Optional[asyncio.Task] = None

    # ── one check ──

    def check_once(self, now: Optional[datetime] = None) -> str:
        now_et = (now or self._clock()).astimezone(_ET)
        today = now_et.date().isoformat()
        if self._done_for == today:
            return DONE_TODAY
        if now_et.weekday() >= 5:
            return NOT_A_WEEKDAY
        if self._marker_date() == today:
            self._done_for = today
            return DONE_TODAY
        contract = self._load_contract()
        if contract is None:
            return NO_READER
        day = contract.read_day(self.out_dir, today)
        if not day["has_picks"]:
            return NO_PICKS
        # The top picks only, in rank order; the contract keeps them first (ov_picks/CONTRACT.md).
        tickers = [p["ticker"] for p in sorted(day["picks"], key=lambda p: p["rank"]) if p["top"]]

        self._upsert_watchlist(tickers, now_et)
        seq = None
        if tickers and now_et.time() < NOTICE_UNTIL_ET:
            seq = self.hub.publish(notice_record(day, tickers, contract), NOTICE_SOURCE)["seq"]
        self._write_marker(today, tickers, seq, now_et)
        self._done_for = today
        return SENT if seq is not None else WATCHLIST_ONLY

    # ── the three writes ──

    def _upsert_watchlist(self, tickers: list[str], now_et: datetime) -> None:
        existing = next((w for w in self.watchlists.load_all() if w.get("id") == WATCHLIST_ID), {})
        rec = dict(existing)
        # A past day the window put there goes, with the name that showed its date.
        past = rec.pop(PAST_DAY_FIELD, None)
        # Keep a name the trader gave the list; the names in it are the day's top picks only.
        name = WATCHLIST_NAME if past else existing.get("name") or WATCHLIST_NAME
        rec.update(id=WATCHLIST_ID, name=name, symbols=tickers, updatedAt=_utc_text(now_et))
        self.watchlists.save(rec)

    def _marker_store(self) -> AtomicJsonStore:
        if self._marker is None:
            self._marker = AtomicJsonStore(self.marker_path)
        return self._marker

    def _marker_date(self) -> Optional[str]:
        if not self.marker_path.exists():
            return None
        doc = self._marker_store().read()
        # An unreadable marker counts as no marker: one repeated notice beats a missed one.
        return doc.get("date") if isinstance(doc, dict) else None

    def _write_marker(self, today: str, tickers: list[str], seq: Optional[int], now_et: datetime) -> None:
        self._marker_store().write({"date": today, "symbols": tickers, "notice_sent": seq is not None,
                                    "at": _utc_text(now_et)})

    # ── the background loop ──

    async def run(self, poll_seconds: float = POLL_SECONDS) -> None:
        last = None
        while True:
            try:
                outcome = await asyncio.to_thread(self.check_once)
            except Exception as exc:
                outcome = f"failed: {type(exc).__name__}: {exc}"
                if outcome != last:
                    log.exception("OV picks: the morning check failed; it runs again every %gs", poll_seconds)
            else:
                if outcome != last and outcome in (SENT, WATCHLIST_ONLY, NO_READER):
                    log.info("OV picks: %s", outcome)
            last = outcome
            await asyncio.sleep(poll_seconds)


def notice_record(day: dict, tickers: list[str], contract: Any) -> dict:
    """The one alert-feed record for the day. `day` is contract.read_day's view with picks,
    and `tickers` are its top picks in rank order."""
    frozen = datetime.fromisoformat(day["frozen_at"].replace("Z", "+00:00"))
    flags = []
    if day["late"]:
        flags.append("LATE")
    if day["shadow"]:
        flags.append("shadow: checking against OV")
    if day["warmup_state"] == contract.NOT_READY:
        flags.append("warmup not ready")
    if day["earnings_status"] != contract.EARNINGS_AVAILABLE:
        flags.append("earnings unavailable")
    label = f"OV picks ready: {', '.join(tickers)}" + (f" ({'; '.join(flags)})" if flags else "")
    return {
        # The first top pick, so a click on the row links a real chart, never a made-up symbol.
        "symbol": tickers[0],
        "direction": "neutral",
        # Alerts are stamped with the START of their bar and the Scanner window shows the
        # close, one minute later (dashboard-v2 windows/scanner/columns.tsx, barClose). One
        # minute before the freeze, so the Time column shows the freeze time.
        "timestamp": (frozen - timedelta(minutes=1)).astimezone(_ET).isoformat(),
        "price": None,
        "trigger": NOTICE_TRIGGER,
        "triggers_fired": [],
        "score": 0,
        "market_regime": "",
        "conditions": {},
        "vwap": None,
        "ema3": None,
        "ema9": None,
        "setup": NOTICE_KIND,
        "setup_label": label,
        "notice": NOTICE_KIND,
        "trading_date": day["trading_date"],
        "frozen_at": day["frozen_at"],
        "picks": list(tickers),
    }


def from_env(app_state: Any, clock: Optional[Callable[[], datetime]] = None,
             marker_path: Path = MARKER_PATH) -> Optional[MorningPicks]:
    """A MorningPicks for this scanner, or None when OV_PICKS_DIR is unset or blank."""
    folder = (os.environ.get(ENV_VAR) or "").strip()
    if not folder:
        log.info("OV picks: %s is not set, so the OV picks watchlist and notice are off.", ENV_VAR)
        return None
    watchlists = getattr(getattr(app_state, "v2", None), "watchlists", None)
    hub = getattr(app_state, "hub", None)
    if watchlists is None or hub is None:
        log.warning("OV picks: this app has no watchlist store or alert feed, so the OV picks "
                    "watchlist and notice are off.")
        return None
    return MorningPicks(Path(folder), watchlists, hub, marker_path=marker_path, clock=clock)


def register_morning(app: Any, app_state: Any, clock: Optional[Callable[[], datetime]] = None,
                     poll_seconds: float = POLL_SECONDS, marker_path: Path = MARKER_PATH) -> None:
    """Start the morning watcher when the app starts, if OV_PICKS_DIR is set then."""

    async def _start_ov_picks_morning() -> None:
        job = from_env(app_state, clock=clock, marker_path=marker_path)
        if job is None:
            return
        # Kept on the app: asyncio holds only a weak reference to a running task.
        app.state.ov_picks_morning = job
        job.task = asyncio.create_task(job.run(poll_seconds))
        log.info("OV picks: watching %s for the day's picks", job.out_dir)

    # The startup list scanner/api.py's @app.on_event("startup") fills too, without
    # FastAPI's deprecation warning on every app the tests build.
    app.router.add_event_handler("startup", _start_ov_picks_morning)


def _import_contract():
    # Same rule as the route: ov_picks/ may be missing from the image, and that must never
    # stop the scanner. A failed import is not cached, so installing it needs no new code.
    try:
        return importlib.import_module("ov_picks.contract")
    except ImportError:
        return None


def _utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
