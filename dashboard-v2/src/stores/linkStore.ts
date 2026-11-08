import { create } from 'zustand'
import type { Alert, LinkColor, WindowBase } from '../types'
import { useScreens } from './screensStore'

type Group = Exclude<LinkColor, 'none'>
/** fork: how a linked chart should frame the symbol it is sent. 'open' = 08:30-11:30 ET on its
 *  last day, sent by the OV picks window (src/lib/openView.ts); null = the chart's usual view. */
export type LinkView = 'open' | null

interface LinkState {
  symbols: Record<Group, string | null>
  /** The alert last selected in a Scanner window of each group. Charts mark it
   *  while they show its symbol; a symbol typed or clicked elsewhere leaves it
   *  behind (the chart just stops drawing it). */
  alerts: Record<Group, Alert | null>
  /** fork: a past ET day (YYYY-MM-DD) sent with the symbol, or null for today (live).
   *  Only the OV picks window sends one, from a past day's list (Ash, 2026-11-07):
   *  charts then end on that day and news ends at its 16:00 ET close. Any other click
   *  sends no day, so the group goes back to live. */
  days: Record<Group, string | null>
  /** fork: the view sent with the symbol (Ash, 2026-11-08), and a count of sends, so a second
   *  click on the same name frames it again. */
  views: Record<Group, LinkView>
  sends: Record<Group, number>
  setSymbol(color: Group, sym: string | null, day?: string | null, view?: LinkView): void
  setAlert(color: Group, alert: Alert | null): void
}

export const useLinks = create<LinkState>()(set => ({
  symbols: { red: null, green: null, blue: null, yellow: null, purple: null },
  alerts: { red: null, green: null, blue: null, yellow: null, purple: null },
  days: { red: null, green: null, blue: null, yellow: null, purple: null },
  views: { red: null, green: null, blue: null, yellow: null, purple: null },
  sends: { red: 0, green: 0, blue: 0, yellow: 0, purple: 0 },
  setSymbol(color, sym, day = null, view = null) {
    set(s => ({
      symbols: { ...s.symbols, [color]: sym ? sym.toUpperCase() : null },
      days: { ...s.days, [color]: sym ? day : null },
      views: { ...s.views, [color]: sym ? view : null },
      sends: { ...s.sends, [color]: s.sends[color] + 1 },
    }))
  },
  setAlert(color, alert) {
    set(s => ({ alerts: { ...s.alerts, [color]: alert } }))
  },
}))

/** The symbol a window should show: its link group's symbol; while the group has
 *  had no click yet (or the window is unlinked) its own configured symbol. */
export function useLinkedSymbol(win: WindowBase, fallback: string | null): string | null {
  const linked = useLinks(s => (win.link === 'none' ? null : s.symbols[win.link]))
  return win.link === 'none' ? fallback : (linked ?? fallback)
}

/** fork: the past day the window's link group was sent with its symbol, or null for
 *  today (live). An unlinked window is always live. */
export function useLinkedDay(win: WindowBase): string | null {
  return useLinks(s => (win.link === 'none' ? null : s.days[win.link]))
}

/** fork: the view the window's link group was sent with its symbol, and how many sends the
 *  group has had (a new send frames the chart again). An unlinked window has neither. */
export function useLinkedView(win: WindowBase): { view: LinkView; send: number } {
  const view = useLinks(s => (win.link === 'none' ? null : s.views[win.link]))
  const send = useLinks(s => (win.link === 'none' ? 0 : s.sends[win.link]))
  return { view, send }
}

/** The selected alert of the window's link group, only while it is for `symbol`. */
export function useLinkedAlert(win: WindowBase, symbol: string | null): Alert | null {
  const a = useLinks(s => (win.link === 'none' ? null : s.alerts[win.link]))
  return a && symbol && a.symbol.toUpperCase() === symbol.toUpperCase() ? a : null
}

/** A window "publishes" a symbol: drives its link group, or (when unlinked) just its own config.
 *  fork: `day` (YYYY-MM-DD) sends a past day with it; without one the group is live. `view`
 *  asks linked charts to frame it a set way (LinkView). */
export function linkSymbol(win: WindowBase, sym: string, ownKey: 'symbol' | null = 'symbol', day: string | null = null, view: LinkView = null) {
  const s = sym.toUpperCase()
  if (win.link !== 'none') {
    useLinks.getState().setSymbol(win.link, s, day, view)
  } else if (ownKey) {
    useScreens.getState().updateWindow(win.id, { [ownKey]: s } as Record<string, unknown>)
  }
}

/** A Scanner window publishes a whole alert: its symbol, plus the event itself for chart markers. */
export function linkAlert(win: WindowBase, alert: Alert) {
  if (win.link === 'none') return
  useLinks.getState().setSymbol(win.link, alert.symbol)
  useLinks.getState().setAlert(win.link, alert)
}
