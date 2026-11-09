// Run: npm test (node's own test runner; no extra dependencies).
// The OV picks window's jobs panel (src/windows/ovpicks/jobs.ts; Ash, 2026-11-09).
// Hand-built status entries; the messages are made up.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { JOB_ROWS, OVERDUE_MS, jobRows, jobsSummary } from '../src/windows/ovpicks/jobs.ts'
import type { OvJobEntry, OvPicksAnswer } from '../src/windows/ovpicks/types.ts'

const TODAY = '2026-11-09'
// A plain formatter, so the test checks which time goes where, not how it reads.
const fmt = (iso: string, today: string, seconds = false) => `${iso}|${today}|${seconds ? 's' : 'm'}`

const entry = (state: string, more: Partial<OvJobEntry> = {}): OvJobEntry => ({
  state, at: '2026-11-09T14:25:00Z', trading_date: TODAY, message: '', next_at: null, ...more,
})
const answer = (jobs: Record<string, OvJobEntry> | null, now = '2026-11-09T14:40:00Z'): OvPicksAnswer =>
  ({ today_et: TODAY, now_et: now,
    status: jobs ? { has_status: true, reason_code: '', reason: '', warnings: [], jobs } : null } as unknown as OvPicksAnswer)

test('every contract job has a row, in the order of the day', () => {
  const rows = jobRows(answer({}), fmt)
  assert.deepEqual(rows.map(r => r.job), ['nightly', 'warmup', 'preview', 'scan', 'settle', 'compare', 'archive', 'reference'])
  assert.equal(rows.length, JOB_ROWS.length)
  // No entry yet: said so, not guessed.
  for (const r of rows) {
    assert.equal(r.entry, null)
    assert.equal(r.state, 'not run yet')
    assert.equal(r.tone, 'info')
    assert.equal(r.overdue, false)
  }
})

test('each state takes its colour from the contract', () => {
  const tone = (job: string, state: string) => jobRows(answer({ [job]: entry(state) }), fmt).find(r => r.job === job)!.tone
  assert.equal(tone('nightly', 'NIGHTLY_OK'), 'ok')
  assert.equal(tone('nightly', 'NIGHTLY_WARN'), 'warn')
  assert.equal(tone('warmup', 'WARMING'), 'info')
  assert.equal(tone('warmup', 'DATA_READY'), 'ok')
  assert.equal(tone('warmup', 'READY'), 'ok')
  assert.equal(tone('warmup', 'NOT_READY'), 'warn')
  assert.equal(tone('preview', 'PREVIEWED'), 'ok')
  assert.equal(tone('scan', 'PICKS_FROZEN'), 'ok')
  assert.equal(tone('settle', 'SETTLED_SAME'), 'ok')
  assert.equal(tone('settle', 'SETTLED_DIFF'), 'warn')
  assert.equal(tone('compare', 'PARITY_MATCH'), 'ok')
  assert.equal(tone('compare', 'PARITY_DIFF'), 'bad')
  assert.equal(tone('compare', 'PARITY_NO_OV_RUN'), 'info')
  assert.equal(tone('archive', 'ARCHIVED'), 'ok')
  assert.equal(tone('reference', 'REF_OK'), 'ok')
  assert.equal(tone('reference', 'REF_WARN'), 'warn')
  assert.equal(tone('scan', 'RUNNING'), 'info')
  assert.equal(tone('scan', 'SKIPPED'), 'info')
  assert.equal(tone('compare', 'REFUSED'), 'warn')
  assert.equal(tone('scan', 'FAILED'), 'bad')
  // A state from a newer module: shown as written, without a colour.
  const r = jobRows(answer({ scan: entry('SOMETHING_NEW') }), fmt).find(x => x.job === 'scan')!
  assert.equal(r.state, 'SOMETHING_NEW')
  assert.equal(r.tone, 'info')
})

test('a row says when the state was set, whether it is today\'s, and when the job runs next', () => {
  const rows = jobRows(answer({
    scan: entry('PICKS_FROZEN', { next_at: '2026-11-10T14:25:00Z' }),
    archive: entry('ARCHIVED', { at: '2026-11-06T21:19:30Z', trading_date: '2026-11-06', next_at: '2026-11-09T21:15:00Z' }),
    reference: entry('REF_OK', { trading_date: null }),
  }), fmt)
  const scan = rows.find(r => r.job === 'scan')!
  assert.equal(scan.at, `2026-11-09T14:25:00Z|${TODAY}|s`)
  assert.equal(scan.next, `2026-11-10T14:25:00Z|${TODAY}|m`)
  assert.equal(scan.today, true)
  const archive = rows.find(r => r.job === 'archive')!
  assert.equal(archive.today, false)            // Friday's: today's runs at 16:15 ET
  assert.equal(archive.overdue, false)
  assert.equal(rows.find(r => r.job === 'reference')!.today, false)
  assert.equal(rows.find(r => r.job === 'nightly')!.next, '')
})

test('a job is overdue only once its next run is more than 15 minutes past, and never while running', () => {
  const now = '2026-11-09T14:40:00Z'
  const dueAt = (ms: number) => new Date(Date.parse(now) - ms).toISOString()
  const row = (e: OvJobEntry) => jobRows(answer({ nightly: e }, now), fmt).find(r => r.job === 'nightly')!
  assert.equal(row(entry('NIGHTLY_OK', { next_at: dueAt(OVERDUE_MS + 1000) })).overdue, true)
  assert.equal(row(entry('NIGHTLY_OK', { next_at: dueAt(OVERDUE_MS) })).overdue, false)      // exactly 15 min: not yet
  assert.equal(row(entry('NIGHTLY_OK', { next_at: dueAt(60_000) })).overdue, false)
  assert.equal(row(entry('NIGHTLY_OK', { next_at: null })).overdue, false)
  assert.equal(row(entry('RUNNING', { next_at: dueAt(OVERDUE_MS * 4) })).overdue, false)
})

test('a job a newer module adds is shown after the known ones, as written', () => {
  const rows = jobRows(answer({ zeta: entry('Z_OK'), alpha: entry('A_OK') }), fmt)
  assert.deepEqual(rows.slice(-2).map(r => [r.job, r.name, r.state]), [['alpha', 'alpha', 'A_OK'], ['zeta', 'zeta', 'Z_OK']])
})

test('the button takes the worst state, and names the jobs that need a look', () => {
  const sum = (jobs: Record<string, OvJobEntry>, now?: string) => jobsSummary(jobRows(answer(jobs, now), fmt))
  assert.deepEqual(sum({}), { tone: 'info', text: 'No job has run yet.' })
  assert.deepEqual(sum({ scan: entry('PICKS_FROZEN'), warmup: entry('WARMING') }), { tone: 'ok', text: 'No job needs a look.' })
  assert.deepEqual(sum({ nightly: entry('NIGHTLY_WARN'), scan: entry('PICKS_FROZEN') }),
    { tone: 'warn', text: '1 job needs a look: Nightly NIGHTLY_WARN.' })
  assert.deepEqual(sum({ nightly: entry('NIGHTLY_WARN'), compare: entry('PARITY_DIFF') }),
    { tone: 'bad', text: '2 jobs need a look: Nightly NIGHTLY_WARN, Compare with OV PARITY_DIFF.' })
  // An overdue job with a good last state counts as a warning.
  assert.deepEqual(sum({ nightly: entry('NIGHTLY_OK', { next_at: '2026-11-09T08:30:00Z' }) }, '2026-11-09T14:40:00Z'),
    { tone: 'warn', text: '1 job needs a look: Nightly overdue.' })
})
