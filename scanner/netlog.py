"""A record of every outside call the scanner makes, one JSON line each (fork: Ash, project
thread "Review OV picks desk architecture", 2026-11-13 14:05 UTC; brief 11).

Why: outside the opening recorder (scanner/open_recorder.py, 09:29-09:36 ET) nothing recorded
the scanner's calls to Alpaca, Yahoo, Nasdaq or GitHub. The 5 s bar poll logged only a warning on
failure, alpaca-py sets no request timeout, so a hung request held its caller with no trace, and
the server log lived in the container, which every rebuild deletes. OpenVector shares the Alpaca
keys, so the rate limit is shared with traffic the scanner cannot see. This file is the record.

It is a record, never a rule: nothing in the scanner reads it back to decide anything, and no
code path waits for it. Every emit is inside try/except and never raises into its caller. Lines
go into a queue and one writer thread appends them; a full queue drops the line and counts it in
`dropped`.

File: data/netlog/YYYY-MM-DD.jsonl (UTC date), append only, one compact JSON object per line,
record format v1 (shared with Edge Trader's companion/netlog.py). Every line has `ts` (UTC),
`kind` and `app` ("edge-scanner"). Kinds written here:

  answer   one HTTP answer, from a requests response hook on the client's own session: vendor,
           method, path (shaped, no query), http, wire_ms (requests' `elapsed`: send until the
           answer's headers), bytes, date (vendor's Date header), retry_after, thread, call and
           attempt (when a `call` is open on the thread), rl_limit, rl_remaining, rl_reset
           (Alpaca's X-RateLimit-* headers when sent).
  call     one logical call as our code sees it, written when it ends: id, vendor, endpoint,
           purpose, asked, wait_ms (+ wait_client_ms / wait_pause_ms), wire_ms (sum over
           attempts), between_ms (retry sleeps and page parsing between answers), after_ms (body
           download, JSON parse and DataFrame build after the last answer's headers), total_ms,
           attempts, pages, http, ok, error, error_text, items, data_ts, data_age_ms, thread, req.
           wait_ms + wire_ms + between_ms + after_ms = total_ms.
  inflight a call still open after 2, 5, 10, 30 and 60 s (alpaca-py has no timeout, review R-13).
  bars     per minute bar M, once the poll stops asking for M: how late each symbol's bar came.
  route    one inbound request to a named route: route, method, http, ms, from, req.
  lag      each minute: heartbeat thread and asyncio loop lateness, threads, CPU, RSS, queue.
  note     start, bar poll start, and an error said once.

Never written: API keys, request or answer bodies, query strings, .env values. Error texts have
URLs cut at the query and the Alpaca keys replaced.

On when the server starts it (scripts/run_live.py calls start()); off with EDGE_SCANNER_NETLOG=0,
which also turns off the WARNING log file in data/logs/. Tests and scripts that never call
start() write nothing.
"""
from __future__ import annotations

import asyncio
import contextvars
import itertools
import json
import logging
import logging.handlers
import math
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

log = logging.getLogger("scanner.netlog")

APP = "edge-scanner"
ENV = "EDGE_SCANNER_NETLOG"
DIR = Path(__file__).resolve().parent.parent / "data" / "netlog"
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
MAX_QUEUE = 20_000
INFLIGHT_AT_S = (2, 5, 10, 30, 60)   # brief 08, kind `inflight`
FORGET_OPEN_S = 3600                 # an open call this old is dropped from the in-flight list
TICK_S = 0.1                         # heartbeat: a thread sleeping 100 ms measures how late it wakes
LAG_EVERY_S = 60
RING = 8192                          # per (vendor, endpoint, purpose): one 5 s poll a day fits
WINDOW_S = 300                       # diag: "last 5 minutes"
DAY_FROM_ET = dtime(9, 25)           # diag: "since 09:25 ET today"
_ET = ZoneInfo("America/New_York")
_MINUTE = timedelta(minutes=1)

# Clocks, patched by tests. Durations from the monotonic clock, timestamps from the UTC wall clock
# (brief 08, rule 3).
_mono: Callable[[], float] = time.monotonic
_wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

_q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
_writer: Optional[threading.Thread] = None
_beat_thread: Optional[threading.Thread] = None
_beat_stop = threading.Event()
_start_lock = threading.Lock()
_started = False
_dir = DIR
_warned = False
dropped = 0

_tl = threading.local()
_req_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("netlog_req", default=None)
_ids = itertools.count(1)
_id_prefix = uuid.uuid4().hex[:6]


