// fork: OV picks card (ashwinurao/edge-scanner#20; spec #8, US1-US21 "Seeing the picks").
// The day's ranked list from the OV picks module, frozen at 09:25:00 ET and served
// read-only at /api/v2/ov-picks: every name it ranked, OV's top picks first and
// highlighted, in the columns the module chose (`metric_labels`). A click on a row opens
// its full details. Display only: no order buttons and no sizing; the trader picks and
// sizes by hand (Ash, 2026-11-06). A click on a ticker drives the window's link group,
// like a watchlist row. From 09:00 to 09:25 ET, before the freeze, it shows the module's
// preview instead: the same ranked list as of the latest minute, marked not frozen
// (Ash, 2026-11-06). The watchlist sync runs for the frozen picks only.
import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { usePoll } from '../../lib/usePoll'
import { DASH } from '../../lib/format'
import { linkSymbol } from '../../stores/linkStore'
import { useWatchlists } from '../../stores/watchlistsStore'
import { Empty } from '../../components/primitives'
import type { OvEntry, OvPick, OvPicksAnswer, OvPicksConfig, OvPicksPreview } from './types'
import { fetchOvPicks, syncOvPicksWatchlist, watchlistHas } from './api'
import { OV_PICKS_POLL_MS, OV_PICKS_WATCHLIST_ID } from './defaults'
import { allWarnings, badges, dayLabel, dayNotes, fmtValue, metricValue, noPicks, previewBadges, previewNotes, routeProblem, topPicks, wholeColumn, whenET, type Note } from './view'
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

// Details show each value as the module wrote it: whole numbers get thousands separators,
// other numbers keep every digit.
const fmtDetail = (v: OvEntry['value']) =>
  typeof v === 'number' && Number.isFinite(v) && !Number.isInteger(v) ? String(v) : fmtValue(v)

function Details({ p }: { p: OvPick }) {
  return (
    <div className="ovp-details">
      <div className="ovp-details-head">
        <b className="mono">{p.ticker}</b> rank {p.rank}, {p.status || DASH}{p.top ? ', one of OV\'s top picks' : ''}.
        {' '}<span className="faint">{p.details.length} field{p.details.length === 1 ? '' : 's'}, as the module wrote them.</span>
      </div>
      {p.details.length === 0
        ? <div className="faint ovp-details-none">The module sent no details for this row.</div>
        : (
          <dl className="ovp-dl">
            {p.details.map((d, i) => (
              <div key={i} className="ovp-dl-item">
                <dt title={d.label}>{d.label}</dt>
                <dd className={typeof d.value === 'number' ? 'mono' : ''}>{fmtDetail(d.value)}</dd>
              </div>
            ))}
          </dl>
        )}
    </div>
  )
}

interface TableProps {
  win: OvPicksConfig
  labels: string[]
  picks: OvPick[]
  inUniverse: Record<string, boolean> | null
  openTicker: string | null
  toggle: (ticker: string) => void
  /** the "top" badge's title */
  topTitle: string
}

