// fork: OV picks card (ashwinurao/edge-scanner#20; spec #8, US1-US21 "Seeing the picks").
// The day's four OV picks, frozen at 09:25:00 ET by the OV picks module and served
// read-only at /api/v2/ov-picks. Display only: no order buttons, nothing here trades.
// A click on a ticker drives the window's link group, like a watchlist row.
import { useEffect, useRef, type ReactNode } from 'react'
import { usePoll } from '../../lib/usePoll'
import { fmtNum, DASH } from '../../lib/format'
import { linkSymbol } from '../../stores/linkStore'
import { useWatchlists } from '../../stores/watchlistsStore'
import { Empty } from '../../components/primitives'
import type { OvPicksAnswer, OvPicksConfig } from './types'
import { fetchOvPicks, syncOvPicksWatchlist, watchlistHas } from './api'
import { OV_PICKS_POLL_MS, OV_PICKS_WATCHLIST_ID } from './defaults'
import { allWarnings, badges, dayLabel, dayNotes, fmtWeight, noPicks, routeProblem, whenET, type Note } from './view'
import './ovpicks.css'

// After the card first sees the day's picks, the scanner's watcher can take a few seconds
// to fill the watchlist (scanner/ov_picks_day.py POLL_SECONDS); retry a few polls, then stop.
const WATCHLIST_SYNC_TRIES = 4

function Notes({ notes }: { notes: Note[] }) {
  return <>{notes.map((n, i) => <div key={i} className={`ovp-note ovp-${n.tone}`}>{n.text}</div>)}</>
}

function Warnings({ a }: { a: OvPicksAnswer }) {
  const ws = allWarnings(a)
  return <Notes notes={ws.map(text => ({ tone: 'warn' as const, text }))} />
}

export function OvPicksWindow({ win }: { win: OvPicksConfig }) {
  const { data, error, loading, refresh } = usePoll(fetchOvPicks, OV_PICKS_POLL_MS)
  const ovList = useWatchlists(s => s.lists[OV_PICKS_WATCHLIST_ID])
  const day = data?.day ?? null
  const tickers = day?.has_picks ? day.picks.map(p => p.ticker) : []
  const picksKey = data?.is_today && day && tickers.length ? `${day.trading_date}|${day.frozen_at}|${tickers.join(',')}` : ''

  // Keep this tab's "OV picks" watchlist in step with the scanner's morning upsert.
  // Runs on every poll (`data`), at most WATCHLIST_SYNC_TRIES times per day's picks.
  const sync = useRef({ key: '', tries: 0, done: false })
  useEffect(() => {
    if (!picksKey) return
    const s = sync.current
    if (s.key !== picksKey) { s.key = picksKey; s.tries = 0; s.done = false }
    if (s.done || s.tries >= WATCHLIST_SYNC_TRIES) return
    s.tries += 1
    syncOvPicksWatchlist(picksKey.split('|')[2].split(','))
      .then(ok => { if (ok && sync.current.key === picksKey) sync.current.done = true })
      .catch(() => { /* the next poll tries again */ })
  }, [picksKey, data])

  const header = (extra?: ReactNode) => (
    <div className="ovp-top">
      {extra}
      <span className="flex-spacer" />
      <button className="btn sm" onClick={() => void refresh()} disabled={loading} title={`Read the picks again now (the card refreshes every ${OV_PICKS_POLL_MS / 1000} s)`}>{loading ? '…' : 'Refresh'}</button>
    </div>
  )

  if (!data) {
    if (error) return <div className="ovp-wrap">{header()}<Empty title="OV picks are not available">{error}</Empty></div>
    return <div className="ovp-wrap">{header()}<Empty title="Reading OV picks…" /></div>
  }
  const stale = error ? <div className="sc-stale">Refresh failed ({error}). Showing the answer from {whenET(data.now_et, data.today_et, true)}.</div> : null

  const problem = routeProblem(data)
  if (problem || !day) {
    return <div className="ovp-wrap">{header()}{stale}<Empty title={problem?.title ?? 'OV picks could not be read'}>{problem?.text ?? 'The scanner sent no day. Reload the page.'}</Empty></div>
  }

  if (!day.has_picks) {
    const { title, notes } = noPicks(data, day)
    return (
      <div className="ovp-wrap">
        {header(<span className="ovp-day">{dayLabel(day.trading_date)}</span>)}
        {stale}
        <div className="ovp-scroll">
          <div className="ovp-title">{title}</div>
          <Notes notes={notes} />
          <Warnings a={data} />
        </div>
      </div>
    )
  }

  const unavailable = day.earnings_status !== 'available'
  const inList = watchlistHas(ovList, tickers)
  return (
    <div className="ovp-wrap">
      {header(
        <>
          <span className="ovp-day">{dayLabel(day.trading_date)}</span>
          {day.frozen_at && <span className="faint mono ovp-frozen" title={day.frozen_at}>frozen {whenET(day.frozen_at, day.trading_date, true).replace(/^today /, '')}</span>}
          {badges(day).map(b => <span key={b.text} className={`badge ovp-b-${b.tone}`} title={b.title}>{b.text}</span>)}
        </>,
      )}
      {stale}
      <div className="ovp-scroll">
        {day.picks.length > 0 && (
          <table className="ovp-table">
            <thead>
              <tr><th className="num">Rank</th><th>Ticker</th><th className="num">Score</th><th>Earnings</th><th>Traded</th><th className="num">Weight</th></tr>
            </thead>
            <tbody>
              {day.picks.map(p => {
                const outside = data.in_universe?.[p.ticker] === false
                return (
                  <tr key={p.ticker} className={p.traded ? 'ovp-traded' : ''}>
                    <td className="num mono">{p.rank}</td>
                    <td>
                      <button className="ovp-sym sym" onClick={() => linkSymbol(win, p.ticker, null)} title={`Show ${p.ticker} in this window's linked charts`}>{p.ticker}</button>
                      {outside && <span className="badge ovp-b-warn ovp-nu" title="Not in the scanner's universe: no live bars and no setups. Charts still work.">no setups</span>}
                    </td>
                    <td className="num mono">{fmtNum(p.score, 2)}</td>
                    <td>{unavailable ? <span className="faint">unavailable</span> : p.earnings_label ? <span className="badge ovp-b-info">{p.earnings_label}</span> : <span className="faint">{DASH}</span>}</td>
                    <td>{p.traded ? <span className="badge ovp-b-ok">traded</span> : <span className="faint">{DASH}</span>}</td>
                    <td className="num mono">{p.traded ? fmtWeight(p.weight) : <span className="faint">{DASH}</span>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        <Notes notes={dayNotes(data, day)} />
        <Warnings a={data} />
        {data.is_today && day.picks.length > 0 && (
          <div className="ovp-foot faint">
            {inList ? 'These names are in the "OV picks" watchlist. ' : ''}OV's traded flag and weight are information only; this card places no orders.
          </div>
        )}
      </div>
    </div>
  )
}
