// fork: OV picks card (ashwinurao/edge-scanner#20). What the card says, as plain data, so
// OvPicksWindow.tsx only lays it out. Every state name comes from ov_picks/CONTRACT.md;
// a state this card does not know is shown as written, never guessed at.
import { etDate, fmtTimeET } from '../../lib/time'
import { DASH } from '../../lib/format'
import type { OvEntry, OvJobEntry, OvPick, OvPicksAnswer, OvPicksDay, OvPicksHistory, OvPicksPreview } from './types'

export type Tone = 'ok' | 'info' | 'warn' | 'bad'
export interface Note { tone: Tone; text: string }
export interface Badge { tone: Tone; text: string; title: string }

// ── times, always ET ────────────────────────────────────────────────────────

const weekday = (ymd: string) =>
  new Date(`${ymd}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' })

/** "Thu 2026-11-05" */
export const dayLabel = (ymd: string) => `${weekday(ymd)} ${ymd}`

/** "today 09:25 ET" or "Mon 2026-11-09 09:25 ET" */
export function whenET(iso: string, today: string, seconds = false): string {
  const d = etDate(iso)
  const t = `${fmtTimeET(iso, seconds)} ET`
  return d === today ? `today ${t}` : `${dayLabel(d)} ${t}`
}

const timeET = (iso: string, seconds = false) => `${fmtTimeET(iso, seconds)} ET`
const sentence = (s: string) => { const t = s.trim(); return t && !/[.!?]$/.test(t) ? `${t}.` : t }
const names = (xs: string[]) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

/** The module's job entries that are about today (ET). */
export function todaysJobs(a: OvPicksAnswer): Record<string, OvJobEntry> {
  const out: Record<string, OvJobEntry> = {}
  for (const [job, e] of Object.entries(a.status?.jobs ?? {})) if (e.trading_date === a.today_et) out[job] = e
  return out
}

// ── route problems: nothing to read ─────────────────────────────────────────

const PROBLEM_TITLE: Record<string, string> = {
  NOT_SET_UP: 'OV picks are not set up',
  NO_READER: 'This scanner has no OV picks reader',
  NO_FOLDER: 'The OV picks folder is not there',
  BAD_DATE: 'That day can\'t be shown',
  ERROR: 'OV picks could not be read',
}

export function routeProblem(a: OvPicksAnswer): { title: string; text: string } | null {
  if (!a.reason_code) return null
  return { title: PROBLEM_TITLE[a.reason_code] ?? `OV picks: ${a.reason_code}`, text: a.reason }
}

// ── the header badges ───────────────────────────────────────────────────────

export function badges(day: OvPicksDay): Badge[] {
  const out: Badge[] = []
  if (day.shadow) out.push({ tone: 'info', text: 'shadow: checking against OV', title: 'The shadow run: these picks are still being proved against OV.' })
  if (day.late) out.push({ tone: 'warn', text: 'LATE', title: 'The first scan ran between 09:25 and 09:35 ET, not on time.' })
  if (day.warmup_state === 'READY') out.push({ tone: 'ok', text: 'READY', title: 'The warmup was ready at the 09:20 ET deadline.' })
  else if (day.warmup_state === 'NOT_READY') out.push({ tone: 'warn', text: 'NOT READY', title: 'The warmup was not ready at the 09:20 ET deadline. The scan ran anyway.' })
  else if (day.warmup_state) out.push({ tone: 'info', text: day.warmup_state, title: 'Warmup state' })
  const settled: Record<string, Badge> = {
    SETTLED_SAME: { tone: 'ok', text: 'SETTLED SAME', title: 'The 09:26 ET re-check made the same top picks and the same ranked list.' },
    SETTLED_DIFF: { tone: 'warn', text: 'SETTLED DIFF', title: 'Bars that came in late for 09:24 would have changed the top picks or the ranked list. The frozen list stands.' },
  }
  if (day.settled_state && settled[day.settled_state]) out.push(settled[day.settled_state])
  return out
}

// ── notes for a day with picks ──────────────────────────────────────────────

function settledNote(day: OvPicksDay, isToday: boolean): Note | null {
  switch (day.settled_state) {
    case 'SETTLED_SAME': return { tone: 'ok', text: '09:26 re-check: same top picks and same ranked list.' }
    case 'SETTLED_DIFF': return { tone: 'warn', text: `09:26 re-check: bars that came in late for 09:24 would have changed the top picks or the ranked list. The frozen list stands. The module says: "${(day.settled_difference ?? '').trim()}"` }
    case 'PENDING': return { tone: 'info', text: isToday ? '09:26 re-check: not run yet.' : '09:26 re-check: no record for this day.' }
    case 'UNREADABLE': return { tone: 'warn', text: '09:26 re-check: its record could not be read. See the warnings.' }
    case null: return null
    default: return { tone: 'info', text: `09:26 re-check: ${day.settled_state}.` }
  }
}

/** The parity line (spec US15, US16). */
export function parityNote(day: OvPicksDay, isToday: boolean): Note | null {
  switch (day.parity_status) {
    case 'PARITY_MATCH': return { tone: 'ok', text: 'OV check: matches OV.' }
    case 'PARITY_DIFF': return { tone: 'bad', text: `OV check: differs from OV. First difference: ${sentence(day.parity_first_difference ?? '')}` }
    case 'PARITY_NO_OV_RUN': return { tone: 'info', text: 'OV check: no OV run to compare.' }
    case 'PENDING': return { tone: 'info', text: isToday ? 'OV check: not run yet. It runs at 10:35 ET.' : 'OV check: no record for this day.' }
    case 'UNREADABLE': return { tone: 'warn', text: 'OV check: its record could not be read. See the warnings.' }
    case null: return null
    default: return { tone: 'info', text: `OV check: ${day.parity_status}.` }
  }
}

export function dayNotes(a: OvPicksAnswer, day: OvPicksDay): Note[] {
  const out: Note[] = []
  const today = a.today_et
  const past = isPastDay(a)
  if (past) {
    out.push({ tone: 'info', text: `A past day: the module's own picks, frozen at 09:25 ET on ${dayLabel(day.trading_date)}. ${PAST_CLICK}` })
  } else if (!a.is_today) {
    const why = sentence(a.market_note) || 'These are not today\'s picks.'
    // The route's note already names the next scan on a holiday; say it once.
    const next = a.next_scan_et && !/next scan/i.test(why) ? ` Next scan: ${whenET(a.next_scan_et, today)}.` : ''
    out.push({ tone: 'info', text: `${why}${next} Showing the last trading day, ${dayLabel(day.trading_date)}.` })
  }
  if (day.late && day.frozen_at) out.push({ tone: 'warn', text: `LATE: the first scan ran at ${timeET(day.frozen_at, true)}, after 09:25 ET. These are not on-time picks.` })
  if (day.warmup_state === 'NOT_READY') out.push({ tone: 'warn', text: `Warmup was not ready at the 09:20 ET deadline, and the picks were made anyway. Why: ${sentence(day.warmup_reason ?? '')}` })
  if (day.earnings_status && day.earnings_status !== 'available') {
    out.push({ tone: 'bad', text: `Earnings data is ${day.earnings_status}, so no row has an earnings label ${past ? 'that day' : 'today'}.` })
  }
  out.push(...listNotes(a, day.picks, past ? 'that day' : 'today'))
  for (const note of [settledNote(day, a.is_today), parityNote(day, a.is_today)]) if (note) out.push(note)
  if (day.reference_promoted.length) out.push({ tone: 'info', text: `New listings this week: ${day.reference_promoted.join(', ')}.` })
  if (day.reference_state === 'REF_WARN') {
    const msg = a.status?.jobs.reference?.message
    out.push({ tone: 'warn', text: `The last sector and listing update had a warning${msg ? `: ${sentence(msg)}` : '.'}` })
  }
  return out
}

