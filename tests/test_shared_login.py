"""Shared login: another app's schwab-py token file copied into our tokens.db when it changes."""
import json
import sqlite3
import threading
import time

import pytest
from cryptography.fernet import Fernet

from scanner.data import schwab, shared_login


def _write(path, key, refresh="R1", access="A1", created=None, expires_at=None):
    created = created or time.time() - 3600
    payload = {"creation_timestamp": created,
               "token": {"access_token": access, "refresh_token": refresh, "id_token": "I",
                         "expires_in": 1800, "expires_at": expires_at or time.time() + 1200,
                         "token_type": "Bearer", "scope": "api"}}
    path.write_bytes(Fernet(key).encrypt(json.dumps(payload).encode()))


def _rows(db):
    with sqlite3.connect(db) as con:
        return con.execute("select refresh_token, access_token, refresh_token_issued from schwabdev").fetchall()


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_login, "_shared_seen", None)
    monkeypatch.delenv("SCHWAB_SHARED_TOKEN_FILE", raising=False)
    return tmp_path / "token.fernet", tmp_path / "tokens.db", Fernet.generate_key().decode()


def test_copies_the_login_and_its_issue_time(files):
    src, db, key = files
    _write(src, key, created=time.time() - 86400)
    assert shared_login.sync_shared_token(src, key, db)
    (refresh, access, _issued), = _rows(db)
    assert (refresh, access) == ("R1", "A1")
    age = schwab.refresh_token_age_days(db)
    assert abs(age - 1.0) < 0.01          # the 7-day clock starts when the other app signed in


def test_unchanged_file_is_not_read_again(files, monkeypatch):
    src, db, key = files
    _write(src, key)
    assert shared_login.sync_shared_token(src, key, db)
    monkeypatch.setattr(shared_login.json, "loads", lambda *_: pytest.fail("decrypted again"))
    assert not shared_login.sync_shared_token(src, key, db)


def test_only_a_new_refresh_token_replaces_ours(files):
    src, db, key = files
    _write(src, key, refresh="R1", access="A1")
    shared_login.sync_shared_token(src, key, db)
    with sqlite3.connect(db) as con:      # our own access-token refresh since then
        con.execute("update schwabdev set access_token = 'OURS'")
    time.sleep(0.01)
    _write(src, key, refresh="R1", access="A2")      # the other app refreshed its access token
    assert not shared_login.sync_shared_token(src, key, db)
    assert _rows(db)[0][1] == "OURS"
    time.sleep(0.01)
    _write(src, key, refresh="R2", access="A3")      # the other app signed in again
    assert shared_login.sync_shared_token(src, key, db)
    assert _rows(db)[0][:2] == ("R2", "A3")


def test_bad_key_or_missing_file_leaves_tokens_alone(files):
    src, db, key = files
    _write(src, key)
    assert not shared_login.sync_shared_token(src, Fernet.generate_key().decode(), db)
    assert not shared_login.sync_shared_token(src.with_name("missing"), key, db)
    assert not db.exists()


def test_without_a_shared_file_nothing_happens(files):
    _src, db, _key = files
    assert not shared_login.shared_login()
    assert not shared_login.sync_shared_token(db=db)
    assert shared_login.start_sync_thread(threading.Event()) is None
    assert shared_login.signin_refusal() is None
    assert "schwab_auth.py" in shared_login.signin_hint()


def test_shared_mode_refuses_to_sign_in_here(files, monkeypatch):
    src, _db, _key = files
    monkeypatch.setenv("SCHWAB_SHARED_TOKEN_FILE", str(src))
    monkeypatch.setenv("SCHWAB_SHARED_APP_NAME", "OpenVector")
    refusal = shared_login.signin_refusal()
    assert refusal and "OpenVector" in refusal and "revoke" in refusal
    assert shared_login.signin_hint() == "sign in to Schwab again in OpenVector"


def test_sync_thread_picks_up_a_new_sign_in(files, monkeypatch):
    src, db, key = files
    monkeypatch.setenv("SCHWAB_SHARED_TOKEN_FILE", str(src))
    monkeypatch.setenv("SCHWAB_SHARED_TOKEN_KEY", key)
    monkeypatch.setattr(shared_login, "TOKENS_DB", db)
    _write(src, key, refresh="R1")
    stop = threading.Event()
    t = shared_login.start_sync_thread(stop, every=0.01)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (db.exists() and _rows(db)):
            time.sleep(0.01)
        assert _rows(db)[0][0] == "R1"
        _write(src, key, refresh="R2")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _rows(db)[0][0] != "R2":
            time.sleep(0.01)
        assert _rows(db)[0][0] == "R2"
    finally:
        stop.set()
        t.join(timeout=1)
