// fork: the chart's opening-range breakout studies (Ash, project thread, 2026-11-08).
// The rules live in src/lib/orbStudy.ts; the levels are drawn by src/lib/orbPrimitive.ts.
// Display only: the chart shows where the research backtest would have traded. It
// places no order.
import { useState } from 'react'
import type { ChartConfig, ChartOverlays } from '../../types'
import { ORB_VARIANTS, describeTrade, fmtR } from '../../lib/orbStudy'
import { orbOn, type OrbStudy } from './useOrbStudy'
import { Menu, MenuHead, MenuItem, MenuSep } from '../../components/Menu'

/** The toolbar chip: one menu for the four variants. */
export function OrbChip({ win, disabled, onToggle }: { win: ChartConfig; disabled: boolean; onToggle(k: keyof ChartOverlays): void }) {
  const [open, setOpen] = useState(false)
  const on = orbOn(win.overlays)
  return (
    <span style={{ position: 'relative' }}>
      <button className={`chip ind${on.length ? ' on' : ''}`} style={{ ['--ind' as string]: 'var(--ind-or1)' }} disabled={disabled}
        title="Opening-range breakout studies: where the research backtest entered, stopped and went flat" onClick={() => setOpen(o => !o)}>
        ORB{on.length > 1 ? ` ${on.length}` : ''} ▾
      </button>
      <Menu open={open} onClose={() => setOpen(false)} right>
        <MenuHead>Opening-range breakout</MenuHead>
        {ORB_VARIANTS.map(v => (
          <MenuItem key={v.key} on={!!win.overlays[v.key]} icon={win.overlays[v.key] ? '✓' : ''} onClick={() => onToggle(v.key)}>{v.label}</MenuItem>
        ))}
        <MenuSep />
        <div className="menu-note">Study only, no orders. Rules as the 2026 backtest: first 1-cent break of the range by 10:15, stop at the other side (at least 2% of daily ATR), flat at the bar close before the time.</div>
      </Menu>
    </span>
  )
}

/** The latest day's result for each selected variant, bottom left of the chart: the
 *  study's name in its chart colour, R as a green or red badge, then what happened. */
export function OrbLegend({ study }: { study: OrbStudy }) {
  if (!study.variants.length) return null
  const last = study.days[study.days.length - 1]
  return (
    <div className="chart-orb-legend">
      <div className="dim">ORB study{last ? ` · ${last.day}` : ''} · not orders</div>
      {study.loading && <div className="dim">Loading 1-minute bars…</div>}
      {!study.loading && !last && <div className="dim">No 09:30-11:59 ET bars to work from.</div>}
      {last?.trades.map(t => (
        <div key={t.variant.key} className="orb-row">
          <span className="orb-name" style={{ ['--c' as string]: `var(--ind-or${t.variant.n})` }}>OR{t.variant.n} · {t.variant.flat}</span>
          <span className={`orb-r ${t.r == null ? 'none' : t.r >= 0 ? 'win' : 'loss'}`}>{t.r == null ? '—' : fmtR(t.r)}</span>
          <span className="orb-text">{describeTrade(t, false)}</span>
        </div>
      ))}
    </div>
  )
}
