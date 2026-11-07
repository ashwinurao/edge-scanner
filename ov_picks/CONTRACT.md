# OV picks file contract, schema version 1

The OV picks module writes these files. Edge Scanner reads them. Nothing else passes
between the two (spec "Outputs", issue ashwinurao/edge-scanner#8; this contract is
ticket #9). The code is `ov_picks/contract.py`: it has a constant for every name below,
the writers, the readers and the checks. It uses the standard library only, so Edge
Scanner imports it on its own Python (3.11 and up) without the module's pinned libraries.

## Layout

```
out/                          the module's output folder (mounted read-only into Edge Scanner)
  status.json                 each job's last state
  2026-11-05/                 one folder per ET trading day, named YYYY-MM-DD
    preview.json              the ranked list as of the latest minute from 09:00 to 09:24 ET, replaced each minute
    picks.json                frozen at 09:25:00 ET, written once
    settled.json              the 09:26 ET re-check, written once
    parity.json               the 10:35 ET comparison with OV, written once
  history/                    OV's own past days, before the module ran, exported by hand
    2026-11-04.json           one day's ranked list, named YYYY-MM-DD.json
```

- Only the module's runner writes `out/`. Edge Scanner reads it and never writes it.
- `picks.json` (`PICKS_FILE`), `settled.json` (`SETTLED_FILE`) and `parity.json`
  (`PARITY_FILE`) are day records. Each is written once, atomically: a temp file in the
  same folder, then a rename that never replaces a file. A second write for the same day
  raises `AlreadyWritten` and leaves the first file as it was.
- `settled.json` and `parity.json` are refused until the day has a `picks.json`. They
  add to the frozen picks and never replace them (spec "Freeze and restart rules").
- `preview.json` (`PREVIEW_FILE`) is not a day record. Each minute's preview replaces the
  last one atomically (a temp file, then a rename). The writer refuses a preview once the
  day has a `picks.json`, so the last preview before the freeze stays as it was. A preview
  is never the day's picks: Edge Scanner shows it only before 09:25 ET, and the watchlist
  and the day's notice come from `picks.json` only.
- `history/` (`HISTORY_DIR`) holds OV's own past days, one file per ET trading day,
  named `YYYY-MM-DD.json` (`HISTORY_SUFFIX`). The module exports them by hand, once; an
  export replaces a day's file atomically. The writer refuses a day the module froze its
  own `picks.json` for: those are the day's list. Edge Scanner shows a history day only
  when it is asked for a past day without module picks.
- `status.json` (`STATUS_FILE`) is rewritten atomically each time a job records a state.
  It assumes one writer process at a time. Two processes writing it at once can lose one
  entry until that job writes again.
- The runner also writes OV's full results and its summary into the day folder,
  under names it owns. They are not part of this contract, and Edge Scanner does not read
  them.
- Every file is UTF-8 JSON with `schema_version` and `kind`. NaN and Infinity are never
  written. A file over 8,000,000 bytes (`MAX_FILE_BYTES`) is treated as unreadable, and
  the writers refuse a record that big. A picks file of 100 rows with full details is
  about 1.5 MB.

Types below: `text` is a JSON string, `date` is `YYYY-MM-DD` text, `time` is a UTC time
written `YYYY-MM-DDTHH:MM:SSZ` (`utc_timestamp()` makes one; fractional seconds are
allowed), `ticker` is upper-case text with no spaces, at most 16 characters.

## Picks file: `picks.json`

`kind` is `ov_picks` (`KIND_PICKS`).

| Field | Type | Allowed values and rules |
|---|---|---|
| `schema_version` | whole number | `1` |
| `kind` | text | `ov_picks` |
| `trading_date` | date | The ET trading day; equals the folder name. Never a Saturday or Sunday. |
| `frozen_at` | time | When the picks froze. Its ET date is `trading_date`. 09:25:00 ET on time; later when `late`. |
| `warmup_state` | text | `READY` or `NOT_READY`: the warmup's verdict at the 09:20 ET deadline. |
| `warmup_reason` | text | Why the warmup was `NOT_READY`; must not be blank then. May be empty for `READY`. |
| `late` | true/false | The first scan ran between 09:25 and 09:35 ET (spec US12). |
| `shadow` | true/false | The shadow run is on: the card says "shadow: checking against OV" (spec US14). |
| `earnings_status` | text | OV's earnings data status, verbatim: `available`, `unavailable` or `missing_prior_session_context`. |
| `metric_labels` | list of text | The window's column headers, in order: at most 20 (`MAX_METRICS`), each label once. Every row's `metrics` has these labels in this order. The module chooses them. |
| `picks` | list | The ranked list: every name OV ranked that day and did not reject, in OV's order, at most 100 rows (`MAX_ROWS`). Empty when no name passed OV's gates. |
| `reference_state` | text or null | The last reference update before this day: `REF_OK`, `REF_WARN`, or null when none ran (the shadow run keeps the updaters off). |
| `reference_promoted` | list of tickers | The names the new-listing rule promoted that week, each once. Empty when `reference_state` is null. |

Each row of `picks`:

| Field | Type | Allowed values and rules |
|---|---|---|
| `rank` | whole number | OV's rank for the row, from 1. |
| `ticker` | ticker | OV's ticker, upper-cased and stripped. |
| `score` | number | OV's score for the row. Finite. |
| `status` | text | OV's status for the row, verbatim. Not blank, at most 200 characters. |
| `top` | true/false | One of OV's top picks for the day: the names OV marks as its candidates, at most 4 (`MAX_PICKS`, the spec's four picks). |
| `earnings_label` | text or null | `AMC` (reported after the prior close), `BMO` (reports before this open) or null. OV's earnings release timing. |
| `metrics` | list of entries | The window's columns for this row: one entry per `metric_labels` label, in that order. |
| `details` | list of entries | The full row, for a click on it: at most 200 entries (`MAX_DETAILS`), in the order the module writes them. |
| `name` | text, optional | The company's name, at most 200 characters. Left out when unknown. |
| `levels` | object, optional | The row's price levels (below). Left out when the module has none. |