def enabled(env=None) -> bool:
    """False when EDGE_SCANNER_NETLOG=0 (or false/no/off)."""
    return (os.environ if env is None else env).get(ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def active() -> bool:
    """True once start() ran with the switch on."""
    return _started


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _r1(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 1)


def _new_id() -> str:
    return f"{_id_prefix}-{next(_ids)}"


# ── shaping: nothing secret reaches a line ───────────────────────────────────

_HASH = re.compile(r"(?<=/)[A-Za-z0-9]{20,}(?=/|$)")
_NUM = re.compile(r"(?<=/)\d{5,}(?=/|$)")
_URL_QUERY = re.compile(r"(https?://[^\s?'\"<>]+)\?[^\s'\"<>)]*")


def shape(url: str) -> str:
    """The path of a URL, no query string, long ids replaced (same rule as Edge Trader's)."""
    path = urlsplit(url).path or "/"
    return _HASH.sub("{hash}", _NUM.sub("{n}", path))


def _secrets() -> list[str]:
    out = []
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        v = (os.environ.get(name) or "").strip()
        if len(v) >= 6:
            out.append(v)
    return out


def scrub(text: Any, limit: int = 200) -> str:
    """Error text safe to write: URLs cut at the query, Alpaca keys replaced, at most `limit` chars."""
    s = _URL_QUERY.sub(r"\1?…", str(text))
    for secret in _secrets():
        s = s.replace(secret, "{key}")
    s = " ".join(s.split())
    return s[:limit]


# ── error classes (brief 08) ─────────────────────────────────────────────────

_TLS_TEXT = re.compile(r"\bssl|\btls\b|certificate|handshake")


def _http_class(http: int) -> str:
    return "http_429" if http == 429 else "http_5xx" if http >= 500 else "http_4xx"


def classify(exc: Optional[BaseException] = None, http: Optional[int] = None) -> Optional[str]:
    """The error class of a failed call: timeout_connect, timeout_read, tls, dns, refused, reset,
    http_429, http_4xx, http_5xx or other. None when nothing failed."""
    if exc is None:
        return _http_class(http) if http is not None and http >= 400 else None
    try:
        status = getattr(exc, "status_code", None)            # alpaca-py APIError
        if not isinstance(status, int):
            code = getattr(exc, "code", None)                 # urllib HTTPError
            status = code if isinstance(code, int) and 100 <= code < 600 else None
        if status is None and http is not None and http >= 400:
            status = http
        if isinstance(status, int) and status >= 400:
            return _http_class(status)
        names, texts, seen, e = [], [], set(), exc
        while e is not None and id(e) not in seen and len(names) < 8:
            seen.add(id(e))
            names.append(type(e).__name__)
            texts.append(str(e))
            e = e.__cause__ or e.__context__
        n, t = " ".join(names), " ".join(texts).lower()
        if "ConnectTimeout" in n or "connecttimeout" in t or "connection to" in t and "timed out" in t:
            return "timeout_connect"
        if "SSL" in n or _TLS_TEXT.search(t):
            return "tls"
        if ("NameResolution" in n or "gaierror" in n or "failed to resolve" in t or "name or service not known" in t
                or "could not resolve" in t or "getaddrinfo" in t or "name resolution" in t
                or "nodename nor servname" in t):
            return "dns"
        if "Timeout" in n or "timed out" in t or "timeout" in t:
            return "timeout_read"
        if "ConnectionRefused" in n or "connection refused" in t or "actively refused" in t:
            return "refused"
        if ("ConnectionReset" in n or "RemoteDisconnected" in n or "connection reset" in t
                or "connection aborted" in t or "remote end closed" in t or "recv failure" in t
                or "ChunkedEncoding" in n or "IncompleteRead" in n):
            return "reset"
        if "RateLimit" in n or "rate limit" in t or "too many requests" in t:
            return "http_429"
    except Exception:
        pass
    return "other"


# ── emit and the writer ──────────────────────────────────────────────────────

def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _start_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_run, name="netlog-writer", daemon=True)
            _writer.start()


def emit(kind: str, **fields) -> None:
    """Queue one line of any kind. Never raises. Nothing before start()."""
    global dropped
    try:
        if not _started:
            return
        _ensure_writer()
        rec = {"ts": _iso(_wall()), "kind": kind, "app": APP, **fields}
        try:
            _rollup.add(rec)
        except Exception:
            pass
        try:
            _q.put_nowait(rec)
        except queue.Full:
            dropped += 1
    except Exception:
        pass


