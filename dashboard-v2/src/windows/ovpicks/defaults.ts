// fork: OV picks card (ashwinurao/edge-scanner#20). Title, icon and size of the window,
// imported by windows/defaults.ts. Free of component imports, like that file.
import type { WindowSize } from '../defaults'

export const OV_PICKS_TITLE = 'OV picks'
export const OV_PICKS_ICON = '◆'
export const OV_PICKS_SIZE: WindowSize = { w: 560, h: 380, minW: 380, minH: 200 }
/** The watchlist the scanner fills each morning (scanner/ov_picks_day.py WATCHLIST_ID). */
export const OV_PICKS_WATCHLIST_ID = 'ov-picks'
/** The card polls the route; the picks file changes once a day. */
export const OV_PICKS_POLL_MS = 15_000
