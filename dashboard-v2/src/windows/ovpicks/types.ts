// fork: OV picks card (ashwinurao/edge-scanner#20). The window's config and the answer of
// GET /api/v2/ov-picks (scanner/ov_picks_route.py). `day` and `status` are the contract
// readers' views (ov_picks/CONTRACT.md, "What read_day() returns" and "Status file").
import type { WindowBase } from '../../types'

/** Display-only: no settings, no order buttons. */
export interface OvPicksConfig extends WindowBase { type: 'ovpicks' }

export interface OvPick {
  rank: number
  ticker: string
  score: number
  earnings_label: 'AMC' | 'BMO' | null
  traded: boolean
  weight: number
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
  /** '' when the folder was read; NOT_SET_UP | NO_READER | NO_FOLDER | ERROR otherwise */
  reason_code: string
  reason: string
  now_et: string
  today_et: string
  trading_date: string | null
  is_today: boolean
  /** null: the route can't tell (it has no holiday calendar) */
  market_closed: boolean | null
  market_note: string
  next_scan_at: string | null
  next_scan_et: string | null
  day: OvPicksDay | null
  status: OvPicksStatus | null
  /** ticker -> scanned by this scanner; null when there are no picks or it can't tell */
  in_universe: Record<string, boolean> | null
  warnings: string[]
}
