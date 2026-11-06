// fork: OV picks card (ashwinurao/edge-scanner#20). Reads only existing scanner routes:
// GET /api/v2/ov-picks and GET /api/v2/watchlists. Never writes.
import type { Watchlist } from '../../types'
import { api } from '../../lib/api'
import { useWatchlists } from '../../stores/watchlistsStore'
import type { OvPicksAnswer } from './types'
import { OV_PICKS_WATCHLIST_ID } from './defaults'

export async function fetchOvPicks(): Promise<OvPicksAnswer> {
  const res = await fetch('/api/v2/ov-picks')
  if (res.status === 404) throw new Error('This scanner has no /api/v2/ov-picks route. Update the scanner and restart it.')
  if (!res.ok) throw new Error(`The scanner answered HTTP ${res.status} for /api/v2/ov-picks. Check the scanner log.`)
  return res.json() as Promise<OvPicksAnswer>
}

const same = (a: string[], b: string[]) => a.length === b.length && a.every((s, i) => s === b[i])

/** True when this tab's copy of the "OV picks" watchlist holds `tickers`, in order. */
export function watchlistHas(list: Watchlist | undefined, tickers: string[]): boolean {
  return !!list && same(list.symbols, tickers)
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