/** How many top picks, and the names the scanner does not scan, for a ranked list. */
function listNotes(a: OvPicksAnswer, picks: OvPick[], when: string): Note[] {
  const out: Note[] = []
  const n = picks.length
  const top = picks.filter(p => p.top).length
  if (n === 0) out.push({ tone: 'warn', text: `No name passed OV's gates ${when}, so the list is empty.` })
  else if (top === 0) out.push({ tone: 'info', text: `OV marked no top picks ${when}. The ranked list has ${n} name${n > 1 ? 's' : ''}.` })
  else if (top < MAX_TOP) out.push({ tone: 'info', text: `OV marked only ${top} top pick${top > 1 ? 's' : ''} ${when}.` })
  const outside = picks.filter(p => a.in_universe?.[p.ticker] === false).map(p => p.ticker)
  if (outside.length) {
    const many = outside.length > 1
    // A long list reads worse than a count; each such row has a "no setups" badge.
    const who = outside.length <= NAME_LIST_MAX ? `${names(outside)} ${many ? 'are' : 'is'}` : `${outside.length} names in the list (marked "no setups") are`
    out.push({ tone: 'warn', text: `${who} not in the scanner's universe, so ${many ? 'they get' : 'it gets'} no live bars and no setups. Charts still work. The universe is set when the scanner starts.` })
  }
  return out
}

