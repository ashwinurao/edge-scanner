// fork: OV picks jobs panel (Ash, 2026-11-09 13:44 UTC: "Is there a place i can see these
// statuses in edge scanner"). Each job of the module's status file (ov_picks/CONTRACT.md,
// "Status file"), in the order the day runs them, with its last state, as plain data so
// JobsPanel.tsx only lays it out. A state this card does not know is shown as written.
import type { OvJobEntry, OvPicksAnswer } from './types'
import type { Tone } from './view'

/** Formats a time for the panel: view.ts whenET in the window. A parameter, so the tests
 *  run under node without the window's time helpers. */
export type WhenFmt = (iso: string, today: string, seconds?: boolean) => string

/** The contract's jobs (ov_picks/contract.py JOBS) in the order of the day, with when they
 *  run (ET, ov_picks/CONTRACT.md "Status file"). The warmup entry also holds the 09:20 ET
 *  readiness verdict (READY or NOT_READY), so readiness has no row of its own. */
export const JOB_ROWS: readonly { job: string; name: string; when: string }[] = [
  { job: 'nightly', name: 'Nightly', when: '03:30' },
  { job: 'warmup', name: 'Warmup, readiness', when: '08:30, deadline 09:20' },
  { job: 'preview', name: 'Preview', when: '09:00-09:24' },
  { job: 'scan', name: 'Scan', when: '09:25' },
  { job: 'settle', name: 'Settle', when: '09:26' },
  { job: 'compare', name: 'Compare with OV', when: '10:35' },
  { job: 'archive', name: 'Archive', when: '16:15' },
  { job: 'reference', name: 'Reference', when: 'Sat 10:00' },
]

/** Each state's colour and what it means (ov_picks/CONTRACT.md, "Status file"). */
const STATES: Record<string, { tone: Tone; means: string }> = {
  NIGHTLY_OK: { tone: 'ok', means: 'The nightly check passed.' },
  NIGHTLY_WARN: { tone: 'warn', means: 'The nightly check found something to look at. See the message.' },
  WARMING: { tone: 'info', means: 'Loading the day\'s data.' },
  DATA_READY: { tone: 'ok', means: 'The day\'s data is loaded. The readiness verdict comes at 09:20 ET.' },
  READY: { tone: 'ok', means: 'Ready at the 09:20 ET deadline.' },
  NOT_READY: { tone: 'warn', means: 'Not ready at the 09:20 ET deadline. The scan still runs at 09:25 ET.' },
  PREVIEWED: { tone: 'ok', means: 'The latest minute\'s preview is written.' },
  PICKS_FROZEN: { tone: 'ok', means: 'The day\'s picks are frozen.' },
  SETTLED_SAME: { tone: 'ok', means: 'The 09:26 re-check made the same top picks and the same ranked list.' },
  SETTLED_DIFF: { tone: 'warn', means: 'Bars that came in late would have changed the top picks or the ranked list. The frozen list stands.' },
  PARITY_MATCH: { tone: 'ok', means: 'The picks match OV\'s own live scan.' },
  PARITY_DIFF: { tone: 'bad', means: 'The picks differ from OV\'s own live scan. See the message.' },
  PARITY_NO_OV_RUN: { tone: 'info', means: 'OV made no live scan to compare with.' },
  ARCHIVED: { tone: 'ok', means: 'The day\'s scan history and bars are saved.' },
  REF_OK: { tone: 'ok', means: 'The sector and listing update finished.' },
  REF_WARN: { tone: 'warn', means: 'The sector and listing update had a warning. See the message.' },
  RUNNING: { tone: 'info', means: 'Running now.' },
  SKIPPED: { tone: 'info', means: 'Not run: a closed market, a start after 09:35 ET, or the reference job during the shadow run.' },
  REFUSED: { tone: 'warn', means: 'A rule stopped it. See the message.' },
  FAILED: { tone: 'bad', means: 'It stopped with an error. See the message.' },
}

/** A job counts as overdue when the time the module set for its next run is this far past
 *  and the entry is still the old one: the module is not running, or the job is stuck. */
export const OVERDUE_MS = 15 * 60_000

export interface JobRow {
  job: string
  name: string
  /** when it runs, ET */
  when: string
  /** null: the status file has no entry for it yet */
  entry: OvJobEntry | null
  /** the state as written, or "not run yet" */
  state: string
  tone: Tone
  /** what the state means */
  means: string
  /** "today 09:25:00 ET" or "Fri 2026-11-06 16:19:30 ET" */
  at: string
  /** the entry is about today (ET); the reference job's entries have no day */
  today: boolean
  /** "today 10:35 ET", or "" when the module set no next run */
  next: string
  /** the next run was due more than OVERDUE_MS ago */
  overdue: boolean
}

export function jobRows(a: OvPicksAnswer, when: WhenFmt): JobRow[] {
  const jobs = a.status?.jobs ?? {}
  const now = Date.parse(a.now_et)
  const known = new Set(JOB_ROWS.map(r => r.job))
  // A job a newer module adds is shown after the known ones, as written.
  const extra = Object.keys(jobs).filter(j => !known.has(j)).sort().map(job => ({ job, name: job, when: '' }))
  return [...JOB_ROWS, ...extra].map(({ job, name, when: runs }) => {
    const e = jobs[job] ?? null
    if (!e) {
      return { job, name, when: runs, entry: null, state: 'not run yet', tone: 'info', means: 'The status file has no entry for this job yet.',
        at: '', today: false, next: '', overdue: false }
    }
    const s = STATES[e.state] ?? { tone: 'info' as Tone, means: 'A state this window does not know, shown as written.' }
    const due = e.next_at ? Date.parse(e.next_at) : NaN
    const overdue = e.state !== 'RUNNING' && Number.isFinite(due) && Number.isFinite(now) && now - due > OVERDUE_MS
    return {
      job, name, when: runs, entry: e, state: e.state, tone: s.tone, means: s.means,
      at: e.at ? when(e.at, a.today_et, true) : '',
      today: e.trading_date === a.today_et,
      next: e.next_at ? when(e.next_at, a.today_et) : '',
      overdue,
    }
  })
}

const RANK: Record<Tone, number> = { info: 0, ok: 1, warn: 2, bad: 3 }

/** The button's summary: the worst state among the jobs (an overdue job counts as a
 *  warning), and the jobs that need a look. */
export function jobsSummary(rows: JobRow[]): { tone: Tone; text: string } {
  const look = rows.filter(r => r.tone === 'warn' || r.tone === 'bad' || r.overdue)
  if (!look.length) {
    const any = rows.some(r => r.entry)
    return { tone: any ? 'ok' : 'info', text: any ? 'No job needs a look.' : 'No job has run yet.' }
  }
  const tone = look.reduce<Tone>((t, r) => {
    const own: Tone = r.overdue && RANK[r.tone] < RANK.warn ? 'warn' : r.tone
    return RANK[own] > RANK[t] ? own : t
  }, 'info')
  const what = look.map(r => `${r.name} ${r.overdue && r.tone !== 'warn' && r.tone !== 'bad' ? 'overdue' : r.state}`)
  return { tone, text: `${look.length} job${look.length > 1 ? 's need' : ' needs'} a look: ${what.join(', ')}.` }
}
