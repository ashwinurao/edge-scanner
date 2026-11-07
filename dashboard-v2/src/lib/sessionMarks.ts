// Where an intraday chart shades extended hours and draws its day lines
// (Ash, 2026-11-07: "pre-market and post market hours shaded darker" and
// "a standard way to differentiate between days").
// The '.ts' import lets `node --test` run this file without a bundler
// (tests/sessionMarks.test.ts).
import { etDate, isExtended } from './time.ts'

export interface SessionMarks {
  /** [first, last] bar indexes of each unbroken run of extended-hours bars. */
  extRuns: [number, number][]
  /** Index of the first bar of each new ET trading day (never 0). */
  dayStarts: number[]
}

/** Split bar start times (sorted, oldest first) into extended-hours runs and
 *  day starts. A bar counts as extended by its start time, the same rule the
 *  EXT toggle uses to hide bars (isExtended in time.ts: before 09:30 or at or
 *  after 16:00 ET). Days are ET calendar days, so a new day starts at its first
 *  bar: 04:00 with extended hours shown, 09:30 without. */
export function sessionMarks(times: readonly string[]): SessionMarks {
  const extRuns: [number, number][] = []
  const dayStarts: number[] = []
  let runStart = -1
  let prevDay = ''
  times.forEach((t, i) => {
    const day = etDate(t)
    if (i > 0 && day !== prevDay) dayStarts.push(i)
    prevDay = day
    if (isExtended(t)) {
      if (runStart < 0) runStart = i
    } else if (runStart >= 0) {
      extRuns.push([runStart, i - 1])
      runStart = -1
    }
  })
  if (runStart >= 0) extRuns.push([runStart, times.length - 1])
  return { extRuns, dayStarts }
}
