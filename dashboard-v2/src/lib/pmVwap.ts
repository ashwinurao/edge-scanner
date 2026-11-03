import type { Bar } from '../types'
import { etDate, isPremarket, toSecET } from './time'

/** A line point, or a gap (no value) that keeps the line from joining one morning to the next. */
export type GapPt = { time: number; value?: number }

/**
 * Premarket VWAP: volume-weighted (h+l+c)/3 over 04:00-09:29 ET, restarting each day.
 * Display only, drawn next to the session VWAP: the engine's VWAP and every VWAP trigger
 * start at 09:30 (scanner/state.py), so this changes no alert. Asked for by Ash on
 * 2026-11-03 ("is there no vwap premarket?").
 */
export function calcPremarketVWAP(bars: Bar[]): GapPt[] {
  let tpv = 0, vol = 0, day = ''
  return bars.map(b => {
    const time = toSecET(b.t)
    if (!isPremarket(b.t)) return { time }
    const d = etDate(b.t)
    if (d !== day) { day = d; tpv = 0; vol = 0 }
    tpv += ((b.h + b.l + b.c) / 3) * b.v
    vol += b.v
    return { time, value: vol > 0 ? tpv / vol : b.c }
  })
}
