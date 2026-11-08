// fork: opening-range breakout studies on the chart (Ash, project thread, 2026-11-08:
// "show the entry stop and exit for ... or1 at 10:00, or1 at 10:30, or2 at 10:00 and
// or2 at 10:30 ... even for past days").
//
// Display only. This works out where the 2026 research backtest would have entered,
// stopped and gone flat on each day of 1-minute bars, so the chart can draw it. It
// places no order and feeds nothing else in the scanner.
//
// The rules are the research backtest's, line for line (project files
// ov-research/opening_drive/: setups.py orb(), or1_time_exits.py trade() and
// manage_t(), engine.py fill() and its constants):
// - The opening range (OR) is the high and low of the first n one-minute bars from
//   09:30 ET (OR1 = the 09:30 bar, OR2 = 09:30 and 09:31). A missing OR bar: no trade.
// - Entry is the first later bar, up to the 10:15 bar, whose high is at least a cent
//   above the OR high (long) or whose low is at least a cent below the OR low (short),
//   at that price or the bar's open if it opened past it. A bar that clears both
//   sides skips the day.
// - The stop is the other side of the OR, but at least 2% of the daily ATR(14) away.
// - On the entry bar, the trade is stopped if the bar closes through the stop, or if
//   it trades through it and is a down bar for a long (an up bar for a short): the
//   backtest's "bar shape" guess at whether the low came before or after the fill.
// - On later bars, an open through the stop exits at that open, a low (high) through
//   it exits at the stop.
// - Otherwise the trade goes flat at the close of the 09:59 bar (flat by 10:00) or
//   the 10:29 bar (flat by 10:30). An entry on or after that bar is skipped.
// - R = profit per share / risk per share.
// The '.ts' import lets `node --test` run this file without a bundler
// (tests/orbStudy.test.ts).
import { etDate, etMinutes } from './time.ts'

export interface MinuteBar { t: string; o: number; h: number; l: number; c: number }
export interface DailyBar { t: string; h: number; l: number; c: number }

export type OrbKey = 'orb1_1000' | 'orb1_1030' | 'orb2_1000' | 'orb2_1030'
export interface OrbVariant { key: OrbKey; n: 1 | 2; flat: '10:00' | '10:30'; label: string }
export const ORB_VARIANTS: readonly OrbVariant[] = [
  { key: 'orb1_1000', n: 1, flat: '10:00', label: 'OR1, flat 10:00' },
  { key: 'orb1_1030', n: 1, flat: '10:30', label: 'OR1, flat 10:30' },
  { key: 'orb2_1000', n: 2, flat: '10:00', label: 'OR2, flat 10:00' },
  { key: 'orb2_1030', n: 2, flat: '10:30', label: 'OR2, flat 10:30' },
]

// engine.py: bar 0 is 09:30 ET; bars run to 11:59 (150 slots).
const OPEN_MIN = 9 * 60 + 30
const SLOTS = 150
export const TICK = 0.01
/** engine.py CUTOFF: the last bar that may enter is 10:15 ET. */
export const CUTOFF_BAR = 45
/** or1_time_exits.py EXITS: the bar whose close is the flat price. */
export const FLAT_BAR: Record<OrbVariant['flat'], number> = { '10:00': 29, '10:30': 59 }
/** engine.py MIN_RISK_ATR: the stop is at least this share of the daily ATR away. */
export const MIN_RISK_ATR = 0.02
/** A bar that should exist but has not arrived this long after its minute ended is
 *  taken as missing (no trade in that minute), as the backtest's NaN bars are. Until
 *  then today's study says it is waiting, so an unfinished day never reads as done. */
const GRACE_MIN = 2

export type SetupStatus = 'waiting_or' | 'no_or' | 'waiting_break' | 'no_break' | 'both_sides' | 'entered'
export interface OrbSetup {
  day: string
  n: 1 | 2
  status: SetupStatus
  orHigh: number | null
  orLow: number | null
  /** 1 long, -1 short, 0 no entry. */
  side: 1 | -1 | 0
  /** Bar index (0 = 09:30 ET) of the entry, or of the bar that broke both sides. */
  entryBar: number
  entryT: string | null
  entry: number | null
}

