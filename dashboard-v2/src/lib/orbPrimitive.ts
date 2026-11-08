import type {
  ISeriesPrimitive, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, Logical,
  SeriesAttachedParameter, Time,
} from 'lightweight-charts'
import type { OrbDrawing } from './orbStudy'

type Target = Parameters<ISeriesPrimitivePaneRenderer['draw']>[0]
type Ctx = CanvasRenderingContext2D
/** or1/or2 name the study, up/down the result, bg rings the dots off the candles. */
export interface OrbColors { or1: string; or2: string; up: string; down: string; bg: string }

const EMPTY: OrbDrawing = { ranges: [], trades: [] }
// Fill strengths, chosen on the demo chart in both themes: the candles stay readable
// through the boxes, and a loss box (risk and result over each other) reads as red.
const RANGE_FILL = 0.14
const RISK_FILL = 0.14
const RESULT_FILL = 0.24
const FONT = '"Roboto Mono", ui-monospace, monospace'

/** fork: draws the opening-range breakout studies (src/lib/orbStudy.ts; Ash, 2026-11-08)
 *  so they read at a glance (Ash, 2026-11-08 14:47 UTC): the range as a shaded box in
 *  the study's colour, the trade as a position box (risk to the stop shaded red, the
 *  result green for a gain or red for a loss), the entry as a solid line with an arrow,
 *  each exit as a dot, and large labels. The shading sits behind the candles; lines,
 *  dots and labels sit on top. lightweight-charts' own markers have small fixed-size
 *  text, so the labels are drawn here. Display only: nothing here places an order or
 *  feeds the engine. */
export class OrbPrimitive implements ISeriesPrimitive<Time> {
  private param: SeriesAttachedParameter<Time> | null = null
  private drawing: OrbDrawing = EMPTY
  private colors: OrbColors
  private readonly views: ISeriesPrimitivePaneView[]

  constructor(colors: OrbColors) {
    this.colors = colors
    const back: ISeriesPrimitivePaneRenderer = { draw: (t: Target) => this.drawShading(t) }
    const front: ISeriesPrimitivePaneRenderer = { draw: (t: Target) => this.drawMarks(t) }
    this.views = [{ zOrder: () => 'bottom', renderer: () => back }, { zOrder: () => 'top', renderer: () => front }]
  }

  attached(param: SeriesAttachedParameter<Time>) { this.param = param }
  detached() { this.param = null }
  paneViews() { return this.views }

  setDrawing(drawing: OrbDrawing) {
    this.drawing = drawing
    this.param?.requestUpdate()
  }

  setColors(colors: OrbColors) {
    this.colors = colors
    this.param?.requestUpdate()
  }

  private study(n: 1 | 2) { return n === 1 ? this.colors.or1 : this.colors.or2 }

  /** Media-space helpers: x of a bar's left and right edges and y of a price. Half a bar
   *  either side, so a box covers its first and last candles. The bar width comes from
   *  two whole indexes (lightweight-charts 4 returns 0 for a fractional one; see
   *  sessionPrimitive.ts). */
  private geometry() {
    const p = this.param
    if (!p) return null
    const ts = p.chart.timeScale()
    const x0 = ts.logicalToCoordinate(0 as Logical), x1 = ts.logicalToCoordinate(1 as Logical)
    if (x0 == null || x1 == null) return null
    const half = (x1 - x0) / 2
    const x = (t: number) => ts.timeToCoordinate(t as Time)
    return {
      half,
      left: (t: number) => { const c = x(t); return c == null ? null : c - half },
      mid: x,
      right: (t: number) => { const c = x(t); return c == null ? null : c + half },
      y: (price: number) => p.series.priceToCoordinate(price),
    }
  }

