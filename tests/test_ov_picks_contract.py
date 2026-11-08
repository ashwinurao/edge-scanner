"""OV picks file contract (ashwinurao/edge-scanner#9): the writer, the readers and the
sample day folders that the module and Edge Scanner both build on.

Every rule is checked with a record where it must fire and one where it must not.
Nothing here needs the module's pinned libraries, and nothing reaches the network.
"""
import copy
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from ov_picks import contract as c

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "ov_picks" / "samples"
DAY = "2026-11-05"                      # a Thursday, after the 2026-11-01 DST change
FROZEN_AT = "2026-11-05T14:25:00Z"      # 09:25:00 ET (EST, UTC-5)
LABELS = ["Price", "Gap %"]             # generic metric labels; the module chooses its own
TICKERS = ["AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "GGGG"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("a contract test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _entries(pairs):
    return [{"label": label, "value": value} for label, value in pairs]


def _pick(rank, ticker, *, score=80.0, status="PICK", top=None, label=None, metrics=None, details=None):
    return {
        "rank": rank, "ticker": ticker, "score": score, "status": status,
        "top": rank <= c.MAX_PICKS if top is None else top, "earnings_label": label,
        "metrics": _entries(zip(LABELS, (10.5, 2.25))) if metrics is None else metrics,
        "details": (_entries([("Ticker", ticker), ("Price", 10.5), ("Sector", "Tech"), ("Note", None)])
                    if details is None else details),
    }


def _rows(count=len(TICKERS)):
    """A ranked list: the first four are the top picks, the rest ranked below them."""
    return [_pick(i + 1, t, score=91.5 - i, status="PICK" if i < c.MAX_PICKS else "WATCH")
            for i, t in enumerate(TICKERS[:count])]


def _many(count):
    return [_pick(i + 1, f"T{i:03d}") for i in range(count)]


def _record(**over):
    rec = {
        "trading_date": DAY,
        "frozen_at": FROZEN_AT,
        "warmup_state": c.READY,
        "warmup_reason": "",
        "late": False,
        "shadow": False,
        "earnings_status": c.EARNINGS_AVAILABLE,
        "metric_labels": list(LABELS),
        "picks": _rows(),
        "reference_state": c.REF_OK,
        "reference_promoted": [],
    }
    rec.update(over)
    return rec


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


# ---------------------------------------------------------------- schema constants

def test_every_job_has_its_spec_result_states_plus_the_common_ones():
    # Spec "Runner", daily schedule table: the result state of each job. The preview job is
    # the owner's addition (Ash, 2026-11-06: a preview each minute from 09:00 ET).
    spec = {
        c.JOB_NIGHTLY: {"NIGHTLY_OK", "NIGHTLY_WARN"},
        c.JOB_WARMUP: {"WARMING", "DATA_READY", "READY", "NOT_READY"},
        c.JOB_PREVIEW: {"PREVIEWED"},
        c.JOB_SCAN: {"PICKS_FROZEN"},
        c.JOB_SETTLE: {"SETTLED_SAME", "SETTLED_DIFF"},
        c.JOB_COMPARE: {"PARITY_MATCH", "PARITY_DIFF", "PARITY_NO_OV_RUN"},
        c.JOB_ARCHIVE: {"ARCHIVED"},
        c.JOB_REFERENCE: {"REF_OK", "REF_WARN"},
    }
    assert set(c.JOBS) == set(spec) == set(c.JOB_STATES)
    # The status file lists the jobs in schedule order.
    assert c.JOBS == ("nightly", "warmup", "preview", "scan", "settle", "compare", "archive", "reference")
    for job, states in spec.items():
        assert set(c.JOB_STATES[job]) == states | {"RUNNING", "SKIPPED", "REFUSED", "FAILED"}, job
    assert c.DAY_STATES == ("WARMING", "DATA_READY", "READY", "NOT_READY",
                            "PICKS_FROZEN", "SETTLED_SAME", "SETTLED_DIFF")


def test_contract_doc_names_every_field_and_state():
    doc = (ROOT / "ov_picks" / "CONTRACT.md").read_text(encoding="utf-8")
    names = (set(c.PICKS_FIELDS) | set(c.PICK_FIELDS) | set(c.ENTRY_FIELDS) | set(c.SETTLED_FIELDS)
             | set(c.PARITY_FIELDS) | set(c.PREVIEW_FIELDS) | set(c.STATUS_FIELDS) | set(c.STATUS_ENTRY_FIELDS)
             | set(c.HISTORY_FIELDS) | set(c.HISTORY_VIEW_FIELDS) | set(c.HISTORY_SOURCES)
             | set(c.VIEW_FIELDS) | set(c.PREVIEW_VIEW_FIELDS) | set(c.STATUS_VIEW_FIELDS) | set(c.JOBS)
             | {s for states in c.JOB_STATES.values() for s in states}
             | set(c.EARNINGS_STATUSES) | set(c.EARNINGS_LABELS)
             | {c.PENDING, c.UNREADABLE, c.MISSING, c.INVALID}
             | {c.PICKS_FILE, c.SETTLED_FILE, c.PARITY_FILE, c.PREVIEW_FILE, c.STATUS_FILE}
             | {c.KIND_PICKS, c.KIND_SETTLED, c.KIND_PARITY, c.KIND_PREVIEW, c.KIND_HISTORY, c.KIND_STATUS}
             | {"HISTORY_DIR", "HISTORY_SUFFIX", "HISTORY_OV_REBUILD", "HISTORY_OV_LIVE"}
             | {"MAX_PICKS", "MAX_ROWS", "MAX_METRICS", "MAX_DETAILS", "MAX_TEXT_LEN", "MAX_FILE_BYTES"}
             | set(c.METRIC_FIELDS) | set(c.DETAIL_FIELDS) | set(c.LIVE_FIELDS) | set(c.BAND_FIELDS)
             | set(c.TONES) | set(c.LIVE_KINDS) | set(c.OPEN_VS_PM_VALUES) | set(c.LEVEL_FIELDS)
             | {"METRIC_FIELDS", "DETAIL_FIELDS", "LIVE_FIELDS", "BAND_FIELDS", "LEVEL_FIELDS", "MAX_BANDS", "TONES",
                "LIVE_KINDS", "OPEN_VS_PM_VALUES"})
    missing = sorted(n for n in names if f"`{n}`" not in doc)
    assert missing == []


def test_contract_imports_with_only_the_standard_library():
    # -S drops site-packages, so any third-party import fails; -I keeps the
    # environment and the current folder out. Edge Scanner's CI runs Python 3.11.
    code = (
        "import sys, sysconfig; sys.path.insert(0, sys.argv[1]); before = set(sys.modules); "
        "import ov_picks.contract as c; "
        "lib = tuple(sysconfig.get_paths()[k] for k in ('stdlib', 'platstdlib')); "
        "bad = sorted(m for m in set(sys.modules) - before if not m.startswith('ov_picks') "
        "and getattr(sys.modules[m], '__file__', None) "
        "and not sys.modules[m].__file__.startswith(lib)); "
        "print(c.SCHEMA_VERSION, bad)"
    )
    out = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(ROOT)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "1 []"


# ---------------------------------------------------------------- picks writer

def test_written_picks_read_back_unchanged(tmp_path):
    path = c.write_picks(tmp_path, _record(reference_promoted=["FFFF"]))

    assert path == tmp_path / DAY / c.PICKS_FILE
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 1 and on_disk["kind"] == c.KIND_PICKS
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True and view["reason"] == "" and view["reason_code"] == ""
    assert view["warnings"] == []
    for key, value in _record(reference_promoted=["FFFF"]).items():
        assert view[key] == value, key
    assert view["settled_state"] == c.PENDING and view["parity_status"] == c.PENDING
    assert set(view) == set(c.VIEW_FIELDS)


def test_a_second_write_for_the_same_day_is_refused_and_the_first_stays(tmp_path):
    path = c.write_picks(tmp_path, _record())
    first = path.read_bytes()

    with pytest.raises(c.AlreadyWritten):
        c.write_picks(tmp_path, _record(picks=[_pick(1, "EEEE")]))

    assert path.read_bytes() == first
    assert sorted(p.name for p in path.parent.iterdir()) == [c.PICKS_FILE]


def test_a_write_that_fails_part_way_leaves_no_picks_file_and_no_temp_file(tmp_path, monkeypatch):
    real_fsync = c.os.fsync

    def _disk_full(_fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(c.os, "fsync", _disk_full)

    with pytest.raises(OSError):
        c.write_picks(tmp_path, _record())

    day = tmp_path / DAY
    assert list(day.iterdir()) == []
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.MISSING
    monkeypatch.setattr(c.os, "fsync", real_fsync)
    c.write_picks(tmp_path, _record())          # the day can still be written once
    assert c.read_day(tmp_path, DAY)["has_picks"] is True


def test_a_refused_record_writes_nothing(tmp_path):
    with pytest.raises(c.ContractError) as err:
        c.write_picks(tmp_path, _record(warmup_state=c.NOT_READY, warmup_reason=""))
    assert "warmup_reason" in str(err.value)
    assert not (tmp_path / DAY).exists()


@pytest.mark.parametrize("over, problem", [
    # Earnings feed down (spec US17): no earnings labels, on a top pick or below.
    ({"earnings_status": c.EARNINGS_UNAVAILABLE,
      "picks": [_pick(1, "AAAA", label=c.EARNINGS_LABEL_AMC)]}, "earnings_label"),
    ({"earnings_status": c.EARNINGS_MISSING_CONTEXT,
      "picks": [_pick(1, "AAAA"), _pick(2, "BBBB", top=False, label=c.EARNINGS_LABEL_BMO)]}, "earnings_label"),
    ({"earnings_status": "down"}, "earnings_status"),
    # The ranked list: at most 100 rows, in OV's rank order from 1, each ticker once.
    ({"picks": _many(c.MAX_ROWS + 1)}, "at most 100"),
    ({"picks": [_pick(2, "AAAA"), _pick(1, "BBBB")]}, "rank order"),
    ({"picks": [_pick(2, "AAAA"), _pick(3, "BBBB")]}, "rank order"),         # not from rank 1
    ({"picks": [_pick(1, "AAAA"), _pick(1, "BBBB")]}, "rank order"),         # not strictly increasing
    ({"picks": [_pick(True, "AAAA")]}, "rank"),
    ({"picks": [_pick(0, "AAAA")]}, "rank"),
    ({"picks": [_pick(1, "AAAA"), _pick(2, "AAAA")]}, "twice"),
    ({"picks": [_pick(1, "aaaa")]}, "ticker"),
    ({"picks": [_pick(1, " AAAA")]}, "ticker"),
    ({"picks": [_pick(1, "")]}, "ticker"),
    ({"picks": [_pick(1, "AAAA", score=float("inf"))]}, "score"),
    ({"picks": [_pick(1, "AAAA", label="PRIOR-EVENING EARNINGS")]}, "earnings_label"),
    # Status: OV's text, verbatim, never blank.
    ({"picks": [_pick(1, "AAAA", status="")]}, "status"),
    ({"picks": [_pick(1, "AAAA", status=None)]}, "status"),
    ({"picks": [_pick(1, "AAAA", status="X" * (c.MAX_TEXT_LEN + 1))]}, "status"),
    # Top: a real boolean; the top picks come first, at most the spec's four.
    ({"picks": [_pick(1, "AAAA", top=1)]}, "top"),
    ({"picks": [_pick(i + 1, t, top=True) for i, t in enumerate(["A", "B", "C", "D", "E"])]}, "at most 4"),
    ({"picks": [_pick(1, "AAAA", top=False), _pick(2, "BBBB", top=True)]}, "come first"),
    ({"picks": [_pick(1, "AAAA"), _pick(2, "BBBB", top=False), _pick(3, "CCCC", top=True)]}, "come first"),
    # Metrics: every row has metric_labels' labels, in order; at most 20 different labels.
    ({"picks": [_pick(1, "AAAA", metrics=_entries([("Gap %", 2.25), ("Price", 10.5)]))]}, "metric_labels"),
    ({"picks": [_pick(1, "AAAA", metrics=_entries([("Price", 10.5)]))]}, "metric_labels"),
    ({"metric_labels": ["Price", "Price"],
      "picks": [_pick(1, "AAAA", metrics=_entries([("Price", 1.0), ("Price", 2.0)]))]}, "metric_labels"),
    ({"metric_labels": [f"M{i}" for i in range(c.MAX_METRICS + 1)], "picks": []}, "metric_labels"),
    ({"metric_labels": [" "], "picks": []}, "metric_labels"),
    ({"metric_labels": "Price"}, "metric_labels"),
    ({"picks": [_pick(1, "AAAA", metrics="Price")]}, "metrics"),
    ({"picks": [_pick(1, "AAAA", metrics=_entries([("Price", float("nan")), ("Gap %", 1.0)]))]}, "value"),
    ({"picks": [_pick(1, "AAAA", metrics=_entries([("Price", True), ("Gap %", 1.0)]))]}, "value"),
    ({"picks": [_pick(1, "AAAA", metrics=[{"label": "Price", "value": 1.0, "unit": "$"},
                                          {"label": "Gap %", "value": 1.0}])]}, "unit"),
    ({"picks": [_pick(1, "AAAA", metrics=[{"label": "Price"}, {"label": "Gap %", "value": 1.0}])]},
     "value is missing"),
    # Details: at most 200 entries; labels not blank; text values at most 200 characters.
    ({"picks": [_pick(1, "AAAA", details=_entries((f"c{i}", i) for i in range(c.MAX_DETAILS + 1)))]},
     "at most 200"),
    ({"picks": [_pick(1, "AAAA", details=_entries([("Note", "x" * (c.MAX_TEXT_LEN + 1))]))]}, "value"),
    ({"picks": [_pick(1, "AAAA", details=_entries([("Flags", ["a", "b"])]))]}, "value"),
    ({"picks": [_pick(1, "AAAA", details=_entries([("  ", 1)]))]}, "label"),
    ({"picks": [_pick(1, "AAAA", details=_entries([("x" * (c.MAX_TEXT_LEN + 1), 1)]))]}, "label"),
    ({"picks": [_pick(1, "AAAA", details={"Price": 1})]}, "details"),
    ({"picks": [_pick(1, "AAAA", details=["Price"])]}, "details[0]"),
    # The allocation fields are gone (owner, 2026-11-06), and a row needs every field it has.
    ({"picks": [{**_pick(1, "AAAA"), "traded": True}]}, "traded"),
    ({"picks": [{**_pick(1, "AAAA"), "weight": 0.25}]}, "weight"),
    ({"picks": [{k: v for k, v in _pick(1, "AAAA").items() if k != "score"}]}, "score"),
    ({"picks": [{k: v for k, v in _pick(1, "AAAA").items() if k != "details"}]}, "details"),
    # Warmup: picks freeze after the 09:20 deadline, so only READY or NOT_READY,
    # and NOT_READY says why (spec US9, US10).
    ({"warmup_state": c.NOT_READY, "warmup_reason": "  "}, "warmup_reason"),
    ({"warmup_state": c.WARMING}, "warmup_state"),
    ({"warmup_state": c.DATA_READY}, "warmup_state"),
    # Flags are real booleans.
    ({"late": "false"}, "late"),
    ({"shadow": None}, "shadow"),
    # Dates and times: a weekday trading date, a UTC time on that ET date.
    ({"trading_date": "2026-10-31"}, "weekend"),
    ({"trading_date": "20261105"}, "trading_date"),
    ({"trading_date": "2026-11-5"}, "trading_date"),
    ({"frozen_at": "2026-11-05T14:25:00"}, "frozen_at"),
    ({"frozen_at": "2026-11-05T09:25:00-05:00"}, "frozen_at"),
    ({"frozen_at": "2026-11-05T03:00:00Z"}, "ET date"),    # 2026-11-04 22:00 ET
    # Reference update: promotions need a reference run.
    ({"reference_state": None, "reference_promoted": ["FFFF"]}, "reference_promoted"),
    ({"reference_state": "REF_DONE"}, "reference_state"),
    ({"reference_promoted": ["FFFF", "FFFF"]}, "twice"),
    # Only schema version 1 is written, and only known fields.
    ({"schema_version": 2}, "schema_version"),
    ({"kind": "picks"}, "kind"),
    ({"notes": "hello"}, "notes"),
])
def test_the_writer_refuses_a_record_that_breaks_a_rule(tmp_path, over, problem):
    with pytest.raises(c.ContractError) as err:
        c.write_picks(tmp_path, _record(**over))
    assert problem in str(err.value)
    assert not (tmp_path / DAY / c.PICKS_FILE).exists()


def test_the_writer_refuses_a_record_bigger_than_a_reader_takes(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MAX_FILE_BYTES", 2_000)
    with pytest.raises(c.ContractError, match="bytes"):
        c.write_picks(tmp_path, _record())
    assert not (tmp_path / DAY / c.PICKS_FILE).exists()
    # The same record fits once the limit is the real one.
    monkeypatch.undo()
    c.write_picks(tmp_path, _record())
    assert c.read_day(tmp_path, DAY)["has_picks"] is True


def test_a_full_ranked_list_with_full_details_fits_well_inside_the_size_limit(tmp_path):
    # 100 rows, 20 metrics and 200 details each, with 40-character labels and 30-character
    # text values: more than a real day holds, still well under MAX_FILE_BYTES.
    labels = [f"metric label {i:02d}".ljust(40, ".") for i in range(c.MAX_METRICS)]
    detail = _entries((f"detail label {j:03d}".ljust(40, "."), "a thirty-character text value." if j % 2
                       else 1234.56789012345) for j in range(c.MAX_DETAILS))
    rows = [_pick(i + 1, f"T{i:03d}", metrics=_entries((label, 0.5) for label in labels), details=detail)
            for i in range(c.MAX_ROWS)]
    path = c.write_picks(tmp_path, _record(metric_labels=labels, picks=rows))
    assert path.stat().st_size < c.MAX_FILE_BYTES / 2
    assert len(c.read_day(tmp_path, DAY)["picks"]) == c.MAX_ROWS


def test_the_writer_refuses_a_record_with_a_missing_field(tmp_path):
    rec = _record()
    del rec["earnings_status"]
    with pytest.raises(c.ContractError, match="earnings_status"):
        c.write_picks(tmp_path, rec)


@pytest.mark.parametrize("over", [
    # The same rules, where they must NOT fire.
    {"earnings_status": c.EARNINGS_UNAVAILABLE},
    {"earnings_status": c.EARNINGS_MISSING_CONTEXT},
    # An earnings day: labels on top picks and on a row below them.
    {"picks": [_pick(1, "AAAA"), _pick(2, "BBBB", label=c.EARNINGS_LABEL_AMC),
               _pick(3, "CCCC", label=c.EARNINGS_LABEL_BMO), _pick(4, "DDDD"),
               _pick(5, "EEEE", status="WATCH", label=c.EARNINGS_LABEL_BMO)]},
    {"picks": _many(c.MAX_ROWS)},                         # the full 100 rows
    {"picks": [_pick(1, "AAAA"), _pick(3, "BBBB", top=False), _pick(7, "CCCC", top=False)]},  # rank gaps
    {"picks": [_pick(1, "AAAA", top=False), _pick(2, "BBBB", top=False)]},   # no name passed the candidate gates
    {"picks": [_pick(1, "AAAA"), _pick(2, "BBBB")]},      # two top picks and nothing below them
    {"picks": [_pick(1, "BRK.B")]},
    {"picks": []},                                        # a thin day: no name passed OV's gates
    {"picks": [_pick(1, "AAAA", status="X" * c.MAX_TEXT_LEN)]},
    {"picks": [_pick(1, "AAAA", details=[])]},
    {"picks": [_pick(1, "AAAA", details=_entries((f"c{i}", i) for i in range(c.MAX_DETAILS)))]},
    {"picks": [_pick(1, "AAAA", details=_entries([("Note", "x" * c.MAX_TEXT_LEN), ("Empty", None),
                                                  ("Count", 3), ("Flag", "true"), ("Note", "again")]))]},
    {"metric_labels": [], "picks": [_pick(1, "AAAA", metrics=[])]},
    {"metric_labels": [f"M{i}" for i in range(c.MAX_METRICS)],
     "picks": [_pick(1, "AAAA", metrics=_entries((f"M{i}", None if i % 2 else "text") for i in range(c.MAX_METRICS)))]},
    {"warmup_state": c.NOT_READY, "warmup_reason": "Daily bars still loading at 09:20 ET."},
    {"warmup_reason": "Ready at 09:02 ET after one retry."},
    {"late": True, "frozen_at": "2026-11-05T14:31:12Z"},
    {"shadow": True, "reference_state": None},
    {"reference_state": c.REF_WARN, "reference_promoted": ["FFFF"]},
    {"frozen_at": "2026-11-05T14:25:00.250Z"},
    {"frozen_at": "2026-11-06T03:00:00Z"},                # 2026-11-05 22:00 ET: still that ET day
    {"trading_date": "2026-10-30", "frozen_at": "2026-10-30T13:25:00Z"},   # 09:25 EDT, before the change
    {"schema_version": 1, "kind": c.KIND_PICKS},
])
def test_the_writer_accepts_the_same_rules_where_they_must_not_fire(tmp_path, over):
    rec = _record(**over)
    assert c.validate_picks(rec) == []
    c.write_picks(tmp_path, rec)
    view = c.read_day(tmp_path, rec["trading_date"])
    assert view["has_picks"] is True, view["reason"]


def test_utc_timestamp_turns_an_et_time_into_the_written_form():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    assert c.utc_timestamp(datetime(2026, 11, 5, 9, 25, tzinfo=et)) == FROZEN_AT
    assert c.utc_timestamp(datetime(2026, 3, 9, 9, 25, tzinfo=et)) == "2026-03-09T13:25:00Z"
    with pytest.raises(ValueError):
        c.utc_timestamp(datetime(2026, 11, 5, 9, 25))     # naive: which zone?


# ---------------------------------------------------------------- settled and parity records

def test_settled_and_parity_records_join_the_frozen_picks(tmp_path):
    c.write_picks(tmp_path, _record(shadow=True, reference_state=None))
    c.write_settled(tmp_path, {"trading_date": DAY, "checked_at": "2026-11-05T14:26:05Z",
                               "state": c.SETTLED_DIFF,
                               "difference": "Rank 4 would be EEEE, not DDDD."})
    c.write_parity(tmp_path, {"trading_date": DAY, "compared_at": "2026-11-05T15:35:00Z",
                              "status": c.PARITY_DIFF,
                              "first_difference": "Rank 4: module DDDD, OV EEEE."})

    view = c.read_day(tmp_path, DAY)

    assert view["picks"] == _rows()                      # the 09:26 re-check never replaces them
    assert (view["settled_state"], view["settled_same"], view["settled_diff"]) == (c.SETTLED_DIFF, False, True)
    assert view["settled_difference"] == "Rank 4 would be EEEE, not DDDD."
    assert view["settled_at"] == "2026-11-05T14:26:05Z"
    assert view["parity_status"] == c.PARITY_DIFF
    assert view["parity_first_difference"] == "Rank 4: module DDDD, OV EEEE."
    assert view["parity_at"] == "2026-11-05T15:35:00Z"


def test_settled_and_parity_are_written_once_too(tmp_path):
    c.write_picks(tmp_path, _record())
    settled = {"trading_date": DAY, "checked_at": "2026-11-05T14:26:00Z",
               "state": c.SETTLED_SAME, "difference": None}
    parity = {"trading_date": DAY, "compared_at": "2026-11-05T15:35:00Z",
              "status": c.PARITY_MATCH, "first_difference": None}
    c.write_settled(tmp_path, settled)
    c.write_parity(tmp_path, parity)
    with pytest.raises(c.AlreadyWritten):
        c.write_settled(tmp_path, {**settled, "state": c.SETTLED_DIFF, "difference": "x"})
    with pytest.raises(c.AlreadyWritten):
        c.write_parity(tmp_path, {**parity, "status": c.PARITY_NO_OV_RUN})
    view = c.read_day(tmp_path, DAY)
    assert (view["settled_state"], view["parity_status"]) == (c.SETTLED_SAME, c.PARITY_MATCH)


def test_settled_and_parity_need_frozen_picks_first(tmp_path):
    with pytest.raises(c.ContractError, match="picks"):
        c.write_settled(tmp_path, {"trading_date": DAY, "checked_at": "2026-11-05T14:26:00Z",
                                   "state": c.SETTLED_SAME, "difference": None})
    with pytest.raises(c.ContractError, match="picks"):
        c.write_parity(tmp_path, {"trading_date": DAY, "compared_at": "2026-11-05T15:35:00Z",
                                  "status": c.PARITY_NO_OV_RUN, "first_difference": None})


@pytest.mark.parametrize("rec, ok", [
    ({"state": c.SETTLED_SAME, "difference": None}, True),
    ({"state": c.SETTLED_DIFF, "difference": "Rank 4 would be EEEE."}, True),
    ({"state": c.SETTLED_DIFF, "difference": None}, False),    # a difference says what differs
    ({"state": c.SETTLED_SAME, "difference": "x"}, False),
    ({"state": c.READY, "difference": None}, False),
])
def test_settled_record_rules(rec, ok):
    full = {"trading_date": DAY, "checked_at": "2026-11-05T14:26:00Z", **rec}
    assert (c.validate_settled(full) == []) is ok


@pytest.mark.parametrize("rec, ok", [
    ({"status": c.PARITY_MATCH, "first_difference": None}, True),
    ({"status": c.PARITY_NO_OV_RUN, "first_difference": None}, True),
    ({"status": c.PARITY_DIFF, "first_difference": "Rank 4: module DDDD, OV EEEE."}, True),
    ({"status": c.PARITY_DIFF, "first_difference": ""}, False),
    ({"status": c.PARITY_MATCH, "first_difference": "x"}, False),
    ({"status": "MATCH", "first_difference": None}, False),
])
def test_parity_record_rules(rec, ok):
    full = {"trading_date": DAY, "compared_at": "2026-11-05T15:35:00Z", **rec}
    assert (c.validate_parity(full) == []) is ok


# ---------------------------------------------------------------- picks reader

def test_a_missing_day_is_a_clear_no_picks_state(tmp_path):
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is False
    assert view["reason_code"] == c.MISSING
    assert DAY in view["reason"]
    assert view["trading_date"] == DAY and view["picks"] == []
    assert set(view) == set(c.VIEW_FIELDS)
    # An out folder that does not exist, or is a file, reads the same way.
    assert c.read_day(tmp_path / "nope", DAY)["reason_code"] == c.MISSING
    (tmp_path / "file").write_text("x")
    assert c.read_day(tmp_path / "file", DAY)["reason_code"] == c.MISSING


@pytest.mark.parametrize("content", ["", "{", '{"schema_version": 1,', "\x00\x01", "NaN",
                                     '{"schema_version": 1, "score": NaN}'])
def test_a_corrupt_file_is_unreadable_not_an_error(tmp_path, content):
    path = tmp_path / DAY / c.PICKS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    view = c.read_day(tmp_path, DAY)
    assert (view["has_picks"], view["reason_code"]) == (False, c.UNREADABLE)
    assert c.PICKS_FILE in view["reason"]


def test_a_file_that_is_not_utf8_or_is_a_folder_is_unreadable(tmp_path):
    path = tmp_path / DAY / c.PICKS_FILE
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00{")
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.UNREADABLE
    path.unlink()
    path.mkdir()
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.UNREADABLE


def test_an_oversized_file_is_unreadable(tmp_path):
    path = tmp_path / DAY / c.PICKS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(" " * (c.MAX_FILE_BYTES + 1) + "{}", encoding="utf-8")
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.UNREADABLE


@pytest.mark.parametrize("change, problem", [
    (lambda r: [], "object"),
    (lambda r: {**r, "kind": c.KIND_STATUS}, "kind"),
    (lambda r: {**r, "trading_date": "2026-11-04"}, "trading_date"),
    (lambda r: {**r, "schema_version": 0}, "schema_version"),
    (lambda r: {**r, "schema_version": "1"}, "schema_version"),
    (lambda r: {**r, "schema_version": True}, "schema_version"),
    (lambda r: {k: v for k, v in r.items() if k != "schema_version"}, "schema_version"),
    (lambda r: {k: v for k, v in r.items() if k != "picks"}, "picks"),
    (lambda r: {**r, "picks": [{**r["picks"][0], "top": "yes"}] + r["picks"][1:]}, "top"),
    # The reader re-checks the rules between fields too.
    (lambda r: {**r, "picks": [{**r["picks"][0], "top": False}] + r["picks"][1:]}, "come first"),
    (lambda r: {**r, "earnings_status": c.EARNINGS_UNAVAILABLE,
                "picks": [{**r["picks"][0], "earnings_label": c.EARNINGS_LABEL_AMC}] + r["picks"][1:]},
     "earnings_label"),
    (lambda r: {**r, "picks": [{**r["picks"][0], "metrics": r["picks"][0]["metrics"][::-1]}] + r["picks"][1:]},
     "metric_labels"),
    (lambda r: {**r, "picks": r["picks"][::-1]}, "rank order"),
])
def test_a_file_that_breaks_the_schema_is_refused_with_the_reason(tmp_path, change, problem):
    rec = {"schema_version": 1, "kind": c.KIND_PICKS, **_record()}
    _write_json(tmp_path / DAY / c.PICKS_FILE, change(rec))
    view = c.read_day(tmp_path, DAY)
    assert (view["has_picks"], view["reason_code"]) == (False, c.INVALID)
    assert problem in view["reason"]
    assert view["picks"] == []


def test_the_reader_ignores_unknown_fields(tmp_path):
    rec = {"schema_version": 1, "kind": c.KIND_PICKS, **_record(), "future_field": {"a": 1}}
    rec["picks"] = [{**p, "sector": "Tech", "details": [{**e, "unit": "$"} for e in p["details"]]}
                    for p in rec["picks"]]
    _write_json(tmp_path / DAY / c.PICKS_FILE, rec)
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True
    assert "future_field" not in view
    # A row written without the optional fields (name, levels, tone, note, live, group) reads
    # back without them (2026-11-07, "What a value means").
    assert all(set(p) == set(c.PICK_REQUIRED) for p in view["picks"])
    assert all(set(e) == set(c.ENTRY_FIELDS) for p in view["picks"] for e in p["details"] + p["metrics"])
    assert view["picks"] == _rows()


def test_the_reader_accepts_an_older_schema_version(monkeypatch):
    # A later ticket raises SCHEMA_VERSION; the frozen version 1 sample must keep loading.
    monkeypatch.setattr(c, "SCHEMA_VERSION", 2)
    view = c.read_day(SAMPLES / "older_schema", DAY)
    assert view["has_picks"] is True, view["reason"]
    assert view["schema_version"] == 1
    assert [p["ticker"] for p in view["picks"]] == ["AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF"]


def test_the_reader_reads_a_newer_version_and_says_so(tmp_path):
    rec = {"schema_version": 2, "kind": c.KIND_PICKS, **_record(), "warmup_state": "READY_LATE",
           "new_field": True}
    _write_json(tmp_path / DAY / c.PICKS_FILE, rec)
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True
    assert view["warmup_state"] == "READY_LATE"       # a newer writer's state is shown as written
    assert view["schema_version"] == 2
    assert any("version 2" in w for w in view["warnings"])


def test_a_newer_version_still_needs_the_fields_this_reader_knows(tmp_path):
    rec = {"schema_version": 2, "kind": c.KIND_PICKS, **_record()}
    del rec["picks"]
    _write_json(tmp_path / DAY / c.PICKS_FILE, rec)
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.INVALID


def test_an_unreadable_settled_or_parity_record_never_looks_complete(tmp_path):
    c.write_picks(tmp_path, _record())
    (tmp_path / DAY / c.SETTLED_FILE).write_text("{", encoding="utf-8")
    _write_json(tmp_path / DAY / c.PARITY_FILE,
                {"schema_version": 1, "kind": c.KIND_PARITY, "trading_date": DAY,
                 "compared_at": "2026-11-05T15:35:00Z", "status": c.PARITY_DIFF,
                 "first_difference": None})
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True                         # the frozen picks still show
    assert (view["settled_state"], view["settled_same"], view["settled_diff"]) == (c.UNREADABLE, False, False)
    assert view["parity_status"] == c.UNREADABLE
    assert len(view["warnings"]) == 2
    assert c.SETTLED_FILE in view["warnings"][0] and c.PARITY_FILE in view["warnings"][1]


@pytest.mark.parametrize("bad", ["../2026-11-05", "2026-11-05/..", "20261105", "", None, 20261105])
def test_read_day_refuses_a_bad_date_argument(tmp_path, bad):
    with pytest.raises(ValueError):
        c.read_day(tmp_path, bad)


def test_read_day_takes_a_date_object(tmp_path):
    from datetime import date
    c.write_picks(tmp_path, _record())
    assert c.read_day(tmp_path, date(2026, 11, 5))["has_picks"] is True


# ---------------------------------------------------------------- preview file
# The owner's every-minute previews (Ash, 2026-11-06): the ranked list as of each minute
# from 09:00 ET, replaced each minute, until the picks freeze at 09:25 ET.

AS_OF = "2026-11-05T14:14:00Z"          # 09:14:00 ET (EST, UTC-5): bars through 09:13


def _preview(**over):
    rec = {
        "trading_date": DAY,
        "as_of": AS_OF,
        "previewed_at": "2026-11-05T14:14:21Z",
        "shadow": True,
        "earnings_status": c.EARNINGS_AVAILABLE,
        "metric_labels": list(LABELS),
        "picks": _rows(),
    }
    rec.update(over)
    return rec


def test_a_written_preview_reads_back_with_the_picks_files_rows(tmp_path):
    path = c.write_preview(tmp_path, _preview())

    assert path == tmp_path / DAY / c.PREVIEW_FILE
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert list(on_disk) == list(c.PREVIEW_FIELDS)
    assert on_disk["kind"] == c.KIND_PREVIEW
    view = c.read_preview(tmp_path, DAY)
    assert list(view) == list(c.PREVIEW_VIEW_FIELDS)
    assert view["has_preview"] is True and view["reason_code"] == "" and view["warnings"] == []
    assert (view["as_of"], view["previewed_at"], view["shadow"]) == (AS_OF, "2026-11-05T14:14:21Z", True)
    # The same rows and metrics as a picks file (owner, 2026-11-06).
    assert view["picks"] == _rows() and view["metric_labels"] == LABELS
    # A preview is not the day's picks.
    assert c.read_day(tmp_path, DAY)["has_picks"] is False


def test_each_preview_replaces_the_last_one(tmp_path):
    c.write_preview(tmp_path, _preview())
    c.write_preview(tmp_path, _preview(as_of="2026-11-05T14:15:00Z", previewed_at="2026-11-05T14:15:19Z",
                                       picks=_rows(3)))

    view = c.read_preview(tmp_path, DAY)
    assert view["as_of"] == "2026-11-05T14:15:00Z"
    assert [p["ticker"] for p in view["picks"]] == TICKERS[:3]
    assert sorted(p.name for p in (tmp_path / DAY).iterdir()) == [c.PREVIEW_FILE]


def test_no_preview_is_written_once_the_picks_are_frozen(tmp_path):
    c.write_preview(tmp_path, _preview())
    c.write_picks(tmp_path, _record())

    with pytest.raises(c.ContractError, match="frozen"):
        c.write_preview(tmp_path, _preview(as_of="2026-11-05T14:24:00Z", previewed_at="2026-11-05T14:24:30Z"))
    # The last preview before the freeze stays as it was.
    assert c.read_preview(tmp_path, DAY)["as_of"] == AS_OF


@pytest.mark.parametrize("over, problem", [
    ({"as_of": "2026-11-05T14:25:00Z", "previewed_at": "2026-11-05T14:25:10Z"}, "when the picks freeze"),
    ({"as_of": "2026-11-05T15:00:00Z", "previewed_at": "2026-11-05T15:00:10Z"}, "when the picks freeze"),
    ({"as_of": "2026-11-05T14:14:30Z"}, "whole minute"),
    ({"as_of": "2026-11-06T14:14:00Z", "previewed_at": "2026-11-06T14:14:21Z"}, "not on trading_date"),
    ({"previewed_at": "2026-11-05T14:13:59Z"}, "before as_of"),
    ({"trading_date": "2026-11-07", "as_of": "2026-11-07T14:14:00Z",
      "previewed_at": "2026-11-07T14:14:21Z"}, "weekend"),
    ({"as_of": "2026-11-05 14:14:00"}, "as_of must be"),
    ({"shadow": "yes"}, "shadow must be"),
    ({"earnings_status": "maybe"}, "earnings_status must be"),
    ({"picks": [_pick(1, "AAAA", top=False), _pick(2, "BBBB", top=True)]}, "come first"),
    ({"picks": [_pick(1, "AAAA"), _pick(1, "BBBB")]}, "rank order"),
    ({"earnings_status": c.EARNINGS_UNAVAILABLE, "picks": [_pick(1, "AAAA", label="AMC")]}, "earnings_label"),
    ({"metric_labels": ["Price"]}, "metric_labels"),
    ({"frozen_at": FROZEN_AT}, "not a field"),
])
def test_the_preview_writer_refuses_a_record_that_breaks_a_rule(tmp_path, over, problem):
    with pytest.raises(c.ContractError, match=problem):
        c.write_preview(tmp_path, _preview(**over))
    assert not (tmp_path / DAY).exists() or not (tmp_path / DAY / c.PREVIEW_FILE).exists()


@pytest.mark.parametrize("over", [
    {"as_of": "2026-11-05T14:00:00Z", "previewed_at": "2026-11-05T14:00:00Z"},    # 09:00 ET, written at once
    {"as_of": "2026-11-05T14:24:00Z", "previewed_at": "2026-11-05T14:25:40Z"},    # the last minute, written late
    {"picks": []},
    {"earnings_status": c.EARNINGS_UNAVAILABLE, "picks": _rows()},
])
def test_the_preview_writer_accepts_the_same_rules_where_they_must_not_fire(tmp_path, over):
    assert c.validate_preview(_preview(**over)) == []
    c.write_preview(tmp_path, _preview(**over))


def test_a_missing_or_bad_preview_reads_as_no_preview(tmp_path):
    view = c.read_preview(tmp_path, DAY)
    assert (view["has_preview"], view["reason_code"]) == (False, c.MISSING)
    assert list(view) == list(c.PREVIEW_VIEW_FIELDS) and view["picks"] == []

    _write_json(tmp_path / DAY / c.PREVIEW_FILE, {"schema_version": 1, "kind": c.KIND_PICKS})
    view = c.read_preview(tmp_path, DAY)
    assert (view["has_preview"], view["reason_code"]) == (False, c.INVALID)

    (tmp_path / DAY / c.PREVIEW_FILE).write_text("{not json", encoding="utf-8")
    view = c.read_preview(tmp_path, DAY)
    assert (view["has_preview"], view["reason_code"]) == (False, c.UNREADABLE)
    assert "Check the OV picks module" in view["reason"]


def test_the_preview_reader_ignores_unknown_fields_and_reads_a_newer_version(tmp_path):
    rec = {"schema_version": 2, "kind": c.KIND_PREVIEW, **_preview(), "new_field": 1}
    _write_json(tmp_path / DAY / c.PREVIEW_FILE, rec)

    view = c.read_preview(tmp_path, DAY)
    assert view["has_preview"] is True and "new_field" not in view
    assert len(view["warnings"]) == 1 and "schema version 2" in view["warnings"][0]


def test_a_preview_in_another_days_folder_is_refused_on_read(tmp_path):
    _write_json(tmp_path / "2026-11-06" / c.PREVIEW_FILE, {"schema_version": 1, "kind": c.KIND_PREVIEW, **_preview()})
    view = c.read_preview(tmp_path, "2026-11-06")
    assert (view["has_preview"], view["reason_code"]) == (False, c.INVALID)
    assert "folder for 2026-11-06" in view["reason"]


def test_the_preview_job_has_a_status_entry_like_any_job(tmp_path):
    c.record_job_state(tmp_path, c.JOB_SCAN, c.PICKS_FROZEN, at="2026-11-04T14:25:00Z", trading_date="2026-11-04")
    c.record_job_state(tmp_path, c.JOB_PREVIEW, c.PREVIEWED, at="2026-11-05T14:14:21Z", trading_date=DAY,
                       message="Preview as of 09:14 ET.", next_at="2026-11-05T14:15:00Z")
    jobs = c.read_status(tmp_path)["jobs"]
    assert list(jobs) == [c.JOB_PREVIEW, c.JOB_SCAN]
    assert jobs[c.JOB_PREVIEW]["state"] == c.PREVIEWED


# ---------------------------------------------------------------- history file
# The owner's calendar of past days (Ash, project thread, 2026-11-07): OV's own ranked list
# for the days before the module ran, exported by the module into out/history/.

EXPORTED_AT = "2026-11-07T15:00:00Z"    # a Saturday, after the day


def _history(**over):
    rec = {
        "trading_date": DAY,
        "source": c.HISTORY_OV_REBUILD,
        "exported_at": EXPORTED_AT,
        "earnings_status": c.EARNINGS_AVAILABLE,
        "metric_labels": list(LABELS),
        "picks": _rows(),
    }
    rec.update(over)
    return rec


def test_a_written_history_day_reads_back_with_the_picks_files_rows(tmp_path):
    path = c.write_history(tmp_path, _history(source=c.HISTORY_OV_LIVE))

    assert path == tmp_path / "history" / f"{DAY}.json" == c.history_path(tmp_path, DAY)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert list(on_disk) == list(c.HISTORY_FIELDS) and on_disk["kind"] == c.KIND_HISTORY
    view = c.read_history(tmp_path, DAY)
    assert set(view) == set(c.HISTORY_VIEW_FIELDS)
    assert (view["has_history"], view["reason_code"], view["warnings"]) == (True, "", [])
    assert (view["source"], view["exported_at"]) == (c.HISTORY_OV_LIVE, EXPORTED_AT)
    assert view["picks"] == _rows() and view["metric_labels"] == LABELS
    # A history day is not the module's picks, and it sits outside the day folders.
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.MISSING
    assert not c.day_dir(tmp_path, DAY).exists()
    assert sorted(p.name for p in path.parent.iterdir()) == [f"{DAY}.json"]   # no temp left


def test_a_new_export_replaces_the_days_history_file(tmp_path):
    c.write_history(tmp_path, _history())
    c.write_history(tmp_path, _history(picks=_rows(5), exported_at="2026-11-08T15:00:00Z"))
    view = c.read_history(tmp_path, DAY)
    assert len(view["picks"]) == 5 and view["exported_at"] == "2026-11-08T15:00:00Z"


def test_no_history_is_written_for_a_day_the_module_froze_its_own_picks(tmp_path):
    c.write_picks(tmp_path, _record())
    with pytest.raises(c.ContractError, match="froze its own picks"):
        c.write_history(tmp_path, _history())
    assert not (tmp_path / c.HISTORY_DIR).exists()


@pytest.mark.parametrize("over, problem", [
    ({"source": "rebuild_current_2026"}, "source must be one of"),
    ({"source": None}, "source must be one of"),
    ({"exported_at": "2026-11-05 15:00"}, "exported_at must be"),
    ({"exported_at": "2026-11-05T23:00:00Z"}, "not after trading_date"),      # 18:00 ET the same day
    ({"trading_date": "2026-11-07", "exported_at": "2026-11-09T15:00:00Z"}, "weekend"),
    ({"earnings_status": "partial"}, "earnings_status must be"),
    ({"picks": _rows()[::-1]}, "rank order"),
    ({"picks": [_pick(1, "AAAA", top=False), _pick(2, "BBBB", top=True)]}, "must come first"),
    ({"picks": _many(c.MAX_ROWS + 1)}, f"at most {c.MAX_ROWS}"),
    ({"earnings_status": c.EARNINGS_UNAVAILABLE, "picks": [_pick(1, "AAAA", label="AMC")]}, "earnings_label"),
    ({"metric_labels": ["Other"]}, "metrics labels must be"),
    ({"frozen_at": FROZEN_AT}, "frozen_at"),
])
def test_the_history_writer_refuses_a_record_that_breaks_a_rule(tmp_path, over, problem):
    with pytest.raises(c.ContractError) as err:
        c.write_history(tmp_path, _history(**over))
    assert any(problem in p for p in err.value.problems), err.value.problems
    assert not (tmp_path / c.HISTORY_DIR).exists()


@pytest.mark.parametrize("over", [
    {"exported_at": "2026-11-06T05:00:00Z"},     # 00:00 ET the next day
    {"picks": []},                               # no name passed OV's gates that day
    {"picks": _rows(3)},                         # fewer than four top picks
    {"earnings_status": c.EARNINGS_UNAVAILABLE},
])
def test_the_history_writer_accepts_the_same_rules_where_they_must_not_fire(tmp_path, over):
    assert c.validate_history(_history(**over)) == []
    c.write_history(tmp_path, _history(**over))


def test_a_missing_or_bad_history_day_reads_as_no_history(tmp_path):
    view = c.read_history(tmp_path, DAY)
    assert (view["has_history"], view["reason_code"]) == (False, c.MISSING)
    assert "history/2026-11-05.json" in view["reason"]
    _write_json(c.history_path(tmp_path, DAY), {"schema_version": 1, "kind": c.KIND_PREVIEW, **_history()})
    view = c.read_history(tmp_path, DAY)
    assert (view["has_history"], view["reason_code"]) == (False, c.INVALID)
    c.history_path(tmp_path, DAY).write_text("{", encoding="utf-8")
    view = c.read_history(tmp_path, DAY)
    assert (view["has_history"], view["reason_code"]) == (False, c.UNREADABLE)
    assert "Check the OV picks module" in view["reason"]


def test_a_history_file_named_for_another_day_is_refused_on_read(tmp_path):
    _write_json(c.history_path(tmp_path, "2026-11-04"), {"schema_version": 1, "kind": c.KIND_HISTORY, **_history()})
    view = c.read_history(tmp_path, "2026-11-04")
    assert (view["has_history"], view["reason_code"]) == (False, c.INVALID)
    assert "named for 2026-11-04" in view["reason"]


def test_the_history_reader_ignores_unknown_fields_and_reads_a_newer_version(tmp_path):
    rec = {"schema_version": 2, "kind": c.KIND_HISTORY, **_history(), "new_field": 1}
    _write_json(c.history_path(tmp_path, DAY), rec)
    view = c.read_history(tmp_path, DAY)
    assert view["has_history"] is True and "new_field" not in view
    assert len(view["warnings"]) == 1 and "schema version 2" in view["warnings"][0]


def test_history_days_lists_the_exported_days_from_their_names(tmp_path):
    assert c.history_days(tmp_path) == []
    for day in ("2026-11-04", "2026-01-02", "2026-11-03"):
        c.write_history(tmp_path, _history(trading_date=day))
    folder = tmp_path / c.HISTORY_DIR
    for name in ("README.md", "2026-13-01.json", ".2026-11-02.json.123.tmp", "2026-11-02.json.tmp"):
        (folder / name).write_text("x", encoding="utf-8")
    assert c.history_days(tmp_path) == ["2026-01-02", "2026-11-03", "2026-11-04"]


# ---------------------------------------------------------------- status file

def test_each_job_keeps_its_own_last_entry(tmp_path):
    c.record_job_state(tmp_path, c.JOB_NIGHTLY, c.NIGHTLY_OK, at="2026-11-05T08:30:04Z",
                       trading_date=DAY)
    c.record_job_state(tmp_path, c.JOB_WARMUP, c.WARMING, at="2026-11-05T13:45:00Z",
                       trading_date=DAY, message="Loading daily bars.")
    c.record_job_state(tmp_path, c.JOB_WARMUP, c.DATA_READY, at="2026-11-05T14:02:00Z",
                       trading_date=DAY)
    c.record_job_state(tmp_path, c.JOB_SCAN, c.SKIPPED, at="2025-11-27T14:25:00Z",
                       trading_date="2025-11-27", message="Market closed (Thanksgiving).",
                       next_at="2025-11-28T14:25:00Z")

    status = c.read_status(tmp_path)

    assert status["has_status"] is True and status["warnings"] == []
    assert set(status["jobs"]) == {c.JOB_NIGHTLY, c.JOB_WARMUP, c.JOB_SCAN}
    assert status["jobs"][c.JOB_WARMUP] == {"state": c.DATA_READY, "at": "2026-11-05T14:02:00Z",
                                           "trading_date": DAY, "message": "", "next_at": None}
    assert status["jobs"][c.JOB_SCAN]["next_at"] == "2025-11-28T14:25:00Z"
    assert set(status) == set(c.STATUS_VIEW_FIELDS)
    assert not [p for p in tmp_path.iterdir() if p.name != c.STATUS_FILE]   # no temp left


@pytest.mark.parametrize("job, state, message, ok", [
    (c.JOB_SCAN, c.PICKS_FROZEN, "", True),
    (c.JOB_SCAN, c.WARMING, "", False),                  # a state of another job
    (c.JOB_SCAN, "DONE", "", False),
    ("backtest", c.RUNNING, "", False),                  # not a job of the module
    (c.JOB_WARMUP, c.READY, "", True),
    (c.JOB_WARMUP, c.NOT_READY, "", False),              # a warning state says why
    (c.JOB_WARMUP, c.NOT_READY, "Daily bars still loading at 09:20 ET.", True),
    (c.JOB_COMPARE, c.REFUSED, "", False),
    (c.JOB_COMPARE, c.REFUSED, "Outside 10:30-23:30 ET on a weekday.", True),
    (c.JOB_REFERENCE, c.REF_WARN, "SEC fetch failed; kept the data from 2026-10-31.", True),
    (c.JOB_ARCHIVE, c.FAILED, "", False),
])
def test_status_entry_rules(tmp_path, job, state, message, ok):
    call = lambda: c.record_job_state(tmp_path, job, state, at="2026-11-05T14:25:00Z",  # noqa: E731
                                      trading_date=DAY, message=message)
    if ok:
        call()
        assert c.read_status(tmp_path)["jobs"][job]["state"] == state
    else:
        with pytest.raises(c.ContractError):
            call()
        assert not (tmp_path / c.STATUS_FILE).exists()


@pytest.mark.parametrize("kw", [
    {"at": "2026-11-05 14:25"}, {"at": None}, {"trading_date": "2026-11-5"},
    {"next_at": "tomorrow"}, {"message": None},
])
def test_status_entry_times_and_dates_are_checked(tmp_path, kw):
    args = {"at": "2026-11-05T14:25:00Z", "trading_date": DAY, "message": "", **kw}
    with pytest.raises(c.ContractError):
        c.record_job_state(tmp_path, c.JOB_SCAN, c.PICKS_FROZEN, **args)


def test_a_missing_or_corrupt_status_file_reads_as_no_status(tmp_path):
    view = c.read_status(tmp_path)
    assert (view["has_status"], view["reason_code"], view["jobs"]) == (False, c.MISSING, {})
    (tmp_path / c.STATUS_FILE).write_text("{]", encoding="utf-8")
    view = c.read_status(tmp_path)
    assert (view["has_status"], view["reason_code"]) == (False, c.UNREADABLE)
    _write_json(tmp_path / c.STATUS_FILE, {"schema_version": 1, "kind": c.KIND_PICKS, "jobs": {}})
    assert c.read_status(tmp_path)["reason_code"] == c.INVALID


def test_a_bad_status_entry_is_dropped_with_a_warning_and_the_rest_still_read(tmp_path):
    _write_json(tmp_path / c.STATUS_FILE, {"schema_version": 1, "kind": c.KIND_STATUS, "jobs": {
        c.JOB_NIGHTLY: {"state": c.NIGHTLY_OK, "at": "2026-11-05T08:30:00Z", "trading_date": DAY,
                        "message": "", "next_at": None, "extra": 1},
        c.JOB_SCAN: {"state": "HALF_DONE", "at": "2026-11-05T14:25:00Z", "trading_date": DAY,
                     "message": "", "next_at": None},
    }})
    view = c.read_status(tmp_path)
    assert view["has_status"] is True
    assert set(view["jobs"]) == {c.JOB_NIGHTLY}
    assert set(view["jobs"][c.JOB_NIGHTLY]) == set(c.STATUS_ENTRY_FIELDS)
    assert len(view["warnings"]) == 1 and c.JOB_SCAN in view["warnings"][0]


def test_a_newer_writers_job_is_kept_on_read_and_on_rewrite(tmp_path):
    _write_json(tmp_path / c.STATUS_FILE, {"schema_version": 2, "kind": c.KIND_STATUS, "jobs": {
        "listing": {"state": "LISTING_OK", "at": "2026-10-31T14:30:00Z", "trading_date": None,
                    "message": "", "next_at": None},
    }})
    c.record_job_state(tmp_path, c.JOB_NIGHTLY, c.NIGHTLY_OK, at="2026-11-05T08:30:00Z",
                       trading_date=DAY)
    view = c.read_status(tmp_path)
    assert set(view["jobs"]) == {"listing", c.JOB_NIGHTLY}
    assert view["jobs"]["listing"]["state"] == "LISTING_OK"


def test_a_corrupt_status_file_is_replaced_by_the_next_entry(tmp_path):
    (tmp_path / c.STATUS_FILE).write_text("not json", encoding="utf-8")
    c.record_job_state(tmp_path, c.JOB_NIGHTLY, c.NIGHTLY_OK, at="2026-11-05T08:30:00Z",
                       trading_date=DAY)
    assert set(c.read_status(tmp_path)["jobs"]) == {c.JOB_NIGHTLY}


# ---------------------------------------------------------------- sample day folders

TOP = ["AAAA", "BBBB", "CCCC", "DDDD"]
SAMPLE_CASES = {
    # case: (has picks, shadow, late, warmup, earnings, top tickers, rows, settled, parity)
    "frozen": (True, False, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 10, c.PENDING, c.PENDING),
    "shadow": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.SETTLED_SAME, c.PENDING),
    "late": (True, False, True, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.PENDING, c.PENDING),
    "not_ready": (True, False, False, c.NOT_READY, c.EARNINGS_AVAILABLE, TOP, 6, c.PENDING, c.PENDING),
    "earnings_unavailable": (True, False, False, c.READY, c.EARNINGS_UNAVAILABLE, TOP, 6, c.PENDING, c.PENDING),
    "parity_match": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 8, c.SETTLED_SAME, c.PARITY_MATCH),
    "parity_diff": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.SETTLED_SAME, c.PARITY_DIFF),
    "no_ov_run": (True, False, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.SETTLED_SAME, c.PARITY_NO_OV_RUN),
    "settled_diff": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.SETTLED_DIFF, c.PENDING),
    "older_schema": (True, False, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.PENDING, c.PENDING),
    "warming": (False, None, None, None, None, [], 0, None, None),
    "preview": (False, None, None, None, None, [], 0, None, None),
    # The shadow sample's day, plus OV's past days in history/ (owner, 2026-11-07).
    "past_days": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 6, c.SETTLED_SAME, c.PENDING),
    # The owner's layout (2026-11-07): tones, notes, live columns, groups and levels, plus one past day.
    "meanings": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 8, c.SETTLED_SAME, c.PENDING),
    # The owner's layout "C1" (2026-11-08): the meanings day with made-up score parts and rank reasons.
    "score_parts": (True, True, False, c.READY, c.EARNINGS_AVAILABLE, TOP, 8, c.SETTLED_SAME, c.PENDING),
}
# The warmup state of each sample without picks: still warming, or ready with a preview showing.
NO_PICKS_WARMUP = {"warming": c.WARMING, "preview": c.DATA_READY}


