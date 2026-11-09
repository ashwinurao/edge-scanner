// fork: OV picks jobs panel (Ash, 2026-11-09 13:44 UTC). One row per job of the module's
// status file: when it runs, its last state, when that was set, when it runs next and the
// module's message (jobs.ts). It reads the answer the window already has: no new calls.
import type { OvPicksAnswer } from './types'
import { jobRows, jobsSummary, type JobRow } from './jobs'
import { whenET } from './view'

function Row({ r }: { r: JobRow }) {
  const old = r.entry && !r.today && r.job !== 'reference'
  return (
    <tr className={old ? 'ovp-jold' : undefined}>
      <td className="ovp-jname">{r.name}<span className="ovp-jwhen">{r.when ? `${r.when} ET` : ''}</span></td>
      <td><span className={`badge ovp-b-${r.tone}`} title={r.means}>{r.state}</span></td>
      <td className="mono ovp-jat">
        {r.at || '—'}
        {r.overdue
          ? <span className="ovp-jnext ovp-joverdue" title="The module set this time for the next run, and no new entry has come since. Check that the OV picks module is running.">overdue: was due {r.next}</span>
          : r.next && <span className="ovp-jnext">next {r.next}</span>}
      </td>
      <td className="ovp-jmsg">{r.entry?.message || <span className="faint">—</span>}</td>
    </tr>
  )
}

export function JobsPanel({ a }: { a: OvPicksAnswer }) {
  const st = a.status
  const rows = jobRows(a, whenET)
  return (
    <div className="ovp-jobs" role="region" aria-label="OV picks module jobs">
      <div className="ovp-jobs-head">
        <b>Module jobs</b>
        <span className="faint">Each job's last state, from the module's status file. Times ET. Hover a state for what it means.</span>
      </div>
      {st && !st.has_status
        ? <div className={`ovp-note ovp-${st.reason_code === 'MISSING' ? 'info' : 'warn'}`}>{st.reason || 'The module has written no status file yet.'}</div>
        : (
          <>
            <table className="ovp-jtable">
              <thead><tr><th>Job</th><th>Last state</th><th>Set</th><th>Message</th></tr></thead>
              <tbody>{rows.map(r => <Row key={r.job} r={r} />)}</tbody>
            </table>
            <div className="ovp-jobs-foot faint">{jobsSummary(rows).text} Greyed rows are from an earlier day: the job has not run yet today.</div>
          </>
        )}
    </div>
  )
}