def _run() -> None:
    global _warned, dropped
    while True:
        batch = [_q.get()]
        try:
            while len(batch) < 500:
                batch.append(_q.get_nowait())
        except queue.Empty:
            pass
        try:
            by_day: dict[str, list[str]] = {}
            for rec in batch:
                # One line at a time (architect, review of Edge Trader's probe, 2026-11-13): a value
                # json cannot write loses its own line, counted in `dropped`, never the batch.
                try:
                    line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
                except Exception:
                    dropped += 1
                    continue
                by_day.setdefault(str(rec.get("ts", ""))[:10] or "unknown", []).append(line)
            _dir.mkdir(parents=True, exist_ok=True)
            for day, lines in by_day.items():
                with open(_dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
                    f.write("".join(lines))
        except Exception as e:
            if not _warned:  # say it once; a full disk must not flood the log
                _warned = True
                log.warning("netlog: could not write to %s: %r", _dir, e)
        finally:
            for _ in batch:
                _q.task_done()


def flush() -> None:
    """Wait until every queued line is written (tests)."""
    _q.join()


# ── start and stop ───────────────────────────────────────────────────────────

_gauges: dict[str, Callable[[], Any]] = {}


def gauge(name: str, fn: Callable[[], Any]) -> None:
    """A number the minute's `lag` line carries (e.g. feed_clients). `fn` must be quick."""
    _gauges[name] = fn


def _commit() -> Optional[str]:
    for name in ("EDGE_SCANNER_COMMIT", "GIT_COMMIT", "SOURCE_COMMIT"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v[:40]
    try:  # a clone has .git; the server image does not (.dockerignore)
        git = Path(__file__).resolve().parent.parent / ".git"
        head = (git / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            p = git / ref
            if p.exists():
                return p.read_text().strip()[:40]
            for line in (git / "packed-refs").read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0][:40]
            return None
        return head[:40]
    except Exception:
        return None


def start(directory: Optional[Path] = None, *, beat: bool = True, **note) -> bool:
    """Start recording: the writer, the heartbeat (lag and in-flight lines) and a start note.
    False, with no thread started, when EDGE_SCANNER_NETLOG=0."""
    global _started, _dir, _beat_thread
    try:
        if not enabled():
            log.info("netlog: off (%s=0)", ENV)
            return False
        if _started:
            return True
        _dir = Path(directory) if directory is not None else DIR
        _started = True
        _ensure_writer()
        if beat:
            _beat_stop.clear()
            _beat_thread = threading.Thread(target=_beat, name="netlog-heartbeat", daemon=True)
            _beat_thread.start()
        try:
            from scanner import __version__ as version
        except Exception:
            version = None
        emit("note", text="start", version=version, commit=_commit(), pid=os.getpid(),
             python=sys.version.split()[0], netlog_dir=str(_dir), **note)
        return True
    except Exception as e:
        log.warning("netlog: not started: %r", e)
        return False


def stop() -> None:
    """Stop recording (tests). Queued lines are still written."""
    global _started
    _started = False
    _beat_stop.set()
    t = _beat_thread
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=2)
    with _open_lock:
        _open.clear()
    _lag.reset()
    _rollup.reset()


# ── answers: the requests response hook ─────────────────────────────────────

Vendor = Union[str, Callable[[str], str]]


def _int(v: Any) -> Any:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return str(v)[:40]


def _answer(r, vendor: Vendor) -> None:
    now = _mono()
    h = r.headers
    url = r.request.url
    wire = r.elapsed.total_seconds() * 1000
    c = _current()
    rec: dict[str, Any] = {
        "vendor": vendor(url) if callable(vendor) else vendor,
        "method": r.request.method,
        "path": shape(url),
        "http": r.status_code,
        "wire_ms": _r1(wire),
        "bytes": None,
        "date": h.get("Date"),
        "retry_after": h.get("Retry-After"),
        "thread": threading.current_thread().name,
    }
    size = h.get("Content-Length") or ""
    if size.isdigit():
        rec["bytes"] = int(size)
    if c is not None:
        rec["call"] = c.id
        rec["attempt"] = c._on_answer(now, wire, r.status_code)
    for field, name in (("rl_limit", "X-RateLimit-Limit"), ("rl_remaining", "X-RateLimit-Remaining"),
                        ("rl_reset", "X-RateLimit-Reset")):
        v = h.get(name)
        if v is not None:
            rec[field] = _int(v)
    emit("answer", **rec)


def _hook(vendor: Vendor):
    def _on_response(r, *args, **kwargs):
        """requests response hook. Returns None, so requests keeps the answer as it is."""
        try:
            if _started:
                _answer(r, vendor)
        except Exception:  # a record must never break the request it describes
            pass
        return None
    _on_response._netlog = True  # type: ignore[attr-defined]
    return _on_response


def attach(session, vendor: Vendor) -> bool:
    """Record every answer this requests session gets (an alpaca-py client's `_session`).
    True when attached. Nothing is attached when EDGE_SCANNER_NETLOG=0."""
    try:
        if not enabled():
            return False
        hooks = session.hooks.setdefault("response", [])
        if not any(getattr(h, "_netlog", False) for h in hooks):
            hooks.append(_hook(vendor))
        return True
    except Exception as e:
        log.warning("netlog: not attached: %r", e)
        return False


def request_hooks(vendor: Vendor) -> dict:
    """Keyword arguments for one requests.get(...) call: `hooks=` with the answer hook, or nothing
    when EDGE_SCANNER_NETLOG=0, so the call is exactly as before."""
    try:
        return {"hooks": {"response": [_hook(vendor)]}} if enabled() else {}
    except Exception:
        return {}


# ── calls ────────────────────────────────────────────────────────────────────

_open: dict[str, "Call"] = {}
_open_lock = threading.Lock()


def _stack() -> list:
    s = getattr(_tl, "stack", None)
    if s is None:
        s = _tl.stack = []
    return s


def _current() -> Optional["Call"]:
    s = getattr(_tl, "stack", None)
    return s[-1] if s else None


class _Null:
    """What call() gives when nothing is recorded: every method does nothing."""
    id = None

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name):
        return lambda *a, **k: None