def test_the_sample_folders_are_the_documented_set():
    assert sorted(p.name for p in SAMPLES.iterdir() if p.is_dir()) == sorted(SAMPLE_CASES)
    doc = (ROOT / "ov_picks" / "CONTRACT.md").read_text(encoding="utf-8")
    assert [case for case in SAMPLE_CASES if f"`{case}`" not in doc] == []


@pytest.mark.parametrize("case", sorted(SAMPLE_CASES))
def test_every_sample_passes_the_writers_own_check(case):
    folder = SAMPLES / case
    history = folder / c.HISTORY_DIR
    for path in sorted(folder.rglob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if path.parent == history:
            check = c.validate_history
        else:
            check = {c.PICKS_FILE: c.validate_picks, c.SETTLED_FILE: c.validate_settled,
                     c.PARITY_FILE: c.validate_parity, c.PREVIEW_FILE: c.validate_preview,
                     c.STATUS_FILE: c.validate_status}[path.name]
        assert check(rec) == [], path
    for path in folder.rglob("*"):
        if path.parent == history:
            assert path.name[:-len(c.HISTORY_SUFFIX)] in c.history_days(folder), path
            continue
        assert path.is_dir() or path.name in (c.PICKS_FILE, c.SETTLED_FILE, c.PARITY_FILE,
                                              c.PREVIEW_FILE, c.STATUS_FILE), path
        assert path.is_dir() or path.parent.name in (case, DAY), path


@pytest.mark.parametrize("case", sorted(SAMPLE_CASES))
def test_the_reader_handles_every_sample(case):
    has, shadow, late, warmup, earnings, top, rows, settled, parity = SAMPLE_CASES[case]

    view = c.read_day(SAMPLES / case, DAY)

    assert view["has_picks"] is has, view["reason"]
    assert view["warnings"] == []
    if not has:
        assert view["reason_code"] == c.MISSING
        return
    assert (view["shadow"], view["late"], view["warmup_state"]) == (shadow, late, warmup)
    assert view["earnings_status"] == earnings
    assert [p["ticker"] for p in view["picks"] if p["top"]] == top
    assert [p["rank"] for p in view["picks"]] == list(range(1, rows + 1))
    assert all([m["label"] for m in p["metrics"]] == view["metric_labels"] for p in view["picks"])
    assert all(p["details"] for p in view["picks"])
    assert (view["settled_state"], view["parity_status"]) == (settled, parity)
    if warmup == c.NOT_READY:
        assert view["warmup_reason"]
    if parity == c.PARITY_DIFF:
        assert view["parity_first_difference"]
    if settled == c.SETTLED_DIFF:
        assert view["settled_diff"] is True and view["settled_difference"]


@pytest.mark.parametrize("case", sorted(set(SAMPLE_CASES) - {"older_schema"}))
def test_every_sample_has_a_status_file_for_the_same_day(case):
    status = c.read_status(SAMPLES / case)
    assert status["has_status"] is True and status["warnings"] == []
    nightly = status["jobs"][c.JOB_NIGHTLY]
    assert (nightly["state"], nightly["trading_date"]) == (c.NIGHTLY_OK, DAY)
    expected_warmup = NO_PICKS_WARMUP.get(case, SAMPLE_CASES[case][3])
    assert status["jobs"][c.JOB_WARMUP]["state"] == expected_warmup
    if case not in NO_PICKS_WARMUP:
        assert status["jobs"][c.JOB_SCAN]["state"] == c.PICKS_FROZEN
        assert status["jobs"][c.JOB_SCAN]["trading_date"] == DAY


def test_the_samples_show_a_ranked_list_and_an_earnings_day():
    # frozen: the top four, then six names ranked below them.
    view = c.read_day(SAMPLES / "frozen", DAY)
    assert [(p["ticker"], p["top"]) for p in view["picks"]][3:6] == [("DDDD", True), ("EEEE", False), ("FFFF", False)]
    assert len(view["metric_labels"]) <= c.MAX_METRICS
    assert view["reference_promoted"] == ["FFFF"]
    # parity_match: an earnings day, with labels on two top picks and on a row below them.
    view = c.read_day(SAMPLES / "parity_match", DAY)
    assert [p["earnings_label"] for p in view["picks"]] == [
        None, c.EARNINGS_LABEL_AMC, c.EARNINGS_LABEL_BMO, None, None, None, c.EARNINGS_LABEL_BMO, None]
    # earnings_unavailable: no labels at all.
    view = c.read_day(SAMPLES / "earnings_unavailable", DAY)
    assert [p["earnings_label"] for p in view["picks"]] == [None] * 6


def test_the_preview_sample_shows_the_ranked_list_before_the_freeze():
    view = c.read_preview(SAMPLES / "preview", DAY)
    assert view["has_preview"] is True and view["warnings"] == []
    assert view["as_of"] == AS_OF
    assert [p["ticker"] for p in view["picks"] if p["top"]] == TOP
    assert [p["rank"] for p in view["picks"]] == list(range(1, 7))
    entry = c.read_status(SAMPLES / "preview")["jobs"][c.JOB_PREVIEW]
    assert (entry["state"], entry["trading_date"]) == (c.PREVIEWED, DAY)
    # No other sample has a preview: each is after the freeze, or before 09:00 ET.
    for case in set(SAMPLE_CASES) - {"preview"}:
        assert c.read_preview(SAMPLES / case, DAY)["reason_code"] == c.MISSING, case


def test_the_past_days_sample_shows_ovs_past_days_beside_the_modules_own_day():
    folder = SAMPLES / "past_days"
    assert c.history_days(folder) == ["2026-11-03", "2026-11-04"]
    for day, source in (("2026-11-03", c.HISTORY_OV_REBUILD), ("2026-11-04", c.HISTORY_OV_LIVE)):
        view = c.read_history(folder, day)
        assert view["has_history"] is True and view["warnings"] == [], view["reason"]
        assert view["source"] == source
        assert [p["ticker"] for p in view["picks"] if p["top"]] == TOP
        assert [p["rank"] for p in view["picks"]] == list(range(1, 7))
    # The module's own day stays its own, and has no history file.
    assert c.read_day(folder, DAY)["has_picks"] is True
    assert c.read_history(folder, DAY)["reason_code"] == c.MISSING
    # No other sample has history.
    for case in set(SAMPLE_CASES) - {"past_days", "meanings"}:
        assert c.history_days(SAMPLES / case) == [], case


# ---------------------------------------------------------------- what a value means (2026-11-07)
# The owner's layout "C1 + E1" (Ash, project thread, 2026-11-07): tones, notes, live columns,
# detail groups and price levels, all optional.

FIRST_MINUTE = {"kind": c.LIVE_FIRST_MINUTE_RANGE_ATR, "bands": [
    {"max": 0.2, "tone": c.TONE_GOOD, "note": "Made-up note: tight."},
    {"min": 0.6, "tone": c.TONE_BAD, "note": "Made-up note: wide."},
    {"note": "Made-up note: in between."}]}
OPEN_VS_PM = {"kind": c.LIVE_OPEN_VS_PM_RANGE, "bands": [
    {"is": c.OPEN_ABOVE, "note": "Made-up note: above."}, {"is": c.OPEN_INSIDE}]}


def _meaningful_pick(rank, ticker):
    pick = _pick(rank, ticker, metrics=[
        {"label": "Price", "value": 10.5, "tone": c.TONE_CAUTION, "note": "Made-up note."},
        {"label": "Gap %", "value": None, "live": copy.deepcopy(FIRST_MINUTE)},
    ], details=[
        {"label": "Ticker", "value": ticker, "group": "At a glance"},
        {"label": "Sector", "value": "Tech", "group": "Company"},
        {"label": "Note", "value": None},
    ])
    pick.update(name=f"{ticker} Inc", levels={"atr": 1.25, "pm_high": 11.0, "pm_low": 9.75, "last": None})
    return pick


def _meaningful_record(**over):
    rows = [_meaningful_pick(i + 1, t) for i, t in enumerate(TICKERS)]
    return _record(picks=rows, **over)


def test_a_row_with_tones_notes_live_columns_groups_and_levels_reads_back_unchanged(tmp_path):
    rec = _meaningful_record()
    assert c.validate_picks(rec) == []
    c.write_picks(tmp_path, rec)
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True
    first = view["picks"][0]
    assert first["name"] == "AAAA Inc"
    # A null level is left out when written, like any optional field without a value.
    assert first["levels"] == {"atr": 1.25, "pm_high": 11.0, "pm_low": 9.75}
    assert first["metrics"][0] == {"label": "Price", "value": 10.5, "tone": "caution", "note": "Made-up note."}
    assert first["metrics"][1] == {"label": "Gap %", "value": None, "live": FIRST_MINUTE}
    assert [d.get("group") for d in first["details"]] == ["At a glance", "Company", None]


def test_the_open_vs_pm_kind_takes_text_bands():
    rec = _meaningful_record()
    rec["picks"][0]["metrics"][1]["live"] = OPEN_VS_PM
    assert c.validate_picks(rec) == []


@pytest.mark.parametrize("change, words", [
    (lambda m: m[0].update(tone="green"), "tone must be one of"),
    (lambda m: m[0].update(note="x" * (c.MAX_TEXT_LEN + 1)), "note must be text"),
    (lambda m: m[1].update(value=0.4), "a live metric is filled by Edge Scanner"),
    (lambda m: m[1]["live"].update(kind="vwap_distance"), "kind must be one of"),
    (lambda m: m[1]["live"].update(bands=[{"min": 0.5, "max": 0.2}]), "min 0.5 is above max 0.2"),
    (lambda m: m[1]["live"].update(bands=[{"is": "above"}]), "is must be null"),
    (lambda m: m[1]["live"].update(bands=[{"note": "n"}] * (c.MAX_BANDS + 1)), "at most 8 bands"),
    (lambda m: m[1]["live"].update(bands=[{"min": "0.5"}]), "min must be a finite number"),
    (lambda m: m[1]["live"].update(bands=[{"tone": "red"}]), "tone must be one of"),
    (lambda m: m[1]["live"].update(extra=1), "extra is not a field"),
    (lambda m: m[1].update(live=OPEN_VS_PM | {"bands": [{"is": "sideways"}]}), "is must be one of"),
    (lambda m: m[1].update(live=OPEN_VS_PM | {"bands": [{"min": 1.0}]}), "min and max must be null"),
    (lambda m: m[1].update(live="first_minute_range_atr"), "must be a JSON object with kind and bands"),
    (lambda m: m[0].update(group="At a glance"), "group is not a field"),
])
def test_a_bad_tone_note_or_live_column_is_refused_by_the_writer(change, words):
    rec = _meaningful_record()
    change(rec["picks"][0]["metrics"])
    problems = c.validate_picks(rec)
    assert any(words in p for p in problems), problems


@pytest.mark.parametrize("change, words", [
    (lambda p: p.update(name="  "), "name must be text"),
    (lambda p: p.update(levels={"atr": "1.2"}), "levels.atr must be a finite number"),
    (lambda p: p.update(levels={"vwap": 1.0}), "levels.vwap is not a field"),
    (lambda p: p.update(levels=[1.0]), "levels must be a JSON object"),
    (lambda p: p["details"][0].update(group=""), "group must be text"),
    (lambda p: p["details"][0].update(tone="good"), "tone is not a field"),
])
def test_a_bad_name_level_or_group_is_refused_by_the_writer(change, words):
    rec = _meaningful_record()
    change(rec["picks"][0])
    problems = c.validate_picks(rec)
    assert any(words in p for p in problems), problems


def test_null_optional_fields_are_allowed_and_written_as_left_out(tmp_path):
    rec = _meaningful_record()
    for pick in rec["picks"]:
        pick.update(name=None, levels=None)
        pick["metrics"][0].update(tone=None, note=None)
    assert c.validate_picks(rec) == []
    c.write_picks(tmp_path, rec)
    first = c.read_day(tmp_path, DAY)["picks"][0]
    assert "name" not in first and "levels" not in first
    assert first["metrics"][0] == {"label": "Price", "value": 10.5}


def test_a_reader_shows_a_newer_writers_tone_and_live_kind_and_takes_a_filled_live_value(tmp_path):
    # A newer version may add a tone or a kind; this reader passes it on as written.
    rec = {"schema_version": c.SCHEMA_VERSION + 1, "kind": c.KIND_PICKS, **_meaningful_record()}
    rec["picks"][0]["metrics"][0]["tone"] = "neutral"
    rec["picks"][0]["metrics"][1].update(value=0.4, live={**FIRST_MINUTE, "kind": "vwap_distance"})
    _write_json(tmp_path / DAY / c.PICKS_FILE, rec)
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True, view["reason"]
    assert view["picks"][0]["metrics"][0]["tone"] == "neutral"
    assert view["picks"][0]["metrics"][1]["live"]["kind"] == "vwap_distance"
    # The same file at this version is refused: the tone is not one this reader knows.
    rec["schema_version"] = c.SCHEMA_VERSION
    _write_json(tmp_path / DAY / c.PICKS_FILE, rec)
    assert c.read_day(tmp_path, DAY)["reason_code"] == c.INVALID


def test_the_meanings_sample_shows_every_optional_field_on_a_day_and_a_past_day():
    folder = SAMPLES / "meanings"
    assert c.history_days(folder) == ["2026-11-04"]
    for rows in (c.read_day(folder, DAY)["picks"], c.read_history(folder, "2026-11-04")["picks"]):
        assert all(p["name"] and set(p["levels"]) == set(c.LEVEL_FIELDS) for p in rows)
        live = [m["live"]["kind"] for m in rows[0]["metrics"] if "live" in m]
        assert live == [c.LIVE_FIRST_MINUTE_RANGE_ATR, c.LIVE_OPEN_VS_PM_RANGE]
        assert {m.get("tone") for p in rows for m in p["metrics"]} >= set(c.TONES) - {c.TONE_BAD}
        assert all(d.get("group") for p in rows for d in p["details"])


# ---------------------------------------------------------------- score parts and rank reason (2026-11-08)
# The owner wants to see what made up each score and why each name ranks above the next (Ash,
# project thread, 2026-11-08). Both optional; the labels and numbers here are made up.

def _parts(*points):
    return [{"label": f"Part {k}", "value": f"{k}.0x", "points": p, "possible": 10.0}
            for k, p in enumerate(points, start=1)]


def _reasoned_record():
    rec = _meaningful_record()
    for i, pick in enumerate(rec["picks"]):
        pick["score_parts"] = _parts(10.0, 10.0 - i)
        pick["rank_reason"] = {"text": "Made-up reason.", "decides": "Part 2",
                               "tie_break": {"label": "Made-up tie-break", "value": "$1M"}}
    rec["picks"][-1]["rank_reason"].update(decides=None)
    return rec


def test_the_score_parts_sample_has_parts_on_every_row_and_a_reason_on_all_but_the_last():
    rows = c.read_day(SAMPLES / "score_parts", DAY)["picks"]
    assert all(len(p["score_parts"]) == 6 for p in rows)
    assert all(sum(x["points"] for x in p["score_parts"]) == pytest.approx(p["score"]) for p in rows)
    assert all(p["rank_reason"]["text"] for p in rows[:-1]) and "rank_reason" not in rows[-1]
    labels = {x["label"] for x in rows[0]["score_parts"]}
    # Each reason is decided by a part or by the tie-break, as on a day when two scores tie.
    decides = [p["rank_reason"]["decides"] for p in rows[:-1]]
    assert set(decides) - labels == {"Sample tie-break"}
    tones = [next(m.get("tone") for m in p["metrics"] if m["label"] == "Ahead of next") for p in rows]
    assert set(tones) == set(c.TONES) | {None}


def test_a_row_with_score_parts_and_a_rank_reason_reads_back_unchanged(tmp_path):
    rec = _reasoned_record()
    assert c.validate_picks(rec) == []
    c.write_picks(tmp_path, rec)
    rows = c.read_day(tmp_path, DAY)["picks"]
    assert rows[1]["score_parts"] == _parts(10.0, 9.0)
    assert rows[1]["rank_reason"] == {"text": "Made-up reason.", "decides": "Part 2",
                                      "tie_break": {"label": "Made-up tie-break", "value": "$1M"}}
    assert rows[-1]["rank_reason"]["decides"] is None
    # The tie-break may decide, as on a day when scores tie.
    rec["picks"][0]["rank_reason"]["decides"] = "Made-up tie-break"
    assert c.validate_picks(rec) == []


def test_rows_without_score_parts_or_a_rank_reason_read_back_without_them(tmp_path):
    rec = _reasoned_record()
    for pick in rec["picks"]:
        pick.update(score_parts=None, rank_reason=None)
    rec["picks"][0]["rank_reason"] = {"text": "t", "decides": None, "tie_break": None}
    assert c.validate_picks(rec) == []
    c.write_picks(tmp_path, rec)
    rows = c.read_day(tmp_path, DAY)["picks"]
    assert "score_parts" not in rows[1] and "rank_reason" not in rows[1]
    assert rows[0]["rank_reason"] == {"text": "t", "decides": None}


@pytest.mark.parametrize("change, words", [
    (lambda p: p.update(score_parts={"Part 1": 1}), "score_parts must be a list of at most 12 parts"),
    (lambda p: p.update(score_parts=_parts(*[1.0] * (c.MAX_SCORE_PARTS + 1))), "at most 12 parts"),
    (lambda p: p["score_parts"][0].update(points=10.5), "points 10.5 is above possible 10.0"),
    (lambda p: p["score_parts"][0].update(points=-1.0), "points must be a finite number of 0 or more"),
    (lambda p: p["score_parts"][0].update(points=float("nan")), "points must be a finite number"),
    (lambda p: p["score_parts"][0].update(possible=0), "possible must be a finite number above 0"),
    (lambda p: p["score_parts"][0].update(label=" "), "score_parts[0].label must be text"),
    (lambda p: p["score_parts"][1].update(label="Part 1"), "each part needs its own label"),
    (lambda p: p["score_parts"][0].update(value=True), "score_parts[0].value must be a finite number"),
    (lambda p: p["score_parts"][0].pop("value"), "score_parts[0].value is missing"),
    (lambda p: p["score_parts"][0].update(tone="good"), "score_parts[0].tone is not a field"),
    (lambda p: p.update(rank_reason="ahead"), "rank_reason must be a JSON object"),
    (lambda p: p["rank_reason"].update(text=""), "rank_reason.text must be text"),
    (lambda p: p["rank_reason"].update(short="+1.00"), "rank_reason.short is not a field"),
    (lambda p: p["rank_reason"].update(text="x" * (c.MAX_TEXT_LEN + 1)), "rank_reason.text must be text"),
    (lambda p: p["rank_reason"].update(decides="Part 9"), "decides must be null or the label"),
    (lambda p: p.update(score_parts=None), "decides must be null or the label"),
    (lambda p: p["rank_reason"].update(tie_break="$1M"), "tie_break must be a JSON object"),
    (lambda p: p["rank_reason"]["tie_break"].update(label=""), "tie_break.label must be text"),
    (lambda p: p["rank_reason"]["tie_break"].update(extra=1), "tie_break.extra is not a field"),
    (lambda p: p["rank_reason"].update(why="x"), "rank_reason.why is not a field"),
    (lambda p: p["score_parts"][0].update(full_at=0.3), "score_parts[0].full_at must be text"),
    (lambda p: p["score_parts"][0].update(zero_at=""), "score_parts[0].zero_at must be text"),
    (lambda p: p["score_parts"][0].update(vs_next="+1"), "score_parts[0].vs_next must be a finite number"),
    (lambda p: p["score_parts"][0].update(vs_next=float("inf")), "score_parts[0].vs_next must be a finite number"),
])
def test_bad_score_parts_or_a_bad_rank_reason_are_refused_by_the_writer(change, words):
    rec = _reasoned_record()
    change(rec["picks"][0])
    problems = c.validate_picks(rec)
    assert any(words in p for p in problems), problems


def test_a_part_with_its_marks_and_its_points_against_the_next_row_reads_back_unchanged(tmp_path):
    # The owner's details (2026-11-08, "A1 + B1 + H1"): where a part starts and stops scoring,
    # and what it gives the row against the next one. All made up here.
    rec = _reasoned_record()
    rec["picks"][0]["score_parts"][0].update(zero_at="0.1x", full_at="2.0x", vs_next=1.5)
    rec["picks"][0]["score_parts"][1].update(vs_next=-0.25)
    rec["picks"][1]["score_parts"][0].update(vs_next=0.0)
    assert c.validate_picks(rec) == []
    c.write_picks(tmp_path, rec)
    rows = c.read_day(tmp_path, DAY)["picks"]
    assert rows[0]["score_parts"][0] == {"label": "Part 1", "value": "1.0x", "points": 10.0, "possible": 10.0,
                                         "zero_at": "0.1x", "full_at": "2.0x", "vs_next": 1.5}
    assert rows[0]["score_parts"][1]["vs_next"] == -0.25
    assert rows[1]["score_parts"][0]["vs_next"] == 0.0
    # Left out, or null, they read back without the field.
    assert set(rows[1]["score_parts"][1]) == {"label", "value", "points", "possible"}
    rec["picks"][2]["score_parts"][0].update(full_at=None, vs_next=None)
    assert c.validate_picks(rec) == []


def test_a_part_exactly_at_its_most_is_allowed():
    rec = _reasoned_record()
    rec["picks"][0]["score_parts"][0].update(points=10.0, possible=10.0)
    rec["picks"][0]["score_parts"][1].update(points=0.0)
    assert c.validate_picks(rec) == []
