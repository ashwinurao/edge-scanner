import { useEffect, useState } from 'react'
import type { ClockInfo, SpyRvPayload, SpyRvPoint } from '../types'
import { api } from '../lib/api'
import { Menu } from './Menu'

/** SPY RV20 next to the regime pill (fork; Ash, project thread, 2026-11-12). Display only.
 *  Red when SPY is calm (RV20 at or below 10.5, scanner/spy_rv.py CALM_MAX), green above it.
 *  There is no yellow band: in the 2026 study, days at 10.5-12 did as well as 12-15.
 *  A click opens how it moved over the last 7, 14 or 30 sessions (Ash, 2026-11-13). */
export function SpyRvBadge({ info }: { info: ClockInfo | null | undefined }) {
  const [open, setOpen] = useState(false)
  const rv = info?.spy_rv
  if (!rv) {
    return (
      <span className="regime spy-rv" title="SPY RV20 is not known yet: the scanner needs 21 daily SPY closes">
        RV20 –
      </span>
    )
  }
  const cut = rv.calm_max
  const through = rv.through ? ` through the ${rv.through} close` : ''
  // Ash adopted half size on calm days for the OR1 strategy (study thread, 2026-11-12 03:41 UTC).
  const meaning = rv.calm
    ? `Calm SPY (${cut} or lower): OV picks lost their OR1/OR2 edge on such days. OR1 at half size.`
    : `Above ${cut}: not a calm SPY day. OR1 at full size.`
  return (
    <div style={{ position: 'relative' }}>
      <button type="button" className={`regime spy-rv ${rv.calm ? 'calm' : 'active'}${open ? ' open' : ''}`}
        aria-expanded={open} onClick={() => setOpen(o => !o)}
        // The menu closes on any outside mousedown; keep this one from closing it just before
        // the click toggles it, which would reopen it.
        onMouseDown={e => e.stopPropagation()}
        title={`SPY RV20 ${rv.rv20.toFixed(2)}${through}: 20-day close-to-close volatility, annualized, in %. ${meaning} Updates before each session (03:30 ET). Click for the last 7, 14 or 30 sessions.`}>
        RV20 {rv.rv20.toFixed(1)}
      </button>
      <Menu open={open} onClose={() => setOpen(false)} right style={{ width: 300, padding: 10 }}>
        <SpyRvHistory />
      </Menu>
    </div>
  )
}

const SPANS = [7, 14, 30] as const
type Span = typeof SPANS[number]
const SPAN_KEY = 'scanner-v2-spy-rv-span'   // a per-browser convenience only

function readSpan(): Span {
  try {
    const n = Number(localStorage.getItem(SPAN_KEY))
    if ((SPANS as readonly number[]).includes(n)) return n as Span
  } catch { /* storage blocked: use the default */ }
  return 7
}

function SpyRvHistory() {
  const [span, setSpan] = useState<Span>(readSpan)
  const [data, setData] = useState<SpyRvPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api.spyRv().then(d => { if (live) setData(d) }).catch(e => { if (live) setError(String(e?.message ?? e)) })
    return () => { live = false }
  }, [])

  const pick = (s: Span) => {
    setSpan(s)
    try { localStorage.setItem(SPAN_KEY, String(s)) } catch { /* ignore */ }
  }

  const rows = (data?.history ?? []).slice(-span)
  const short = data && rows.length < span
  return (
    <div className="spy-rv-panel">
      <div className="spy-rv-head">
        <span className="spy-rv-title">SPY RV20, last {span} sessions</span>
        <div className="seg" role="group" aria-label="Sessions to show">
          {SPANS.map(s => <button key={s} type="button" className={s === span ? 'on' : ''} onClick={() => pick(s)}>{s}</button>)}
        </div>
      </div>
      {error && <div className="down" style={{ fontSize: 11 }}>Could not load the history: {error}</div>}
      {!data && !error && <div className="faint" style={{ fontSize: 11 }}>Loading…</div>}
      {data && rows.length === 0 && (
        <div className="faint" style={{ fontSize: 11 }}>No history yet: the scanner needs 21 daily SPY closes.</div>
      )}
      {rows.length > 0 && <>
        <Summary rows={rows} />
        <Chart rows={rows} cut={data!.calm_max} />
        {short && <div className="faint" style={{ fontSize: 10.5 }}>Only {rows.length} sessions are loaded.</div>}
        <div className="spy-rv-list mono">
          {[...rows].reverse().map(r => (
            <div key={r.date} className="spy-rv-row">
              <span className="faint">{r.date}</span>
              <span className={r.calm ? 'down' : 'up'}>{r.rv20.toFixed(2)}</span>
            </div>
          ))}
        </div>
        <div className="faint" style={{ fontSize: 10.5 }}>
          Each value is RV20 after that day's close: the number the badge showed the next morning.
          Red at {data!.calm_max} or lower (calm).
        </div>
      </>}
    </div>
  )
}

