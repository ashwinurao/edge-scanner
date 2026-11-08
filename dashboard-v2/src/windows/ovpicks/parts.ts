// fork: what made up each OV score, and how each name stands against the next, drawn with
// colour instead of words (Ash, project thread, 2026-11-08, round 3 layout "C1": "how can
// visually call attention to things, without showing so many words"). The module writes
// the parts, their points and the reason (ov_picks/CONTRACT.md, "Score parts and rank
// reason"); this file only decides how they look. Plain data, so OvPicksWindow.tsx only
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

/** The points a part lost, as the cell shows them beside its value ("−1.3"), or '' when full. */
export function lostText(part: OvPart): string {
  const lost = part.possible - part.points
  if (lost < FULL_SLACK) return ''
  return `−${lost < 1 ? lost.toFixed(2) : lost.toFixed(1)}`
}

const pts = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(2))

/** The cell's tooltip: the part's points, and whether it is what puts the name ahead. */
export function partTitle(part: OvPart, decides: boolean): string {
  const full = part.possible - part.points < FULL_SLACK
  const head = `${part.label}: ${pts(part.points)} of ${pts(part.possible)} points${full ? ', full' : ''}.`
  return decides ? `${head} This part puts it ahead of the next name.` : head
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
