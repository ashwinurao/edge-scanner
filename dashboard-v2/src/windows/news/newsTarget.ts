import type { NewsConfig } from '../../types'

/** fork: the ticker a News window shows news for, or null for market-wide news.
 *  `groupSymbol` is the last ticker sent to the window's link group (null when the window is
 *  unlinked or nothing was sent yet).
 *
 *  A linked News window follows the tickers sent to its group in either mode (Ash, project
 *  thread, 2026-11-12: a News window linked red stayed on market-wide news when an OV picks
 *  row was clicked, while the red charts switched). A News window added from the top bar
 *  starts "Market-wide", and that mode used to ignore the link color. Now "Market-wide" only
 *  says what a linked window shows before its group is sent a ticker. */
export function newsSymbol(win: Pick<NewsConfig, 'mode' | 'link' | 'symbol'>, groupSymbol: string | null): string | null {
  if (win.link !== 'none' && groupSymbol) return groupSymbol
  return win.mode === 'linked' ? win.symbol : null
}
