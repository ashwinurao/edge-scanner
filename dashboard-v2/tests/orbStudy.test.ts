// Run: npm test (node's own test runner; no extra dependencies).
// The chart's opening-range breakout studies (src/lib/orbStudy.ts; Ash, 2026-11-08).
// Hand-built 1-minute bars with hand-worked prices and R. The first test is the
// research backtest's own hand check (ov-research/opening_drive/or1_time_exits.py).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ORB_VARIANTS, dailyAtr14, describeTrade, orbDays, orbDrawing, type MinuteBar, type OrbTrade } from '../src/lib/orbStudy.ts'

// 2026-11-06 is EST: 09:30 ET = 14:30 UTC. Bar j starts j minutes after 09:30.
const at = (j: number, day = '2026-11-06', openUtcHour = 14) => {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d, openUtcHour, 30 + j)).toISOString()
}
const PAST = { day: '2026-11-08', min: 12 * 60 }     // a later day: every bar is final
const v = (key: string) => ORB_VARIANTS.find(x => x.key === key)!

/** 150 flat bars at `p` (09:30-11:59), then `edit` sets single bars [o, h, l, c]. */
function day(p: number, edit: Record<number, [number, number, number, number]>, opts: { drop?: number[]; day?: string; utcOpen?: number } = {}): MinuteBar[] {
  const out: MinuteBar[] = []
  for (let j = 0; j < 150; j++) {
    if (opts.drop?.includes(j)) continue
    const [o, h, l, c] = edit[j] ?? [p, p, p, p]
    out.push({ t: at(j, opts.day, opts.utcOpen), o, h, l, c })
  }
  return out
}
const run = (bars: MinuteBar[], keys: string[], atr: number | null = 10, now = PAST) =>
  orbDays(bars, keys.map(v), () => atr, now)[0]
const near = (a: number | null, b: number) => assert.ok(a != null && Math.abs(a - b) < 1e-9, `${a} != ${b}`)

// OR1 = 100.00-101.00; the 09:31 bar breaks up.
const BASE = { 0: [100.5, 101.0, 100.0, 100.8], 1: [100.8, 101.3, 100.7, 101.2] } as Record<number, [number, number, number, number]>

test('the backtest hand check: long 101.01, stop 100.00, flat at the 09:59 close', () => {
  const d = run(day(101.5, BASE), ['orb1_1000', 'orb1_1030'])
  const [t10, t1030] = d.trades
  assert.equal(d.setups[1].status, 'entered')
  assert.equal(d.setups[1].entryBar, 1)
  near(d.setups[1].entry, 101.01)
  assert.equal(t10.status, 'flat')
  assert.equal(t10.exitBar, 29)                 // the 09:59 bar's close is the 10:00 exit
  near(t10.stop, 100.0)
  near(t10.r, 0.49 / 1.01)
  assert.equal(t1030.exitBar, 59)
  near(t1030.r, 0.49 / 1.01)
})

test('a low through the stop exits at the stop, -1R', () => {
  const d = run(day(101.5, { ...BASE, 25: [101.5, 101.5, 99.5, 101.5] }), ['orb1_1000'])
  assert.equal(d.trades[0].status, 'stopped')
  assert.equal(d.trades[0].exitBar, 25)
  near(d.trades[0].exit, 100.0)
  near(d.trades[0].r, -1)
})

test('a high exactly at the OR high does NOT enter; one cent above does', () => {
  const touch = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8], 1: [100.8, 101.0, 100.7, 100.9] }), ['orb1_1000'])
  assert.equal(touch.setups[1].status, 'no_break')
  assert.equal(touch.trades[0].status, 'no_trade')
  const cent = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8], 1: [100.8, 101.01, 100.7, 100.9] }), ['orb1_1000'])
  assert.equal(cent.setups[1].status, 'entered')
  near(cent.setups[1].entry, 101.01)
})

test('a bar that opens past the trigger fills at its open', () => {
  const d = run(day(102, { ...BASE, 1: [101.4, 101.6, 101.3, 101.5] }), ['orb1_1000'])
  near(d.setups[1].entry, 101.4)
})

test('a bar that clears both sides skips the day', () => {
  const d = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8], 1: [100.8, 101.5, 99.5, 100.5] }), ['orb1_1000'])
  assert.equal(d.setups[1].status, 'both_sides')
  assert.equal(d.trades[0].status, 'no_trade')
  assert.match(describeTrade(d.trades[0]), /both sides/)
})

test('entry bar: an up bar through the stop survives, a down bar or a close through is stopped', () => {
  // OR1 100.00-101.00; the entry bar's low touches the 100.00 stop (a low at 99.99 would
  // also break the range's low: both sides, no trade).
  const up = run(day(101.5, { 0: [100.5, 101.0, 100.0, 100.8], 1: [100.5, 101.3, 100.0, 101.2] }), ['orb1_1000'])
  assert.equal(up.trades[0].status, 'flat')
  const down = run(day(101.5, { 0: [100.5, 101.0, 100.0, 100.8], 1: [101.2, 101.3, 100.0, 100.5] }), ['orb1_1000'])
  assert.equal(down.trades[0].status, 'stopped')
  assert.equal(down.trades[0].exitBar, 1)
  near(down.trades[0].r, -1)
  // An up bar that closes on the stop is stopped too.
  const closed = run(day(101.5, { 0: [100.5, 101.0, 100.0, 100.8], 1: [99.995, 101.3, 99.995, 100.0] }), ['orb1_1000'])
  assert.equal(closed.trades[0].status, 'stopped')
})

