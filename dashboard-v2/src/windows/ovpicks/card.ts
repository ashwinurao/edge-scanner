// fork: what a value means in the OV picks window (Ash, 2026-11-07, layout "C1 + E1").
// The module decides each cell's tone and note, groups the details and sends the row's
// price levels (ov_picks/CONTRACT.md, "What a value means"); the scanner fills the live
// columns after the open (scanner/ov_picks_live.py). Nothing here reads meaning into a
// label or a number: it lays out what the module and the scanner sent.
import type { OvBar, OvEntry, OvLevels, OvPick } from './types'
import { fmtValue } from './view'

export const KIND_RANGE_ATR = 'first_minute_range_atr'
export const KIND_OPEN_VS_PM = 'open_vs_pm_range'
export const OTHER_GROUP = 'Other fields'

const TONES = new Set(['good', 'caution', 'bad'])

/** The cell class for a tone; none for no tone or one this window does not know. */
export function toneClass(tone: OvEntry['tone']): string {
  return tone && TONES.has(tone) ? `ovp-tone-${tone}` : ''
}

export function metricOf(p: OvPick, label: string): OvEntry | undefined {
  return p.metrics.find(m => m.label === label)
}

/** The live kind of the column `label`, from any row; null for a column the module fills. */
export function liveKind(picks: OvPick[], label: string): string | null {
  for (const p of picks) {
    const k = metricOf(p, label)?.live?.kind
    if (k) return k
  }
  return null
}

/** A cell's text. An open against the pre-market range gets an arrow, so it reads at a glance. */
export function fmtCell(m: OvEntry | undefined, whole: boolean): string {
  const v = m?.value
  if (m?.live?.kind === KIND_OPEN_VS_PM && typeof v === 'string') {
    return v === 'above' ? '▲ above' : v === 'below' ? '▼ below' : v
  }
  return fmtValue(v, whole)
}

/** Whether a row carries anything for the trade card; without it the plain list of fields shows. */
export function hasMeanings(p: OvPick): boolean {
  return Boolean(p.levels && Object.keys(p.levels).length)
    || p.metrics.some(m => m.tone || m.note || m.live)
    || p.details.some(d => d.group)
}

export interface DetailGroup { name: string; entries: OvEntry[] }

/** The details by group, in the order the module wrote them; entries without a group last. */
export function groupDetails(details: OvEntry[]): DetailGroup[] {
  const groups = new Map<string, OvEntry[]>()
  for (const d of details) {
    const name = d.group || OTHER_GROUP
    if (!groups.has(name)) groups.set(name, [])
    groups.get(name)!.push(d)
  }
  const out = [...groups].map(([name, entries]) => ({ name, entries }))
  return [...out.filter(g => g.name !== OTHER_GROUP), ...out.filter(g => g.name === OTHER_GROUP)]
}

/** A price, or a price difference, as the card shows it: 2 decimals, 4 for a stock under $1
 *  (`ref` is the stock's price when `v` is a difference). */
