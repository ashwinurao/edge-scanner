"""OV picks file contract, schema version 1 (ashwinurao/edge-scanner#9).

The OV picks module writes these files and Edge Scanner reads them. Nothing else passes
between the two (spec "Outputs"). ov_picks/CONTRACT.md is the schema: every file, field,
type and allowed value. This module is its code:

- constants for every file name, field name, job and state, for other code to import;
- write_picks, write_settled, write_parity: write one day record once, atomically;
- record_job_state: replace one job's entry in the status file;
- read_day, read_status: readers that never raise on a missing or bad file;
- validate_picks, validate_settled, validate_parity, validate_status: the writer's checks.

Standard library only, so Edge Scanner (Python 3.11 and up, its own requirements) and
the module (Python 3.12, OV's pins) both import it.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 1          # the version this code writes
MIN_SCHEMA_VERSION = 1      # the oldest version the readers accept

# Files. One folder per ET trading day, out/<YYYY-MM-DD>/, holds the day records; the
# status file sits at the top of out/. The runner writes OV's full results and its
# summary into the day folder too, under names it owns (spec "Outputs").
PICKS_FILE = "picks.json"
SETTLED_FILE = "settled.json"
PARITY_FILE = "parity.json"
STATUS_FILE = "status.json"

# The "kind" field of each file, so a file put in the wrong place is refused.
KIND_PICKS = "ov_picks"
KIND_SETTLED = "ov_picks_settled"
KIND_PARITY = "ov_picks_parity"
KIND_STATUS = "ov_picks_status"

# The spec's four pre-market picks (ashwinurao/edge-scanner#8).
MAX_PICKS = 4
# The traded weights sum to 1, added with math.fsum. 1e-9 is the spec's compare tolerance.
WEIGHT_SUM_TOLERANCE = 1e-9
# A sanity bound only; the runner upper-cases and strips tickers the way OV's live code does.
MAX_TICKER_LEN = 16
# The picks file is under 2 KB; anything this big is not one.
MAX_FILE_BYTES = 1_000_000

# ---------------------------------------------------------------- jobs and states
# Spec "Runner", daily schedule table: one job per row, with its result states.

JOB_NIGHTLY = "nightly"        # 03:30 ET
JOB_WARMUP = "warmup"          # 08:45 ET, then the 09:20 ET readiness deadline
JOB_SCAN = "scan"              # 09:25:00 ET
JOB_SETTLE = "settle"          # 09:26 ET
JOB_COMPARE = "compare"        # 10:35 ET
JOB_ARCHIVE = "archive"        # 16:15 ET
JOB_REFERENCE = "reference"    # Saturday 10:00 ET, after go-live only
JOBS = (JOB_NIGHTLY, JOB_WARMUP, JOB_SCAN, JOB_SETTLE, JOB_COMPARE, JOB_ARCHIVE, JOB_REFERENCE)

NIGHTLY_OK = "NIGHTLY_OK"
NIGHTLY_WARN = "NIGHTLY_WARN"
WARMING = "WARMING"
DATA_READY = "DATA_READY"
READY = "READY"
NOT_READY = "NOT_READY"
PICKS_FROZEN = "PICKS_FROZEN"
SETTLED_SAME = "SETTLED_SAME"
SETTLED_DIFF = "SETTLED_DIFF"
PARITY_MATCH = "PARITY_MATCH"
PARITY_DIFF = "PARITY_DIFF"
PARITY_NO_OV_RUN = "PARITY_NO_OV_RUN"
ARCHIVED = "ARCHIVED"
REF_OK = "REF_OK"
REF_WARN = "REF_WARN"

# Any job can be in these. The spec names a refusal (sector coverage, the OV window),
# a market-closed day and failures without giving them a state of their own.
RUNNING = "RUNNING"
SKIPPED = "SKIPPED"
REFUSED = "REFUSED"
FAILED = "FAILED"
COMMON_STATES = (RUNNING, SKIPPED, REFUSED, FAILED)

JOB_STATES = {
    JOB_NIGHTLY: (NIGHTLY_OK, NIGHTLY_WARN) + COMMON_STATES,
    JOB_WARMUP: (WARMING, DATA_READY, READY, NOT_READY) + COMMON_STATES,
    JOB_SCAN: (PICKS_FROZEN,) + COMMON_STATES,
    JOB_SETTLE: (SETTLED_SAME, SETTLED_DIFF) + COMMON_STATES,
    JOB_COMPARE: (PARITY_MATCH, PARITY_DIFF, PARITY_NO_OV_RUN) + COMMON_STATES,
    JOB_ARCHIVE: (ARCHIVED,) + COMMON_STATES,
    JOB_REFERENCE: (REF_OK, REF_WARN) + COMMON_STATES,
}
# A status entry in one of these says why in its message.
STATES_NEEDING_MESSAGE = (NIGHTLY_WARN, NOT_READY, SETTLED_DIFF, PARITY_DIFF, REF_WARN,
                          SKIPPED, REFUSED, FAILED)
# The morning's progress, in order, for a card that shows one line.
DAY_STATES = (WARMING, DATA_READY, READY, NOT_READY, PICKS_FROZEN, SETTLED_SAME, SETTLED_DIFF)

# Picks freeze after the 09:20 ET deadline, so the warmup state in a picks file is its verdict.
PICKS_WARMUP_STATES = (READY, NOT_READY)
SETTLED_STATES = (SETTLED_SAME, SETTLED_DIFF)
PARITY_STATUSES = (PARITY_MATCH, PARITY_DIFF, PARITY_NO_OV_RUN)
REFERENCE_STATES = (REF_OK, REF_WARN)

# OV's earnings data status, as OV writes it.
EARNINGS_AVAILABLE = "available"
EARNINGS_UNAVAILABLE = "unavailable"
EARNINGS_MISSING_CONTEXT = "missing_prior_session_context"
EARNINGS_STATUSES = (EARNINGS_AVAILABLE, EARNINGS_UNAVAILABLE, EARNINGS_MISSING_CONTEXT)

# OV's earnings release timing, the same
# event as its earnings_label text "PRIOR-EVENING EARNINGS" and "SAME-MORNING EARNINGS".
EARNINGS_LABEL_AMC = "AMC"     # reported after the prior session's close
EARNINGS_LABEL_BMO = "BMO"     # reports before this session's open
EARNINGS_LABELS = (EARNINGS_LABEL_AMC, EARNINGS_LABEL_BMO)

# Reader-only values. The writers never store them.
PENDING = "PENDING"            # the 09:26 or 10:35 record is not written yet
MISSING = "MISSING"            # reason_code: the file is not there
UNREADABLE = "UNREADABLE"      # reason_code, settled_state or parity_status: the file cannot be read
INVALID = "INVALID"            # reason_code: the file reads but breaks the contract

# ---------------------------------------------------------------- field names

F_SCHEMA_VERSION = "schema_version"
F_KIND = "kind"
F_TRADING_DATE = "trading_date"
F_FROZEN_AT = "frozen_at"
F_WARMUP_STATE = "warmup_state"
F_WARMUP_REASON = "warmup_reason"
F_LATE = "late"
F_SHADOW = "shadow"
F_EARNINGS_STATUS = "earnings_status"
F_PICKS = "picks"
F_REFERENCE_STATE = "reference_state"
F_REFERENCE_PROMOTED = "reference_promoted"
PICKS_FIELDS = (F_SCHEMA_VERSION, F_KIND, F_TRADING_DATE, F_FROZEN_AT, F_WARMUP_STATE,
                F_WARMUP_REASON, F_LATE, F_SHADOW, F_EARNINGS_STATUS, F_PICKS,
                F_REFERENCE_STATE, F_REFERENCE_PROMOTED)

F_RANK = "rank"
F_TICKER = "ticker"
F_SCORE = "score"
F_EARNINGS_LABEL = "earnings_label"
F_TRADED = "traded"
F_WEIGHT = "weight"
PICK_FIELDS = (F_RANK, F_TICKER, F_SCORE, F_EARNINGS_LABEL, F_TRADED, F_WEIGHT)

F_CHECKED_AT = "checked_at"
F_STATE = "state"
F_DIFFERENCE = "difference"
SETTLED_FIELDS = (F_SCHEMA_VERSION, F_KIND, F_TRADING_DATE, F_CHECKED_AT, F_STATE, F_DIFFERENCE)

F_COMPARED_AT = "compared_at"
F_STATUS = "status"
F_FIRST_DIFFERENCE = "first_difference"
PARITY_FIELDS = (F_SCHEMA_VERSION, F_KIND, F_TRADING_DATE, F_COMPARED_AT, F_STATUS,
                 F_FIRST_DIFFERENCE)

F_JOBS = "jobs"
F_AT = "at"
F_MESSAGE = "message"
F_NEXT_AT = "next_at"
STATUS_FIELDS = (F_SCHEMA_VERSION, F_KIND, F_JOBS)
STATUS_ENTRY_FIELDS = (F_STATE, F_AT, F_TRADING_DATE, F_MESSAGE, F_NEXT_AT)

# What read_day returns: the picks file's fields plus the 09:26 and 10:35 records.
V_HAS_PICKS = "has_picks"
V_REASON_CODE = "reason_code"
V_REASON = "reason"
V_WARNINGS = "warnings"
V_SETTLED_STATE = "settled_state"
V_SETTLED_SAME = "settled_same"
V_SETTLED_DIFF = "settled_diff"
V_SETTLED_DIFFERENCE = "settled_difference"
V_SETTLED_AT = "settled_at"
V_PARITY_STATUS = "parity_status"
V_PARITY_FIRST_DIFFERENCE = "parity_first_difference"
V_PARITY_AT = "parity_at"
VIEW_FIELDS = ((V_HAS_PICKS, V_REASON_CODE, V_REASON, V_WARNINGS)
               + tuple(f for f in PICKS_FIELDS if f != F_KIND)
               + (V_SETTLED_STATE, V_SETTLED_SAME, V_SETTLED_DIFF, V_SETTLED_DIFFERENCE,
                  V_SETTLED_AT, V_PARITY_STATUS, V_PARITY_FIRST_DIFFERENCE, V_PARITY_AT))

# What read_status returns.
V_HAS_STATUS = "has_status"
STATUS_VIEW_FIELDS = (V_HAS_STATUS, V_REASON_CODE, V_REASON, V_WARNINGS, F_SCHEMA_VERSION, F_JOBS)

# Steps that turn a record of version N into version N + 1, keyed by N. Version 1 is the
# first, so there are none yet. A version that adds a field older files lack adds a step
# here, so saved days keep loading (spec US67; CONTRIBUTING.md "Saved work survives").
_UPGRADES: dict[int, Callable[[dict], dict]] = {}


class ContractError(ValueError):
    """A record breaks the contract, so nothing was written. `problems` lists every reason."""

    def __init__(self, what: str, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__(f"{what} refused: " + "; ".join(self.problems))


class AlreadyWritten(FileExistsError):
    """The day's record exists. Day records are written once (spec "Freeze and restart rules")."""


