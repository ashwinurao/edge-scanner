// fork: what made up each OV score, and how each name stands against the next, drawn with
// colour instead of words (Ash, project thread, 2026-11-08, round 3 layout "C1": "how can
// visually call attention to things, without showing so many words"). The module writes
// the parts, their points and the reason (ov_picks/CONTRACT.md, "Score parts and rank
// reason"); this file only decides how they look. Round 4 (Ash, 2026-11-08, "A1 + B1 + H1")
// adds each part's score out of 100 in the cell's corner, arrows on every part that differs
// from the next name, and where full points start in the part's header. Plain data, so OvPicksWindow.tsx only
// lays it out and tests/ovParts.test.ts can check it.
import type { OvPart, OvPick } from './types'

/** The part labels of a list, in the module's order: the window's part columns. */
export function partLabels(picks: OvPick[]): string[] {
  const out: string[] = []
  for (const p of picks) for (const part of p.score_parts ?? []) if (!out.includes(part.label)) out.push(part.label)
  return out
}

/** A part that lost less than this many points reads as full: the module rounds points. */
export const FULL_SLACK = 0.05
/** A part that lost this share of its points or more is well short (orange); less is short (amber). */
export const WELL_SHORT = 0.25

export type PartHeat = 'full' | 'short' | 'well-short'

/** How a part's cell is coloured: faint when full, so only the parts that lost points stand out. */
export function partHeat(part: OvPart): PartHeat {
  const lost = part.possible - part.points
  if (lost < FULL_SLACK) return 'full'
  return lost / part.possible >= WELL_SHORT ? 'well-short' : 'short'
}

/** The part's score out of 100, as the cell's corner shows it (round 4, "A1"): 100 only when
 *  full, so a part that lost points never reads as 100 after rounding. */
export function partScore(part: OvPart): number {
  if (partHeat(part) === 'full') return 100
  if (!(part.possible > 0)) return 0
  return Math.min(99, Math.max(0, Math.round((part.points / part.possible) * 100)))
}

/** Points against the next name under this are rounding, so they get no arrow. */
export const ARROW_SLACK = 0.005

export type PartArrow = 'up' | 'down' | null

/** The arrow on a part (round 4, "B1"): up when it gives the name more points than the next
 *  name, down when fewer. The ringed part needs none: the ring already says it leads. */
export function partArrow(part: OvPart, decides: boolean): PartArrow {
  const v = part.vs_next
  if (decides || typeof v !== 'number' || Math.abs(v) < ARROW_SLACK) return null
  return v > 0 ? 'up' : 'down'
}

const pts = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(2))
const signed = (x: number) => `${x > 0 ? '+' : '−'}${pts(Math.abs(x))}`

/** The cell's tooltip: the part's score and points, and what it gives against the next name. */
export function partTitle(part: OvPart, decides: boolean): string {
  const lost = part.possible - part.points
  const head = `${part.label}: ${partScore(part)} of 100, ${pts(part.points)} of ${pts(part.possible)} points`
    + (partHeat(part) === 'full' ? ', full.' : `, ${pts(lost)} short.`)
  const v = typeof part.vs_next === 'number' && Math.abs(part.vs_next) >= ARROW_SLACK ? part.vs_next : null
  if (decides) return `${head} This part puts it ahead of the next name${v !== null ? `: ${signed(v)} points` : ''}.`
  return v !== null ? `${head} Against the next name: ${signed(v)} points.` : head
}

/** A part column's header (round 4, "H1"): where full points start, under the label, and the
 *  part's marks on hover. The module writes the marks; rows of one list share them. */
export function partHeader(picks: OvPick[], label: string): { full: string | null, title: string } {
  const part = picks.flatMap(p => p.score_parts ?? []).find(x => x.label === label && x.full_at)
  const key = "Corner: the part's score out of 100, green when full, amber or orange when short. "
    + '▲▼: more or fewer points than the next name. Ringed: the part that puts the name ahead of the next one.'
  if (!part) return { full: null, title: `${label}: a part of OV's score. ${key}` }
  const zero = part.zero_at ? `No points up to ${part.zero_at}, full` : 'Full'
  return {
    full: part.full_at ?? null,
    title: `${label}: a part of OV's score, up to ${pts(part.possible)} points. ${zero} points from ${part.full_at}. ${key}`,
  }
}

/** The rank badge: gold for OV's first pick, green for the other top picks, an outline for
 *  the two names after them, plain for the rest. */
export type RankLook = 'first' | 'top' | 'next' | 'rest'

/** How many names after the top picks get an outlined badge (Ash, 2026-11-08: "maybe 5 and 6"). */
export const NEXT_AFTER_TOP = 2

export function rankLooks(picks: OvPick[]): RankLook[] {
  const firstTop = picks.findIndex(p => p.top)
  let after = 0
  return picks.map((p, i) => {
    if (p.top) return i === firstTop ? 'first' : 'top'
    if (firstTop >= 0 && i > firstTop && after < NEXT_AFTER_TOP) { after += 1; return 'next' }
    return 'rest'
  })
}

/** The rank badge's tooltip; `topTitle` says whose top picks they are ("One of OV's top picks today"). */
export function rankTitle(p: OvPick, look: RankLook, topTitle: string): string {
  if (look === 'first') return `Rank ${p.rank}, OV's first pick. ${topTitle}.`
  if (look === 'top') return `Rank ${p.rank}. ${topTitle}.`
  return `Rank ${p.rank}`
}