_NULL = _Null()


class Call:
    """One logical call. Answers on this thread while it is open count as its attempts."""

    def __init__(self, vendor: str, endpoint: str, purpose: str, visible: bool, defer: bool,
                 fields: dict) -> None:
        self.id = _new_id()
        self.vendor, self.endpoint, self.purpose = vendor, endpoint, purpose
        self.visible = visible
        self.defer = defer
        self.fields = dict(fields)
        self.asked_wall = _wall()
        self.asked = _mono()
        self.thread = threading.current_thread().name
        self.req = _req_var.get()
        self.first_send: Optional[float] = None
        self.last_answer: Optional[float] = None
        self.wire = 0.0
        self.attempts = 0
        self.pages = 0
        self.http: Optional[int] = None
        self.end: Optional[float] = None
        self.exc: Optional[BaseException] = None
        self.items: Optional[int] = None
        self.data_ts: Optional[datetime] = None
        self.marks: set = set()
        self.wait_parts: dict[str, float] = {}
        self.written = False

    def __bool__(self) -> bool:
        return True

    def _on_answer(self, now: float, wire_ms: float, http: int) -> int:
        self.attempts += 1
        if self.first_send is None:
            self.first_send = now - wire_ms / 1000
        self.last_answer = now
        self.wire += wire_ms
        self.http = http
        if 200 <= http < 300:
            self.pages += 1
        return self.attempts

    def set(self, **fields) -> None:
        """Extra fields for the line (http= counts when no answer was seen)."""
        try:
            self.fields.update(fields)
        except Exception:
            pass

    def seen(self, items: Optional[int] = None, data_ts: Optional[datetime] = None) -> None:
        """Things returned, and the newest vendor timestamp among them (aware datetime)."""
        try:
            if items is not None:
                self.items = int(items)
            if isinstance(data_ts, datetime) and data_ts.tzinfo is not None:
                self.data_ts = data_ts
        except Exception:
            pass

    def waited(self, part: str) -> None:
        """Mark the end of a wait before the call, from the call's start: wait_pause_ms etc."""
        try:
            self.wait_parts[part] = (_mono() - self.asked) * 1000
        except Exception:
            pass

    def error(self, exc: BaseException) -> None:
        """A failure our code caught inside the call. The first one counts."""
        if self.exc is None:
            self.exc = exc

    def finish(self, exc: Optional[BaseException] = None) -> None:
        if self.end is not None:
            return
        self.end = _mono()
        if exc is not None and self.exc is None:
            self.exc = exc
        with _open_lock:
            _open.pop(self.id, None)
        s = getattr(_tl, "stack", None)
        if s and self in s:
            s.remove(self)

    def write(self, **fields) -> None:
        """Write the `call` line (once). Never raises."""
        try:
            if self.written:
                return
            self.written = True
            if self.end is None:
                self.finish()
            if "items" in fields or "data_ts" in fields:
                self.seen(fields.pop("items", None), fields.pop("data_ts", None))
            self.fields.update(fields)
            emit("call", **self.record())
        except Exception:
            pass

    def end_call(self, exc: Optional[BaseException] = None, **fields) -> None:
        """For begin(): end and write in one step."""
        try:
            self.finish(exc)
            self.write(**fields)
        except Exception:
            pass

    def record(self) -> dict:
        end = self.end if self.end is not None else _mono()
        f = dict(self.fields)
        http = self.http if self.http is not None else f.pop("http", None)
        f.pop("http", None)
        total = (end - self.asked) * 1000
        rec: dict[str, Any] = {
            "id": self.id, "vendor": self.vendor, "endpoint": self.endpoint, "purpose": self.purpose,
            "asked": _iso(self.asked_wall),
        }
        wait = between = after = None
        if self.visible and self.first_send is not None:
            wait = max(0.0, (self.first_send - self.asked) * 1000)
            between = max(0.0, (self.last_answer - self.first_send) * 1000 - self.wire)
            after = max(0.0, (end - self.last_answer) * 1000)
        rec["wait_ms"] = _r1(wait)
        if self.visible and wait is not None:
            rec["wait_client_ms"] = _r1(max(0.0, wait - sum(self.wait_parts.values())))
        for part, ms in self.wait_parts.items():
            rec[part] = _r1(ms)
        rec["wire_ms"] = _r1(self.wire) if self.visible and self.attempts else None
        rec["between_ms"] = _r1(between)
        rec["after_ms"] = _r1(after)
        rec["total_ms"] = _r1(total)
        rec["attempts"] = self.attempts if self.visible else None
        if not self.visible:
            rec["attempts_visible"] = False
        rec["pages"] = self.pages if self.visible else None
        rec["http"] = http
        err = classify(self.exc, http) if (self.exc is not None or (http is not None and http >= 400)) else None
        rec["ok"] = err is None
        rec["error"] = err
        rec["error_text"] = scrub(f"{type(self.exc).__name__}: {self.exc}") if self.exc is not None else None
        rec["items"] = self.items
        rec["data_ts"] = _iso(self.data_ts) if self.data_ts is not None else None
        if self.data_ts is not None:
            got = self.last_answer if self.last_answer is not None else end
            received = self.asked_wall + timedelta(seconds=got - self.asked)
            rec["data_age_ms"] = _r1((received - self.data_ts).total_seconds() * 1000)
        else:
            rec["data_age_ms"] = None
        rec["thread"] = self.thread
        if self.req:
            rec["req"] = self.req
        rec.update(f)
        return rec


