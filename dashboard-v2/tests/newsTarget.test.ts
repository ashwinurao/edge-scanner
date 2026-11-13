// Run: npm test (node's own test runner; no extra dependencies).
// Which ticker a News window shows (src/windows/news/newsTarget.ts). Ash, project thread,
// 2026-11-12: a News window linked red stayed on market-wide news when an OV picks row was
// clicked, while the red charts switched. A News window added from the top bar starts
// "Market-wide", and that mode ignored the link color.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { newsSymbol } from '../src/windows/news/newsTarget.ts'

test('a market-wide window linked red follows the ticker sent to red', () => {
  assert.equal(newsSymbol({ mode: 'market', link: 'red', symbol: null }, 'NVDA'), 'NVDA')
})

test('a market-wide window linked red shows market news until red is sent a ticker', () => {
  assert.equal(newsSymbol({ mode: 'market', link: 'red', symbol: null }, null), null)
})

test('an unlinked market-wide window stays market-wide', () => {
  assert.equal(newsSymbol({ mode: 'market', link: 'none', symbol: 'AAPL' }, 'NVDA'), null)
})

test('a linked-ticker window follows its group, and its own ticker before the first send', () => {
  assert.equal(newsSymbol({ mode: 'linked', link: 'red', symbol: 'AAPL' }, 'NVDA'), 'NVDA')
  assert.equal(newsSymbol({ mode: 'linked', link: 'red', symbol: 'AAPL' }, null), 'AAPL')
  assert.equal(newsSymbol({ mode: 'linked', link: 'red', symbol: null }, null), null)
})

test('an unlinked linked-ticker window shows its own ticker', () => {
  assert.equal(newsSymbol({ mode: 'linked', link: 'none', symbol: 'AAPL' }, 'NVDA'), 'AAPL')
})
