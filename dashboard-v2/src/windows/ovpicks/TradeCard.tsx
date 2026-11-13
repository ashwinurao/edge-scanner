// fork: the OV picks window's details card, "E1" (Ash, 2026-11-07): the row's price
// levels as a ladder, the numbers the module explains as tiles with their one-line notes,
// the first group of fields beside them, and every field, grouped, one click away.
// What each value means comes from the module and the scanner (card.ts); this lays it out.
import { DASH } from '../../lib/format'
import type { OvEntry, OvPick } from './types'
import { fmtCell, fmtPrice, groupDetails, ladder, liveDetail, toneClass, type Ladder } from './card'
import { fmtValue } from './view'
import { snapshotBar } from './live'

// Details show each value as the module wrote it: whole numbers get thousands separators,
// other numbers keep every digit.
const fmtDetail = (v: OvEntry['value']) =>
  typeof v === 'number' && Number.isFinite(v) && !Number.isInteger(v) ? String(v) : fmtValue(v)

export function FieldList({ entries }: { entries: OvEntry[] }) {
  return (
    <dl className="ovp-dl">
      {entries.map((d, i) => (
        <div key={i} className="ovp-dl-item">
          <dt title={d.label}>{d.label}</dt>
          <dd className={typeof d.value === 'number' ? 'mono' : ''}>{fmtDetail(d.value)}</dd>
        </div>
      ))}
    </dl>
  )
}

const MARK_CLASS = { pm: 'ovp-lad-pm', pd: 'ovp-lad-pd', last: 'ovp-lad-last', fm: 'ovp-lad-fm', open: 'ovp-lad-open' } as const

function LevelLadder({ lad, ticker }: { lad: Ladder; ticker: string }) {
  const W = 260, AXIS = 64, LABEL = 96
  const said = lad.marks.map(m => `${m.label} ${fmtPrice(m.price)}`).join(', ')
  return (
    <svg className="ovp-ladder" viewBox={`0 0 ${W} ${lad.height}`} width="100%" role="img"
      aria-label={`Price levels for ${ticker}: ${said}${lad.atr ? `; one ATR is $${fmtPrice(lad.atr.dollars, lad.marks[0].price)}` : ''}`}>
      <line className="ovp-lad-axis" x1={AXIS} y1={4} x2={AXIS} y2={lad.height - 4} />
      {lad.pm && <rect className="ovp-lad-bar-pm" x={AXIS - 8} y={lad.pm.from} width={5} height={Math.max(1, lad.pm.to - lad.pm.from)} />}
      {lad.pd && <rect className="ovp-lad-bar-pd" x={AXIS + 3} y={lad.pd.from} width={5} height={Math.max(1, lad.pd.to - lad.pd.from)} />}
      {lad.fm && <rect className="ovp-lad-bar-fm" x={AXIS - 15} y={lad.fm.from} width={5} height={Math.max(1, lad.fm.to - lad.fm.from)} />}
      {lad.atr && (
        <g className="ovp-lad-atr">
          <line x1={20} y1={lad.atr.from} x2={20} y2={lad.atr.to} />
          <line x1={16} y1={lad.atr.from} x2={24} y2={lad.atr.from} />
          <line x1={16} y1={lad.atr.to} x2={24} y2={lad.atr.to} />
          <text x={11} y={(lad.atr.from + lad.atr.to) / 2} transform={`rotate(-90 11 ${(lad.atr.from + lad.atr.to) / 2})`} textAnchor="middle">1 ATR ${fmtPrice(lad.atr.dollars, lad.marks[0].price)}</text>
        </g>
      )}
      {lad.marks.map(m => (
        <g key={m.key} className={MARK_CLASS[m.kind]}>
          {m.kind === 'last' || m.kind === 'open'
            ? <circle cx={AXIS} cy={m.y} r={m.kind === 'open' ? 3.5 : 3} />
            : <line x1={AXIS - 9} y1={m.y} x2={AXIS + 9} y2={m.y} />}
          <line className="ovp-lad-lead" x1={AXIS + 10} y1={m.y} x2={LABEL - 4} y2={m.labelY - 3.5} />
          <text x={LABEL} y={m.labelY}>{m.label}</text>
          <text x={W - 6} y={m.labelY} textAnchor="end" className="mono">{fmtPrice(m.price)}</text>
        </g>
      ))}
    </svg>
  )
}

/** The trade card for one row. */
export function TradeCard({ p }: { p: OvPick }) {
  const lad = ladder(p.levels, p.first_minute)
  const tiles = p.metrics.filter(m => m.tone || m.note)
  const groups = groupDetails(p.details)
  const [first, ...rest] = groups
  return (
    <div className="ovp-card">
      <div className="ovp-details-head">
        <b className="mono">{p.ticker}</b>
        {p.name && <> · {p.name}</>}
        {' '}· rank {p.rank}{p.top ? ', one of OV\'s top picks' : ''} · {p.status || DASH}
        {p.earnings_label && <> · <span className="badge ovp-b-info">{p.earnings_label}</span></>}
      </div>
      <div className="ovp-card-grid">
        {lad && (
          <section className="ovp-box">
            <h4>Levels</h4>
            <LevelLadder lad={lad} ticker={p.ticker} />
            <p className="faint ovp-box-note">
              Orange: the pre-market range. Grey: the prior day's range.{p.first_minute
                ? (snapshotBar(p) ? ' Blue: the first minute as Alpaca\'s snapshot shows it, not confirmed yet.'
                  : p.first_minute_as_of ? ` Blue: the first minute so far, to ${p.first_minute_as_of} ET.` : ' Blue: the first minute.')
                : ' The first minute shows from 09:30 ET.'}
            </p>
          </section>
        )}
        {tiles.length > 0 && (
          <section className="ovp-tiles" aria-label="What the numbers mean">
            {tiles.map(m => {
              const extra = liveDetail(m, p.levels, p.first_minute)
              return (
                <div key={m.label} className={`ovp-tile ${toneClass(m.tone)}`}>
                  <span className="ovp-tile-k">{m.label}</span>
                  <span className="ovp-tile-v mono">{fmtCell(m, Number.isInteger(m.value))}</span>
                  <span className="ovp-tile-w">{extra}{extra && m.note ? ' ' : ''}{m.note}</span>
                </div>
              )
            })}
          </section>
        )}
        {first && (
          <section className="ovp-box">
            <h4>{first.name}</h4>
            <FieldList entries={first.entries} />
          </section>
        )}
      </div>
      {rest.length > 0 && (
        <details className="ovp-all">
          <summary>All fields, grouped ({p.details.length - first.entries.length} more)</summary>
          {rest.map(g => (
            <section key={g.name} className="ovp-group">
              <h5>{g.name}</h5>
              <FieldList entries={g.entries} />
            </section>
          ))}
        </details>
      )}
    </div>
  )
}