Each entry of `metrics` and `details` is an object with two fields, plus the optional
fields below:

| Field | Type | Allowed values and rules |
|---|---|---|
| `label` | text | What the value is. Not blank, at most 200 characters (`MAX_TEXT_LEN`). |
| `value` | number, text or null | A finite number, text of at most 200 characters, or null for no value. Never true or false: the module writes a flag as text. |

The module decides which values go in `metrics` and `details`, and what each label says.
Edge Scanner shows the labels and values as written and never reads meaning into them.

### What a value means

The owner's layout for the window, "C1 + E1" (Ash, project thread, 2026-11-07): the
research numbers come first as columns, coloured where the research found an effect, and a
click on ▸ opens a card with the row's price levels, its key numbers with one line each on
what they mean, and every field in groups. The module says what each value means with these
optional fields; an older reader ignores them. A writer leaves an optional field out when it
has no value, so a row without them reads back unchanged.

A metric (`METRIC_FIELDS`) may also carry:

| Field | Type | Allowed values and rules |
|---|---|---|
| `tone` | text, optional | How the window colours the cell: `good` (`TONE_GOOD`), `caution` (`TONE_CAUTION`) or `bad` (`TONE_BAD`), all in `TONES`. Left out for no colour. |
| `note` | text, optional | One line on what the value means, at most 200 characters. A metric with a note is one of the row's key numbers: the details card shows it as a tile with its note. |
| `live` | object, optional | A column Edge Scanner fills after the open (below). Its `value` in the file is null. |

A detail (`DETAIL_FIELDS`) may also carry:

| Field | Type | Allowed values and rules |
|---|---|---|
| `group` | text, optional | The section of the details card the entry goes in. Not blank, at most 200 characters. The card shows the groups in the order they first appear, and the entries of a group in their order. The first group sits beside the key numbers; the rest open below. |

A row's `levels` holds any of these (`LEVEL_FIELDS`), each a finite number or null, in the
row's own price terms:

