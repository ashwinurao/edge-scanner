// fork: OV picks card (ashwinurao/edge-scanner#20). The window's config and the answer of
// GET /api/v2/ov-picks (scanner/ov_picks_route.py). `day`, `preview` and `status` are the
// contract readers' views (ov_picks/CONTRACT.md, "What read_day() returns", "Preview file"
// and "Status file").
import type { WindowBase } from '../../types'

/** Display-only: no settings, no order buttons. */
export interface OvPicksConfig extends WindowBase { type: 'ovpicks' }

/** How the window colours a value (ov_picks/CONTRACT.md, "What a value means"). The module
 *  decides; another value from a newer module is shown without a colour. */
export type OvTone = 'good' | 'caution' | 'bad'

/** A band of a live column: the first band its value falls in gives the cell its tone and note. */
export interface OvBand {
  min?: number
  max?: number
  is?: string
  tone?: OvTone | string
  note?: string
}

/** A column the scanner fills after the open, from the day's 09:30 ET minute bar
 *  (scanner/ov_picks_live.py): first_minute_range_atr or open_vs_pm_range. */
export interface OvLive {
  kind: string
  bands: OvBand[]
}

/** One label and value. The module chooses the labels; the card shows them as written. */
export interface OvEntry {
  label: string
  /** a finite number, text, or null when there is no value */
  value: number | string | null
  /** metrics: the cell's colour, as the module (or, for a live column, its band) says */
  tone?: OvTone | string | null
  /** metrics: one line on what the value means; for a blank live cell, why it is blank */
  note?: string | null
  /** metrics: a column the scanner fills after the open */
  live?: OvLive
  /** details: the section of the details card */
  group?: string
}

/** A row's price levels, in that day's prices (ov_picks/CONTRACT.md, "What a value means"). */
export interface OvLevels {
  atr?: number
  prior_close?: number
  prior_high?: number
  prior_low?: number
  pm_high?: number
  pm_low?: number
  /** the last pre-market price the module saw */
  last?: number
}

/** The 09:30 ET minute bar, or the minute so far, added by the scanner to rows with a live column. */
export interface OvBar {
  open: number
  high: number
  low: number
  close: number
}

/** One part of a row's score, as the module writes it (ov_picks/CONTRACT.md, "Score parts
 *  and rank reason"). The window draws it and works nothing out. */
export interface OvPart {
  label: string
  /** what the part scored, as the window shows it */
  value: number | string | null
  /** the points the part added to the score */
  points: number
  /** the most points the part can add */
  possible: number
  /** where the part starts scoring, as the window shows values (no points at or below it) */
  zero_at?: string
  /** where the part scores full points, as the window shows values */
  full_at?: string
  /** the points the part gives against the next row: positive puts this row ahead */
  vs_next?: number
}

/** Why a row ranks above the next one, as the module writes it. */
export interface OvRankReason {
  text: string
  /** the label of the part, or of the tie-break, that puts the row above the next one */
  decides: string | null
  tie_break?: { label: string; value: number | string | null } | null
}

/** One row of the day's ranked list (ov_picks/CONTRACT.md, "Each row of picks"). */
export interface OvPick {
  rank: number
  ticker: string
  score: number
  /** the module's status text for the row, verbatim */
  status: string
  /** one of the day's top picks: they come first, at most 4 */
  top: boolean
  earnings_label: 'AMC' | 'BMO' | null
  /** the table's columns for this row, labelled as `metric_labels` */
  metrics: OvEntry[]
  /** the full row, shown when the row is opened */
  details: OvEntry[]
  /** the company's name, when the module sends it */
  name?: string
  /** the row's price levels, for the details card's ladder */
  levels?: OvLevels
  /** the 09:30 ET bar, or the minute so far while it forms: null until there is a trade;
   *  absent on rows without a live column */
  first_minute?: OvBar | null
  /** while the minute forms: the ET time ("09:30:41") the live cells run to; absent once
   *  they come from the 09:30 bar (scanner/ov_picks_live.py) */
  first_minute_as_of?: string
  /** what made up the score, when the module sends it */
  score_parts?: OvPart[]
  /** why the row ranks above the next one; absent on the last row */
  rank_reason?: OvRankReason
}

