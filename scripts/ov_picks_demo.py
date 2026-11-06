"""Demo mode for the OV picks card: the dashboard on a sample picks folder, at a fake time.

Fork-owned (ashwinurao/edge-scanner#20). For screenshots and for trying the card without
the OV picks module, market data or broker keys:

    npm --prefix dashboard-v2 run build
    python scripts/ov_picks_demo.py --sample frozen --at "2026-11-05 09:25:05"
    python scripts/ov_picks_demo.py --sample preview --at "2026-11-05 09:14:30"
    # then open http://127.0.0.1:7787/v2/

It runs the real app (scanner/api.py create_app) with a stub scanner and no data feed,
in a new scratch folder under .logs/ (ignored), so the data/ it writes (layouts,
watchlists, the alert archive, the notice marker) never touches yours. It copies the
sample (ov_picks/samples/<name>, or any out/ folder) there, points OV_PICKS_DIR at the
copy and seeds one screen: the OV picks card, the "OV picks" watchlist and a Scanner
window that shows every alert. The route and the morning watcher run on a clock that
starts at --at (ET) and then moves with real time. It listens on 127.0.0.1 only, on the
demo port 7787 by default, and makes no network call.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

ET = ZoneInfo("America/New_York")
SAMPLES = _REPO_ROOT / "ov_picks" / "samples"


def _screen() -> dict:
    """One saved screen: the card, the "OV picks" watchlist, and a Scanner window on "All"."""
    base = {"link": "blue", "muted": False, "sound": {"tone": "off", "tts": False}}
    windows = {
        "ovp": {**base, "id": "ovp", "type": "ovpicks"},
        "wl": {**base, "id": "wl", "type": "watchlist", "watchlistId": "ov-picks",
               "columns": ["symbol", "price", "chg", "rvol"]},
        "sc": {**base, "id": "sc", "type": "scanner", "link": "none", "sources": [], "setups": [],
               "noSetups": False, "direction": "all", "minScore": 0, "symbolFilter": "",
               "columns": ["time", "symbol", "dir", "setup", "price"], "rowTint": True, "maxRows": 500},
    }
    layout = [
        {"i": "ovp", "x": 0, "y": 0, "w": 0.7, "h": 0.66, "z": 1},
        {"i": "wl", "x": 0.705, "y": 0, "w": 0.295, "h": 0.66, "z": 2},
        {"i": "sc", "x": 0, "y": 0.67, "w": 1, "h": 0.33, "z": 3},
    ]
    stamp = "2026-11-05T00:00:00Z"
    return {"id": "ov-picks-demo", "name": "OV picks demo", "version": 1, "grid": 3, "createdAt": stamp,
            "updatedAt": stamp, "locked": False, "layout": layout, "windows": windows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", default="frozen",
                    help="a folder name under ov_picks/samples, or the path of an out/ folder")
    ap.add_argument("--at", default="2026-11-05 09:25:05", help="the fake ET start time, YYYY-MM-DD HH:MM[:SS]")
    ap.add_argument("--not-scanned", default="",
                    help="comma-separated tickers the stub scanner does not scan (the card's universe note)")
    ap.add_argument("--unset", action="store_true", help="leave OV_PICKS_DIR unset (the 'not set up' card)")
    ap.add_argument("--port", type=int, default=7787)
    ap.add_argument("--work", default="", help="scratch folder (default .logs/ov-picks-demo/<sample>)")
    args = ap.parse_args()

    sample = Path(args.sample) if Path(args.sample).is_dir() else SAMPLES / args.sample
    if not sample.is_dir():
        sys.exit(f"No sample folder {args.sample}. Pick one of: {', '.join(sorted(p.name for p in SAMPLES.iterdir()))}")
    work = Path(args.work) if args.work else _REPO_ROOT / ".logs" / "ov-picks-demo" / sample.name
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(sample, work / "out")
    (work / "data" / "layouts").mkdir(parents=True)
    (work / "data" / "layouts" / "ov-picks-demo.json").write_text(json.dumps(_screen(), indent=2))
    os.chdir(work)   # every data/ path in the app is relative to the working folder

    import uvicorn
    from scanner import ov_picks_route
    from scanner.api import AppState, create_app

    start = datetime.fromisoformat(args.at).replace(tzinfo=ET)
    t0 = time.monotonic()

    def clock() -> datetime:
        return datetime.fromtimestamp(start.timestamp() + (time.monotonic() - t0), ET)

    # create_app looks register up on the module when it runs (scanner/api.py, the fork
    # line), so the demo hands it the fake clock without a hook in the app itself.
    real_register = ov_picks_route.register
    ov_picks_route.register = lambda app, app_state: real_register(app, app_state, clock=clock)

    # Set after the imports: scanner/data/alpaca.py loads .env on import and would override it.
    if args.unset:
        os.environ.pop("OV_PICKS_DIR", None)
    else:
        os.environ["OV_PICKS_DIR"] = str(work / "out")

    # The stub scanner scans every name in the folder's days and previews, except --not-scanned.
    from ov_picks import contract
    folders = [d for d in (work / "out").iterdir() if d.is_dir()]
    tickers = {p["ticker"] for d in folders if (d / contract.PICKS_FILE).is_file()
               for p in contract.read_day(work / "out", d.name)["picks"]}
    tickers |= {p["ticker"] for d in folders if (d / contract.PREVIEW_FILE).is_file()
                for p in contract.read_preview(work / "out", d.name)["picks"]}
    skip = {s.strip().upper() for s in args.not_scanned.split(",") if s.strip()}
    scanned = sorted(tickers - skip) or ["SPY"]
    stub = SimpleNamespace(_states={s: object() for s in scanned}, _spy_state=None, _regime=None, _sector_map={})
    app = create_app(AppState(scanner=stub, feed=None))  # type: ignore[arg-type]
    print(f"OV picks demo: {sample.name} at {start:%Y-%m-%d %H:%M:%S} ET, http://127.0.0.1:{args.port}/v2/ "
          f"(work folder {work})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
