import type {
  ISeriesPrimitive, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, Logical,
  SeriesAttachedParameter, Time,
} from 'lightweight-charts'
import type { SessionMarks } from './sessionMarks'

type Target = Parameters<ISeriesPrimitivePaneRenderer['draw']>[0]
export interface SessionColors { ext: string; dayLine: string }

/** Draws the shaded extended-hours bands and the dashed day lines behind the
 *  candles of an intraday chart. Display only: nothing here feeds the engine.
 *  Bar indexes are the chart's logical indexes, so the candle series must hold
 *  every bar the marks were built from (ChartWindow.tsx sets both from visibleBars). */
export class SessionPrimitive implements ISeriesPrimitive<Time> {
  private param: SeriesAttachedParameter<Time> | null = null
  private marks: SessionMarks = { extRuns: [], dayStarts: [] }
  private colors: SessionColors
  private readonly view: ISeriesPrimitivePaneView

  constructor(colors: SessionColors) {
    this.colors = colors
    const renderer: ISeriesPrimitivePaneRenderer = {
      draw: () => {},
      drawBackground: (target: Target) => this.drawBackground(target),
    }
    this.view = { zOrder: () => 'bottom', renderer: () => renderer }
  }

  attached(param: SeriesAttachedParameter<Time>) { this.param = param }
  detached() { this.param = null }
  paneViews() { return [this.view] }

  setMarks(marks: SessionMarks) {
    this.marks = marks
    this.param?.requestUpdate()
  }

  setColors(colors: SessionColors) {
    this.colors = colors
    this.param?.requestUpdate()
  }

  private drawBackground(target: Target) {
    const ts = this.param?.chart.timeScale()
    if (!ts) return
    // Band and line edges sit half a bar either side of a bar's centre, so a
    // band covers its bars fully and a day line falls in the gap between days.
    // logicalToCoordinate returns 0 for a fractional index in lightweight-charts 4,
    // so the bar width comes from two whole indexes.
    const x0 = ts.logicalToCoordinate(0 as Logical)
    const x1 = ts.logicalToCoordinate(1 as Logical)
    if (x0 == null || x1 == null) return
    const edge = (i: number) => x0 + (i - 0.5) * (x1 - x0)
    const { extRuns, dayStarts } = this.marks
    const { ext, dayLine } = this.colors
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      ctx.fillStyle = ext
      for (const [a, b] of extRuns) {
        const from = edge(a), to = edge(b + 1)
        if (to < 0 || from > bitmapSize.width / hpr) continue
        const left = Math.round(from * hpr)
        ctx.fillRect(left, 0, Math.max(1, Math.round(to * hpr) - left), bitmapSize.height)
      }
      // Dashed vertical line at each new day, like the "session breaks" line of
      // the common charting platforms.
      const w = Math.max(1, Math.floor(hpr))
      ctx.strokeStyle = dayLine
      ctx.lineWidth = w
      ctx.setLineDash([4 * vpr, 4 * vpr])
      ctx.beginPath()
      for (const i of dayStarts) {
        const x = edge(i)
        if (x < 0 || x > bitmapSize.width / hpr) continue
        // Centre the line on a pixel so a 1px line stays crisp.
        const px = Math.round(x * hpr) + (w % 2 ? 0.5 : 0)
        ctx.moveTo(px, 0)
        ctx.lineTo(px, bitmapSize.height)
      }
      ctx.stroke()
      ctx.setLineDash([])
    })
  }
}