# ---------------------------------------------------------------- small checks

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UTC_Z_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_STATUS_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _et() -> ZoneInfo:
    # Built on first use, so the readers work even where the time-zone database is missing.
    return ZoneInfo("America/New_York")


def _is_bool(v: Any) -> bool:
    return type(v) is bool


def _is_count(v: Any) -> bool:
    return type(v) is int and v >= 1


def _is_number(v: Any) -> bool:
    # bool is an int in Python; a JSON true is not a score or a weight.
    return type(v) in (int, float) and math.isfinite(v)


def _is_text(v: Any) -> bool:
    return isinstance(v, str)


def _has_text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_date_text(v: Any) -> bool:
    if not isinstance(v, str) or not _DATE_RE.fullmatch(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _parse_time(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.utcoffset() is not None else None


def _is_time(v: Any, *, strict: bool) -> bool:
    # Writers store UTC with a Z (CLAUDE.md: times UTC); readers take any ISO time with an offset.
    if strict and not (isinstance(v, str) and _UTC_Z_RE.fullmatch(v)):
        return False
    return _parse_time(v) is not None


def _is_ticker(v: Any) -> bool:
    return (isinstance(v, str) and 0 < len(v) <= MAX_TICKER_LEN and v == v.strip().upper()
            and all(ch.isprintable() and not ch.isspace() for ch in v))


def _is_state(v: Any, allowed: tuple[str, ...], known_values: bool) -> bool:
    # A newer writer may add a state; an older reader shows it as written.
    return v in allowed if known_values else _has_text(v)


def _one_of(allowed: tuple[Any, ...]) -> str:
    return ", ".join("null" if a is None else str(a) for a in allowed)


def utc_timestamp(dt: datetime) -> str:
    """`dt` as the contract writes times: UTC, whole seconds, with a Z."""
    if dt.utcoffset() is None:
        raise ValueError("utc_timestamp needs a time with a time zone, not a naive datetime")
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _date_arg(value: Any) -> str:
    # The date names a folder, so only a real YYYY-MM-DD gets through.
    if isinstance(value, datetime):
        raise ValueError("pass the trading date as a date, not a datetime")
    if isinstance(value, date):
        return value.isoformat()
    if _is_date_text(value):
        return value
    raise ValueError(f"the trading date must be a date or a YYYY-MM-DD string, not {value!r}")


def day_dir(out_dir: str | os.PathLike, trading_date: date | str) -> Path:
    """The folder that holds one ET trading day's records."""
    return Path(out_dir) / _date_arg(trading_date)


# ---------------------------------------------------------------- record checks

def _header_problems(rec: Any, kind: str, *, strict: bool) -> tuple[int | None, list[str]]:
    """The schema version and kind. A writer may leave both out: it fills them in."""
    if not isinstance(rec, Mapping):
        return None, ["the file is not a JSON object"]
    version = rec.get(F_SCHEMA_VERSION, SCHEMA_VERSION if strict else None)
    if type(version) is not int:
        return None, [f"schema_version must be a whole number, not {version!r}"]
    if strict and version != SCHEMA_VERSION:
        return version, [f"schema_version must be {SCHEMA_VERSION} (the version this writer "
                         f"writes), not {version}"]
    if version < MIN_SCHEMA_VERSION:
        return version, [f"schema_version {version} is older than the first version, "
                         f"{MIN_SCHEMA_VERSION}"]
    if rec.get(F_KIND, kind if strict else None) != kind:
        return version, [f"kind must be {kind!r}, not {rec.get(F_KIND)!r}"]
    return version, []


def _unknown_keys(rec: Mapping, fields: tuple[str, ...], where: str) -> list[str]:
    return [f"{where}{key} is not a field of this record" for key in rec if key not in fields]


def _pick_problems(i: int, pick: Any, *, strict: bool, known_values: bool) -> list[str]:
    where = f"picks[{i}]"
    if not isinstance(pick, Mapping):
        return [f"{where} is not a JSON object"]
    problems = _unknown_keys(pick, PICK_FIELDS, f"{where}.") if strict else []
    checks = (
        (F_RANK, _is_count, "a whole number from 1"),
        (F_TICKER, _is_ticker, "an upper-case ticker with no spaces"),
        (F_SCORE, _is_number, "a finite number"),
        (F_EARNINGS_LABEL,
         lambda v: v is None or _is_state(v, EARNINGS_LABELS, known_values),
         f"one of {_one_of(EARNINGS_LABELS + (None,))}"),
        (F_TRADED, _is_bool, "true or false"),
        (F_WEIGHT, lambda v: _is_number(v) and 0.0 <= v <= 1.0, "a number from 0 to 1"),
    )
    for key, ok, what in checks:
        if key not in pick:
            problems.append(f"{where}.{key} is missing")
        elif not ok(pick[key]):
            problems.append(f"{where}.{key} must be {what}, not {pick[key]!r}")
    return problems


def _picks_problems(rec: Mapping, *, strict: bool, known_values: bool) -> list[str]:
    """Every field but the header, and the rules between fields."""
    problems = _unknown_keys(rec, PICKS_FIELDS, "") if strict else []
    checks = (
        (F_TRADING_DATE, _is_date_text, "a date written YYYY-MM-DD"),
        (F_FROZEN_AT, lambda v: _is_time(v, strict=strict),
         "a UTC time written YYYY-MM-DDTHH:MM:SSZ" if strict else "an ISO time with an offset"),
        (F_WARMUP_STATE, lambda v: _is_state(v, PICKS_WARMUP_STATES, known_values),
         f"one of {_one_of(PICKS_WARMUP_STATES)}"),
        (F_WARMUP_REASON, _is_text, "text"),
        (F_LATE, _is_bool, "true or false"),
        (F_SHADOW, _is_bool, "true or false"),
        (F_EARNINGS_STATUS, lambda v: _is_state(v, EARNINGS_STATUSES, known_values),
         f"one of {_one_of(EARNINGS_STATUSES)}"),
        (F_PICKS, lambda v: isinstance(v, list), "a list"),
        (F_REFERENCE_STATE, lambda v: v is None or _is_state(v, REFERENCE_STATES, known_values),
         f"one of {_one_of(REFERENCE_STATES + (None,))}"),
        (F_REFERENCE_PROMOTED, lambda v: isinstance(v, list) and all(map(_is_ticker, v)),
         "a list of upper-case tickers"),
    )
    bad = set()
    for key, ok, what in checks:
        if key not in rec:
            problems.append(f"{key} is missing")
            bad.add(key)
        elif not ok(rec[key]):
            problems.append(f"{key} must be {what}, not {rec[key]!r}")
            bad.add(key)
    picks = rec.get(F_PICKS) if F_PICKS not in bad else []
    for i, pick in enumerate(picks):
        found = _pick_problems(i, pick, strict=strict, known_values=known_values)
        problems += found
        if found:
            bad.add(F_PICKS)

    if F_TRADING_DATE not in bad:
        day = date.fromisoformat(rec[F_TRADING_DATE])
        if strict and day.weekday() >= 5:
            problems.append(f"trading_date {day} is a {_WEEKDAYS[day.weekday()]}, a weekend; "
                            "the scan runs on trading days only")
        if strict and F_FROZEN_AT not in bad:
            et_day = _parse_time(rec[F_FROZEN_AT]).astimezone(_et()).date()
            if et_day != day:
                problems.append(f"frozen_at {rec[F_FROZEN_AT]} is on ET date {et_day}, "
                                f"not on trading_date {day}")
    if F_WARMUP_STATE not in bad and F_WARMUP_REASON not in bad:
        if rec[F_WARMUP_STATE] == NOT_READY and not _has_text(rec[F_WARMUP_REASON]):
            problems.append("warmup_reason must say why the warmup was NOT_READY (spec US9)")
    if F_REFERENCE_STATE not in bad and F_REFERENCE_PROMOTED not in bad:
        if rec[F_REFERENCE_STATE] is None and rec[F_REFERENCE_PROMOTED]:
            problems.append("reference_promoted lists names, but reference_state is null: "
                            "no reference update ran")
        problems += [f"reference_promoted lists {t} twice" for t in _twice(rec[F_REFERENCE_PROMOTED])]
    if F_PICKS not in bad:
        problems += _picks_rules(picks, rec.get(F_EARNINGS_STATUS), F_EARNINGS_STATUS not in bad)
    return problems


def _twice(values: list) -> list:
    seen, twice = set(), []
    for v in values:
        if v in seen and v not in twice:
            twice.append(v)
        seen.add(v)
    return twice


def _picks_rules(picks: list, earnings_status: Any, earnings_ok: bool) -> list[str]:
    problems = []
    if len(picks) > MAX_PICKS:
        problems.append(f"picks has {len(picks)} entries; at most {MAX_PICKS} "
                        "(the spec's four picks)")
    ranks = [p[F_RANK] for p in picks]
    if ranks != list(range(1, len(picks) + 1)):
        problems.append(f"picks must be ranked 1 to {len(picks)} in order, not {ranks}")
    problems += [f"picks lists {t} twice" for t in _twice([p[F_TICKER] for p in picks])]
    for i, p in enumerate(picks):
        if not p[F_TRADED] and p[F_WEIGHT] != 0:
            problems.append(f"picks[{i}] is not traded, so its weight must be 0, not {p[F_WEIGHT]!r}")
    traded = [p[F_WEIGHT] for p in picks if p[F_TRADED]]
    if traded and abs(math.fsum(traded) - 1.0) > WEIGHT_SUM_TOLERANCE:
        problems.append(f"the traded picks' weights sum to {math.fsum(traded)!r}, not 1")
    # Spec US17 and OV's live refusal to arm without earnings data: when the earnings feed
    # is not "available" there is no traded set and no earnings focus.
    if earnings_ok and earnings_status != EARNINGS_AVAILABLE:
        for i, p in enumerate(picks):
            if p[F_TRADED]:
                problems.append(f"picks[{i}] is traded, but earnings_status is {earnings_status!r}: "
                                "no traded set without earnings data")
            if p[F_EARNINGS_LABEL] is not None:
                problems.append(f"picks[{i}] has earnings_label {p[F_EARNINGS_LABEL]!r}, but "
                                f"earnings_status is {earnings_status!r}")
    return problems


def _follow_up_problems(rec: Mapping, *, strict: bool, known_values: bool, time_field: str,
                        state_field: str, states: tuple[str, ...], text_field: str,
                        diff_state: str, fields: tuple[str, ...]) -> list[str]:
    """The settled and parity records share one shape: a date, a time, a state and a text."""
    problems = _unknown_keys(rec, fields, "") if strict else []
    checks = (
        (F_TRADING_DATE, _is_date_text, "a date written YYYY-MM-DD"),
        (time_field, lambda v: _is_time(v, strict=strict), "a UTC time written YYYY-MM-DDTHH:MM:SSZ"),
        (state_field, lambda v: _is_state(v, states, known_values), f"one of {_one_of(states)}"),
        (text_field, lambda v: v is None or _is_text(v), "text or null"),
    )
    bad = set()
    for key, ok, what in checks:
        if key not in rec:
            problems.append(f"{key} is missing")
            bad.add(key)
        elif not ok(rec[key]):
            problems.append(f"{key} must be {what}, not {rec[key]!r}")
            bad.add(key)
    if state_field not in bad and text_field not in bad:
        if rec[state_field] == diff_state and not _has_text(rec[text_field]):
            problems.append(f"{text_field} must say what differs when {state_field} is {diff_state}")
        if rec[state_field] in states and rec[state_field] != diff_state and rec[text_field] is not None:
            problems.append(f"{text_field} must be null when {state_field} is {rec[state_field]}")
    return problems


def _settled_problems(rec: Mapping, *, strict: bool, known_values: bool) -> list[str]:
    return _follow_up_problems(rec, strict=strict, known_values=known_values,
                               time_field=F_CHECKED_AT, state_field=F_STATE,
                               states=SETTLED_STATES, text_field=F_DIFFERENCE,
                               diff_state=SETTLED_DIFF, fields=SETTLED_FIELDS)


def _parity_problems(rec: Mapping, *, strict: bool, known_values: bool) -> list[str]:
    return _follow_up_problems(rec, strict=strict, known_values=known_values,
                               time_field=F_COMPARED_AT, state_field=F_STATUS,
                               states=PARITY_STATUSES, text_field=F_FIRST_DIFFERENCE,
                               diff_state=PARITY_DIFF, fields=PARITY_FIELDS)


def _entry_problems(job: Any, entry: Any, *, strict: bool, known_values: bool) -> list[str]:
    where = f"status entry {job!r}"
    if strict and job not in JOB_STATES:
        return [f"{where}: {job!r} is not a job of the OV picks module ({_one_of(JOBS)})"]
    if not isinstance(entry, Mapping):
        return [f"{where} is not a JSON object"]
    problems = _unknown_keys(entry, STATUS_ENTRY_FIELDS, f"{where}: ") if strict else []
    allowed = JOB_STATES.get(job, ())
    checks = (
        (F_STATE, lambda v: _is_state(v, allowed, known_values and job in JOB_STATES),
         f"one of {_one_of(allowed)}"),
        (F_AT, lambda v: _is_time(v, strict=strict), "a UTC time written YYYY-MM-DDTHH:MM:SSZ"),
        (F_TRADING_DATE, lambda v: v is None or _is_date_text(v), "a YYYY-MM-DD date or null"),
        (F_MESSAGE, _is_text, "text"),
        (F_NEXT_AT, lambda v: v is None or _is_time(v, strict=strict), "a UTC time or null"),
    )
    for key, ok, what in checks:
        if key not in entry:
            # A reader fills the optional fields; a writer writes all of them.
            if strict or key in (F_STATE, F_AT):
                problems.append(f"{where}: {key} is missing")
        elif not ok(entry[key]):
            problems.append(f"{where}: {key} must be {what}, not {entry[key]!r}")
    if strict and not problems and entry[F_STATE] in STATES_NEEDING_MESSAGE \
            and not _has_text(entry[F_MESSAGE]):
        problems.append(f"{where}: state {entry[F_STATE]} needs a message that says why")
    return problems


def validate_picks(record: Any) -> list[str]:
    """Every reason the picks writer would refuse `record`; [] when it would write it."""
    _, problems = _header_problems(record, KIND_PICKS, strict=True)
    if problems or not isinstance(record, Mapping):
        return problems
    return _picks_problems(record, strict=True, known_values=True)


def validate_settled(record: Any) -> list[str]:
    """Every reason the 09:26 settled-record writer would refuse `record`."""
    _, problems = _header_problems(record, KIND_SETTLED, strict=True)
    if problems or not isinstance(record, Mapping):
        return problems
    return _settled_problems(record, strict=True, known_values=True)


def validate_parity(record: Any) -> list[str]:
    """Every reason the 10:35 parity-record writer would refuse `record`."""
    _, problems = _header_problems(record, KIND_PARITY, strict=True)
    if problems or not isinstance(record, Mapping):
        return problems
    return _parity_problems(record, strict=True, known_values=True)


def validate_status(record: Any) -> list[str]:
    """Every reason `record` is not a status file as this version writes it."""
    _, problems = _header_problems(record, KIND_STATUS, strict=True)
    if problems or not isinstance(record, Mapping):
        return problems
    problems = _unknown_keys(record, STATUS_FIELDS, "")
    jobs = record.get(F_JOBS)
    if not isinstance(jobs, Mapping):
        return problems + [f"jobs must be a JSON object, not {jobs!r}"]
    for job, entry in jobs.items():
        problems += _entry_problems(job, entry, strict=True, known_values=True)
    return problems


# ---------------------------------------------------------------- writers

def _dump(record: Mapping) -> str:
    # allow_nan=False: NaN and Infinity are not JSON, and a reader in another language
    # would refuse the file.
    return json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _temp_path(path: Path) -> Path:
    # Same folder, so the final step is a rename on one file system; a dot name, so a
    # reader listing the folder never mistakes it for a record.
    return path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")


def _write_temp(tmp: Path, text: str) -> None:
    with open(tmp, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(folder: Path) -> None:
    # Makes the new name survive a power cut on POSIX. Windows cannot open a folder; skip.
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_once(path: Path, record: Mapping) -> Path:
    """Write `record` to `path` through a temp file, and never over an existing file."""
    text = _dump(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise AlreadyWritten(f"{path} exists; day records are written once")
    tmp = _temp_path(path)
    try:
        _write_temp(tmp, text)
        try:
            # A hard link is a rename that refuses to replace: if another writer got there
            # between the check above and now, this fails instead of overwriting.
            os.link(tmp, path)
        except FileExistsError:
            raise AlreadyWritten(f"{path} exists; day records are written once") from None
        except OSError:
            # No hard links on this file system: rename after a fresh check. The runner is
            # the only writer of day folders (CONTRACT.md), so the gap is not raced.
            if path.exists():
                raise AlreadyWritten(f"{path} exists; day records are written once") from None
            os.rename(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    _fsync_dir(path.parent)
    return path


def _write_replace(path: Path, record: Mapping) -> Path:
    text = _dump(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_path(path)
    try:
        _write_temp(tmp, text)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    _fsync_dir(path.parent)
    return path


def _ordered(record: Mapping, kind: str, fields: tuple[str, ...]) -> dict:
    full = {F_SCHEMA_VERSION: SCHEMA_VERSION, F_KIND: kind, **record}
    return {key: full[key] for key in fields}


def write_picks(out_dir: str | os.PathLike, record: Mapping) -> Path:
    """Freeze a day's picks: write out/<trading_date>/picks.json once, atomically.

    `record` holds every field in PICKS_FIELDS; schema_version and kind may be left out.
    Raises ContractError (nothing written) when it breaks a rule, and AlreadyWritten when
    the day already has picks.
    """
    problems = validate_picks(record)
    if problems:
        raise ContractError("picks record", problems)
    rec = _ordered(record, KIND_PICKS, PICKS_FIELDS)
    rec[F_PICKS] = [{key: p[key] for key in PICK_FIELDS} for p in rec[F_PICKS]]
    return _write_once(day_dir(out_dir, rec[F_TRADING_DATE]) / PICKS_FILE, rec)


def _write_follow_up(out_dir, record, *, what, problems, kind, fields, name) -> Path:
    if problems:
        raise ContractError(what, problems)
    folder = day_dir(out_dir, record[F_TRADING_DATE])
    if not (folder / PICKS_FILE).is_file():
        raise ContractError(what, [f"no frozen picks for {record[F_TRADING_DATE]}: "
                                   f"{PICKS_FILE} comes first"])
    return _write_once(folder / name, _ordered(record, kind, fields))


def write_settled(out_dir: str | os.PathLike, record: Mapping) -> Path:
    """Record the 09:26 settled re-check once, next to the frozen picks it never replaces."""
    return _write_follow_up(out_dir, record, what="settled record", problems=validate_settled(record),
                            kind=KIND_SETTLED, fields=SETTLED_FIELDS, name=SETTLED_FILE)


def write_parity(out_dir: str | os.PathLike, record: Mapping) -> Path:
    """Record the 10:35 comparison with OV's live result once, next to the frozen picks."""
    return _write_follow_up(out_dir, record, what="parity record", problems=validate_parity(record),
                            kind=KIND_PARITY, fields=PARITY_FIELDS, name=PARITY_FILE)


def record_job_state(out_dir: str | os.PathLike, job: str, state: str, *, at: str,
                     message: str = "", trading_date: str | None = None,
                     next_at: str | None = None) -> dict:
    """Replace `job`'s entry in out/status.json and keep every other job's entry.

    `at` and `next_at` are UTC times from utc_timestamp. `trading_date` is the ET trading
    day the entry is about, so a reader can tell today's state from yesterday's. The file
    is rewritten atomically; it assumes one writer process at a time (CONTRACT.md).
    """
    entry = {F_STATE: state, F_AT: at, F_TRADING_DATE: trading_date, F_MESSAGE: message,
             F_NEXT_AT: next_at}
    problems = _entry_problems(job, entry, strict=True, known_values=True)
    if problems:
        raise ContractError(f"status entry for {job!r}", problems)
    path = Path(out_dir) / STATUS_FILE
    with _STATUS_LOCK:
        obj, code, _ = _load_json(path)
        old = {}
        # An unreadable file is replaced: its entries could not be shown anyway, and each
        # job writes its entry again on its next run.
        if not code and isinstance(obj, Mapping) and obj.get(F_KIND) == KIND_STATUS \
                and isinstance(obj.get(F_JOBS), Mapping):
            old = dict(obj[F_JOBS])
        old[job] = entry
        # Keep a newer writer's jobs too (spec US67), after the known ones in schedule order.
        jobs = {j: old[j] for j in JOBS if j in old}
        jobs.update((j, e) for j, e in old.items() if j not in jobs)
        _write_replace(path, {F_SCHEMA_VERSION: SCHEMA_VERSION, F_KIND: KIND_STATUS, F_JOBS: jobs})
    return dict(entry)


# ---------------------------------------------------------------- readers

def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON")


def _load_json(path: Path) -> tuple[Any, str, str]:
    """(parsed, "", "") or (None, MISSING or UNREADABLE, why). Never raises."""
    try:
        size = path.stat().st_size
    except (FileNotFoundError, NotADirectoryError):
        return None, MISSING, "not there"
    except OSError as exc:
        return None, UNREADABLE, f"{type(exc).__name__}: {exc.strerror or exc}"
    if size > MAX_FILE_BYTES:
        return None, UNREADABLE, f"{size} bytes, more than a picks-contract file ever holds"
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text, parse_constant=_reject_constant), "", ""
    except OSError as exc:
        return None, UNREADABLE, f"{type(exc).__name__}: {exc.strerror or exc}"
    except UnicodeDecodeError:
        return None, UNREADABLE, "not UTF-8 text"
    except ValueError as exc:
        return None, UNREADABLE, f"not valid JSON ({exc})"


def _read_record(path: Path, kind: str, checker: Callable[..., list[str]],
                 day: str) -> tuple[dict | None, int | None, str, str]:
    """(record, version, "", "") or (None, version, code, why) for one day record."""
    obj, code, why = _load_json(path)
    if code:
        return None, None, code, why
    version, problems = _header_problems(obj, kind, strict=False)
    if not problems:
        rec = dict(obj)
        for step_from in range(version, SCHEMA_VERSION):
            step = _UPGRADES.get(step_from)
            if step is not None:
                rec = step(rec)
        problems = checker(rec, strict=False, known_values=version <= SCHEMA_VERSION)
        if not problems and rec[F_TRADING_DATE] != day:
            problems = [f"trading_date is {rec[F_TRADING_DATE]}, but the file is in the folder for {day}"]
        if not problems:
            return rec, version, "", ""
    return None, version, INVALID, "; ".join(problems)


def _newer_warning(name: str, version: int) -> str:
    return (f"{name} is schema version {version}; this reader knows version {SCHEMA_VERSION} "
            "and ignores the fields it does not know")


def read_day(out_dir: str | os.PathLike, trading_date: date | str) -> dict:
    """The day's picks as Edge Scanner shows them, with the reason when there are none.

    Never raises on a missing, unreadable or invalid file: has_picks is False and
    reason_code and reason say why. Unknown fields are ignored, and older schema versions
    load. Raises ValueError only for a trading_date that is not a date.
    """
    day = _date_arg(trading_date)
    folder = Path(out_dir) / day
    view = {
        V_HAS_PICKS: False, V_REASON_CODE: "", V_REASON: "", V_WARNINGS: [],
        F_SCHEMA_VERSION: None, F_TRADING_DATE: day, F_FROZEN_AT: None, F_WARMUP_STATE: None,
        F_WARMUP_REASON: None, F_LATE: None, F_SHADOW: None, F_EARNINGS_STATUS: None,
        F_PICKS: [], F_REFERENCE_STATE: None, F_REFERENCE_PROMOTED: [],
        V_SETTLED_STATE: None, V_SETTLED_SAME: False, V_SETTLED_DIFF: False,
        V_SETTLED_DIFFERENCE: None, V_SETTLED_AT: None, V_PARITY_STATUS: None,
        V_PARITY_FIRST_DIFFERENCE: None, V_PARITY_AT: None,
    }
    rec, version, code, why = _read_record(folder / PICKS_FILE, KIND_PICKS, _picks_problems, day)
    name = f"{day}/{PICKS_FILE}"
    if code:
        view[V_REASON_CODE] = code
        view[V_REASON] = {
            MISSING: f"No OV picks for {day}: {name} is not there. The module writes it once, "
                     "at 09:25 ET on trading days.",
            UNREADABLE: f"No OV picks for {day}: {name} could not be read ({why}). "
                        "Check the OV picks module.",
            INVALID: f"No OV picks for {day}: {name} does not match the picks contract ({why}). "
                     "Check the OV picks module.",
        }[code]
        return view
    view[V_HAS_PICKS] = True
    view[F_SCHEMA_VERSION] = version
    if version > SCHEMA_VERSION:
        view[V_WARNINGS].append(_newer_warning(name, version))
    for key in PICKS_FIELDS[2:]:
        view[key] = rec[key]
    view[F_PICKS] = [{key: p[key] for key in PICK_FIELDS} for p in rec[F_PICKS]]
    view[F_REFERENCE_PROMOTED] = list(rec[F_REFERENCE_PROMOTED])

    for file_name, kind, checker, state_key, at_key, text_key, out in (
        (SETTLED_FILE, KIND_SETTLED, _settled_problems, F_STATE, F_CHECKED_AT, F_DIFFERENCE,
         (V_SETTLED_STATE, V_SETTLED_AT, V_SETTLED_DIFFERENCE)),
        (PARITY_FILE, KIND_PARITY, _parity_problems, F_STATUS, F_COMPARED_AT, F_FIRST_DIFFERENCE,
         (V_PARITY_STATUS, V_PARITY_AT, V_PARITY_FIRST_DIFFERENCE)),
    ):
        sub, sub_version, code, why = _read_record(folder / file_name, kind, checker, day)
        if code == MISSING:
            view[out[0]] = PENDING
        elif code:
            # Never show "matches" or "same" from a record that did not read.
            view[out[0]] = UNREADABLE
            view[V_WARNINGS].append(f"{day}/{file_name} could not be used ({why})")
        else:
            view[out[0]], view[out[1]], view[out[2]] = sub[state_key], sub[at_key], sub[text_key]
            if sub_version > SCHEMA_VERSION:
                view[V_WARNINGS].append(_newer_warning(f"{day}/{file_name}", sub_version))
    view[V_SETTLED_SAME] = view[V_SETTLED_STATE] == SETTLED_SAME
    view[V_SETTLED_DIFF] = view[V_SETTLED_STATE] == SETTLED_DIFF
    return view


def read_status(out_dir: str | os.PathLike) -> dict:
    """Each job's last entry from out/status.json. Never raises on a bad file.

    A bad entry is left out with a warning; the other entries still read. Each entry's
    trading_date says which day it is about: compare it with today before showing it.
    """
    view = {V_HAS_STATUS: False, V_REASON_CODE: "", V_REASON: "", V_WARNINGS: [],
            F_SCHEMA_VERSION: None, F_JOBS: {}}
    obj, code, why = _load_json(Path(out_dir) / STATUS_FILE)
    problems: list[str] = []
    if not code:
        version, problems = _header_problems(obj, KIND_STATUS, strict=False)
        if not problems and not isinstance(obj.get(F_JOBS), Mapping):
            problems = [f"jobs must be a JSON object, not {obj.get(F_JOBS)!r}"]
        if problems:
            code, why = INVALID, "; ".join(problems)
    if code:
        view[V_REASON_CODE] = code
        view[V_REASON] = {
            MISSING: f"No {STATUS_FILE} yet: the OV picks module has not recorded a job.",
            UNREADABLE: f"{STATUS_FILE} could not be read ({why}). Check the OV picks module.",
            INVALID: f"{STATUS_FILE} does not match the status contract ({why}). "
                     "Check the OV picks module.",
        }[code]
        return view
    view[V_HAS_STATUS] = True
    view[F_SCHEMA_VERSION] = version
    if version > SCHEMA_VERSION:
        view[V_WARNINGS].append(_newer_warning(STATUS_FILE, version))
    for job, entry in obj[F_JOBS].items():
        found = _entry_problems(job, entry, strict=False, known_values=version <= SCHEMA_VERSION)
        if found:
            view[V_WARNINGS].append(f"{STATUS_FILE}: the {job!r} entry is left out ({'; '.join(found)})")
            continue
        view[F_JOBS][job] = {F_STATE: entry[F_STATE], F_AT: entry[F_AT],
                             F_TRADING_DATE: entry.get(F_TRADING_DATE),
                             F_MESSAGE: entry.get(F_MESSAGE, ""), F_NEXT_AT: entry.get(F_NEXT_AT)}
    return view
