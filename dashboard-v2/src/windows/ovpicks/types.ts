// fork: OV picks card (ashwinurao/edge-scanner#20). The window's config and the answer of
// GET /api/v2/ov-picks (scanner/ov_picks_route.py). `day`, `preview` and `status` are the
// contract readers' views (ov_picks/CONTRACT.md, "What read_day() returns", "Preview file"
// and "Status file").
import type { WindowBase } from '../../types'

/** Display-only: no settings, no order buttons. */
export interface OvPicksConfig extends WindowBase { type: 'ovpicks' }

/** One label and value. The module chooses the labels; the card shows them as written. */
export interface OvEntry {
  label: string
  /** a finite number, text, or null when there is no value */
  value: number | string | null
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
}