export type TradeStatus = 'no_trade' | 'late' | 'stopped' | 'flat' | 'open'
export interface OrbTrade {
  variant: OrbVariant
  setup: OrbSetup
  status: TradeStatus
  /** The stop used, after the 2% ATR minimum. */
  stop: number | null
  /** True when the 2% ATR minimum moved the stop past the OR's other side. */
  floored: boolean
  /** False when there was no daily ATR, so the 2% minimum could not be applied. */
  atrKnown: boolean
  exitBar: number
  exitT: string | null
  /** The exit price, or for an open trade the last finished bar's close. */
  exit: number | null
  r: number | null
}

export interface OrbDay {
  day: string
  /** Start times of the day's 1-minute bars by slot (0 = 09:30 ET), null when missing. */
  times: (string | null)[]
  setups: Record<1 | 2, OrbSetup>
  trades: OrbTrade[]
}

/** The clock the study reads: today's ET date and ET minutes since midnight. */
export interface EtNow { day: string; min: number }
export function etNow(now: Date = new Date()): EtNow {
  const iso = now.toISOString()
  return { day: etDate(now), min: etMinutes(iso) + now.getUTCSeconds() / 60 }
}

type Slot = MinuteBar | null

class DayBars {
  readonly slots: Slot[]
  readonly day: string
  private readonly now: EtNow
  constructor(day: string, slots: Slot[], now: EtNow) { this.day = day; this.slots = slots; this.now = now }
  /** The slot's minute has ended (a bar that is still forming is not used). */
  private ended(j: number) { return this.day < this.now.day || this.now.min >= OPEN_MIN + j + 1 }
  /** The slot's bar can no longer arrive: missing now means missing for good. */
  settled(j: number) { return this.day < this.now.day || this.now.min >= OPEN_MIN + j + 1 + GRACE_MIN }
  bar(j: number): Slot { return this.ended(j) ? this.slots[j] : null }
}

function findSetup(d: DayBars, n: 1 | 2): OrbSetup {
  const base: OrbSetup = { day: d.day, n, status: 'no_or', orHigh: null, orLow: null, side: 0, entryBar: -1, entryT: null, entry: null }
  let orh = -Infinity, orl = Infinity
  for (let j = 0; j < n; j++) {
    const b = d.bar(j)
    if (!b) return { ...base, status: d.settled(j) ? 'no_or' : 'waiting_or' }
    orh = Math.max(orh, b.h); orl = Math.min(orl, b.l)
  }
  const ranged = { ...base, orHigh: orh, orLow: orl }
  for (let j = n; j <= CUTOFF_BAR; j++) {
    const b = d.bar(j)
    if (!b) {
      if (!d.settled(j)) return { ...ranged, status: 'waiting_break' }
      continue
    }
    // The same sums as setups.py (orh + TICK), so float edges fall the same way.
    const up = b.h >= orh + TICK, dn = b.l <= orl - TICK
    if (up && dn) return { ...ranged, status: 'both_sides', entryBar: j, entryT: b.t }
    if (up) return { ...ranged, status: 'entered', side: 1, entryBar: j, entryT: b.t, entry: b.o > orh + TICK ? b.o : orh + TICK }
    if (dn) return { ...ranged, status: 'entered', side: -1, entryBar: j, entryT: b.t, entry: b.o < orl - TICK ? b.o : orl - TICK }
  }
  return { ...ranged, status: 'no_break' }
}

function runTrade(d: DayBars, setup: OrbSetup, variant: OrbVariant, atr: number | null): OrbTrade {
  const atrKnown = atr != null && isFinite(atr) && atr > 0
  const none: OrbTrade = { variant, setup, status: 'no_trade', stop: null, floored: false, atrKnown, exitBar: -1, exitT: null, exit: null, r: null }
  if (setup.status !== 'entered' || setup.entry == null || setup.orHigh == null || setup.orLow == null) return none
  const tEnd = FLAT_BAR[variant.flat]
  const e = setup.entryBar
  const s = setup.side
  const entry = setup.entry
  let stop = s > 0 ? setup.orLow : setup.orHigh
  if (e >= tEnd) return { ...none, status: 'late', stop }
  let risk = s * (entry - stop)
  let floored = false
  if (atrKnown && risk < MIN_RISK_ATR * atr!) {
    stop = entry - s * MIN_RISK_ATR * atr!
    risk = MIN_RISK_ATR * atr!
    floored = true
  }
  // "Long space" as in engine.py: a short is worked on mirrored prices
  // (o, h, l, c) -> (-o, -l, -h, -c), so one routine covers both sides.
  const O = (b: MinuteBar) => s * b.o, L = (b: MinuteBar) => (s > 0 ? b.l : -b.h), C = (b: MinuteBar) => s * b.c
  const S = s * stop
  const done = (status: TradeStatus, x: number, j: number): OrbTrade =>
    ({ ...none, status, stop, floored, exitBar: j, exitT: d.slots[j]?.t ?? null, exit: x, r: s * (x - entry) / risk })
  const lastClose = (from: number): [number, number] | null => {
    for (let j = from; j >= e; j--) { const b = d.bar(j); if (b) return [b.c, j] }
    return null
  }
  const eb = d.bar(e)!
  if (C(eb) <= S) return done('stopped', stop, e)
  if (L(eb) <= S && C(eb) <= O(eb)) return done('stopped', stop, e)
  for (let j = e + 1; j <= tEnd; j++) {
    const b = d.bar(j)
    if (!b) {
      if (!d.settled(j)) {
        // Still running today: marked at the last finished bar, never as an exit.
        const lc = lastClose(j - 1)!
        return done('open', lc[0], lc[1])
      }
      continue
    }
    if (O(b) <= S) return done('stopped', b.o, j)
    if (L(b) <= S) return done('stopped', stop, j)
  }
  const lc = lastClose(tEnd)!
  return done('flat', lc[0], lc[1])
}

