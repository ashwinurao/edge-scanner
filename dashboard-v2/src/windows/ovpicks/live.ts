// fork: the OV picks window's first-minute columns, live (Ash, project thread, 2026-11-08
// 05:10 UTC: "the 1st min atr and open vs pm should update every 2-3 secs along with live
// data so I can quickly make decision whether to take or1 or o2 right after"). The scanner
// fills them with the 09:30 ET minute so far and says until when (scanner/ov_picks_live.py);
// this file only decides how often the card asks and which cells are marked "so far".
// Plain data, so OvPicksWindow.tsx only lays it out and tests/ovLive.test.ts can check it.
import type { OvPick } from './types'

/** How often the card asks the route, in ms: the route's `refresh_seconds` while today's
 *  first-minute columns are about to fill or still forming, else `normalMs`. */
export function pollMs(normalMs: number, refreshSeconds: number | null | undefined): number {
  return typeof refreshSeconds === 'number' && refreshSeconds > 0 ? Math.max(1, refreshSeconds) * 1000 : normalMs
}

/** The ET time a row's live cells run to while the minute forms ("09:30:41"); null once
 *  they come from the 09:30 bar, or for a column the module fills. */
export function soFar(p: OvPick, liveColumn: boolean): string | null {
  return liveColumn && p.first_minute && p.first_minute_as_of ? p.first_minute_as_of : null
}

/** Whether any row's live cells are the minute so far: the key then explains the dot. */
export function anySoFar(picks: OvPick[]): boolean {
  return picks.some(p => soFar(p, true) !== null)
}
