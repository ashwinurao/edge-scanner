// fork: OV picks card (ashwinurao/edge-scanner#20; spec #8, US1-US21 "Seeing the picks").
// The day's ranked list from the OV picks module, frozen at 09:25:00 ET and served
// read-only at /api/v2/ov-picks: every name it ranked, OV's top picks first and
// highlighted, in the columns the module chose (`metric_labels`). A click on a row drives
// the window's link group, like a watchlist row; the ▸ arrow opens the row's full details
// (Ash, 2026-11-07). Display only: no order buttons and no sizing; the trader picks and
// sizes by hand (Ash, 2026-11-06). From 09:00 to 09:25 ET, before the freeze, it shows the module's
// preview instead: the same ranked list as of the latest minute, marked not frozen
// (Ash, 2026-11-06). The watchlist sync runs for the frozen picks only.
// A calendar shows a past day (Ash, 2026-11-07): the module's own frozen picks for it, or,
// before the module ran, OV's own list from the history files. A click on a row there
// sends that day with the ticker, so the linked charts and news show the stock as it was
// then, and the "OV picks" watchlist holds that day's top picks until Today.
// Each cell takes the tone and note the module gives it, and the columns that fill after
// the open say so; the ▸ arrow opens a trade card when the module sends levels, notes or
// groups (Ash, 2026-11-07, layout "C1 + E1"; card.ts, TradeCard.tsx).
// Each row's rank is a badge (gold for OV's first pick, green for the other top picks), and
// the parts that made up its score are coloured cells after the columns, so what puts each
// name above the next reads without words (Ash, 2026-11-08, round 3 layout "C1"; parts.ts).
import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { usePoll } from '../../lib/usePoll'
import { DASH } from '../../lib/format'
import { linkSymbol } from '../../stores/linkStore'
import { useWatchlists } from '../../stores/watchlistsStore'
import { Empty } from '../../components/primitives'
import type { OvPick, OvPicksAnswer, OvPicksConfig, OvPicksHistory, OvPicksPreview } from './types'
import { KIND_RANGE_ATR, fmtCell, hasMeanings, liveKind, metricOf, toneClass } from './card'
import { FieldList, TradeCard } from './TradeCard'
import { lostText, partHeat, partLabels, partTitle, rankLooks, rankTitle } from './parts'
import { fetchOvPicks, fetchOvPicksDays, fillOvPicksWatchlist, syncOvPicksWatchlist, watchlistHas } from './api'
import { OV_PICKS_DAYS_POLL_MS, OV_PICKS_PAST_POLL_MS, OV_PICKS_POLL_MS, OV_PICKS_WATCHLIST_ID } from './defaults'
import { allWarnings, badges, dayLabel, dayNotes, fmtValue, historyBadges, historyNotes, isPastDay, metricValue, noPastDay, noPicks, previewBadges, previewNotes, routeProblem, smallColumn, topPicks, wholeColumn, whenET, type Note } from './view'
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

/** A row's details: the trade card when the module says what its values mean, else every
 *  field as the module wrote it. */
function Details({ p }: { p: OvPick }) {
  if (hasMeanings(p)) return <div className="ovp-details"><TradeCard p={p} /></div>
  return (
    <div className="ovp-details">
      <div className="ovp-details-head">
        <b className="mono">{p.ticker}</b> rank {p.rank}, {p.status || DASH}{p.top ? ', one of OV\'s top picks' : ''}.
        {' '}<span className="faint">{p.details.length} field{p.details.length === 1 ? '' : 's'}, as the module wrote them.</span>
      </div>
      {p.details.length === 0
        ? <div className="faint ovp-details-none">The module sent no details for this row.</div>
        : <FieldList entries={p.details} />}
    </div>
  )
}

/** Under a table with coloured cells: what the colours are. The module says what each one
 *  means, in the cell's tooltip. */
