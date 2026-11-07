// Run: npm test (node's own test runner; no extra dependencies).
// Hand-built bar times. ET is computed by the code with America/New_York, so
// both an EST day (UTC-5) and an EDT day (UTC-4) are covered.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sessionMarks } from '../src/lib/sessionMarks.ts'

test('EST day with extended hours: regular hours are one run', () => {
  // 2026-11-06 is EST: 09:30 ET = 14:30 UTC, 16:00 ET = 21:00 UTC.
  const t = [
    '2026-11-06T09:00:00Z', // 04:00 ET pre-market
    '2026-11-06T14:29:00Z', // 09:29 ET pre-market, last one
    '2026-11-06T14:30:00Z', // 09:30 ET RTH, first shaded bar
    '2026-11-06T20:59:00Z', // 15:59 ET RTH, last shaded bar
    '2026-11-06T21:00:00Z', // 16:00 ET after-hours: must NOT be shaded
    '2026-11-07T00:59:00Z', // 19:59 ET after-hours, still 2026-11-06 in ET
  ]
  // 09:29 and 16:00 are extended and must NOT be shaded.
  assert.deepEqual(sessionMarks(t), { rthRuns: [[2, 3]], dayStarts: [] })
})

test('EDT day uses the summer offset: 13:30 UTC is the open', () => {
  // 2026-07-15 is EDT: 09:30 ET = 13:30 UTC.
  const t = ['2026-07-15T13:29:00Z', '2026-07-15T13:30:00Z', '2026-07-15T19:59:00Z', '2026-07-15T20:00:00Z']
  assert.deepEqual(sessionMarks(t).rthRuns, [[1, 2]])
})

test('two days: each regular session is its own run, the day line falls on 04:00', () => {
  const t = [
    '2026-11-05T20:59:00Z', // 15:59 ET 11-05 RTH
    '2026-11-05T21:00:00Z', // 16:00 ET 11-05 after-hours
    '2026-11-06T09:00:00Z', // 04:00 ET 11-06 pre-market: new day
    '2026-11-06T14:30:00Z', // 09:30 ET 11-06 RTH
  ]
  assert.deepEqual(sessionMarks(t), { rthRuns: [[0, 0], [3, 3]], dayStarts: [2] })
})

test('regular hours only: every bar is shaded, the day line falls on the 09:30 bar', () => {
  const t = ['2026-11-05T20:55:00Z', '2026-11-06T14:30:00Z', '2026-11-06T14:35:00Z']
  assert.deepEqual(sessionMarks(t), { rthRuns: [[0, 2]], dayStarts: [1] })
})

test('no bars: nothing to draw', () => {
  assert.deepEqual(sessionMarks([]), { rthRuns: [], dayStarts: [] })
})