def _open_call(vendor: str, endpoint: str, purpose: str, visible: bool, defer: bool, fields: dict,
               push: bool = True):
    if not _started or _stack():
        # Inside another call on this thread (the poll's pass around the feed's own request):
        # the outer call keeps the answers and writes the one line.
        return _NULL
    c = Call(vendor, endpoint, purpose, visible, defer, fields)
    if push:
        _stack().append(c)
    with _open_lock:
        _open[c.id] = c
    return c


class call:
    """`with netlog.call(vendor, endpoint, purpose, **fields) as c:` around one logical call.

    Writes one `call` line when the block ends, ok or not; an exception goes on as before.
    visible=False: the library does not use a hooked requests session (yfinance, urllib), so
    attempts and wire time are not known. defer=True: the block ends the call, c.write(...)
    writes the line later (the poll adds what happened after the fetch)."""

    __slots__ = ("_args", "_c")

    def __init__(self, vendor: str, endpoint: str, purpose: str, *, visible: bool = True,
                 defer: bool = False, **fields) -> None:
        self._args = (vendor, endpoint, purpose, visible, defer, fields)
        self._c: Any = _NULL

    def __enter__(self):
        try:
            self._c = _open_call(*self._args)
        except Exception:
            self._c = _NULL
        return self._c

    def __exit__(self, et, ev, tb) -> bool:
        c = self._c
        if c:
            try:
                c.finish(ev)
                if not c.defer:
                    c.write()
            except Exception:
                pass
        return False


def begin(vendor: str, endpoint: str, purpose: str, **fields):
    """Open a call without a with-block, for a library whose answers are not visible (yfinance);
    end it with c.end_call(exc). It is not put on the thread's stack, so a call that is never
    ended cannot swallow later ones; the in-flight list forgets it after an hour. Never raises."""
    try:
        return _open_call(vendor, endpoint, purpose, False, False, fields, push=False)
    except Exception:
        return _NULL


def check_inflight(now: Optional[float] = None) -> None:
    """One `inflight` line for each open call that just passed 2, 5, 10, 30 or 60 s."""
    try:
        now = _mono() if now is None else now
        with _open_lock:
            calls = list(_open.values())
        for c in calls:
            age = now - c.asked
            if age > FORGET_OPEN_S:
                with _open_lock:
                    _open.pop(c.id, None)
                continue
            for at in INFLIGHT_AT_S:
                if age >= at and at not in c.marks:
                    c.marks.add(at)
                    emit("inflight", id=c.id, vendor=c.vendor, endpoint=c.endpoint, purpose=c.purpose,
                         asked=_iso(c.asked_wall), age_ms=_r1(age * 1000), at_s=at, thread=c.thread)
    except Exception:
        pass


# ── data timestamps ──────────────────────────────────────────────────────────

def newest_bar_end(df: Any) -> Optional[datetime]:
    """The end of the newest 1-minute bar in an Alpaca bars frame (index symbol, timestamp)."""
    try:
        if df is None or getattr(df, "empty", True):
            return None
        idx = df.index
        stamps = idx.get_level_values(-1) if getattr(idx, "nlevels", 1) > 1 else idx
        newest = stamps.max()
        ts = newest.to_pydatetime() if hasattr(newest, "to_pydatetime") else newest
        if not isinstance(ts, datetime):
            return None
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return ts + _MINUTE
    except Exception:
        return None


def newest_trade(snapshots: Any) -> Optional[datetime]:
    """The newest latest-trade time in Alpaca snapshots ({symbol: Snapshot})."""
    try:
        best = None
        for snap in (snapshots or {}).values():
            ts = getattr(getattr(snap, "latest_trade", None), "timestamp", None)
            if isinstance(ts, datetime) and ts.tzinfo is not None and (best is None or ts > best):
                best = ts
        return best
    except Exception:
        return None


def newest_iso(texts: Iterable[Any]) -> Optional[datetime]:
    """The newest of some ISO timestamps (news created_at)."""
    best = None
    try:
        for t in texts:
            try:
                ts = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is not None and (best is None or ts > best):
                best = ts
    except Exception:
        pass
    return best


# ── bar lateness (brief 11, item 4) ──────────────────────────────────────────