  private drawShading(target: Target) {
    const { ranges, trades } = this.drawing
    const g = ranges.length ? this.geometry() : null
    if (!g) return
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      const box = (from: number, to: number, a: number, b: number, color: string, alpha: number) => {
        const l = g.left(from), r = g.right(to), ya = g.y(a), yb = g.y(b)
        if (l == null || r == null || ya == null || yb == null) return
        const top = Math.round(Math.min(ya, yb) * vpr), bot = Math.round(Math.max(ya, yb) * vpr)
        ctx.globalAlpha = alpha
        ctx.fillStyle = color
        ctx.fillRect(Math.round(l * hpr), top, Math.max(1, Math.round((r - l) * hpr)), Math.max(1, bot - top))
      }
      for (const r of ranges) box(r.from, r.to, r.high, r.low, this.study(r.n), RANGE_FILL)
      for (const t of trades) {
        box(t.from, t.to, t.entry, t.stop, this.colors.down, RISK_FILL)
        for (const e of t.exits) box(t.from, e.time, t.entry, e.price, e.r >= 0 ? this.colors.up : this.colors.down, RESULT_FILL)
      }
      ctx.globalAlpha = 1
    })
  }

  private drawMarks(target: Target) {
    const { ranges, trades } = this.drawing
    const g = ranges.length ? this.geometry() : null
    if (!g) return
    const { up, down, bg } = this.colors
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      const X = (v: number) => Math.round(v * hpr), Y = (v: number) => Math.round(v * vpr)
      const hline = (l: number, r: number, y: number, color: string, width: number, dash: number[] = []) => {
        const w = Math.max(1, Math.round(width * hpr))
        ctx.strokeStyle = color
        ctx.lineWidth = w
        ctx.setLineDash(dash.map(d => d * hpr))
        const py = Y(y) + (w % 2 ? 0.5 : 0)
        ctx.beginPath(); ctx.moveTo(X(l), py); ctx.lineTo(X(r), py); ctx.stroke()
        ctx.setLineDash([])
      }
      const labels: Label[] = []

      for (const r of ranges) {
        const l = g.left(r.from), rr = g.right(r.to), yh = g.y(r.high), yl = g.y(r.low)
        if (l == null || rr == null || yh == null || yl == null) continue
        const c = this.study(r.n)
        hline(l, rr, yh, c, 1)
        hline(l, rr, yl, c, 1)
        // The range's name just left of its box, level with its top, so the space over
        // the trade stays free for the entry's label.
        labels.push({ text: r.label, x: l - 4, align: 'right', y: Math.min(yh, yl), anchor: 'top', fill: c, size: 11, step: 1 })
      }

      for (const t of trades) {
        const l = g.left(t.from), r = g.right(t.to), ye = g.y(t.entry), ys = g.y(t.stop), xm = g.mid(t.from)
        if (l == null || r == null || ye == null || ys == null || xm == null) continue
        const c = this.study(t.n)
        hline(l, r, ys, down, 1.5, [5, 3])
        for (const e of t.exits) {
          const er = g.right(e.time), ey = g.y(e.price)
          if (er == null || ey == null) continue
          const top = Math.min(ye, ey), h = Math.abs(ey - ye)
          ctx.strokeStyle = e.r >= 0 ? up : down
          ctx.lineWidth = Math.max(1, Math.round(hpr))
          ctx.strokeRect(X(l) + 0.5, Y(top) + 0.5, X(er) - X(l) - 1, Math.max(1, Y(top + h) - Y(top) - 1))
        }
        hline(l, r, ye, c, 2.5)
        triangle(ctx, X(xm), Y(ye), t.side, 7 * hpr, c, bg, 1.5 * hpr)
        // The entry's label sits on the stop side of the entry line, just right of the
        // entry arrow, so it never covers the opening range's candles.
        const stopAbove = ys < ye
        labels.push({ text: t.text, x: xm + 10, y: stopAbove ? ye - 4 : ye + 4, anchor: stopAbove ? 'bottom' : 'top', fill: c, size: 12, step: stopAbove ? -1 : 1 })
        for (const e of t.exits) {
          const ex = g.mid(e.time), ey = g.y(e.price)
          if (ex == null || ey == null) continue
          const col = e.r >= 0 ? up : down
          dot(ctx, X(ex), Y(ey), 5 * hpr, col, bg, 1.5 * hpr, e.open)
          // The result just right of its exit, at the exit price.
          labels.push({ text: e.text, x: ex + g.half + 7, y: ey, anchor: 'middle', fill: col, size: 13, step: e.price >= t.entry ? -1 : 1 })
        }
      }

      drawLabels(ctx, labels, hpr, vpr)
    })
  }
}

