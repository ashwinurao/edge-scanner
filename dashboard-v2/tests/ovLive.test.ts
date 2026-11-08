// Run: npm test (node's own test runner; no extra dependencies).
// The OV picks window's first-minute columns, live (src/windows/ovpicks/live.ts; Ash,
// 2026-11-08). Hand-built rows; the tickers and numbers are made up.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { anySoFar, pollMs, soFar } from '../src/windows/ovpicks/live.ts'
import type { OvPick } from '../src/windows/ovpicks/types.ts'

const row = (more: Partial<OvPick> = {}): OvPick => ({
  rank: 1, ticker: 'AAAA', score: 90, status: 'WATCHLIST', top: true, earnings_label: null,
  metrics: [], details: [], ...more,
})
const bar = { open: 10, high: 10.4, low: 9.9, close: 10.2 }

test('the card asks every 2 s while the route says so, and at its own pace otherwise', () => {
  assert.equal(pollMs(15_000, 2), 2_000)
  assert.equal(pollMs(15_000, null), 15_000)
  assert.equal(pollMs(15_000, undefined), 15_000)
  assert.equal(pollMs(15_000, 0), 15_000)
  assert.equal(pollMs(15_000, 0.2), 1_000)   // never faster than once a second
})

test('a live cell is "so far" only while the row has the minute so far', () => {
  assert.equal(soFar(row({ first_minute: bar, first_minute_as_of: '09:30:41' }), true), '09:30:41')
  assert.equal(soFar(row({ first_minute: bar, first_minute_as_of: '09:30:41' }), false), null)
  assert.equal(soFar(row({ first_minute: bar }), true), null)              // the 09:30 bar: final
  assert.equal(soFar(row({ first_minute: null, first_minute_as_of: '09:30:41' }), true), null)
})

test('the key explains the dot only while some row is so far', () => {
  assert.equal(anySoFar([row({ first_minute: bar }), row({ ticker: 'BBBB' })]), false)
  assert.equal(anySoFar([row({ first_minute: bar }), row({ ticker: 'BBBB', first_minute: bar, first_minute_as_of: '09:30:12' })]), true)
})