| Field | Meaning |
|---|---|
| `atr` | The row's daily ATR (`LEVEL_ATR`). |
| `prior_close`, `prior_high`, `prior_low` | The prior session's close, high and low. |
| `pm_high`, `pm_low` | The pre-market high and low (`LEVEL_PM_HIGH`, `LEVEL_PM_LOW`). |
| `last` | The last pre-market price. |

#### Live metrics

A live metric is a column the module places and Edge Scanner fills after the open, from the
day's 09:30 ET minute bar and the row's `levels`. Edge Scanner fetches that bar once per day
shown, on its own market-data keys (owner's go, 2026-11-07). Before 09:31 ET on the day, or
without the bar or the levels it needs, the value stays null and the card says why.

`live` has exactly these fields (`LIVE_FIELDS`):

| Field | Type | Allowed values and rules |
|---|---|---|
| `kind` | text | What Edge Scanner fills, one of `LIVE_KINDS`: `first_minute_range_atr` (`LIVE_FIRST_MINUTE_RANGE_ATR`), the bar's high minus its low, divided by `levels.atr`, rounded to 2 decimals; or `open_vs_pm_range` (`LIVE_OPEN_VS_PM_RANGE`), the bar's open against `levels.pm_high` and `levels.pm_low`: `above` (`OPEN_ABOVE`), `below` (`OPEN_BELOW`) or `inside` (`OPEN_INSIDE`, edges included), all in `OPEN_VS_PM_VALUES`. |
| `bands` | list | At most 8 (`MAX_BANDS`). What each value means: the first band the value falls in gives the cell its tone and its note. |

Each band has these optional fields (`BAND_FIELDS`):

| Field | Type | Allowed values and rules |
|---|---|---|
| `min` | number or null | A number value is in the band when it is at least this. Null: no lower bound. |
| `max` | number or null | A number value is in the band when it is at most this. Null: no upper bound. Not below `min`. |
| `is` | text or null | A text value is in the band when it equals this. For `first_minute_range_atr` it is null; for `open_vs_pm_range`, `min` and `max` are null and `is` is one of `OPEN_VS_PM_VALUES`. |
| `tone` | text or null | One of `TONES`, or null for no colour. |
| `note` | text or null | One line on what the value means, at most 200 characters. |

A band with no `min`, `max` or `is` takes any value: put it last, as the default.

Rules between fields:

- The ranks start at 1 and strictly increase down the list: the rows are in OV's order.
  A gap is allowed, so the ranks stay OV's when the module leaves a row out.
- Each ticker is listed once.
- The rows marked `top` come first, and there are at most 4 of them. Fewer when fewer
  names pass OV's candidate gates.
- Every row's `metrics` labels equal `metric_labels`, in order.
- When `earnings_status` is not `available`, no row has an `earnings_label` (spec US17:
  no earnings labels without earnings data).

## Preview file: `preview.json`

`kind` is `ov_picks_preview` (`KIND_PREVIEW`). The owner's every-minute previews (Ash,
2026-11-06): from 09:00 to 09:24 ET the module runs OV's pre-market scan as of each
minute, so the top contenders show early. A preview has the picks file's ranked list,
with the same rows, metrics and details, and the time it is as of.

| Field | Type | Allowed values and rules |
|---|---|---|
| `schema_version` | whole number | `1` |
| `kind` | text | `ov_picks_preview` |
| `trading_date` | date | The ET trading day; equals the folder name. Never a Saturday or Sunday. |
| `as_of` | time | The minute the preview is as of: OV scanned the bars before it, as the frozen scan at 09:25:00 scans the bars through 09:24. A whole minute; its ET date is `trading_date`; its ET time is before 09:25:00 (`PREVIEW_BEFORE_ET`). |
| `previewed_at` | time | When the preview was written. Not before `as_of`. |
| `shadow` | true/false | The shadow run is on, as in the picks file. |
| `earnings_status` | text | OV's earnings data status, verbatim, as in the picks file. |
| `metric_labels` | list of text | As in the picks file. |
| `picks` | list | The ranked list as of `as_of`, rows as in the picks file. Empty when no name passed OV's gates. |