export interface OvPicksDay {
  has_picks: boolean
  /** '' with picks; MISSING | UNREADABLE | INVALID without */
  reason_code: string
  reason: string
  warnings: string[]
  schema_version: number | null
  trading_date: string
  /** UTC, YYYY-MM-DDTHH:MM:SSZ */
  frozen_at: string | null
  warmup_state: string | null
  warmup_reason: string | null
  late: boolean | null
  shadow: boolean | null
  /** OV's verbatim status: available | unavailable | missing_prior_session_context */
  earnings_status: string | null
  /** the table's column labels, in order; every row's metrics have these labels */
  metric_labels: string[]
  /** the ranked list, in rank order */
  picks: OvPick[]
  reference_state: string | null
  reference_promoted: string[]
  /** SETTLED_SAME | SETTLED_DIFF | PENDING | UNREADABLE, null without picks */
  settled_state: string | null
  settled_difference: string | null
  settled_at: string | null
  /** PARITY_MATCH | PARITY_DIFF | PARITY_NO_OV_RUN | PENDING | UNREADABLE, null without picks */
  parity_status: string | null
  parity_first_difference: string | null
  parity_at: string | null
}

/** The ranked list as of one minute from 09:00 to 09:24 ET, before the picks freeze
 *  (ov_picks/CONTRACT.md, "Preview file"; Ash, 2026-11-06). Not frozen: it changes each minute. */
export interface OvPicksPreview {
  has_preview: boolean
  /** '' with a preview; MISSING | UNREADABLE | INVALID without */
  reason_code: string
  reason: string
  warnings: string[]
  schema_version: number | null
  trading_date: string
  /** UTC, a whole minute: the bars before this minute */
  as_of: string | null
  /** UTC: when the module wrote it */
  previewed_at: string | null
  shadow: boolean | null
  earnings_status: string | null
  metric_labels: string[]
  /** the ranked list as of that minute, in rank order; the same rows as the picks */
  picks: OvPick[]
}

/** OV's own ranked list for a past day before the module ran (ov_picks/CONTRACT.md,
 *  "History file"; Ash, 2026-11-07). Rows as in the picks file. */
export interface OvPicksHistory {
  has_history: boolean
  /** '' with a history file; MISSING | UNREADABLE | INVALID without */
  reason_code: string
  reason: string
  warnings: string[]
  schema_version: number | null
  trading_date: string
  /** ov_rebuild: OV's 2026 rebuild; ov_live: OV's own live 09:25 ET scan. Another value is shown as written. */
  source: string | null
  /** UTC: when the module exported it */
  exported_at: string | null
  earnings_status: string | null
  metric_labels: string[]
  picks: OvPick[]
}

/** GET /api/v2/ov-picks/days: the days the calendar can show. */
export interface OvPicksDays {
  set_up: boolean
  reason_code: string
  reason: string
  today_et: string
  /** YYYY-MM-DD, oldest first: days with the module's picks or OV's history, up to today */
  days: string[]
}

export interface OvJobEntry {
  state: string
  at: string
  trading_date: string | null
  message: string
  next_at: string | null
}

export interface OvPicksStatus {
  has_status: boolean
  reason_code: string
  reason: string
  warnings: string[]
  jobs: Record<string, OvJobEntry>
}

export interface OvPicksAnswer {
  set_up: boolean
  /** '' when the folder was read; NOT_SET_UP | NO_READER | NO_FOLDER | BAD_DATE | ERROR otherwise */
  reason_code: string
  reason: string
  now_et: string
  today_et: string
  /** the ?date this answers, as sent; null for today's answer */
  asked_date: string | null
  trading_date: string | null
  is_today: boolean
  /** null: the route can't tell (it has no holiday calendar) */
  market_closed: boolean | null
  market_note: string
  next_scan_at: string | null
  next_scan_et: string | null
  day: OvPicksDay | null
  status: OvPicksStatus | null
  /** today's preview, from 09:00 to 09:25 ET before the picks freeze; null otherwise */
  preview: OvPicksPreview | null
  /** a past day without the module's picks: OV's own list for it, or why there is none;
   *  null for today and for a past day with the module's picks */
  history: OvPicksHistory | null
  /** ticker of each row (the preview's, without picks) -> scanned by this scanner; null when
   *  there are no rows, it can't tell, or the day is a past day */
  in_universe: Record<string, boolean> | null
  warnings: string[]
  /** while today's first-minute columns are about to fill or still forming: how often to ask
   *  again, in seconds; null otherwise (scanner/ov_picks_route.py) */
  refresh_seconds: number | null
}