interface Label {
  text: string; x: number; y: number
  /** Which edge of the label sits at x (default left). */
  align?: 'left' | 'right'
  /** Which edge of the label sits at y. */
  anchor: 'top' | 'middle' | 'bottom'
  fill: string; size: number
  /** Which way to move it (-1 up, 1 down) when it would cover another label. */
  step: -1 | 1
}

function triangle(ctx: Ctx, x: number, y: number, side: 1 | -1, size: number, fill: string, ring: string, ringW: number) {
  // Points the way of the trade, its tip on the entry price.
  ctx.beginPath()
  ctx.moveTo(x, y)
  ctx.lineTo(x - size * 0.8, y + side * size * 1.3)
  ctx.lineTo(x + size * 0.8, y + side * size * 1.3)
  ctx.closePath()
  ctx.lineWidth = ringW * 2
  ctx.strokeStyle = ring
  ctx.stroke()
  ctx.fillStyle = fill
  ctx.fill()
}

function dot(ctx: Ctx, x: number, y: number, r: number, fill: string, ring: string, ringW: number, hollow: boolean) {
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.lineWidth = ringW * 2
  ctx.strokeStyle = ring
  ctx.stroke()
  if (hollow) {
    // A trade still running: a ring, not a filled exit.
    ctx.lineWidth = ringW * 1.5
    ctx.strokeStyle = fill
    ctx.stroke()
    return
  }
  ctx.fillStyle = fill
  ctx.fill()
}

/** Labels as filled pills, nudged up or down so none covers another. */
function drawLabels(ctx: Ctx, labels: Label[], hpr: number, vpr: number) {
  const placed: { l: number; t: number; r: number; b: number }[] = []
  for (const lb of labels) {
    ctx.font = `600 ${Math.round(lb.size * vpr)}px ${FONT}`
    const padX = 5 * hpr, h = Math.round((lb.size + 6) * vpr)
    const w = Math.ceil(ctx.measureText(lb.text).width + padX * 2)
    const l = Math.round(lb.x * hpr) - (lb.align === 'right' ? w : 0)
    let t = Math.round(lb.y * vpr) - (lb.anchor === 'top' ? 0 : lb.anchor === 'middle' ? h / 2 : h)
    for (let k = 0; k < 6 && placed.some(p => l < p.r && l + w > p.l && t < p.b && t + h > p.t); k++) t += lb.step * (h + 2 * vpr)
    placed.push({ l, t, r: l + w, b: t + h })
    ctx.fillStyle = lb.fill
    ctx.beginPath()
    if (typeof ctx.roundRect === 'function') ctx.roundRect(l, t, w, h, 3 * hpr)
    else ctx.rect(l, t, w, h)
    ctx.fill()
    ctx.fillStyle = textOn(lb.fill)
    ctx.textBaseline = 'middle'
    ctx.fillText(lb.text, l + padX, t + h / 2 + 0.5 * vpr)
  }
}

/** Black or white text, whichever reads better on `fill` (WCAG contrast). The palette
 *  colours arrive as #rrggbb or rgb()/rgba() (theme.ts resolveColor). */
export function textOn(fill: string): string {
  const rgb = rgbOf(fill)
  if (!rgb) return '#ffffff'
  const [r, g, b] = rgb.map(v => { const s = v / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4 })
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
  return (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? '#0b0f17' : '#ffffff'
}

function rgbOf(c: string): [number, number, number] | null {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c.trim())
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split('').map(x => x + x).join('') : hex[1]
    const v = parseInt(h, 16)
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255]
  }
  const m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i.exec(c.trim())
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null
}