The rules between fields are the picks file's: the rows' ranks, tickers, `top` rows,
metric labels and earnings labels follow the same rules.

`read_preview(out_dir, trading_date)` returns `has_preview`, `reason_code`, `reason`,
`warnings`, `schema_version`, and the fields above but `kind` (`PREVIEW_VIEW_FIELDS`). It
never raises on a missing, unreadable or invalid file: `has_preview` is false, and
`reason_code` (`MISSING`, `UNREADABLE` or `INVALID`) and `reason` say why. It raises
`ValueError` only for a `trading_date` that is not a date.

## History file: `history/YYYY-MM-DD.json`

`kind` is `ov_picks_history` (`KIND_HISTORY`). The owner's calendar of past days (Ash,
project thread, 2026-11-07): Edge Scanner's OV picks window can show a past day, and before
the module ran those days come from OV itself. A history file has the picks file's ranked
list, with the same rows, metrics and details, and where it comes from.

| Field | Type | Allowed values and rules |
|---|---|---|
| `schema_version` | whole number | `1` |
| `kind` | text | `ov_picks_history` |
| `trading_date` | date | The ET trading day; equals the file name. Never a Saturday or Sunday. |
| `source` | text | Where the ranked list comes from: `ov_rebuild` (`HISTORY_OV_REBUILD`), OV's 2026 rebuild, OV's current code and config run again on clean bars; or `ov_live` (`HISTORY_OV_LIVE`), OV's own live 09:25 ET scan that morning. |
| `exported_at` | time | When the module wrote the file. Its ET date is after `trading_date`: a history file holds a past day. |
| `earnings_status` | text | OV's earnings data status that day, verbatim, as in the picks file. |
| `metric_labels` | list of text | As in the picks file. |
| `picks` | list | That day's ranked list, rows as in the picks file. Empty when no name passed OV's gates. |

The rules between fields are the picks file's: the rows' ranks, tickers, `top` rows,
metric labels and earnings labels follow the same rules.

`read_history(out_dir, trading_date)` returns `has_history`, `reason_code`, `reason`,
`warnings`, `schema_version`, and the fields above but `kind` (`HISTORY_VIEW_FIELDS`). It
never raises on a missing, unreadable or invalid file: `has_history` is false, and
`reason_code` (`MISSING`, `UNREADABLE` or `INVALID`) and `reason` say why. It raises
`ValueError` only for a `trading_date` that is not a date. `history_days(out_dir)` lists
the days `history/` has a file for, oldest first, from the file names alone.

## Settled record: `settled.json`

`kind` is `ov_picks_settled` (`KIND_SETTLED`). The 09:26 ET re-check in a scratch area.

| Field | Type | Allowed values and rules |
|---|---|---|
| `schema_version` | whole number | `1` |
| `kind` | text | `ov_picks_settled` |
| `trading_date` | date | Equals the folder name. |
| `checked_at` | time | When the re-check ran. |
| `state` | text | `SETTLED_SAME` or `SETTLED_DIFF` (bars that came in late for 09:24 would have changed the picks, spec US11). |
| `difference` | text or null | What differs; not blank for `SETTLED_DIFF`, null for `SETTLED_SAME`. |

## Parity record: `parity.json`

`kind` is `ov_picks_parity` (`KIND_PARITY`). The 10:35 ET comparison with OV's live result.

| Field | Type | Allowed values and rules |
|---|---|---|
| `schema_version` | whole number | `1` |
| `kind` | text | `ov_picks_parity` |
| `trading_date` | date | Equals the folder name. |
| `compared_at` | time | When the comparison ran. |
| `status` | text | `PARITY_MATCH` ("matches OV"), `PARITY_DIFF` (shows the first difference) or `PARITY_NO_OV_RUN` ("no OV run to compare", spec US15, US16). |
| `first_difference` | text or null | Not blank for `PARITY_DIFF`, null otherwise. |

## What `read_day()` returns

`read_day(out_dir, trading_date)` joins the three day records into one JSON object, the
one Edge Scanner's route serves. It never raises on a missing, unreadable or invalid
file; it raises `ValueError` only for a `trading_date` that is not a date. Every key is
always present (`VIEW_FIELDS`).

