import type { ClockInfo } from '../types'

/** SPY RV20 next to the regime pill (fork; Ash, project thread, 2026-11-12). Display only.
 *  Red when SPY is calm (RV20 at or below 10.5, scanner/spy_rv.py CALM_MAX), green above it.
 *  There is no yellow band: in the 2026 study, days at 10.5-12 did as well as 12-15. */
export function SpyRvBadge({ info }: { info: ClockInfo | null | undefined }) {
  const rv = info?.spy_rv
  if (!rv) {
    return (
      <span className="regime spy-rv" title="SPY RV20 is not known yet: the scanner needs 21 daily SPY closes">
        RV20 –
      </span>
    )
  }
  const cut = rv.calm_max
  const through = rv.through ? ` through the ${rv.through} close` : ''
  // Ash adopted half size on calm days for the OR1 strategy (study thread, 2026-11-12 03:41 UTC).
  const meaning = rv.calm
    ? `Calm SPY (${cut} or lower): OV picks lost their OR1/OR2 edge on such days. OR1 at half size.`
    : `Above ${cut}: not a calm SPY day. OR1 at full size.`
  return (
    <span className={`regime spy-rv ${rv.calm ? 'calm' : 'active'}`}
      title={`SPY RV20 ${rv.rv20.toFixed(2)}${through}: 20-day close-to-close volatility, annualized, in %. ${meaning} Updates before each session (03:30 ET).`}>
      RV20 {rv.rv20.toFixed(1)}
    </span>
  )
}