test('a later bar that opens through the stop exits at that open', () => {
  const d = run(day(101.5, { ...BASE, 10: [99.0, 99.2, 98.8, 99.1] }), ['orb1_1000'])
  assert.equal(d.trades[0].status, 'stopped')
  near(d.trades[0].exit, 99.0)
  near(d.trades[0].r, (99.0 - 101.01) / 1.01)
})

test('a short mirrors: break of the low, stop at the OR high', () => {
  // OR1 100.00-101.00; the 09:31 bar breaks down to 99.80, entry 99.99, stop 101.00.
  const d = run(day(99.0, { 0: [100.5, 101.0, 100.0, 100.2], 1: [100.2, 100.3, 99.8, 99.9] }), ['orb1_1000'])
  assert.equal(d.setups[1].side, -1)
  near(d.setups[1].entry, 99.99)
  near(d.trades[0].stop, 101.0)
  near(d.trades[0].r, (99.99 - 99.0) / 1.01)
  // Entry bar for a short: an UP bar that trades through the stop is stopped.
  const s = run(day(99.0, { 0: [100.5, 101.0, 100.0, 100.2], 1: [99.95, 101.0, 99.8, 99.97] }), ['orb1_1000'])
  assert.equal(s.trades[0].status, 'stopped')
})

test('OR2 uses the 09:30 and 09:31 bars', () => {
  // OR2 = 100.00-101.30 (bar 1's high); bar 2 breaks it.
  const d = run(day(102, { ...BASE, 1: [100.8, 101.3, 100.7, 101.0], 2: [101.0, 101.5, 100.9, 101.4] }), ['orb2_1000'])
  assert.equal(d.setups[2].entryBar, 2)
  near(d.setups[2].orHigh, 101.3)
  near(d.setups[2].entry, 101.31)
  near(d.trades[0].r, (102 - 101.31) / 1.31)
})

test('the stop is at least 2% of the daily ATR away', () => {
  // OR1 100.00-100.05, entry 100.06: risk 0.06 < 2% of ATR 10 = 0.20, so the stop is 99.86.
  const d = run(day(100.5, { 0: [100.0, 100.05, 100.0, 100.02], 1: [100.02, 100.1, 100.01, 100.08] }), ['orb1_1000'])
  assert.equal(d.trades[0].floored, true)
  near(d.trades[0].stop, 100.06 - 0.2)
  near(d.trades[0].r, (100.5 - 100.06) / 0.2)
  // Without a daily ATR the minimum cannot apply, and the legend says so.
  const n = run(day(100.5, { 0: [100.0, 100.05, 100.0, 100.02], 1: [100.02, 100.1, 100.01, 100.08] }), ['orb1_1000'], null)
  assert.equal(n.trades[0].floored, false)
  near(n.trades[0].stop, 100.0)
  assert.match(describeTrade(n.trades[0]), /no daily ATR/)
})

test('an entry on or after the flat bar is skipped for that exit only', () => {
  // First break on the 09:59 bar (29): too late for flat 10:00, fine for flat 10:30.
  const d = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8], 29: [100.9, 101.2, 100.8, 101.1], 59: [101.4, 101.5, 101.3, 101.4] }), ['orb1_1000', 'orb1_1030'])
  assert.equal(d.trades[0].status, 'late')
  assert.equal(d.trades[1].status, 'flat')
  near(d.trades[1].exit, 101.4)
})

test('a break after the 10:15 bar does NOT enter', () => {
  const late = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8], 46: [100.9, 101.2, 100.8, 101.1] }), ['orb1_1030'])
  assert.equal(late.setups[1].status, 'no_break')
  const last = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8], 45: [100.9, 101.2, 100.8, 101.1] }), ['orb1_1030'])
  assert.equal(last.setups[1].entryBar, 45)
})

test('a missing 09:30 bar means no trade; a missing later bar is skipped', () => {
  const d = run(day(100.5, BASE, { drop: [0] }), ['orb1_1000'])
  assert.equal(d.setups[1].status, 'no_or')
  // No 09:59 bar on a past day: flat at the last close before it, as the backtest does.
  const f = run(day(101.5, { ...BASE, 28: [101.5, 101.6, 101.4, 101.6] }, { drop: [29] }), ['orb1_1000'])
  assert.equal(f.trades[0].status, 'flat')
  assert.equal(f.trades[0].exitBar, 28)
  near(f.trades[0].exit, 101.6)
})