| Field | Meaning |
|---|---|
| `has_picks` | true when `picks.json` read and passed the contract. |
| `reason_code` | Empty with picks. Otherwise `MISSING` (no file yet), `UNREADABLE` (cannot be read or is not JSON) or `INVALID` (reads, but breaks the contract). |
| `reason` | Empty with picks. Otherwise what happened and what to do next. |
| `warnings` | List of text: a newer schema version, or a settled or parity record that could not be used. |
| `schema_version` | The version the picks file was written with; null without picks. |
| `trading_date` | The day asked for. |
| `frozen_at` ... `reference_promoted` | The picks file's fields as above; null (or an empty list) without picks. |
| `settled_state` | `SETTLED_SAME`, `SETTLED_DIFF`, `PENDING` (no record yet) or `UNREADABLE` (a record that could not be used; see `warnings`). Null without picks. |
| `settled_same`, `settled_diff` | true/false flags from `settled_state`. Both false while `PENDING` or `UNREADABLE`. |
| `settled_difference`, `settled_at` | From `settled.json`, or null. |
| `parity_status` | `PARITY_MATCH`, `PARITY_DIFF`, `PARITY_NO_OV_RUN`, `PENDING` or `UNREADABLE`. Null without picks. |
| `parity_first_difference`, `parity_at` | From `parity.json`, or null. |

`PENDING` and `UNREADABLE` are reader values only; no file stores them. A record that
did not read is never shown as `SETTLED_SAME` or `PARITY_MATCH`.

## Status file: `status.json`

`kind` is `ov_picks_status` (`KIND_STATUS`). `jobs` maps each job to its last entry.
`record_job_state(out_dir, job, state, at=..., message=..., trading_date=..., next_at=...)`
replaces one job's entry and keeps the others.

| Field | Type | Allowed values and rules |
|---|---|---|
| `schema_version` | whole number | `1` |
| `kind` | text | `ov_picks_status` |
| `jobs` | object | Job name to entry. |

Each entry:

| Field | Type | Allowed values and rules |
|---|---|---|
| `state` | text | One of the job's states below. |
| `at` | time | When the job recorded this state. |
| `trading_date` | date or null | The ET trading day the entry is about; null for the Saturday reference job. Compare it with today before showing a state as today's. |
| `message` | text | What happened and what to do next. Required (not blank) for `NIGHTLY_WARN`, `NOT_READY`, `SETTLED_DIFF`, `PARITY_DIFF`, `REF_WARN`, `SKIPPED`, `REFUSED` and `FAILED`. |
| `next_at` | time or null | When the job runs next, so a closed-market card can say when the next scan runs (spec US18). |

Jobs (`JOBS`) and their states (`JOB_STATES`), from the spec's daily schedule table and
the owner's previews. All times are ET.

| Job | When | States |
|---|---|---|
| `nightly` | 03:30 | `NIGHTLY_OK`, `NIGHTLY_WARN` |
| `warmup` | 08:30, deadline 09:20 | `WARMING` then `DATA_READY`; at the deadline `READY` or `NOT_READY` |
| `preview` | each minute from 09:00 to 09:24, prepared from the warmup on | `PREVIEWED` after each preview |
| `scan` | 09:25:00 | `PICKS_FROZEN` |
| `settle` | 09:26 | `SETTLED_SAME`, `SETTLED_DIFF` |
| `compare` | 10:35 | `PARITY_MATCH`, `PARITY_DIFF`, `PARITY_NO_OV_RUN` |
| `archive` | 16:15 | `ARCHIVED` |
| `reference` | Saturday 10:00, after go-live | `REF_OK`, `REF_WARN` |

Every job can also be in `RUNNING`, `SKIPPED` (not run today: a closed market, after
09:35, or the reference job during the shadow run), `REFUSED` (a rule stopped it: sector
coverage below OV's minimum, or an OV-touching job outside its window) or `FAILED` (it
raised). The spec names these cases without giving them states, so the contract adds
these four (`COMMON_STATES`).

