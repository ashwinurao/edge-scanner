// fork: the data behind the chart's opening-range breakout studies (OrbStudy.tsx;
// Ash, 2026-11-08). Display only.
import { useMemo } from 'react'
import type { Bar, ChartOverlays, ChartTimeframe } from '../../types'
import { api } from '../../lib/api'
import { usePoll } from '../../lib/usePoll'
import { ORB_VARIANTS, dailyAtr14, etNow, orbDays, type OrbDay, type OrbVariant } from '../../lib/orbStudy'

export interface OrbStudy { days: OrbDay[]; variants: OrbVariant[]; loading: boolean }

export const orbOn = (o: ChartOverlays): OrbVariant[] => ORB_VARIANTS.filter(v => !!o[v.key])

/** The study's days for the chart: worked out from 1-minute bars whatever the chart's
 *  frame. A 1-minute chart's own bars are used; other frames fetch the 1-minute bars
 *  of the same day range (/api/bars 1min: about the last three sessions). */
export function useOrbStudy(args: {
  symbol: string | null; tf: ChartTimeframe; day: string | null; bars: Bar[] | null; daily: Bar[]
  overlays: ChartOverlays; session: boolean; pollMs: number
}): OrbStudy {
  const { symbol, tf, day, bars, daily, overlays, session, pollMs } = args
  const variants = useMemo(() => (session ? orbOn(overlays) : []), [overlays, session])
  const own = tf === '1m'
  const need = !!symbol && variants.length > 0 && !own
  const key = `${symbol}|${day ?? ''}`
  const { data } = usePoll(async () => {
    const k = `${symbol}|${day ?? ''}`
    try { return { key: k, bars: await api.bars(symbol!, '1min', day) } }
    catch { return { key: k, bars: [] as Bar[] } }
  }, pollMs, need, [symbol, day])
  const minute = own ? bars : data?.key === key ? data.bars : null
  // Re-read the clock with every new set of bars, so an open trade turns flat.
  const days = useMemo(() => {
    if (!variants.length || !minute) return []
    const sorted = minute.slice().sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0))
    return orbDays(sorted, variants, d => dailyAtr14(daily, d), etNow())
  }, [minute, variants, daily])
  return { days, variants, loading: variants.length > 0 && !minute }
}

