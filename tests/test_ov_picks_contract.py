"""OV picks file contract (ashwinurao/edge-scanner#9): the writer, the readers and the
sample day folders that the module and Edge Scanner both build on.

Every rule is checked with a record where it must fire and one where it must not.
Nothing here needs the module's pinned libraries, and nothing reaches the network.
"""
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
UNEQUAL = (0.4, 0.3, 0.2, 0.1)          # unequal traded weights; the numbers only show the format


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("a contract test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _pick(rank, ticker, *, score=80.0, label=None, traded=True, weight=0.25):
    return {"rank": rank, "ticker": ticker, "score": score, "earnings_label": label,
            "traded": traded, "weight": weight}


def _picks4(**kw):
    return [_pick(1, "AAAA", score=91.5, **kw), _pick(2, "BBBB", score=88.25, **kw),
            _pick(3, "CCCC", score=86.0, **kw), _pick(4, "DDDD", score=85.75, **kw)]


def _record(**over):
    rec = {
        "trading_date": DAY,
        "frozen_at": FROZEN_AT,
        "warmup_state": c.READY,
        "warmup_reason": "",
        "late": False,
        "shadow": False,
        "earnings_status": c.EARNINGS_AVAILABLE,
        "picks": _picks4(),
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
    # Spec "Runner", daily schedule table: the result state of each job.
    spec = {
        c.JOB_NIGHTLY: {"NIGHTLY_OK", "NIGHTLY_WARN"},
        c.JOB_WARMUP: {"WARMING", "DATA_READY", "READY", "NOT_READY"},
        c.JOB_SCAN: {"PICKS_FROZEN"},
        c.JOB_SETTLE: {"SETTLED_SAME", "SETTLED_DIFF"},
        c.JOB_COMPARE: {"PARITY_MATCH", "PARITY_DIFF", "PARITY_NO_OV_RUN"},
        c.JOB_ARCHIVE: {"ARCHIVED"},
        c.JOB_REFERENCE: {"REF_OK", "REF_WARN"},
    }
    assert set(c.JOBS) == set(spec) == set(c.JOB_STATES)
    for job, states in spec.items():
        assert set(c.JOB_STATES[job]) == states | {"RUNNING", "SKIPPED", "REFUSED", "FAILED"}, job
    assert c.DAY_STATES == ("WARMING", "DATA_READY", "READY", "NOT_READY",
                            "PICKS_FROZEN", "SETTLED_SAME", "SETTLED_DIFF")


def test_contract_doc_names_every_field_and_state():
    doc = (ROOT / "ov_picks" / "CONTRACT.md").read_text(encoding="utf-8")
    names = (set(c.PICKS_FIELDS) | set(c.PICK_FIELDS) | set(c.SETTLED_FIELDS)
             | set(c.PARITY_FIELDS) | set(c.STATUS_FIELDS) | set(c.STATUS_ENTRY_FIELDS)
             | set(c.VIEW_FIELDS) | set(c.STATUS_VIEW_FIELDS) | set(c.JOBS)
             | {s for states in c.JOB_STATES.values() for s in states}
             | set(c.EARNINGS_STATUSES) | set(c.EARNINGS_LABELS)
             | {c.PENDING, c.UNREADABLE, c.MISSING, c.INVALID}
             | {c.PICKS_FILE, c.SETTLED_FILE, c.PARITY_FILE, c.STATUS_FILE}
             | {c.KIND_PICKS, c.KIND_SETTLED, c.KIND_PARITY, c.KIND_STATUS})
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
        c.write_picks(tmp_path, _record(picks=[_pick(1, "EEEE", weight=1.0)]))

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
    # Earnings feed down (spec US17): no traded set and no earnings labels.
    ({"earnings_status": c.EARNINGS_UNAVAILABLE}, "traded"),
    ({"earnings_status": c.EARNINGS_MISSING_CONTEXT}, "traded"),
    ({"earnings_status": c.EARNINGS_UNAVAILABLE,
      "picks": [_pick(1, "AAAA", label=c.EARNINGS_LABEL_AMC, traded=False, weight=0.0)]}, "earnings_label"),
    ({"earnings_status": "down"}, "earnings_status"),
    # Weights: the traded set sums to 1 (OV normalizes by fsum); an untraded pick weighs 0.
    ({"picks": _picks4(weight=0.3)}, "weights"),
    ({"picks": [_pick(1, "AAAA", traded=False, weight=0.5)]}, "weight"),
    ({"picks": [_pick(1, "AAAA", weight=1.5)]}, "weight"),
    ({"picks": [_pick(1, "AAAA", weight=float("nan"))]}, "weight"),
    ({"picks": [_pick(1, "AAAA", weight="1.0")]}, "weight"),
    # At most OV's four picks, ranked 1..n in order, each ticker once.
    ({"picks": _picks4() + [_pick(5, "EEEE")]}, "at most 4"),
    ({"picks": [_pick(2, "AAAA", weight=0.5), _pick(1, "BBBB", weight=0.5)]}, "rank"),
    ({"picks": [_pick(1, "AAAA", weight=0.5), _pick(3, "BBBB", weight=0.5)]}, "rank"),
    ({"picks": [_pick(True, "AAAA", weight=1.0)]}, "rank"),
    ({"picks": [_pick(1, "AAAA", weight=0.5), _pick(2, "AAAA", weight=0.5)]}, "twice"),
    ({"picks": [_pick(1, "aaaa", weight=1.0)]}, "ticker"),
    ({"picks": [_pick(1, " AAAA", weight=1.0)]}, "ticker"),
    ({"picks": [_pick(1, "", weight=1.0)]}, "ticker"),
    ({"picks": [_pick(1, "AAAA", score=float("inf"), weight=1.0)]}, "score"),
    ({"picks": [_pick(1, "AAAA", label="PRIOR-EVENING EARNINGS", weight=1.0)]}, "earnings_label"),
    ({"picks": [_pick(1, "AAAA", traded=1, weight=1.0)]}, "traded"),
    ({"picks": [{**_pick(1, "AAAA", weight=1.0), "sector": "Tech"}]}, "sector"),
    ({"picks": [{k: v for k, v in _pick(1, "AAAA", weight=1.0).items() if k != "score"}]}, "score"),
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


def test_the_writer_refuses_a_record_with_a_missing_field(tmp_path):
    rec = _record()
    del rec["earnings_status"]
    with pytest.raises(c.ContractError, match="earnings_status"):
        c.write_picks(tmp_path, rec)


@pytest.mark.parametrize("over", [
    # The same rules, where they must NOT fire.
    {"earnings_status": c.EARNINGS_UNAVAILABLE, "picks": _picks4(traded=False, weight=0.0)},
    {"earnings_status": c.EARNINGS_MISSING_CONTEXT, "picks": _picks4(traded=False, weight=0.0)},
    {"picks": [_pick(i + 1, t * 4, weight=w) for i, (t, w) in enumerate(zip("ABCD", UNEQUAL))]},
    {"picks": [_pick(1, "AAAA", traded=False, weight=0.0),
               _pick(2, "BBBB", label=c.EARNINGS_LABEL_AMC, weight=0.5),
               _pick(3, "CCCC", label=c.EARNINGS_LABEL_BMO, weight=0.5),
               _pick(4, "DDDD", traded=False, weight=0.0)]},
    {"picks": [_pick(1, "AAAA", weight=1)]},              # a whole-number weight is a number
    {"picks": [_pick(1, "BRK.B", weight=1.0)]},
    {"picks": []},                                        # a thin day: no name passed OV's gates
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

    assert view["picks"] == _picks4()                    # the 09:26 re-check never replaces them
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
    (lambda r: {**r, "picks": [{**r["picks"][0], "weight": "big"}]}, "weight"),
    # The reader re-checks the safety rule: an earnings-down day never shows a traded set.
    (lambda r: {**r, "earnings_status": c.EARNINGS_UNAVAILABLE}, "traded"),
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
    rec["picks"] = [{**p, "sector": "Tech"} for p in rec["picks"]]
    _write_json(tmp_path / DAY / c.PICKS_FILE, rec)
    view = c.read_day(tmp_path, DAY)
    assert view["has_picks"] is True
    assert "future_field" not in view
    assert all(set(p) == set(c.PICK_FIELDS) for p in view["picks"])


def test_the_reader_accepts_an_older_schema_version(monkeypatch):
    # A later ticket raises SCHEMA_VERSION; the frozen version 1 sample must keep loading.
    monkeypatch.setattr(c, "SCHEMA_VERSION", 2)
    view = c.read_day(SAMPLES / "older_schema", DAY)
    assert view["has_picks"] is True, view["reason"]
    assert view["schema_version"] == 1
    assert [p["ticker"] for p in view["picks"]] == ["AAAA", "BBBB", "CCCC", "DDDD"]


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

SAMPLE_CASES = {
    # case: (has picks, shadow, late, warmup, earnings, traded tickers, settled, parity)
    "frozen": (True, False, False, c.READY, c.EARNINGS_AVAILABLE,
               ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING),
    "shadow": (True, True, False, c.READY, c.EARNINGS_AVAILABLE,
               ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_SAME, c.PENDING),
    "late": (True, False, True, c.READY, c.EARNINGS_AVAILABLE,
             ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING),
    "not_ready": (True, False, False, c.NOT_READY, c.EARNINGS_AVAILABLE,
                  ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING),
    "earnings_unavailable": (True, False, False, c.READY, c.EARNINGS_UNAVAILABLE,
                             [], c.PENDING, c.PENDING),
    "parity_match": (True, True, False, c.READY, c.EARNINGS_AVAILABLE,
                     ["BBBB", "CCCC"], c.SETTLED_SAME, c.PARITY_MATCH),
    "parity_diff": (True, True, False, c.READY, c.EARNINGS_AVAILABLE,
                    ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_SAME, c.PARITY_DIFF),
    "no_ov_run": (True, False, False, c.READY, c.EARNINGS_AVAILABLE,
                  ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_SAME, c.PARITY_NO_OV_RUN),
    "settled_diff": (True, True, False, c.READY, c.EARNINGS_AVAILABLE,
                     ["AAAA", "BBBB", "CCCC", "DDDD"], c.SETTLED_DIFF, c.PENDING),
    "older_schema": (True, False, False, c.READY, c.EARNINGS_AVAILABLE,
                     ["AAAA", "BBBB", "CCCC", "DDDD"], c.PENDING, c.PENDING),
    "warming": (False, None, None, None, None, [], None, None),
}


def test_the_sample_folders_are_the_documented_set():
    assert sorted(p.name for p in SAMPLES.iterdir() if p.is_dir()) == sorted(SAMPLE_CASES)
    doc = (ROOT / "ov_picks" / "CONTRACT.md").read_text(encoding="utf-8")
    assert [case for case in SAMPLE_CASES if f"`{case}`" not in doc] == []


@pytest.mark.parametrize("case", sorted(SAMPLE_CASES))
def test_every_sample_passes_the_writers_own_check(case):
    folder = SAMPLES / case
    for path in sorted(folder.rglob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        check = {c.PICKS_FILE: c.validate_picks, c.SETTLED_FILE: c.validate_settled,
                 c.PARITY_FILE: c.validate_parity, c.STATUS_FILE: c.validate_status}[path.name]
        assert check(rec) == [], path
    for path in folder.rglob("*"):
        assert path.is_dir() or path.name in (c.PICKS_FILE, c.SETTLED_FILE, c.PARITY_FILE,
                                              c.STATUS_FILE), path
        assert path.is_dir() or path.parent.name in (case, DAY), path


@pytest.mark.parametrize("case", sorted(SAMPLE_CASES))
def test_the_reader_handles_every_sample(case):
    has, shadow, late, warmup, earnings, traded, settled, parity = SAMPLE_CASES[case]

    view = c.read_day(SAMPLES / case, DAY)

    assert view["has_picks"] is has, view["reason"]
    assert view["warnings"] == []
    if not has:
        assert view["reason_code"] == c.MISSING
        return
    assert (view["shadow"], view["late"], view["warmup_state"]) == (shadow, late, warmup)
    assert view["earnings_status"] == earnings
    assert [p["ticker"] for p in view["picks"] if p["traded"]] == traded
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
    expected_warmup = c.WARMING if case == "warming" else SAMPLE_CASES[case][3]
    assert status["jobs"][c.JOB_WARMUP]["state"] == expected_warmup
    if case != "warming":
        assert status["jobs"][c.JOB_SCAN]["state"] == c.PICKS_FROZEN
        assert status["jobs"][c.JOB_SCAN]["trading_date"] == DAY


def test_the_samples_show_unequal_and_earnings_day_weights():
    # frozen: four traded names with unequal weights.
    view = c.read_day(SAMPLES / "frozen", DAY)
    assert [p["weight"] for p in view["picks"]] == list(UNEQUAL)
    assert view["reference_promoted"] == ["FFFF"]
    # parity_match: an earnings day where only the AMC and BMO names are traded.
    view = c.read_day(SAMPLES / "parity_match", DAY)
    assert [(p["earnings_label"], p["weight"]) for p in view["picks"]] == [
        (None, 0.0), (c.EARNINGS_LABEL_AMC, 0.5), (c.EARNINGS_LABEL_BMO, 0.5), (None, 0.0)]