def pct(values: list, q: float) -> Optional[float]:
    """Nearest-rank percentile of sorted `values`: the ceil(q*n)-th value."""
    if not values:
        return None
    return values[max(0, math.ceil(q * len(values)) - 1)]


def _spread(values: list) -> Optional[dict]:
    if not values:
        return None
    v = sorted(values)
    return {"min": _r1(v[0]), "p50": _r1(pct(v, 0.50)), "p95": _r1(pct(v, 0.95)), "max": _r1(v[-1])}


class BarLateness:
    """How late each minute's bars came, all day: one `bars` line per minute M once the poll
    stops asking for M. first_seen_ms_after_close = when the poll first returned symbol S's bar
    for M, minus M's end; handed_ms_after_close = when the poll handed it to the scanner."""

    MAX_OPEN_MINUTES = 60

    def __init__(self, expected: int, started: datetime) -> None:
        self.expected = expected
        self.started = started
        self._seen: dict[datetime, dict[str, float]] = {}
        self._handed: dict[datetime, list[float]] = {}

    def seen(self, df: Any, at: Optional[datetime] = None) -> None:
        """A poll answer arrived at `at` (default now)."""
        try:
            if not _started or df is None or getattr(df, "empty", True) or getattr(df.index, "nlevels", 1) < 2:
                return
            at = at or _wall()
            syms = df.index.get_level_values(0)
            stamps = df.index.get_level_values(1)
            for sym, ts in zip(syms, stamps):
                m = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                m = m if m.tzinfo else m.replace(tzinfo=timezone.utc)
                end = m + _MINUTE
                if end <= self.started:
                    continue                  # closed before the poll started: not measurable
                d = self._seen.setdefault(m, {})
                s = str(sym)
                if s not in d:
                    d[s] = (at - end).total_seconds() * 1000
        except Exception:
            pass

    def handed(self, bars: list, at: Optional[datetime] = None) -> None:
        try:
            if not _started:
                return
            at = at or _wall()
            for b in bars:
                m = b["timestamp"]
                if m + _MINUTE <= self.started:
                    continue
                self._handed.setdefault(m, []).append((at - (m + _MINUTE)).total_seconds() * 1000)
        except Exception:
            pass

    def moved_past(self, start: datetime) -> None:
        """The poll now asks from `start`: every minute before it is done."""
        try:
            if not _started:
                return
            keys = sorted(set(self._seen) | set(self._handed))
            old = [m for m in keys if m < start]
            if len(keys) - len(old) > self.MAX_OPEN_MINUTES:
                old = keys[: len(keys) - self.MAX_OPEN_MINUTES]
            for m in old:
                seen = self._seen.pop(m, {})
                handed = self._handed.pop(m, [])
                emit("bars", minute=_iso(m), minute_et=m.astimezone(_ET).isoformat(timespec="minutes"),
                     expected=self.expected, with_bar=len(seen),
                     first_seen_ms_after_close=_spread(list(seen.values())),
                     handed=len(handed), handed_ms_after_close=_spread(handed))
        except Exception:
            pass


# ── lag: heartbeat thread and asyncio loop ───────────────────────────────────

class _Lag:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.thread: list[float] = []
        self.loop: list[float] = []
        self.cpu = time.process_time()

    def add_thread(self, ms: float) -> None:
        with self.lock:
            self.thread.append(ms)

    def add_loop(self, ms: float) -> None:
        with self.lock:
            self.loop.append(ms)

    def take(self) -> tuple[list[float], list[float], float]:
        with self.lock:
            t, l, cpu0 = self.thread, self.loop, self.cpu
            self.thread, self.loop, self.cpu = [], [], time.process_time()
        return t, l, self.cpu - cpu0


_lag = _Lag()
_loop_task: Optional[asyncio.Task] = None


def _rss_mb() -> Optional[float]:
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1e6, 1)
    except Exception:
        return None


def lag_line() -> None:
    """Write the minute's `lag` line and start a new minute."""
    try:
        t, l, cpu = _lag.take()
        t.sort()
        rec: dict[str, Any] = {
            "thread_lag_max_ms": _r1(t[-1]) if t else None,
            "thread_lag_p95_ms": _r1(pct(t, 0.95)),
            "loop_lag_max_ms": _r1(max(l)) if l else None,
            "threads": threading.active_count(),
            "cpu_s": round(cpu, 2),
            "rss_mb": _rss_mb(),
            "queue": _q.qsize(),
            "dropped": dropped,
        }
        with _open_lock:
            rec["inflight"] = len(_open)
        for name, fn in list(_gauges.items()):
            try:
                rec[name] = fn()
            except Exception:
                rec[name] = None
        emit("lag", **rec)
    except Exception:
        pass