// ── notes for a preview, before the picks freeze ────────────────────────────

/** A preview older than this, against the route's clock, says so: one comes each minute. */
const PREVIEW_STALE_MS = 2 * 60_000

export function previewBadges(p: OvPicksPreview): Badge[] {
  const out: Badge[] = [{ tone: 'warn', text: 'PREVIEW: not frozen', title: 'OV\'s ranked list as of one minute before 09:25 ET. It changes each minute; the picks freeze at 09:25 ET.' }]
  if (p.shadow) out.push({ tone: 'info', text: 'shadow: checking against OV', title: 'The shadow run: these previews are still being proved against OV.' })
  return out
}

export function previewNotes(a: OvPicksAnswer, p: OvPicksPreview): Note[] {
  const out: Note[] = []
  const at = p.as_of ? timeET(p.as_of) : 'an unknown minute'
  out.push({ tone: 'info', text: `Preview as of ${at}: OV's ranked list from the bars before that minute. A new one comes each minute until the picks freeze at 09:25 ET. The "OV picks" watchlist and the notice wait for the frozen picks.` })
  const job = todaysJobs(a).preview
  const age = p.as_of ? Date.parse(a.now_et) - Date.parse(p.as_of) : 0
  if (age > PREVIEW_STALE_MS) {
    const why = job && job.state !== 'PREVIEWED' && job.message ? ` The module says: ${sentence(job.message)}` : ''
    out.push({ tone: 'warn', text: `No newer preview has come since ${at}.${why}` })
  }
  const w = todaysJobs(a).warmup
  if (w && WARMUP_LINE[w.state]) out.push(WARMUP_LINE[w.state](w))
  if (p.earnings_status && p.earnings_status !== 'available') {
    out.push({ tone: 'bad', text: `Earnings data is ${p.earnings_status}, so no row has an earnings label.` })
  }
  out.push(...listNotes(a, p.picks, `as of ${at}`))
  return out
}

const PREVIEW_LINE: Record<string, (e: OvJobEntry) => Note> = {
  RUNNING: e => ({ tone: 'info', text: `Previews: getting ready since ${timeET(e.at)}. The first one is as of 09:00 ET.` }),
  FAILED: e => ({ tone: 'warn', text: `Previews: ${sentence(e.message)}` }),
  REFUSED: e => ({ tone: 'warn', text: `Previews: ${sentence(e.message)}` }),
  SKIPPED: e => ({ tone: 'info', text: `Previews: ${sentence(e.message)}` }),
}

// ── notes for a day without picks ───────────────────────────────────────────

const WARMUP_LINE: Record<string, (e: OvJobEntry) => Note> = {
  WARMING: e => ({ tone: 'info', text: `Warming up since ${timeET(e.at)}${e.message ? `: ${sentence(e.message)}` : '.'}` }),
  DATA_READY: e => ({ tone: 'ok', text: `Data ready at ${timeET(e.at)}. The readiness check is at 09:20 ET.` }),
  READY: () => ({ tone: 'ok', text: 'Warmup ready at the 09:20 ET deadline. The picks freeze at 09:25 ET.' }),
  NOT_READY: e => ({ tone: 'warn', text: `Warmup was not ready at the 09:20 ET deadline. The scan still runs at 09:25 ET. Why: ${sentence(e.message)}` }),
  FAILED: e => ({ tone: 'bad', text: `The warmup failed: ${sentence(e.message)}` }),
  RUNNING: e => ({ tone: 'info', text: `Warmup running since ${timeET(e.at)}.` }),
}