/** Daily ATR(14), Wilder's smoothing seeded on the first true range, from the daily
 *  bars before `day`: the daily ATR the backtest's 2% minimum was taken of. null with
 *  fewer than 14 bars. */
export function dailyAtr14(daily: readonly DailyBar[], day: string): number | null {
  const prior = daily.filter(b => b.t.slice(0, 10) < day)
  if (prior.length < 14) return null
  let atr = prior[0].h - prior[0].l
  for (let i = 1; i < prior.length; i++) {
    const b = prior[i], pc = prior[i - 1].c
    const tr = Math.max(b.h - b.l, Math.abs(b.h - pc), Math.abs(b.l - pc))
    atr += (tr - atr) / 14
  }
  return atr
}

/** Every ET day in the 1-minute bars, oldest first, with its OR1 and OR2 setups and
 *  the trades of the asked variants. `atrFor` gives the daily ATR(14) as of a day. */
export function orbDays(
  bars: readonly MinuteBar[], variants: readonly OrbVariant[], atrFor: (day: string) => number | null, now: EtNow,
): OrbDay[] {
  const byDay = new Map<string, Slot[]>()
  for (const b of bars) {
    const j = etMinutes(b.t) - OPEN_MIN
    if (j < 0 || j >= SLOTS) continue
    const day = etDate(b.t)
    let slots = byDay.get(day)
    if (!slots) { slots = new Array<Slot>(SLOTS).fill(null); byDay.set(day, slots) }
    slots[j] = b
  }
  return [...byDay.keys()].sort().map(day => {
    const d = new DayBars(day, byDay.get(day)!, now)
    const setups = { 1: findSetup(d, 1), 2: findSetup(d, 2) }
    const atr = variants.length ? atrFor(day) : null
    return {
      day,
      times: d.slots.map(b => b?.t ?? null),
      setups,
      trades: variants.map(v => runTrade(d, setups[v.n], v, atr)),
    }
  })
}

const px = (v: number | null) => (v == null ? '' : v.toFixed(2))
export const fmtR = (r: number | null) => (r == null ? '' : `${r >= 0 ? '+' : '−'}${Math.abs(r).toFixed(2)}R`)

/** One line for the chart's legend: what this variant did on its day. */
export function describeTrade(t: OrbTrade): string {
  const st = t.setup
  switch (st.status) {
    case 'waiting_or': return `waiting for the ${st.n === 1 ? '09:30 bar' : '09:30 and 09:31 bars'}`
    case 'no_or': return 'no trade: an opening-range bar is missing'
    case 'waiting_break': return `range ${px(st.orLow)}-${px(st.orHigh)}, no break yet`
    case 'no_break': return `range ${px(st.orLow)}-${px(st.orHigh)}, no break by 10:15`
    case 'both_sides': return 'no trade: one bar broke both sides of the range'
  }
  const side = st.side > 0 ? 'long' : 'short'
  const head = `${side} ${px(st.entry)}, stop ${px(t.stop)}${t.floored ? ' (2% ATR minimum)' : ''}`
  const noAtr = t.atrKnown ? '' : ', no daily ATR so no 2% minimum'
  switch (t.status) {
    case 'late': return `no trade: entry after ${t.variant.flat}`
    case 'stopped': return `${head}, stopped ${px(t.exit)}, ${fmtR(t.r)}${noAtr}`
    case 'flat': return `${head}, flat ${px(t.exit)}, ${fmtR(t.r)}${noAtr}`
    case 'open': return `${head}, open ${fmtR(t.r)} so far (flat at ${t.variant.flat})${noAtr}`
    default: return ''
  }
}