def _beat() -> None:
    next_line = time.monotonic() + LAG_EVERY_S
    while not _beat_stop.is_set():
        try:
            t0 = time.monotonic()
            if _beat_stop.wait(TICK_S):
                return
            now = time.monotonic()
            _lag.add_thread(max(0.0, (now - t0 - TICK_S) * 1000))
            check_inflight()
            if now >= next_line:
                next_line = now + LAG_EVERY_S
                lag_line()
        except Exception:
            pass


async def _loop_lag() -> None:
    while _started:
        t0 = time.monotonic()
        await asyncio.sleep(TICK_S)
        _lag.add_loop(max(0.0, (time.monotonic() - t0 - TICK_S) * 1000))


def start_loop_monitor() -> None:
    """From the app's startup: a task on its asyncio loop measures how late the loop runs."""
    global _loop_task
    try:
        if _started and (_loop_task is None or _loop_task.done()):
            _loop_task = asyncio.get_running_loop().create_task(_loop_lag())
    except Exception:
        pass


# ── inbound routes ───────────────────────────────────────────────────────────

ROUTE_PREFIXES = ("/api/v2/ov-picks", "/api/v2/clock", "/api/bars/", "/api/v2/health")
_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")


def _header(scope: dict, name: bytes) -> Optional[str]:
    for k, v in scope.get("headers") or ():
        if k.lower() == name:
            s = _SAFE.sub("", v.decode("latin-1"))[:64]
            return s or None
    return None


