// Run: npm test (node's own test runner; no extra dependencies).
// How the OV picks window colours score parts and rank badges (src/windows/ovpicks/parts.ts;
// Ash, 2026-11-08, round 3 layout "C1"). Hand-built parts and rows; the labels and numbers
// are made up.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { lostText, partHeat, partLabels, partTitle, rankLooks } from '../src/windows/ovpicks/parts.ts'
import type { OvPick } from '../src/windows/ovpicks/types.ts'

const part = (points: number, possible = 10, label = 'Size') => ({ label, value: '1.0', points, possible })

test('a full part is faint; one that lost points is short, then well short from a quarter', () => {
  assert.equal(partHeat(part(10)), 'full')
  // The module rounds points, so a few hundredths lost still reads as full...
  assert.equal(partHeat(part(9.96)), 'full')
  // ...but not 0.05: that is a real loss and must NOT read as full.
  assert.equal(partHeat(part(9.95)), 'short')
  assert.equal(partHeat(part(7.51)), 'short')
  assert.equal(partHeat(part(7.5)), 'well-short')
  assert.equal(partHeat(part(0)), 'well-short')
})

test('the points lost show beside the value, and nothing when full', () => {
  assert.equal(lostText(part(10)), '')
  assert.equal(lostText(part(9.81)), '−0.19')
  assert.equal(lostText(part(4.8)), '−5.2')
})

test('the tooltip gives the points and says when the part puts the name ahead', () => {
  assert.equal(partTitle(part(10), false), 'Size: 10 of 10 points, full.')
  assert.equal(partTitle(part(4.8, 7.5), true), 'Size: 4.80 of 7.50 points. This part puts it ahead of the next name.')
})

const row = (rank: number, top: boolean, labels: string[] = []): OvPick => ({
  rank, ticker: `T${rank}`, score: 90, status: '', top, earnings_label: null, metrics: [], details: [],
  score_parts: labels.map(l => part(10, 10, l)),
})

test('the part columns are every label in the module order, once', () => {
  assert.deepEqual(partLabels([row(1, true, ['A', 'B']), row(2, true, ['A', 'C']), row(3, false)]), ['A', 'B', 'C'])
  assert.deepEqual(partLabels([row(1, true)]), [])
})

test('gold for the first top pick, green for the other top picks, an outline for the next two', () => {
  const rows = [row(1, true), row(2, true), row(3, true), row(4, true), row(5, false), row(6, false), row(7, false)]
  assert.deepEqual(rankLooks(rows), ['first', 'top', 'top', 'top', 'next', 'next', 'rest'])
})

test('without top picks no badge is gold, green or outlined', () => {
  assert.deepEqual(rankLooks([row(1, false), row(2, false), row(3, false)]), ['rest', 'rest', 'rest'])
})
