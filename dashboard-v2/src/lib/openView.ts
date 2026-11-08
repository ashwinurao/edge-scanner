// fork: the view a 1-minute chart opens on when a name is clicked in the OV picks window
// (Ash, project thread, 2026-11-08 18:45 UTC: "the 1m chart ... should have a default zoom
// ... so it starts at 8:30 and goes till 11:30"). The chart window uses it in
// src/windows/chart/ChartWindow.tsx; the link carries the ask (src/stores/linkStore.ts).

/** The view's first and last minute, ET. */
export const OPEN_VIEW_FROM_MIN = 8 * 60 + 30
export const OPEN_VIEW_TO_MIN = 11 * 60 + 30
const DAY_SEC = 86_400

/** The logical range (bar indexes, half a bar past each end) that shows 08:30 to 11:30 ET on
 *  the chart's last day. `times` are the chart's sorted bar start times in its own time space
 *  (ET wall clock read as UTC seconds, toSecET) and `barSec` the bar length in seconds.
 *  - It starts at the day's first bar from 08:30 (09:30 when extended hours are hidden).
 *  - On a day past 11:30 it ends at the last bar starting by 11:30. Earlier in the day it runs
 *    on into empty space where the coming bars will appear, so the view stays put.
 *  - null when the last day has no bar from 08:30 yet: the chart keeps its usual view. */
export function openViewRange(times: readonly number[], barSec: number): { from: number; to: number } | null {
  const n = times.length
  if (!n || !(barSec > 0)) return null
  const last = times[n - 1]
  const dayStart = Math.floor(last / DAY_SEC) * DAY_SEC
  const fromSec = dayStart + OPEN_VIEW_FROM_MIN * 60
  const toSec = dayStart + OPEN_VIEW_TO_MIN * 60
  if (last < fromSec) return null
  let first = n - 1
  while (first > 0 && times[first - 1] >= fromSec) first--
  let end: number
  if (last <= toSec) end = n - 1 + (toSec - last) / barSec
  else {
    end = n - 1
    while (end > first && times[end] > toSec) end--
  }
  return { from: first - 0.5, to: end + 0.5 }
}