`DAY_STATES` lists the morning's progress in order, for a one-line card: `WARMING`,
`DATA_READY`, `READY`, `NOT_READY`, `PICKS_FROZEN`, `SETTLED_SAME`, `SETTLED_DIFF`.

`read_status(out_dir)` returns `has_status`, `reason_code`, `reason`, `warnings`,
`schema_version` and `jobs` (`STATUS_VIEW_FIELDS`). It never raises on a bad file. A
bad entry is left out with a warning, and the other entries still read.

## Versions

- Writers write `SCHEMA_VERSION` (1) only, and refuse fields this version does not have.
- Readers ignore fields they do not know and accept every version from
  `MIN_SCHEMA_VERSION` (1). A file from a newer version reads with a warning; a state it
  adds is shown as written. A file from an older version is upgraded on read.
- A new version only adds fields or values. It never changes what an existing field
  means. Version 1 gained the preview file and the `preview` job without a new version:
  no existing record changed, and a reader that predates them keeps the `preview` status
  entry as written. It gained the history file the same way: a reader that predates it
  never opens `history/`. It gained the optional fields of "What a value means" (2026-11-07)
  the same way: a file without them is unchanged, and a reader that predates them ignores
  them. A version that adds a field older files lack adds an upgrade step to
  `_UPGRADES` in `ov_picks/contract.py`, so saved days keep loading (spec US67;
  CONTRIBUTING.md "Saved work survives").

## Sample folders: `ov_picks/samples/`

Each sample is an `out/` folder for 2026-11-05, a Thursday. The tickers, statuses, labels
and numbers are made up; they show the format, not OV's metrics. Unless the table says
otherwise, a sample's ranked list has 6 rows, the first 4 marked `top`. The contract's own
writer made every file, and `tests/test_ov_picks_contract.py` checks that each one still
passes the writer's checks and reads as described.

| Folder | What it shows |
|---|---|
| `frozen` | On-time picks after go-live: a ranked list of 10 rows, the first 4 marked `top`. Nine metrics per row and 13 details. The labels are generic and the numbers only show the format. One name promoted. No settled or parity record yet. |
| `shadow` | The shadow run: `shadow` true, no reference update, the 09:26 re-check the same. |
| `late` | A first scan at 09:31 ET, marked `late`. |
| `not_ready` | Warmup `NOT_READY` at the deadline, with the reason; picks made anyway. |
| `earnings_unavailable` | `earnings_status` `unavailable`: no earnings labels. |
| `parity_match` | Shadow run, `PARITY_MATCH`. An earnings day: two top picks are labelled `AMC` and `BMO`, and one row below them `BMO`. |
| `parity_diff` | Shadow run, `PARITY_DIFF` with the first difference. |
| `no_ov_run` | After go-live, `PARITY_NO_OV_RUN`. |
| `settled_diff` | The 09:26 re-check found a different rank 4: `SETTLED_DIFF`; the frozen picks stand. |
| `older_schema` | A schema version 1 picks file, no status file. Never edit it once version 1 has shipped. When `SCHEMA_VERSION` goes above 1, it is the older-version sample, and the test reads it with the newer reader. |
| `warming` | 08:50 ET: `status.json` says `WARMING`, and there is no picks file yet. |
| `past_days` | The `shadow` sample's day, plus OV's past days in `history/`: 2026-11-03 from `ov_rebuild` and 2026-11-04 from `ov_live`, 6 rows each, the first 4 marked `top`. |
| `meanings` | The owner's layout "C1 + E1" (2026-11-07): the `shadow` sample's day with 8 rows, the first 4 marked `top`, and 12 metrics with tones, notes and two live columns (`first_minute_range_atr`, `open_vs_pm_range`); every row has a name, levels, and details in groups. Plus one past day in `history/`, 2026-11-04 from `ov_rebuild`. The notes say "Sample note" and the band edges are made up. |
| `preview` | 09:14 ET in the shadow run: `status.json` says the warmup is `DATA_READY` and the preview job `PREVIEWED`; `preview.json` is as of 09:14 ET with 6 rows, the first 4 marked `top`. No picks file yet. |
