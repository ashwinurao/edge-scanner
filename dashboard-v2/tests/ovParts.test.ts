// Run: npm test (node's own test runner; no extra dependencies).
// How the OV picks window colours score parts and rank badges (src/windows/ovpicks/parts.ts;
// Ash, 2026-11-08, round 3 layout "C1", round 4 "A1 + B1 + H1"). Hand-built parts and rows;
// the labels and numbers are made up.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { partArrow, partHeader, partHeat, partLabels, partScore, partTitle, rankLooks } from '../src/windows/ovpicks/parts.ts'
import type { OvPart, OvPick } from '../src/windows/ovpicks/types.ts'

const part = (points: number, possible = 10, label = 'Size', more: Partial<OvPart> = {}): OvPart =>
  ({ label, value: '1.0', points, possible, ...more })

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

test('the corner score is the part out of 100, and 100 only when full', () => {
  assert.equal(partScore(part(10)), 100)
  assert.equal(partScore(part(9.96)), 100)
  // 9.95 of 10 rounds to 100 but lost a real 0.05, so it must NOT read as 100.
  assert.equal(partScore(part(9.95)), 99)
  assert.equal(partScore(part(4.8, 7.5)), 64)
  assert.equal(partScore(part(0)), 0)
})

test('a part that differs from the next name gets an arrow, and the ringed part gets none', () => {
  assert.equal(partArrow(part(10, 10, 'Size', { vs_next: 1.5 }), false), 'up')
  assert.equal(partArrow(part(10, 10, 'Size', { vs_next: -0.2 }), false), 'down')
  // The ring already says this part leads.
  assert.equal(partArrow(part(10, 10, 'Size', { vs_next: 1.5 }), true), null)
  // Rounding, the same points, or no next name: no arrow.
  assert.equal(partArrow(part(10, 10, 'Size', { vs_next: 0.004 }), false), null)
  assert.equal(partArrow(part(10, 10, 'Size', { vs_next: 0 }), false), null)
  assert.equal(partArrow(part(10), false), null)
})

test('the tooltip gives the score, the points, and what the part gives against the next name', () => {
  assert.equal(partTitle(part(10), false), 'Size: 100 of 100, 10 of 10 points, full.')
  assert.equal(partTitle(part(4.8, 7.5, 'Size', { vs_next: -1.25 }), false),
    'Size: 64 of 100, 4.80 of 7.50 points, 2.70 short. Against the next name: −1.25 points.')
  assert.equal(partTitle(part(10, 10, 'Size', { vs_next: 2 }), true),
    'Size: 100 of 100, 10 of 10 points, full. This part puts it ahead of the next name: +2 points.')
  assert.equal(partTitle(part(10), true), 'Size: 100 of 100, 10 of 10 points, full. This part puts it ahead of the next name.')
})

test('the part header says where full points start, from the module, and the marks on hover', () => {
  const picks = [{ ...row(1, true), score_parts: [part(10, 10, 'Size', { zero_at: '±0.10', full_at: '±0.40' })] }]
  const head = partHeader(picks, 'Size')
  assert.equal(head.full, '±0.40')
  assert.match(head.title, /^Size: a part of OV's score, up to 10 points\. No points up to ±0\.10, full points from ±0\.40\. /)
  // A module that sends no marks: the label alone, nothing made up.
  assert.equal(partHeader([row(1, true, ['Size'])], 'Size').full, null)
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
