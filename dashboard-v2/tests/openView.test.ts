// Run: npm test (node's own test runner; no extra dependencies).
// The 08:30-11:30 ET view a 1-minute chart opens on from the OV picks window
// (src/lib/openView.ts; Ash, 2026-11-08). Hand-built bar times, hand-counted indexes.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { openViewRange } from '../src/lib/openView.ts'

// Chart time space: ET wall clock read as UTC seconds (toSecET).
const et = (day: string, hh: number, mm: number) => {
  const [y, m, d] = day.split('-').map(Number)
  return Date.UTC(y, m - 1, d, hh, mm) / 1000
}
/** One-minute bar times on `day` from `from` up to and including `to` (HH:MM). */
function minutes(day: string, from: string, to: string, skip: string[] = []): number[] {
  const [fh, fm] = from.split(':').map(Number), [th, tm] = to.split(':').map(Number)
  const out: number[] = []
  for (let t = fh * 60 + fm; t <= th * 60 + tm; t++) {
    const hhmm = `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
    if (!skip.includes(hhmm)) out.push(et(day, Math.floor(t / 60), t % 60))
  }
  return out
}

test('a past day: from the 08:30 bar to the 11:30 bar, earlier days left of it', () => {
  const prior = minutes('2026-11-05', '04:00', '19:59')            // 960 bars
  const day = minutes('2026-11-06', '04:00', '19:59')
  // 08:30 is 270 bars after 04:00 and 11:30 is 450.
  assert.deepEqual(openViewRange([...prior, ...day], 60), { from: 960 + 270 - 0.5, to: 960 + 450 + 0.5 })
})

test('today at 09:45: the view runs on to 11:30 into empty space', () => {
  const day = minutes('2026-11-09', '04:00', '09:45')              // last bar index 345
  // 105 minutes from 09:45 to 11:30.
  assert.deepEqual(openViewRange(day, 60), { from: 270 - 0.5, to: 345 + 105 + 0.5 })
})

test('before 08:30 there is nothing to frame yet: the usual view', () => {
  const prior = minutes('2026-11-06', '04:00', '19:59')
  assert.equal(openViewRange([...prior, ...minutes('2026-11-09', '04:00', '08:29')], 60), null)
  assert.equal(openViewRange([], 60), null)
})

test('a missing 08:30 bar starts the view at the next bar', () => {
  const day = minutes('2026-11-06', '08:00', '12:00', ['08:30', '08:31'])
  // 08:00-08:29 are bars 0-29, so 08:32 is bar 30; 11:30 is bar 30 + 178.
  assert.deepEqual(openViewRange(day, 60), { from: 30 - 0.5, to: 208 + 0.5 })
})

test('extended hours hidden: the view starts at the 09:30 bar', () => {
  const prior = minutes('2026-11-05', '09:30', '15:59')            // 390 bars
  const day = minutes('2026-11-06', '09:30', '15:59')
  assert.deepEqual(openViewRange([...prior, ...day], 60), { from: 390 - 0.5, to: 390 + 120 + 0.5 })
})
