"""Share another app's Schwab login instead of signing in here.

Schwab keeps one valid sign-in per app: when another program signs in with the same app key it
revokes ours, and the reverse. With SCHWAB_SHARED_TOKEN_FILE and SCHWAB_SHARED_TOKEN_KEY set, the
scanner never signs in itself. It reads the other app's login (a schwab-py token file, Fernet
encrypted) whenever that file changes, copies it into schwabdev's tokens.db, and schwabdev makes
the scanner's own access tokens from it. The other file is only ever read.

Adapted from Edge Trader's companion/schwab.py (ashwinurao/edge-trader-for-schwab PR #2), which
runs the same way beside the same app.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

TOKENS_DB = Path(os.path.expanduser("~/.schwabdev/tokens.db"))   # schwabdev's default
SYNC_SECONDS = 60.0     # how often a running scanner looks for a new sign-in in the other app

_shared_seen: tuple | None = None     # (mtime_ns, size) of the shared file last copied

_SCHEMA = """CREATE TABLE IF NOT EXISTS schwabdev (
    access_token_issued TEXT NOT NULL, refresh_token_issued TEXT NOT NULL,
    access_token TEXT NOT NULL, refresh_token TEXT NOT NULL, id_token TEXT NOT NULL,
    expires_in INTEGER, token_type TEXT, scope TEXT)"""   # schwabdev's own table


def shared_file() -> Path | None:
    raw = os.environ.get("SCHWAB_SHARED_TOKEN_FILE", "").strip()
    return Path(os.path.expanduser(raw)) if raw else None


def shared_login() -> bool:
    return shared_file() is not None


def signin_hint() -> str:
    """What to do when the Schwab login is missing or running out."""
    if shared_login():
        name = os.environ.get("SCHWAB_SHARED_APP_NAME") or "the app whose login the scanner shares"
        return f"sign in to Schwab again in {name}"
    return "run: python scripts/schwab_auth.py"


def signin_refusal() -> str | None:
    """Why scripts/schwab_auth.py must not sign in here, or None when it may."""
    if not shared_login():
        return None
    return ("The scanner shares another app's Schwab login (SCHWAB_SHARED_TOKEN_FILE is set). "
            "Signing in here would revoke that app's login. Instead, " + signin_hint() + ".")


def sync_shared_token(src: Path | None = None, key: str | None = None, db: Path | None = None) -> bool:
    """Copy the shared login into tokens.db if it changed since the last copy. Returns True if copied.

    Only a new refresh token is copied: access tokens refreshed here stay ours. Never raises: a
    missing or unreadable file leaves tokens.db as it is (the login then runs out and says so)."""
    global _shared_seen
    src, db = src or shared_file(), db or TOKENS_DB
    key = key or os.environ.get("SCHWAB_SHARED_TOKEN_KEY", "")
    if src is None:
        return False
    try:
        st = src.stat()
        seen = (st.st_mtime_ns, st.st_size)
        if seen == _shared_seen:
            return False
        from cryptography.fernet import Fernet      # installed with schwabdev
        payload = json.loads(Fernet(key.encode()).decrypt(src.read_bytes()))
        tok = payload["token"]
        refresh = tok["refresh_token"]
        rt_issued = datetime.fromtimestamp(float(payload["creation_timestamp"]), timezone.utc)
        expires_in = int(tok.get("expires_in") or 1800)
        at_issued = (datetime.fromtimestamp(float(tok["expires_at"]) - expires_in, timezone.utc)
                     if tok.get("expires_at") else rt_issued)
    except Exception as e:
        # The type only: the message of a decrypt or parse error can carry token bytes.
        log.warning("shared Schwab login %s not readable: %s", src, type(e).__name__)
        return False
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db, timeout=30) as con:
        con.execute(_SCHEMA)
        row = con.execute("select refresh_token from schwabdev").fetchone()
        if row and row[0] == refresh:
            _shared_seen = seen       # the other app refreshed its access token: nothing new for us
            return False
        con.execute("delete from schwabdev")
        con.execute("insert into schwabdev values (?, ?, ?, ?, ?, ?, ?, ?)",
                    (at_issued.isoformat(), rt_issued.isoformat(), tok.get("access_token") or "", refresh,
                     tok.get("id_token") or "", expires_in, tok.get("token_type") or "Bearer",
                     tok.get("scope") or "api"))
    _shared_seen = seen
    log.info("copied the shared Schwab login (issued %s)", rt_issued.isoformat(timespec="seconds"))
    return True


def start_sync_thread(stop: threading.Event, every: float = SYNC_SECONDS) -> threading.Thread | None:
    """Keep copying the other app's login while the scanner runs.

    The other app signs in again at least weekly, which revokes the login copied at startup, so a
    scanner running for days must pick up the new one. None when no shared login is set."""
    if not shared_login():
        return None

    def _run() -> None:
        while not stop.wait(every):
            sync_shared_token()

    t = threading.Thread(target=_run, daemon=True, name="schwab-shared-login")
    t.start()
    return t