export function fmtPrice(v: number, ref = v): string {
  const digits = Math.abs(ref) < 1 ? 4 : 2
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** For a filled live tile, the prices behind it, before the module's note. */
export function liveDetail(m: OvEntry, levels: OvLevels | undefined, bar: OvBar | null | undefined): string {
  if (!bar || m.value === null || m.value === undefined) return ''
  if (m.live?.kind === KIND_RANGE_ATR) {
    return `$${fmtPrice(bar.high - bar.low, bar.open)} wide, ${fmtPrice(bar.low)} to ${fmtPrice(bar.high)}.`
  }
  if (m.live?.kind === KIND_OPEN_VS_PM && levels?.pm_high !== undefined && levels?.pm_low !== undefined) {
    if (m.value === 'above') return `Opened at ${fmtPrice(bar.open)}, ${fmtPrice(bar.open - levels.pm_high, bar.open)} over the PM high.`
    if (m.value === 'below') return `Opened at ${fmtPrice(bar.open)}, ${fmtPrice(levels.pm_low - bar.open, bar.open)} under the PM low.`
    return `Opened at ${fmtPrice(bar.open)}, inside ${fmtPrice(levels.pm_low)} to ${fmtPrice(levels.pm_high)}.`
  }
  return ''
}


// ---------------------------------------------------------------- the level ladder

export type MarkKind = 'pm' | 'pd' | 'last' | 'fm' | 'open'

export interface Mark {
  key: string
  label: string
  price: number
  kind: MarkKind
  /** where the price is, and where its label goes (labels never overlap) */
  y: number
  labelY: number
}

export interface Span { from: number; to: number }

export interface Ladder {
  height: number
  marks: Mark[]
  pm: Span | null
  pd: Span | null
  fm: Span | null
  /** the 1 ATR bracket */
  atr: (Span & { dollars: number }) | null
}

export const LADDER = { top: 10, bottom: 12, rowGap: 14, minHeight: 200 }

/** Where each level goes on a vertical price scale, highest at the top. Null with fewer
 *  than two levels. The scale spans at least one ATR, so the bracket always fits. */
export function ladder(levels: OvLevels | undefined, bar: OvBar | null | undefined): Ladder | null {
  const lv = levels ?? {}
  const raw: Array<[string, string, number | undefined, MarkKind]> = [
    ['pm_high', 'PM high', lv.pm_high, 'pm'],
    ['prior_high', 'Prior high', lv.prior_high, 'pd'],
    ['last', 'Last pre-market', lv.last, 'last'],
    ['pm_low', 'PM low', lv.pm_low, 'pm'],
    ['fm_high', '1st min high', bar?.high, 'fm'],
    ['open', 'Open 09:30', bar?.open, 'open'],
    ['fm_low', '1st min low', bar?.low, 'fm'],
    ['prior_close', 'Prior close', lv.prior_close, 'pd'],
    ['prior_low', 'Prior low', lv.prior_low, 'pd'],
  ]
  const present = raw.filter((r): r is [string, string, number, MarkKind] => typeof r[2] === 'number' && Number.isFinite(r[2]))
  if (present.length < 2) return null
  let hi = Math.max(...present.map(r => r[2]))
  let lo = Math.min(...present.map(r => r[2]))
  const atr = typeof lv.atr === 'number' && lv.atr > 0 ? lv.atr : null
  const want = Math.max(atr ? atr * 1.1 : 0, (hi - lo) * 1.08, hi * 0.002)
  const mid = (hi + lo) / 2
  if (hi - lo < want) { hi = mid + want / 2; lo = mid - want / 2 }
  const height = Math.max(LADDER.minHeight, present.length * LADDER.rowGap + LADDER.top + LADDER.bottom + 20)
  const plot = height - LADDER.top - LADDER.bottom
  const y = (price: number) => LADDER.top + ((hi - price) / (hi - lo)) * plot
  const marks: Mark[] = present
    .map(([key, label, price, kind]) => ({ key, label, price, kind, y: y(price), labelY: y(price) }))
    .sort((a, b) => a.y - b.y)
  // Push labels apart: down from the top, then back up from the bottom edge.
  for (let i = 1; i < marks.length; i++) marks[i].labelY = Math.max(marks[i].labelY, marks[i - 1].labelY + LADDER.rowGap)
  const last = height - LADDER.bottom
  if (marks[marks.length - 1].labelY > last) marks[marks.length - 1].labelY = last
  for (let i = marks.length - 2; i >= 0; i--) marks[i].labelY = Math.min(marks[i].labelY, marks[i + 1].labelY - LADDER.rowGap)
  const span = (a: number | undefined, b: number | undefined): Span | null =>
    typeof a === 'number' && typeof b === 'number' ? { from: y(Math.max(a, b)), to: y(Math.min(a, b)) } : null
  const atrSpan = atr ? { from: y(mid + atr / 2), to: y(mid - atr / 2), dollars: atr } : null
  return { height, marks, pm: span(lv.pm_high, lv.pm_low), pd: span(lv.prior_high, lv.prior_low), fm: span(bar?.high, bar?.low), atr: atrSpan }
}