class RouteTimer:
    """ASGI middleware: one `route` line per request to the named routes (ROUTE_PREFIXES). It
    passes every message through unchanged; `from` and `req` come from Edge Trader's X-Edge-App
    and X-Edge-Req headers. The live feed (/ws/alerts) is a stream and is not timed."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _started or not str(scope.get("path", "")).startswith(ROUTE_PREFIXES):
            await self.app(scope, receive, send)
            return
        t0 = time.monotonic()
        state: dict[str, Any] = {"http": None, "done": None}
        try:
            req = _header(scope, b"x-edge-req") or _new_id()
            frm = _header(scope, b"x-edge-app") or "other"
            token = _req_var.set(req)
        except Exception:
            req, frm, token = None, "other", None

        async def _send(message):
            try:
                if message["type"] == "http.response.start":
                    state["http"] = message.get("status")
                elif message["type"] == "http.response.body" and not message.get("more_body", False):
                    state["done"] = time.monotonic()
            except Exception:
                pass
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, _send)
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if token is not None:
                    _req_var.reset(token)
                route = scope.get("route")
                pattern = getattr(route, "path", None) or scope.get("path")
                end = state["done"] or time.monotonic()
                emit("route", route=pattern, method=scope.get("method"),
                     http=state["http"] if state["http"] is not None else (500 if failed else None),
                     ms=_r1((end - t0) * 1000), **{"from": frm}, req=req)
            except Exception:
                pass


# ── diag rollup (GET /api/v2/diag/net) ───────────────────────────────────────

class _Rollup:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.calls: dict[tuple, deque] = {}
        self.added: dict[tuple, int] = {}
        self.last_ok: dict[tuple, float] = {}
        self.rl: dict[str, deque] = {}
        self.last: dict[str, dict] = {}

    def add(self, rec: dict) -> None:
        kind = rec.get("kind")
        if kind == "call":
            key = (rec.get("vendor"), rec.get("endpoint"), rec.get("purpose"))
            at = _epoch(rec["ts"])
            row = (at, rec.get("total_ms"), rec.get("wire_ms"), rec.get("error"))
            with self.lock:
                ring = self.calls.get(key)
                if ring is None:
                    ring = self.calls[key] = deque(maxlen=RING)
                ring.append(row)
                self.added[key] = self.added.get(key, 0) + 1
                if rec.get("ok") and at > self.last_ok.get(key, float("-inf")):
                    self.last_ok[key] = at
        elif kind == "answer" and isinstance(rec.get("rl_remaining"), int):
            with self.lock:
                ring = self.rl.get(rec.get("vendor"))
                if ring is None:
                    ring = self.rl[rec.get("vendor")] = deque(maxlen=RING)
                ring.append((_epoch(rec["ts"]), rec["rl_remaining"], rec.get("rl_limit")))
        elif kind in ("lag", "bars"):
            with self.lock:
                self.last[kind] = rec

    def copy(self):
        with self.lock:
            return ({k: list(v) for k, v in self.calls.items()}, dict(self.added), dict(self.last_ok),
                    {k: list(v) for k, v in self.rl.items()}, dict(self.last))


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


_rollup = _Rollup()


def _window(rows: list, since: float, now: float, ring_full: bool) -> dict:
    sel = [r for r in rows if since <= r[0] <= now + 1]
    errors: dict[str, int] = {}
    for r in sel:
        if r[3]:
            errors[r[3]] = errors.get(r[3], 0) + 1
    total = sorted(r[1] for r in sel if r[1] is not None)
    wire = sorted(r[2] for r in sel if r[2] is not None)
    stats = lambda v: ({"p50": _r1(pct(v, 0.5)), "p95": _r1(pct(v, 0.95)), "max": _r1(v[-1])} if v else None)
    return {"count": len(sel), "errors": errors, "wire_ms": stats(wire), "total_ms": stats(total),
            # a full ring may have dropped calls inside the window
            "complete": not ring_full or not rows or rows[0][0] < since}


def diag(now: Optional[datetime] = None) -> dict:
    """The rollup: per vendor, endpoint and purpose, the last 5 minutes and since 09:25 ET today."""
    now_dt = now or _wall()
    now_s = now_dt.timestamp()
    et = now_dt.astimezone(_ET)
    day_from = datetime.combine(et.date(), DAY_FROM_ET, tzinfo=_ET).timestamp()
    calls, added, last_ok, rl, last = _rollup.copy()
    rows = []
    for key in sorted(calls, key=lambda k: tuple(str(x) for x in k)):
        ring = calls[key]
        full = added.get(key, 0) > len(ring)
        ok_at = last_ok.get(key)
        rows.append({
            "vendor": key[0], "endpoint": key[1], "purpose": key[2],
            "last_5_min": _window(ring, now_s - WINDOW_S, now_s, full),
            "since_0925_et": _window(ring, day_from, now_s, full) if now_s >= day_from else None,
            "last_ok_age_s": round(now_s - ok_at, 1) if ok_at is not None else None,
        })
    rl_low = {}
    for vendor, ring in rl.items():
        def low(since: float):
            sel = [r for r in ring if r[0] >= since]
            if not sel:
                return None
            r = min(sel, key=lambda x: x[1])
            return {"rl_remaining": r[1], "rl_limit": r[2], "ts": _iso(datetime.fromtimestamp(r[0], timezone.utc))}
        rl_low[vendor] = {"last_5_min": low(now_s - WINDOW_S),
                          "since_0925_et": low(day_from) if now_s >= day_from else None}
    mono = _mono()
    with _open_lock:
        open_calls = list(_open.values())
    inflight = [{"id": c.id, "vendor": c.vendor, "endpoint": c.endpoint, "purpose": c.purpose,
                 "asked": _iso(c.asked_wall), "age_ms": _r1((mono - c.asked) * 1000), "thread": c.thread}
                for c in open_calls]
    return {
        "app": APP, "active": _started, "now": _iso(now_dt),
        "since_0925_et_from": datetime.fromtimestamp(day_from, timezone.utc).isoformat(),
        "calls": rows, "rl_remaining_lowest": rl_low, "inflight": inflight,
        "last_lag": last.get("lag"), "last_bars": last.get("bars"),
        "dropped": dropped, "queue": _q.qsize(), "dir": str(_dir),
    }


# ── the WARNING log file that survives rebuilds (brief 11, item 7) ──────────

class _DailyFile(logging.Handler):
    """WARNING and above to data/logs/edge-scanner-YYYY-MM-DD.log (UTC date)."""

    def __init__(self, directory: Path) -> None:
        super().__init__(logging.WARNING)
        self.directory = Path(directory)
        self._day: Optional[str] = None
        self._f = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.now(timezone.utc).date().isoformat()
            if day != self._day or self._f is None:
                if self._f is not None:
                    self._f.close()
                self.directory.mkdir(parents=True, exist_ok=True)
                self._f = open(self.directory / f"edge-scanner-{day}.log", "a", encoding="utf-8")
                self._day = day
            self._f.write(self.format(record) + "\n")
            self._f.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._f is not None:
                self._f.close()
                self._f = None
        finally:
            super().close()


class _DropQueueHandler(logging.handlers.QueueHandler):
    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass


_log_listener: Optional[logging.handlers.QueueListener] = None


def log_to_file(directory: Optional[Path] = None) -> bool:
    """Copy WARNING and above to a daily file in data/logs/ through a queue and a thread, so a
    caller never waits on the disk. False when EDGE_SCANNER_NETLOG=0."""
    global _log_listener
    try:
        if not enabled() or _log_listener is not None:
            return False
        fh = _DailyFile(directory or LOG_DIR)
        fmt = logging.Formatter("%(asctime)s.%(msecs)03dZ %(levelname)-8s %(threadName)s %(name)s  %(message)s",
                                datefmt="%Y-%m-%dT%H:%M:%S")
        fmt.converter = time.gmtime
        fh.setFormatter(fmt)
        q: queue.Queue = queue.Queue(maxsize=10_000)
        qh = _DropQueueHandler(q)
        qh.setLevel(logging.WARNING)
        _log_listener = logging.handlers.QueueListener(q, fh, respect_handler_level=True)
        _log_listener.start()
        logging.getLogger().addHandler(qh)
        return True
    except Exception as e:
        log.warning("netlog: no log file: %r", e)
        return False


def stop_log_file() -> None:
    """Undo log_to_file() (tests)."""
    global _log_listener
    listener, _log_listener = _log_listener, None
    if listener is None:
        return
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, _DropQueueHandler):
            root.removeHandler(h)
    listener.stop()
    for h in listener.handlers:
        h.close()