/** The ranked list: the frozen picks, or a preview of them. */
function RankedTable({ win, labels, picks, inUniverse, openTicker, toggle, topTitle }: TableProps) {
  // A column of numbers is right-aligned, header included.
  const numeric = labels.map(l => picks.some(p => typeof metricValue(p, l) === 'number'))
  const whole = labels.map(l => wholeColumn(picks, l))
  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, ticker: string) => {
    if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
    e.preventDefault()
    toggle(ticker)
  }
  return (
    <table className="ovp-table">
      <thead>
        <tr>
          <th className="ovp-tk">Ticker</th>
          {labels.map((l, i) => <th key={l} className={numeric[i] ? 'num' : ''}>{l}</th>)}
        </tr>
      </thead>
      <tbody>
        {picks.map((p, i) => {
          const outside = inUniverse?.[p.ticker] === false
          const isOpen = openTicker === p.ticker
          // A line under the last top pick, where the rest of the ranked list starts.
          const lastTop = p.top && !picks[i + 1]?.top && i + 1 < picks.length
          const cls = ['ovp-row', p.top ? 'ovp-toprow' : '', lastTop ? 'ovp-split' : '', isOpen ? 'ovp-open' : ''].filter(Boolean).join(' ')
          return (
            <Fragment key={p.ticker}>
              <tr className={cls} tabIndex={0} aria-expanded={isOpen} onClick={() => toggle(p.ticker)} onKeyDown={e => onRowKey(e, p.ticker)} title={isOpen ? 'Click to close the details' : 'Click for the full row'}>
                <td className="ovp-tk">
                  <span className="ovp-chev" aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
                  <button className="ovp-sym sym" onClick={e => { e.stopPropagation(); linkSymbol(win, p.ticker, null) }} title={`Show ${p.ticker} in this window's linked charts`}>{p.ticker}</button>
                  {p.top && <span className="badge ovp-b-ok ovp-tag" title={topTitle}>top</span>}
                  {p.earnings_label && <span className="badge ovp-b-info ovp-tag" title="Earnings label">{p.earnings_label}</span>}
                  {outside && <span className="badge ovp-b-warn ovp-tag" title="Not in the scanner's universe: no live bars and no setups. Charts still work.">no setups</span>}
                </td>
                {labels.map((l, j) => <td key={l} className={numeric[j] ? 'num mono' : ''}>{fmtValue(metricValue(p, l), whole[j])}</td>)}
              </tr>
              {isOpen && (
                <tr className="ovp-details-row">
                  <td colSpan={labels.length + 1}><Details p={p} /></td>
                </tr>
              )}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}

export function OvPicksWindow({ win }: { win: OvPicksConfig }) {
  const { data, error, loading, refresh } = usePoll(fetchOvPicks, OV_PICKS_POLL_MS)
  const ovList = useWatchlists(s => s.lists[OV_PICKS_WATCHLIST_ID])
  const day = data?.day ?? null
  // The watchlist holds the top picks only, like the scanner's morning upsert.
  const tickers = day?.has_picks ? topPicks(day).map(p => p.ticker) : []
  const picksKey = data?.is_today && day && tickers.length ? `${day.trading_date}|${day.frozen_at}|${tickers.join(',')}` : ''
  // The open row belongs to one frozen list; a new day's list opens with every row closed.
  // A preview keeps its open row from minute to minute, until the picks freeze.
  const preview: OvPicksPreview | null = day && !day.has_picks && data?.preview?.has_preview ? data.preview : null
  const dayKey = preview ? `${preview.trading_date}|preview` : day ? `${day.trading_date}|${day.frozen_at}` : ''
  const [open, setOpen] = useState<{ key: string; ticker: string } | null>(null)
  const openTicker = open?.key === dayKey ? open.ticker : null
  const toggle = (ticker: string) => setOpen(openTicker === ticker ? null : { key: dayKey, ticker })

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

  if (preview) {
    const top = preview.picks.filter(p => p.top).length
    const asOf = preview.as_of ? `${whenET(preview.as_of, data.today_et).replace(/^today /, '')}` : ''
    return (
      <div className="ovp-wrap">
        {header(
          <>
            <span className="ovp-day">{dayLabel(preview.trading_date)}</span>
            {asOf && <span className="faint mono ovp-frozen" title={preview.as_of ?? ''}>preview as of {asOf}</span>}
            <span className="faint ovp-count">{preview.picks.length} ranked, {top} top</span>
            {previewBadges(preview).map(b => <span key={b.text} className={`badge ovp-b-${b.tone}`} title={b.title}>{b.text}</span>)}
          </>,
        )}
        {stale}
        <div className="ovp-scroll">
          {preview.picks.length > 0 && (
            <RankedTable win={win} labels={preview.metric_labels} picks={preview.picks} inUniverse={data.in_universe}
              openTicker={openTicker} toggle={toggle} topTitle={`One of OV's top picks as of ${asOf || 'this preview'}: not frozen`} />
          )}
          <Notes notes={previewNotes(data, preview)} />
          <Warnings a={data} />
          {preview.picks.length > 0 && (
            <div className="ovp-foot faint">
              A preview, not the picks: highlighted rows are OV's top picks as of that minute, and they may change until 09:25 ET. Click a row for its full details. This card shows the list only: it places no orders and sizes nothing.
            </div>
          )}
        </div>
      </div>
    )
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

  const inList = watchlistHas(ovList, tickers)
  return (
    <div className="ovp-wrap">
      {header(
        <>
          <span className="ovp-day">{dayLabel(day.trading_date)}</span>
          {day.frozen_at && <span className="faint mono ovp-frozen" title={day.frozen_at}>frozen {whenET(day.frozen_at, day.trading_date, true).replace(/^today /, '')}</span>}
          <span className="faint ovp-count">{day.picks.length} ranked, {tickers.length} top</span>
          {badges(day).map(b => <span key={b.text} className={`badge ovp-b-${b.tone}`} title={b.title}>{b.text}</span>)}
        </>,
      )}
      {stale}
      <div className="ovp-scroll">
        {day.picks.length > 0 && (
          <RankedTable win={win} labels={day.metric_labels} picks={day.picks} inUniverse={data.in_universe}
            openTicker={openTicker} toggle={toggle} topTitle="One of OV's top picks today" />
        )}
        <Notes notes={dayNotes(data, day)} />
        <Warnings a={data} />
        {day.picks.length > 0 && (
          <div className="ovp-foot faint">
            {data.is_today && inList ? 'The top picks are in the "OV picks" watchlist. ' : ''}
            Highlighted rows are OV's top picks. Click a row for its full details. This card shows the list only: it places no orders and sizes nothing.
          </div>
        )}
      </div>
    </div>
  )
}