// ---------------------------------------------------------------- what the chart draws

export type OrbLineKind = 'range' | 'entry' | 'stop'
/** A level from one chart bar to another. Times are the chart's (toSecET) bar times. */
export interface OrbSegment { n: 1 | 2; kind: OrbLineKind; from: number; to: number; price: number }
export interface OrbMarker {
  time: number; n: 1 | 2; kind: 'entry' | 'win' | 'loss'
  position: 'aboveBar' | 'belowBar'; shape: 'arrowUp' | 'arrowDown' | 'circle'; text: string
}

/** A slot's start in the chart's time space: ET wall-clock read as UTC (toSecET). */
const slotSec = (day: string, j: number) => {
  const [y, m, d] = day.split('-').map(Number)
  return Date.UTC(y, m - 1, d, 9, 30 + j) / 1000
}

/** The chart bar holding second `sec`: the last bar starting at or before it, or the
 *  chart's last bar when `sec` is later than every bar. null before the first bar. */
function barAt(chartTimes: readonly number[], sec: number): number | null {
  if (!chartTimes.length || sec < chartTimes[0]) return null
  let lo = 0, hi = chartTimes.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (chartTimes[mid] <= sec) lo = mid; else hi = mid - 1
  }
  return chartTimes[lo]
}

const TRADED: TradeStatus[] = ['stopped', 'flat', 'open']

/** Lines and markers for the selected variants on the chart's bars (sorted start
 *  times, any intraday frame). On a 5-minute chart an entry at 09:41 marks the
 *  09:40 bar. OR1 and OR2 share one entry and stop each when both exits are on. */
export function orbDrawing(days: readonly OrbDay[], chartTimes: readonly number[]): { segments: OrbSegment[]; markers: OrbMarker[] } {
  const segments: OrbSegment[] = [], markers: OrbMarker[] = []
  for (const d of days) {
    for (const n of [1, 2] as const) {
      const trades = d.trades.filter(t => t.variant.n === n)
      if (!trades.length) continue
      const st = d.setups[n]
      if (st.orHigh == null || st.orLow == null) continue
      const traded = trades.filter(t => TRADED.includes(t.status))
      const lastBar = Math.max(CUTOFF_BAR, ...traded.map(t => t.exitBar))
      const from = barAt(chartTimes, slotSec(d.day, 0))
      const to = barAt(chartTimes, slotSec(d.day, lastBar))
      if (from == null || to == null) continue
      segments.push({ n, kind: 'range', from, to, price: st.orHigh }, { n, kind: 'range', from, to, price: st.orLow })
      if (!traded.length || st.entry == null) continue
      const long = st.side > 0
      const at = barAt(chartTimes, slotSec(d.day, st.entryBar))!
      const end = barAt(chartTimes, slotSec(d.day, Math.max(...traded.map(t => t.exitBar))))!
      segments.push({ n, kind: 'entry', from: at, to: end, price: st.entry })
      // The stop is the same for both exits (one entry, one ATR minimum).
      segments.push({ n, kind: 'stop', from: at, to: end, price: traded[0].stop! })
      markers.push({ time: at, n, kind: 'entry', position: long ? 'belowBar' : 'aboveBar', shape: long ? 'arrowUp' : 'arrowDown', text: `OR${n} ${long ? 'long' : 'short'}` })
      const seen = new Set<string>()
      for (const t of traded) {
        const label = t.status === 'stopped' ? 'stop' : t.status === 'open' ? `open, flat ${t.variant.flat}` : t.variant.flat
        const text = `OR${n} ${label} ${fmtR(t.r)}`
        // Stopped before 10:00: both exits are the same exit, drawn once.
        const key = t.status === 'stopped' ? `stop|${t.exitBar}` : `${t.variant.flat}|${t.exitBar}`
        if (seen.has(key)) continue
        seen.add(key)
        markers.push({
          time: barAt(chartTimes, slotSec(d.day, t.exitBar))!, n, kind: (t.r ?? 0) >= 0 ? 'win' : 'loss',
          position: long ? 'aboveBar' : 'belowBar', shape: 'circle', text,
        })
      }
    }
  }
  markers.sort((a, b) => a.time - b.time)
  return { segments, markers }
}
