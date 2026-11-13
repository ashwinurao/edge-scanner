// fork: the OV picks window's first-minute columns, live (Ash, project thread, 2026-11-08
// 05:10 UTC: "the 1st min atr and open vs pm should update every 2-3 secs along with live
// data so I can quickly make decision whether to take or1 or o2 right after"). The scanner
// fills them from Alpaca's snapshot first, not confirmed, and says the time the values run
// to: "09:31:00" for the snapshot's 09:30 bar (brief 20, 2026-11-13), a trade's time for the
// minute so far (scanner/ov_picks_live.py). This file only decides how often the card asks
// and which cells are marked not confirmed.
// Plain data, so OvPicksWindow.tsx only lays it out and tests/ovLive.test.ts can check it.
import type { OvPick } from './types'

/** How often the card asks the route, in ms: the route's `refresh_seconds` while today's
 *  first-minute columns are about to fill or still forming, else `normalMs`. */
export function pollMs(normalMs: number, refreshSeconds: number | null | undefined): number {
  return typeof refreshSeconds === 'number' && refreshSeconds > 0 ? Math.max(1, refreshSeconds) * 1000 : normalMs
}

/** The ET time a row's live cells run to while they come from the snapshot ("09:30:41",
 *  or "09:31:00" for its 09:30 bar); null once they come from the 09:30 bar, or for a
 *  column the module fills. */
export function soFar(p: OvPick, liveColumn: boolean): string | null {
  return liveColumn && p.first_minute && p.first_minute_as_of ? p.first_minute_as_of : null
}

/** Whether any row's live cells come from the snapshot: the key then explains the dot. */
export function anySoFar(picks: OvPick[]): boolean {
  return picks.some(p => soFar(p, true) !== null)
}

/** The minute's end: live cells that run to it hold the whole 09:30 bar as Alpaca's snapshot
 *  shows it, not the minute so far (scanner/ov_picks_live.py, brief 20). */
export const MINUTE_END_ET = '09:31:00'

/** Whether a row's live cells are the snapshot's 09:30 bar, waiting for the bar request. */
export function snapshotBar(p: OvPick): boolean {
  return soFar(p, true) === MINUTE_END_ET
}
