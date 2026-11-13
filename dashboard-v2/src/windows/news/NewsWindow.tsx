import { useEffect, useState } from 'react'
import type { NewsConfig, NewsItem } from '../../types'
import { api } from '../../lib/api'
import { usePoll } from '../../lib/usePoll'
import { fmtAge } from '../../lib/time'
import { useLinkedDay, useLinks, linkSymbol } from '../../stores/linkStore'
import { newsSymbol } from './newsTarget'
import { Empty, Field, Select, SymbolInput, Toggle } from '../../components/primitives'
import { ArticleModal } from '../../components/ArticleModal'

/** fork: a past day's news covers at least these hours before its close (3 days). */
const PAST_DAY_HOURS = 72

export function NewsWindow({ win }: { win: NewsConfig }) {
  // fork: a linked window follows its group in either mode (newsTarget.ts; Ash, 2026-11-12).
  const groupSymbol = useLinks(s => (win.link === 'none' ? null : s.symbols[win.link]))
  const symbol = newsSymbol(win, groupSymbol)
  // fork: a past day sent from the OV picks window (Ash, 2026-11-07): the news ends at that
  // day's 16:00 ET close and covers at least the 3 days before it. Market-wide news stays live.
  const linkedDay = useLinkedDay(win)
  const day = symbol ? linkedDay : null
  const hours = day ? Math.max(win.hours, PAST_DAY_HOURS) : win.hours
  const enabled = win.mode === 'market' || !!symbol
  const { data, error } = usePoll(() => api.news(symbol ? [symbol] : null, win.limit, hours, day), day ? 600_000 : 60_000, enabled, [symbol, win.limit, hours, win.mode, day])
  const [now, setNow] = useState(() => Date.now())
  const [open, setOpen] = useState<NewsItem | null>(null)
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(t) }, [])

  if (win.mode === 'linked' && !symbol) return <Empty title="No symbol">Link this window to a color group or set a symbol in settings.</Empty>
  if (error && !data) return <Empty title="News unavailable">{error}</Empty>
  const items = data?.items ?? []
  const span = day ? `The ${Math.round(hours / 24)} days up to ${day} 16:00 ET.` : `Last ${win.hours}h.`
  if (data && items.length === 0) return <Empty title={symbol ? `No news for ${symbol}` : 'No market news'}>{`${span} ${data.error ?? ''}`}</Empty>

  return (
    <div className="news-list">
      {day && <div className="faint" style={{ padding: '3px 10px', fontSize: 10 }}>{span} Sent from the OV picks window: not live.</div>}
      {!!data?.widened_hours && <div className="faint" style={{ padding: '3px 10px', fontSize: 10 }}>Nothing in the {day ? `${Math.round(hours / 24)} days up to ${day}` : `last ${win.hours}h`} for {symbol}. Showing the latest from the {day ? `${Math.round(data.widened_hours / 24)} days before ${day} 16:00 ET` : `past ${Math.round(data.widened_hours / 24)} days`}.</div>}
      {data?.stale && <div className="faint" style={{ padding: '3px 10px', fontSize: 10 }}>showing cached news{data.error ? ` (${data.error})` : ''}</div>}
      {items.map(it => (
        <div key={it.id || it.url} className="news-item" style={{ cursor: 'pointer' }} onClick={() => setOpen(it)} title="Open article">
          {win.thumbnails && it.image && <img className="news-thumb" src={it.image} alt="" loading="lazy" />}
          <div style={{ minWidth: 0, flex: 1 }}>
            <span className="news-head">{it.headline}</span>
            <div className="news-meta">
              <span>{it.source}</span><span>·</span><span title={it.created_at}>{fmtAge(it.created_at, now)}</span>
              {it.symbols.slice(0, 6).map(s => (
                <button key={s} className="sym-chip" onClick={e => { e.stopPropagation(); linkSymbol(win, s) }} title={`Link ${s}`}>{s}</button>
              ))}
              <a className="faint" href={it.url} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()} title="Open original in a new tab" style={{ marginLeft: 'auto', textDecoration: 'none' }}>↗</a>
            </div>
            {it.summary && <div className="news-sum">{it.summary}</div>}
          </div>
        </div>
      ))}
      {open && <ArticleModal item={open} onClose={() => setOpen(null)} onSymbol={s => { linkSymbol(win, s); setOpen(null) }} />}
    </div>
  )
}

export function NewsSettings({ win, onChange }: { win: NewsConfig; onChange(p: Partial<NewsConfig>): void }) {
  return (
    <>
      <div className="grid2">
        <Field label="Mode"><Select value={win.mode} onChange={mode => onChange({ mode })} options={[{ value: 'market', label: 'Market-wide' }, { value: 'linked', label: 'Linked ticker' }]} /></Field>
        <Field label="Symbol (when unlinked)"><SymbolInput value={win.symbol} onCommit={s => onChange({ symbol: s })} /></Field>
        <Field label="Window"><Select value={String(win.hours)} onChange={v => onChange({ hours: Number(v) })} options={[{ value: '6', label: '6 hours' }, { value: '24', label: '24 hours' }, { value: '72', label: '3 days' }, { value: '168', label: '7 days' }]} /></Field>
        <Field label="Items"><Select value={String(win.limit)} onChange={v => onChange({ limit: Number(v) })} options={['20', '50'].map(v => ({ value: v, label: v }))} /></Field>
      </div>
      <Toggle checked={win.thumbnails} onChange={thumbnails => onChange({ thumbnails })} label="Show thumbnails" />
      <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>With a link color, the window shows the news for each ticker sent to that color, in either mode. Mode sets what it shows before then.</div>
    </>
  )
}