export function noPicks(a: OvPicksAnswer, day: OvPicksDay): { title: string; notes: Note[] } {
  const jobs = todaysJobs(a)
  const scan = jobs.scan
  const notes: Note[] = []
  let title = 'No picks yet'
  if (day.reason_code === 'UNREADABLE' || day.reason_code === 'INVALID') {
    title = 'The picks file can\'t be used'
    notes.push({ tone: 'bad', text: day.reason })
  } else if (a.market_closed === true) {
    title = 'Market closed'
    notes.push({ tone: 'info', text: sentence(a.market_note) })
    notes.push({ tone: 'info', text: 'No saved picks to show yet.' })
  } else if (scan?.state === 'SKIPPED') {
    // A holiday and a start after 09:35 ET both skip the scan; the status file can't tell
    // them apart, so the card says both (spec: refuse rather than guess).
    title = 'No scan today'
    notes.push({ tone: 'warn', text: `Today's scan was skipped${scan.message ? `: ${sentence(scan.message)}` : '.'} A market holiday and a start after 09:35 ET both skip it; this card can't tell which.` })
  } else if (scan?.state === 'REFUSED') {
    title = 'The scan refused to run'
    notes.push({ tone: 'bad', text: sentence(scan.message) })
  } else if (scan?.state === 'FAILED') {
    title = 'The scan failed'
    notes.push({ tone: 'bad', text: sentence(scan.message) })
  } else {
    const w = jobs.warmup
    if (w) notes.push(WARMUP_LINE[w.state]?.(w) ?? { tone: 'info', text: `Warmup: ${w.state}.` })
    else if (a.market_closed === null && a.market_note) notes.push({ tone: 'info', text: sentence(a.market_note) })
    // Inside the preview window without a preview: the module's reason when it gave one,
    // else the reader's (the file is not there yet, or can't be used).
    const pv = jobs.preview
    if (a.preview && !a.preview.has_preview) {
      notes.push(pv && PREVIEW_LINE[pv.state] ? PREVIEW_LINE[pv.state](pv)
        : { tone: a.preview.reason_code === 'MISSING' ? 'info' : 'warn', text: a.preview.reason })
    }
    notes.push({ tone: 'info', text: day.reason })
  }
  if (a.next_scan_et) notes.push({ tone: 'info', text: `Next scan: ${whenET(a.next_scan_et, a.today_et)}.` })
  if (a.status && !a.status.has_status && a.status.reason) notes.push({ tone: 'info', text: a.status.reason })
  return { title, notes }
}

/** Every warning the route, the day, the history file and the status file carry, once each. */
export function allWarnings(a: OvPicksAnswer): string[] {
  return Array.from(new Set([...(a.warnings ?? []), ...(a.day?.warnings ?? []), ...(a.preview?.warnings ?? []),
    ...(a.history?.warnings ?? []), ...(a.status?.warnings ?? [])]))
}

// ── past days: the calendar (Ash, 2026-11-07) ───────────────────────────────

/** The answer is for a day the trader picked before today, not today's. */
export const isPastDay = (a: OvPicksAnswer) => !!a.asked_date && !a.is_today

const PAST_CLICK = 'A click on a row shows that day in the linked charts and news.'

/** Where a history file's list comes from (ov_picks/CONTRACT.md, "History file"). */
const HISTORY_SOURCE: Record<string, { text: string; title: string; note: string }> = {
  ov_rebuild: {
    // Not just 2026 since the 2020-2025 rebuild (Ash, project thread, 2026-11-11 00:34 UTC).
    text: 'OV rebuild',
    title: 'OV\'s current code and settings, run again on clean bars for this day. It can differ from what OV showed live that morning, or from OV\'s stored backtest.',
    note: 'OV rebuild: OV\'s current code and settings, run again on clean bars for this day, before this module ran. It can differ from what OV showed live that morning, or from OV\'s stored backtest.',
  },
  ov_live: {
    text: 'OV live scan',
    title: 'OV\'s own live 09:25 ET scan that morning.',
    note: 'OV\'s own live 09:25 ET scan that morning, before this module ran.',
  },
}

