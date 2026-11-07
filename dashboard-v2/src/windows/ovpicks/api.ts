// fork: OV picks card (ashwinurao/edge-scanner#20). Reads GET /api/v2/ov-picks (?date for a
// past day), GET /api/v2/ov-picks/days and GET /api/v2/watchlists. Its one write is the
// "OV picks" watchlist, through PUT /api/v2/watchlists/ov-picks, while the calendar shows
// a past day (fillOvPicksWatchlist).
import type { Watchlist } from '../../types'
import { api } from '../../lib/api'
import { useWatchlists } from '../../stores/watchlistsStore'
import type { OvPicksAnswer, OvPicksDays } from './types'
import { OV_PICKS_WATCHLIST_ID, OV_PICKS_WATCHLIST_NAME } from './defaults'

/** Today's answer, or a past day's (`day`, YYYY-MM-DD; Ash, 2026-11-07). */
export async function fetchOvPicks(day: string | null = null): Promise<OvPicksAnswer> {
  const res = await fetch(day ? `/api/v2/ov-picks?date=${encodeURIComponent(day)}` : '/api/v2/ov-picks')
  if (res.status === 404) throw new Error('This scanner has no /api/v2/ov-picks route. Update the scanner and restart it.')
  if (!res.ok) throw new Error(`The scanner answered HTTP ${res.status} for /api/v2/ov-picks. Check the scanner log.`)
  return res.json() as Promise<OvPicksAnswer>
}

/** The days the calendar can show. An older scanner without the route has none: the card
 *  then offers today only, and says why. */
export async function fetchOvPicksDays(): Promise<OvPicksDays> {
  const res = await fetch('/api/v2/ov-picks/days')
  if (res.status === 404) throw new Error('This scanner has no past days for OV picks. Update the scanner and restart it.')
  if (!res.ok) throw new Error(`The scanner answered HTTP ${res.status} for /api/v2/ov-picks/days. Check the scanner log.`)
  return res.json() as Promise<OvPicksDays>
}

const same = (a: string[], b: string[]) => a.length === b.length && a.every((s, i) => s === b[i])

/** True when this tab's copy of the "OV picks" watchlist holds `tickers`, in order. */
export function watchlistHas(list: Watchlist | undefined, tickers: string[]): boolean {
  return !!list && same(list.symbols, tickers)
}

/** The name the "OV picks" watchlist shows while it holds a past day. */
export const pastDayListName = (day: string) => `OV picks · ${day}`

/** Put `tickers` in the "OV picks" watchlist (Ash, 2026-11-07): a past day's top picks with
 *  `day`, named with its date, or with `day` null the shown day's picks again under the
 *  plain name. Links from the list then send that day (WatchlistWindow). The scanner's
 *  morning upsert puts today's picks back and drops the day (scanner/ov_picks_day.py). */
export async function fillOvPicksWatchlist(tickers: string[], day: string | null): Promise<void> {
  const old = useWatchlists.getState().lists[OV_PICKS_WATCHLIST_ID]
  const rest: Partial<Watchlist> = { ...old }
  delete rest.ov_picks_day
  const rec: Watchlist = {
    ...rest,
    id: OV_PICKS_WATCHLIST_ID,
    name: day ? pastDayListName(day) : (old?.ov_picks_day ? OV_PICKS_WATCHLIST_NAME : old?.name || OV_PICKS_WATCHLIST_NAME),
    symbols: tickers,
    updatedAt: new Date().toISOString(),
    ...(day ? { ov_picks_day: day } : {}),
  }
  useWatchlists.setState(s => ({
    lists: { ...s.lists, [rec.id]: rec },
    order: s.order.includes(rec.id) ? s.order : [...s.order, rec.id],
  }))
  await api.watchlists.save(rec)
}

/** Bring this tab's copy of the "OV picks" watchlist up to the server's.
 *
 *  The watchlist store loads once per page (App.tsx) and saves whole records, so a tab
 *  opened before 09:25 ET would show yesterday's names, and an edit there would write them
 *  back over the scanner's morning upsert (scanner/ov_picks_day.py). Returns true once
 *  the tab's copy holds `tickers`. */
export async function syncOvPicksWatchlist(tickers: string[]): Promise<boolean> {
  const store = useWatchlists.getState()
  if (!store.loaded) return false
  if (watchlistHas(store.lists[OV_PICKS_WATCHLIST_ID], tickers)) return true
  const server = (await api.watchlists.list()).find(w => w.id === OV_PICKS_WATCHLIST_ID)
  if (!server) return false
  useWatchlists.setState(s => ({
    lists: { ...s.lists, [server.id]: server },
    order: s.order.includes(server.id) ? s.order : [...s.order, server.id],
  }))
  return same(server.symbols, tickers)
}