test('today: a running trade is open, never flat, and a bar still forming is not used', () => {
  // 09:45:30 ET: bars 0-14 have ended; bar 15 (09:45) is forming and dips through the stop.
  const now = { day: '2026-11-06', min: 9 * 60 + 45.5 }
  const bars = day(101.5, { ...BASE, 15: [101.5, 101.5, 99.0, 99.5] }).slice(0, 16)
  const d = run(bars, ['orb1_1000'], 10, now)
  assert.equal(d.trades[0].status, 'open')
  assert.equal(d.trades[0].exitBar, 14)
  near(d.trades[0].r, 0.49 / 1.01)
  assert.match(describeTrade(d.trades[0]), /open .* so far/)
  // 09:30:40: the 09:30 bar has not ended, so the study waits.
  const w = run(day(101.5, BASE).slice(0, 1), ['orb1_1000'], 10, { day: '2026-11-06', min: 9 * 60 + 30 + 40 / 60 })
  assert.equal(w.setups[1].status, 'waiting_or')
  // 09:33:10: no break yet and the next bar may still come.
  const nb = run(day(100.5, { 0: [100.5, 101.0, 100.0, 100.8] }).slice(0, 3), ['orb1_1000'], 10, { day: '2026-11-06', min: 9 * 60 + 33 + 10 / 60 })
  assert.equal(nb.setups[1].status, 'waiting_break')
})

test('a summer (EDT) day opens at 13:30 UTC', () => {
  const d = run(day(101.5, BASE, { day: '2026-07-15', utcOpen: 13 }), ['orb1_1000'])
  assert.equal(d.day, '2026-07-15')
  assert.equal(d.setups[1].entryBar, 1)
  near(d.trades[0].r, 0.49 / 1.01)
})

test('several days each get their own trade', () => {
  const two = [...day(101.5, BASE, { day: '2026-11-05' }), ...day(101.5, { ...BASE, 25: [101.5, 101.5, 99.5, 101.5] })]
  const days = orbDays(two, [v('orb1_1000')], () => 10, PAST)
  assert.deepEqual(days.map(d => [d.day, d.trades[0].status]), [['2026-11-05', 'flat'], ['2026-11-06', 'stopped']])
})

test('daily ATR(14): Wilder smoothing of the true range, from the bars before the day', () => {
  // 14 days with a 2.00 range and flat closes: ATR 2.00. A 15th day gaps to
  // 115-116: true range 116 - 100 = 16, so ATR = 2 + (16 - 2) / 14 = 3.
  const daily = Array.from({ length: 14 }, (_, i) => ({ t: `2026-10-${String(i + 1).padStart(2, '0')}`, h: 101, l: 99, c: 100 }))
  assert.equal(dailyAtr14(daily, '2026-10-15'), 2)
  const more = [...daily, { t: '2026-10-15', h: 116, l: 115, c: 115.5 }]
  assert.equal(dailyAtr14(more, '2026-10-16'), 3)
  assert.equal(dailyAtr14(more, '2026-10-15'), 2)     // the day itself is not used
  assert.equal(dailyAtr14(daily.slice(0, 13), '2026-10-15'), null)
})

test('legend lines say what happened', () => {
  const d = run(day(101.5, BASE), ['orb1_1000'])
  const t: OrbTrade = d.trades[0]
  assert.equal(describeTrade(t), 'long 101.01, stop 100.00, flat 101.50, +0.49R')
})

test('drawing on a 5-minute chart: entry and exits mark the bars holding them', () => {
  const days = orbDays(day(101.5, { ...BASE, 7: [101.5, 101.5, 101.5, 101.5] }), [v('orb1_1000'), v('orb1_1030')], () => 10, PAST)
  // 5-minute bars from 09:30 to 11:55 ET on 2026-11-06, in the chart's time space.
  const t0 = Date.UTC(2026, 10, 6, 9, 30) / 1000
  const chart = Array.from({ length: 30 }, (_, i) => t0 + i * 300)
  const { segments, markers } = orbDrawing(days, chart)
  assert.deepEqual(markers.map(m => [(m.time - t0) / 60, m.text]), [
    [0, 'OR1 long'],          // 09:31 entry -> the 09:30 bar
    [25, 'OR1 10:00 +0.49R'], // 09:59 close -> the 09:55 bar
    [55, 'OR1 10:30 +0.49R'], // 10:29 close -> the 10:25 bar
  ])
  const stop = segments.find(s => s.kind === 'stop')!
  assert.deepEqual([(stop.from - t0) / 60, (stop.to - t0) / 60, stop.price], [0, 55, 100])
  assert.equal(segments.filter(s => s.kind === 'range').length, 2)
})

test('drawing: a stop before 10:00 is one exit for both flat times', () => {
  const days = orbDays(day(101.5, { ...BASE, 25: [101.5, 101.5, 99.5, 101.5] }), [v('orb1_1000'), v('orb1_1030')], () => 10, PAST)
  const t0 = Date.UTC(2026, 10, 6, 9, 30) / 1000
  const chart = Array.from({ length: 150 }, (_, i) => t0 + i * 60)
  const { markers } = orbDrawing(days, chart)
  assert.deepEqual(markers.map(m => [(m.time - t0) / 60, m.text, m.kind]), [[1, 'OR1 long', 'entry'], [25, 'OR1 stop −1.00R', 'loss']])
})