function ToneKey({ picks }: { picks: OvPick[] }) {
  const tones = new Set(picks.flatMap(p => p.metrics.map(m => m.tone).filter(Boolean)))
  const parts = picks.some(p => p.score_parts?.length)
  if (!tones.size && !parts) return null
  return (
    <div className="ovp-key faint">
      {parts && (
        <>
          <span><i className="ovp-rb ovp-rb-first">1</i>OV's first pick</span>
          <span><i className="ovp-rb ovp-rb-top">2</i>top picks</span>
          <span><i className="ovp-rb ovp-rb-next">5</i>next two</span>
          <span className="ovp-key-sep">score parts:</span>
          <span><i className="ovp-sw ovp-part-full" />full</span>
          <span><i className="ovp-sw ovp-part-short" />short</span>
          <span><i className="ovp-sw ovp-part-well-short" />well short</span>
          <span><i className="ovp-sw ovp-part-decides" />puts it ahead of the next name</span>
          {tones.size > 0 && <span className="ovp-key-sep">columns:</span>}
        </>
      )}
      {tones.has('good') && <span><i className="ovp-sw ovp-tone-good" />good</span>}
      {tones.has('caution') && <span><i className="ovp-sw ovp-tone-caution" />caution</span>}
      {tones.has('bad') && <span><i className="ovp-sw ovp-tone-bad" />warning</span>}
      <span>as the OV picks module marks them; hover a cell for what it means.</span>
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
  /** the row last sent to the linked windows */
  selTicker: string | null
  pick: (ticker: string) => void
  /** the "top" badge's title */
  topTitle: string
  /** a past day: a row click sends it to the linked windows; null for today */
  linkDay: string | null
}

/** The ranked list: the frozen picks, a preview of them, or a past day's list. A click on a
 *  row sends its ticker to the linked windows; the ▸ arrow opens its details. */
function RankedTable({ win, labels, picks, inUniverse, openTicker, toggle, selTicker, pick, topTitle, linkDay }: TableProps) {
  // A column of numbers is right-aligned, header included. A live column says when it fills.
  const kinds = labels.map(l => liveKind(picks, l))
  const numeric = labels.map((l, i) => kinds[i] === KIND_RANGE_ATR || picks.some(p => typeof metricValue(p, l) === 'number'))
  const whole = labels.map(l => wholeColumn(picks, l))
  const small = labels.map(l => smallColumn(picks, l))
  const parts = partLabels(picks)
  const looks = rankLooks(picks)
  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, ticker: string) => {
    if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
    e.preventDefault()
    pick(ticker)
  }
  const rowTitle = (ticker: string) => win.link === 'none'
    ? 'Pick a link color for this window to show its tickers in charts and news'
    : `Click to show ${ticker}${linkDay ? ` on ${linkDay}` : ''} in this window's linked charts and news`
  return (
    <table className="ovp-table">
      <thead>
        <tr>
          <th className="ovp-tk">Ticker</th>
          {labels.map((l, i) => (
            <th key={l} className={numeric[i] ? 'num' : ''} title={kinds[i] ? 'Fills after the open, from the 09:30 ET minute bar (at 09:31 ET)' : undefined}>
              {l}{kinds[i] && <span className="ovp-when">09:31</span>}
            </th>
          ))}
          {parts.map((l, k) => (
            <th key={`part:${l}`} className={`num ovp-parth${k === 0 ? ' ovp-part1' : ''}`}
              title={`A part of OV's score. Faint: full points; amber or orange: points lost; ringed: the part that puts the name ahead of the next one. Hover a cell for its points.`}>
              {l}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {picks.map((p, i) => {
          const outside = inUniverse?.[p.ticker] === false
          const isOpen = openTicker === p.ticker
          // A line under the last top pick, where the rest of the ranked list starts.
          const lastTop = p.top && !picks[i + 1]?.top && i + 1 < picks.length
          const look = looks[i]
          const cls = ['ovp-row', p.top ? 'ovp-toprow' : '', look === 'first' ? 'ovp-first' : '', lastTop ? 'ovp-split' : '', isOpen ? 'ovp-open' : '', selTicker === p.ticker ? 'ovp-sel' : ''].filter(Boolean).join(' ')
          const decides = p.rank_reason?.decides ?? null
          return (
            <Fragment key={p.ticker}>
              <tr className={cls} tabIndex={0} onClick={() => pick(p.ticker)} onKeyDown={e => onRowKey(e, p.ticker)} title={rowTitle(p.ticker)}>
                <td className="ovp-tk">
                  <button className="ovp-chev" aria-expanded={isOpen} aria-label={`${isOpen ? 'Close' : 'Open'} the details of ${p.ticker}`}
                    onClick={e => { e.stopPropagation(); toggle(p.ticker) }} title={isOpen ? 'Close the details' : 'Show every field of this row'}>{isOpen ? '▾' : '▸'}</button>
                  <span className={`ovp-rb ovp-rb-${look}`} title={rankTitle(p, look, topTitle)} aria-label={rankTitle(p, look, topTitle)}>{p.rank}</span>
                  <span className="ovp-sym sym">{p.ticker}</span>
                  {p.earnings_label && <span className="badge ovp-b-info ovp-tag" title="Earnings label">{p.earnings_label}</span>}
                  {outside && <span className="badge ovp-b-warn ovp-tag" title="Not in the scanner's universe: no live bars and no setups. Charts still work.">no setups</span>}
                </td>
                {labels.map((l, j) => {
                  const m = metricOf(p, l)
                  const cls = [numeric[j] ? 'num mono' : '', toneClass(m?.tone), kinds[j] && (m?.value === null || m?.value === undefined) ? 'ovp-wait' : ''].filter(Boolean).join(' ')
                  return <td key={l} className={cls} title={m?.note || undefined}>{fmtCell(m, whole[j], small[j])}</td>
                })}
                {parts.map((l, k) => {
                  const part = p.score_parts?.find(x => x.label === l)
                  const first = k === 0 ? ' ovp-part1' : ''
                  if (!part) return <td key={`part:${l}`} className={`num mono ovp-part${first}`}>{DASH}</td>
                  const ring = decides === l
                  const lost = lostText(part)
                  return (
                    <td key={`part:${l}`} className={`num mono ovp-part ovp-part-${partHeat(part)}${ring ? ' ovp-part-decides' : ''}${first}`} title={partTitle(part, ring)}>
                      {fmtValue(part.value)}{lost && <sup className="ovp-lost">{lost}</sup>}
                    </td>
                  )
                })}
              </tr>
              {isOpen && (
                <tr className="ovp-details-row">
                  <td colSpan={labels.length + parts.length + 1}><Details p={p} /></td>
                </tr>
              )}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}

interface DayNavProps {
  /** the day the card shows */
  shown: string
  today: string
  /** the days with a list, oldest first */
  days: string[]
  /** null: today's view */
  picked: string | null
  daysError: string | null
  go: (day: string | null) => void
}

/** The calendar: the previous and next days with a list, any day by date, and back to today. */
function DayNav({ shown, today, days, picked, daysError, go }: DayNavProps) {
  const prev = [...days].reverse().find(d => d < shown) ?? null
  const next = days.find(d => d > shown) ?? null
  const why = daysError ? ` The list of days could not be read: ${daysError}` : ''
  return (
    <span className="ovp-nav">
      <button className="btn sm" onClick={() => go(prev)} disabled={!prev} aria-label="Previous day with a list"
        title={prev ? `Show ${dayLabel(prev)}` : `No earlier day with a list.${why}`}>‹</button>
      <input type="date" className="input sm mono ovp-date" aria-label="Pick a day" value={shown} min={days[0]} max={today || undefined}
        onChange={e => { if (e.target.value) go(e.target.value) }}
        title={`Pick a day to show its list. Days with a list: ${days.length ? `${days[0]} to ${days[days.length - 1]}` : 'none yet'}.${why}`} />
      <button className="btn sm" onClick={() => go(next)} disabled={!picked} aria-label="Next day with a list"
        title={picked ? (next && next < today ? `Show ${dayLabel(next)}` : 'Back to today') : 'This is today'}>›</button>
      {picked && <button className="btn sm" onClick={() => go(null)} title="Back to today's live picks">Today</button>}
    </span>
  )
}

export function OvPicksWindow({ win }: { win: OvPicksConfig }) {
  // The calendar's day: null is today, live; YYYY-MM-DD is a past day. Not saved: a
  // reload opens on today.
  const [picked, setPicked] = useState<string | null>(null)
  const { data: answer, error, loading, refresh } = usePoll(() => fetchOvPicks(picked), picked ? OV_PICKS_PAST_POLL_MS : OV_PICKS_POLL_MS, true, [picked])
  const { data: daysAnswer, error: daysError } = usePoll(fetchOvPicksDays, OV_PICKS_DAYS_POLL_MS)
  // usePoll keeps the last answer while the next one loads: show only the answer for the
  // day asked, so a slow reply never puts one day's list under another day's date.
  const data = answer && (answer.asked_date ?? null) === picked ? answer : null
  const ovList = useWatchlists(s => s.lists[OV_PICKS_WATCHLIST_ID])
  const day = data?.day ?? null
  const past = data ? isPastDay(data) : false
  // The watchlist holds the top picks only, like the scanner's morning upsert.
  const tickers = day?.has_picks ? topPicks(day).map(p => p.ticker) : []
  const picksKey = data?.is_today && day && tickers.length ? `${day.trading_date}|${day.frozen_at}|${tickers.join(',')}` : ''
  // The open row belongs to one frozen list; a new day's list opens with every row closed.
  // A preview keeps its open row from minute to minute, until the picks freeze.
  const preview: OvPicksPreview | null = day && !day.has_picks && data?.preview?.has_preview ? data.preview : null
  const history: OvPicksHistory | null = day && !day.has_picks && data?.history?.has_history ? data.history : null
  const dayKey = preview ? `${preview.trading_date}|preview` : history ? `${history.trading_date}|history` : day ? `${day.trading_date}|${day.frozen_at}` : ''
  const [open, setOpen] = useState<{ key: string; ticker: string } | null>(null)
  const openTicker = open?.key === dayKey ? open.ticker : null
  const toggle = (ticker: string) => setOpen(openTicker === ticker ? null : { key: dayKey, ticker })
  // The row last sent to the linked windows, marked in this day's list.
  const [sel, setSel] = useState<{ key: string; ticker: string } | null>(null)
  const selTicker = sel?.key === dayKey ? sel.ticker : null
  const pickFor = (linkDay: string | null) => (ticker: string) => {
    linkSymbol(win, ticker, null, linkDay)
    setSel({ key: dayKey, ticker })
  }

  // The "OV picks" watchlist follows the calendar (Ash, 2026-11-07): a past day puts that
  // day's top picks there, named with its date; back on today's view, the shown day's picks
  // go back. Without a list for the day it stays as it is.
  const wlLoaded = useWatchlists(s => s.loaded)
  const listDay = ovList?.ov_picks_day ?? null
  const shownTop = (rows: OvPick[]) => rows.filter(p => p.top).sort((x, y) => x.rank - y.rank).map(p => p.ticker)
  const wantList: { day: string | null; tickers: string[] } | null = !data ? null
    : past ? (history ? { day: history.trading_date, tickers: shownTop(history.picks) }
      : day?.has_picks ? { day: day.trading_date, tickers: shownTop(day.picks) } : null)
    : listDay && day?.has_picks ? { day: null, tickers: shownTop(day.picks) } : null
  const wantKey = wantList ? `${wantList.day ?? ''}|${wantList.tickers.join(',')}` : ''
  const haveKey = ovList ? `${listDay ?? ''}|${ovList.symbols.join(',')}` : ''
  useEffect(() => {
    if (!wlLoaded || !wantList || wantKey === haveKey) return
    fillOvPicksWatchlist(wantList.tickers, wantList.day).catch(() => { /* the next answer tries again */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wlLoaded, wantKey, haveKey])

  // Keep this tab's "OV picks" watchlist in step with the scanner's morning upsert.
  // Runs on every poll (`data`), at most WATCHLIST_SYNC_TRIES times per day's picks.
  const sync = useRef({ key: '', tries: 0, done: false })
  useEffect(() => {
    // While the list holds a past day, the effect below puts today's picks back itself.
    if (!picksKey || ovList?.ov_picks_day) return
    const s = sync.current
    if (s.key !== picksKey) { s.key = picksKey; s.tries = 0; s.done = false }
    if (s.done || s.tries >= WATCHLIST_SYNC_TRIES) return
    s.tries += 1
    syncOvPicksWatchlist(picksKey.split('|')[2].split(','))
      .then(ok => { if (ok && sync.current.key === picksKey) sync.current.done = true })
      .catch(() => { /* the next poll tries again */ })
  }, [picksKey, data, ovList?.ov_picks_day])

  const today = data?.today_et ?? answer?.today_et ?? daysAnswer?.today_et ?? ''
  const shown = picked ?? data?.trading_date ?? today
  // A day from today on is today's view; the route has nothing after today.
  const go = (d: string | null) => setPicked(d && today && d < today ? d : null)
  const nav = today
    ? <DayNav shown={shown} today={today} days={daysAnswer?.days ?? []} picked={picked} daysError={daysError ?? (daysAnswer?.reason_code ? daysAnswer.reason : null)} go={go} />
    : null
  const header = (extra?: ReactNode) => (
    <div className="ovp-top">
      {nav}
      {extra}
      <span className="flex-spacer" />
      <button className="btn sm" onClick={() => void refresh()} disabled={loading} title={picked ? 'Read this day again now' : `Read the picks again now (the card refreshes every ${OV_PICKS_POLL_MS / 1000} s)`}>{loading ? '…' : 'Refresh'}</button>
    </div>
  )

  if (!data) {
    if (error && !loading) return <div className="ovp-wrap">{header()}<Empty title="OV picks are not available">{error}</Empty></div>
    return <div className="ovp-wrap">{header()}<Empty title={picked ? `Reading ${dayLabel(picked)}…` : 'Reading OV picks…'} /></div>
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
              openTicker={openTicker} toggle={toggle} selTicker={selTicker} pick={pickFor(null)} topTitle={`One of OV's top picks as of ${asOf || 'this preview'}: not frozen`} linkDay={null} />
          )}
          <ToneKey picks={preview.picks} />
          <Notes notes={previewNotes(data, preview)} />
          <Warnings a={data} />
          {preview.picks.length > 0 && (
            <div className="ovp-foot faint">
              A preview, not the picks: highlighted rows are OV's top picks as of that minute, and they may change until 09:25 ET. Click a row to show it in the linked charts and news; click ▸ for its full details. This card shows the list only: it places no orders and sizes nothing.
            </div>
          )}
        </div>
      </div>
    )
  }

  if (history) {
    const top = history.picks.filter(p => p.top).length
    return (
      <div className="ovp-wrap">
        {header(
          <>
            <span className="ovp-day">{dayLabel(history.trading_date)}</span>
            <span className="faint ovp-count">{history.picks.length} ranked, {top} top</span>
            {historyBadges(history).map(b => <span key={b.text} className={`badge ovp-b-${b.tone}`} title={b.title}>{b.text}</span>)}
          </>,
        )}
        {stale}
        <div className="ovp-scroll">
          {history.picks.length > 0 && (
            <RankedTable win={win} labels={history.metric_labels} picks={history.picks} inUniverse={null}
              openTicker={openTicker} toggle={toggle} selTicker={selTicker} pick={pickFor(history.trading_date)} topTitle="One of OV's top picks that day" linkDay={history.trading_date} />
          )}
          <ToneKey picks={history.picks} />
          <Notes notes={historyNotes(data, history)} />
          <Warnings a={data} />
          {history.picks.length > 0 && (
            <div className="ovp-foot faint">
              OV's own list for a past day. Highlighted rows are OV's top picks that day; they are in the "OV picks" watchlist until you go back to Today. Click a row to show it on that day in the linked charts and news; click ▸ for its full details. This card shows the list only: it places no orders and sizes nothing.
            </div>
          )}
        </div>
      </div>
    )
  }

  if (!day.has_picks) {
    const { title, notes } = past ? noPastDay(data, day) : noPicks(data, day)
    return (
      <div className="ovp-wrap">
        {header(
          <>
            <span className="ovp-day">{dayLabel(day.trading_date)}</span>
            {past && <span className="badge ovp-b-info" title="Not today: a day picked in the calendar.">past day</span>}
          </>,
        )}
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
          {past && <span className="badge ovp-b-info" title="Not today: a day picked in the calendar.">past day</span>}
          {badges(day).map(b => <span key={b.text} className={`badge ovp-b-${b.tone}`} title={b.title}>{b.text}</span>)}
        </>,
      )}
      {stale}
      <div className="ovp-scroll">
        {day.picks.length > 0 && (
          <RankedTable win={win} labels={day.metric_labels} picks={day.picks} inUniverse={data.in_universe}
            openTicker={openTicker} toggle={toggle} selTicker={selTicker} pick={pickFor(past ? day.trading_date : null)}
            topTitle={past ? 'One of OV\'s top picks that day' : 'One of OV\'s top picks today'} linkDay={past ? day.trading_date : null} />
        )}
        <ToneKey picks={day.picks} />
        <Notes notes={dayNotes(data, day)} />
        <Warnings a={data} />
        {day.picks.length > 0 && (
          <div className="ovp-foot faint">
            {(data.is_today || past) && inList ? 'The top picks are in the "OV picks" watchlist. ' : ''}
            Highlighted rows are OV's top picks{past ? ' that day' : ''}. Click a row to show it{past ? ' on that day' : ''} in the linked charts and news; click ▸ for its full details. This card shows the list only: it places no orders and sizes nothing.
          </div>
        )}
      </div>
    </div>
  )
}