export function historyBadges(h: OvPicksHistory): Badge[] {
  const src = h.source ? HISTORY_SOURCE[h.source] : undefined
  return [
    { tone: 'info', text: 'past day', title: 'Not today: a day picked in the calendar.' },
    src ? { tone: 'info', text: src.text, title: src.title } : { tone: 'warn', text: h.source || 'source unknown', title: 'Where this list comes from, as the file says.' },
  ]
}

export function historyNotes(a: OvPicksAnswer, h: OvPicksHistory): Note[] {
  const out: Note[] = []
  const src = h.source ? HISTORY_SOURCE[h.source] : undefined
  out.push({ tone: 'info', text: `${src ? src.note : `From "${h.source ?? ''}", as the file says.`} ${PAST_CLICK}` })
  if (h.earnings_status && h.earnings_status !== 'available') {
    out.push({ tone: 'bad', text: `Earnings data was ${h.earnings_status} that day, so no row has an earnings label.` })
  }
  out.push(...listNotes(a, h.picks, 'that day'))
  return out
}

/** A past day with neither the module's picks nor OV's history. */
export function noPastDay(a: OvPicksAnswer, day: OvPicksDay): { title: string; notes: Note[] } {
  const notes: Note[] = []
  const unusable = (code: string | undefined) => code === 'UNREADABLE' || code === 'INVALID'
  if (unusable(day.reason_code)) notes.push({ tone: 'bad', text: day.reason })
  if (a.history && unusable(a.history.reason_code)) notes.push({ tone: 'bad', text: a.history.reason })
  const bad = notes.length > 0
  if (!bad) {
    notes.push({ tone: 'info', text: `Nothing is saved for ${dayLabel(day.trading_date)}: no picks from this module and no OV list. It may be a market holiday, or a day before the saved history starts.` })
    notes.push({ tone: 'info', text: 'Use ‹ and › to step through the days that have a list.' })
  }
  return { title: bad ? 'That day\'s file can\'t be used' : 'Nothing saved for this day', notes }
}

// ── the ranked list ─────────────────────────────────────────────────────────

/** The contract's limit on top picks (ov_picks/contract.py MAX_PICKS). */
export const MAX_TOP = 4
/** Up to this many names, a note lists them; above it, it gives a count. */
const NAME_LIST_MAX = 5

/** The day's top picks in rank order: the rows the watchlist and the notice carry. */
export function topPicks(day: OvPicksDay): OvPick[] {
  return day.picks.filter(p => p.top).sort((x, y) => x.rank - y.rank)
}

/** The value of the column `label` in a row's metrics, or undefined when it has none. */
export function metricValue(p: OvPick, label: string): OvEntry['value'] | undefined {
  return p.metrics.find(m => m.label === label)?.value
}

/** A value as the card shows it. The labels and units are the module's; the card adds
 *  none and rounds only for reading. In a column of whole numbers (`whole`), thousands
 *  separators; otherwise 2 decimals, so 86 and 85.75 line up. Up to 4 decimals only for a
 *  value below 1 in a column of such values (`small`), so 0.0012 still reads, while
 *  0.27 lines up with 1.20 in a column that has both. */
export function fmtValue(v: OvEntry['value'] | undefined, whole = Number.isInteger(v),
  small = typeof v === 'number' && Math.abs(v) < 1): string {
  if (v === null || v === undefined || v === '') return DASH
  if (typeof v === 'string') return v
  if (!Number.isFinite(v)) return DASH
  if (whole && Number.isInteger(v)) return v.toLocaleString('en-US')
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: small && Math.abs(v) < 1 ? 4 : 2 })
}

/** Whether every number in the column `label` is below 1, so its values keep up to 4 decimals. */
export function smallColumn(picks: OvPick[], label: string): boolean {
  return picks.every(p => { const v = metricValue(p, label); return typeof v !== 'number' || Math.abs(v) < 1 })
}

/** Whether every number in the column `label` is whole (true for a column with none). */
export function wholeColumn(picks: OvPick[], label: string): boolean {
  return picks.every(p => { const v = metricValue(p, label); return typeof v !== 'number' || Number.isInteger(v) })
}