function Summary({ rows }: { rows: SpyRvPoint[] }) {
  const first = rows[0], last = rows[rows.length - 1]
  const diff = last.rv20 - first.rv20
  const calmDays = rows.filter(r => r.calm).length
  return (
    <div className="spy-rv-summary">
      <span className="mono">{first.rv20.toFixed(1)} → {last.rv20.toFixed(1)}</span>
      <span className={`mono ${diff < 0 ? 'down' : diff > 0 ? 'up' : 'faint'}`}>{diff > 0 ? '+' : ''}{diff.toFixed(1)}</span>
      <span className="faint">{calmDays} of {rows.length} calm</span>
    </div>
  )
}

/** Line of RV20 per close, with the calm line dashed. Points take the badge's colours. */
function Chart({ rows, cut }: { rows: SpyRvPoint[]; cut: number }) {
  const W = 280, H = 100, PAD_L = 28, PAD_R = 6, PAD_T = 6, PAD_B = 18
  const vals = rows.map(r => r.rv20)
  const lo = Math.floor(Math.min(...vals, cut) - 0.5)
  const hi = Math.ceil(Math.max(...vals, cut) + 0.5)
  const x = (i: number) => PAD_L + (rows.length === 1 ? (W - PAD_L - PAD_R) / 2 : i * (W - PAD_L - PAD_R) / (rows.length - 1))
  const y = (v: number) => PAD_T + (hi - v) * (H - PAD_T - PAD_B) / (hi - lo || 1)
  const path = rows.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(r.rv20).toFixed(1)}`).join(' ')
  return (
    <svg className="spy-rv-chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
      aria-label={`SPY RV20 over ${rows.length} sessions, from ${vals[0].toFixed(1)} to ${vals[vals.length - 1].toFixed(1)}`}>
      {/* Axis ends, unless they would sit on the calm label */}
      {Math.abs(y(hi) - y(cut)) > 12 && <text x={PAD_L - 4} y={y(hi) + 4} textAnchor="end" style={{ fill: 'var(--text-faint)', fontSize: 9 }}>{hi}</text>}
      {Math.abs(y(lo) - y(cut)) > 12 && <text x={PAD_L - 4} y={y(lo)} textAnchor="end" style={{ fill: 'var(--text-faint)', fontSize: 9 }}>{lo}</text>}
      <line x1={PAD_L} x2={W - PAD_R} y1={y(cut)} y2={y(cut)} style={{ stroke: 'var(--down)', strokeDasharray: '3 3', strokeWidth: 1 }} />
      <text x={PAD_L - 4} y={y(cut) + 3} textAnchor="end" style={{ fill: 'var(--down-text)', fontSize: 9 }}>{cut}</text>
      <path d={path} style={{ fill: 'none', stroke: 'var(--text-dim)', strokeWidth: 1.5 }} />
      {rows.map((r, i) => (
        <circle key={r.date} cx={x(i)} cy={y(r.rv20)} r={rows.length > 14 ? 2 : 3}
          style={{ fill: r.calm ? 'var(--down)' : 'var(--up)' }}><title>{`${r.date}: ${r.rv20.toFixed(2)}`}</title></circle>
      ))}
      <text x={PAD_L} y={H - 2} style={{ fill: 'var(--text-faint)', fontSize: 9 }}>{rows[0].date.slice(5)}</text>
      <text x={W - PAD_R} y={H - 2} textAnchor="end" style={{ fill: 'var(--text-faint)', fontSize: 9 }}>{rows[rows.length - 1].date.slice(5)}</text>
    </svg>
  )
}
