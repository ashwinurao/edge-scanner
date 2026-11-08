import type {
  ISeriesPrimitive, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, Logical,
  SeriesAttachedParameter, Time,
} from 'lightweight-charts'
import type { OrbSegment } from './orbStudy'

type Target = Parameters<ISeriesPrimitivePaneRenderer['draw']>[0]
export interface OrbColors { or1: string; or2: string; stop: string }

/** fork: draws the opening-range breakout studies' levels over the candles
 *  (src/lib/orbStudy.ts; Ash, 2026-11-08): the range as dotted lines, the entry as
 *  a solid line and the stop as a dashed line, each from its first bar to its last.
 *  Display only: nothing here places an order or feeds the engine. */
export class OrbPrimitive implements ISeriesPrimitive<Time> {
  private param: SeriesAttachedParameter<Time> | null = null
  private segments: OrbSegment[] = []
  private colors: OrbColors
  private readonly view: ISeriesPrimitivePaneView

  constructor(colors: OrbColors) {
    this.colors = colors
    const renderer: ISeriesPrimitivePaneRenderer = { draw: (target: Target) => this.draw(target) }
    this.view = { zOrder: () => 'normal', renderer: () => renderer }
  }

  attached(param: SeriesAttachedParameter<Time>) { this.param = param }
  detached() { this.param = null }
  paneViews() { return [this.view] }

  setSegments(segments: OrbSegment[]) {
    this.segments = segments
    this.param?.requestUpdate()
  }

  setColors(colors: OrbColors) {
    this.colors = colors
    this.param?.requestUpdate()
  }

  private draw(target: Target) {
    const p = this.param
    if (!p || !this.segments.length) return
    const ts = p.chart.timeScale()
    // Half a bar either side, so a level covers its first and last candles. The bar
    // width comes from two whole indexes (lightweight-charts 4 returns 0 for a
    // fractional one; see sessionPrimitive.ts).
    const x0 = ts.logicalToCoordinate(0 as Logical), x1 = ts.logicalToCoordinate(1 as Logical)
    if (x0 == null || x1 == null) return
    const half = (x1 - x0) / 2
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      for (const s of this.segments) {
        const a = ts.timeToCoordinate(s.from as Time), b = ts.timeToCoordinate(s.to as Time)
        const y = p.series.priceToCoordinate(s.price)
        if (a == null || b == null || y == null) continue
        const color = s.kind === 'stop' ? this.colors.stop : s.n === 1 ? this.colors.or1 : this.colors.or2
        ctx.strokeStyle = color
        ctx.lineWidth = Math.max(1, Math.floor((s.kind === 'range' ? 1 : 2) * hpr))
        ctx.setLineDash(s.kind === 'range' ? [2 * hpr, 3 * hpr] : s.kind === 'stop' ? [6 * hpr, 4 * hpr] : [])
        const py = Math.round(y * vpr) + (ctx.lineWidth % 2 ? 0.5 : 0)
        ctx.beginPath()
        ctx.moveTo(Math.round((a - half) * hpr), py)
        ctx.lineTo(Math.round((b + half) * hpr), py)
        ctx.stroke()
      }
      ctx.setLineDash([])
    })
  }
}
