import { useEffect, useState } from 'react'
import { useScreens, SCREEN_TEMPLATES, type ScreenTemplate } from '../stores/screensStore'
import { useSettings } from '../stores/settingsStore'
import { useFeeds, visibleSources, SOURCE_LABEL, SOURCE_SHORT } from '../stores/feedsStore'
import { useCapabilities } from '../stores/capabilitiesStore'
import type { ClockInfo, WindowType } from '../types'
import { THEMES } from '../lib/theme'
import { nowET } from '../lib/time'
import { audioReady, unlockAudio, playTone } from '../lib/audio'
import { usePoll } from '../lib/usePoll'
import { api } from '../lib/api'
import { Menu, MenuHead, MenuItem, MenuSep } from './Menu'
import { SetupsPanel } from './SetupsPanel'
import { WINDOW_ICONS, WINDOW_TITLES } from '../windows/defaults'

const WINDOW_ORDER: WindowType[] = ['chart', 'scanner', 'toplist', 'news', 'stockinfo', 'setupcheck', 'watchlist', 'ovpicks', 'clock'] // fork: OV picks card (#20)

function FeedDots() {
  const status = useFeeds(s => s.status)
  const counts = useFeeds(s => s.counts)
  const hasSystem = useCapabilities(s => s.system_setups)
  const cls = status === 'connected' ? 'ok' : status === 'reconnecting' ? 'warn' : 'bad'
  return (
    <div className="row" style={{ gap: 8 }} title={`Alert connection to the scanner (/ws/alerts): ${status}. Whether market data is still arriving is the chip beside it.`}>
      <span className="row" style={{ gap: 4, fontSize: 10.5 }}><span className={`dot ${cls}`} /><span className="faint">ALERTS</span></span>
      {visibleSources(hasSystem).map(f => (
        <span key={f} className="faint mono" style={{ fontSize: 10.5 }} title={`${SOURCE_LABEL[f]}: ${counts[f]} today`}>{SOURCE_SHORT[f]} {counts[f]}</span>
      ))}
    </div>
  )
}

// The browser tab names the version and the provider, so two scanners side by
// side (Alpaca and Schwab) are told apart without knowing their ports.
const tabTitle = { version: '', provider: '' }
function setTabTitle(part: Partial<typeof tabTitle>) {
  Object.assign(tabTitle, part)
  document.title = ['Edge Scanner' + (tabTitle.version ? ` v${tabTitle.version}` : ''), tabTitle.provider].filter(Boolean).join(' · ')
}

/** Provider, newest bar and its age. The alert socket being connected says
 *  nothing about whether market data is still flowing; this does. Colored only
 *  in regular hours, when every minute brings bars for a liquid universe. */
function DataChip({ info }: { info: ClockInfo | null | undefined }) {
  const d = info?.data
  useEffect(() => { if (d?.provider) setTabTitle({ provider: d.provider }) }, [d?.provider])
  if (!d || !d.provider) return null
  const age = d.last_bar_age_s
  const bar = d.last_bar_et ? d.last_bar_et.slice(11, 16) : null
  const live = info?.session === 'rth' && !info?.replay
  const level = !live || age == null ? '' : age <= 90 ? 'ok' : age <= 180 ? 'warn' : 'bad'
  const ageText = age == null ? '' : age < 90 ? `${Math.round(age)}s` : age < 5400 ? `${Math.round(age / 60)}m` : `${Math.round(age / 3600)}h`
  const title = bar
    ? `Market data from ${d.provider}. Newest bar ${bar} ET, received ${ageText} ago.` +
      (level === 'bad' ? ' No bars for over 3 minutes in regular hours: the data stream may have stopped.' : '')
    : `Market data from ${d.provider}. No bars received yet.`
  return (
    <span className={`data-chip ${level}`} title={title}>
      {level && <span className={`dot ${level}`} />}
      <b>{d.provider}</b>
      <span className="faint mono">{bar ? `bar ${bar} · ${ageText}` : 'waiting for bars'}</span>
    </span>
  )
}

/** "v1.2.0 available" beside the feed dots once the startup check has answered.
 *  The scanner asks GitHub once at startup (scanner/update_check.py); this only
 *  reads the answer. Dismiss hides it for this release until the page reloads.
 *  The first answer also puts the running version in the browser tab. */
function UpdateBadge() {
  const [v, setV] = useState<{ latest: string; url: string } | null>(null)
  const [hidden, setHidden] = useState(false)
  useEffect(() => {
    let tries = 0
    const t = setInterval(() => {
      api.version().then(r => {
        if (r.current) setTabTitle({ version: r.current })
        if (r.checked) { clearInterval(t); if (r.available && r.latest) setV({ latest: r.latest, url: r.url }) }
        else if (++tries > 20) clearInterval(t)
      }).catch(() => { if (++tries > 20) clearInterval(t) })
    }, 3000)
    return () => clearInterval(t)
  }, [])
  if (!v || hidden) return null
  return (
    <span className="row" style={{ gap: 6, fontSize: 11 }} title="A newer release is on GitHub. Update: git pull, then rebuild the dashboard.">
      <a href={v.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)', fontWeight: 600 }}>{v.latest} available</a>
      <button className="btn sm icon" onClick={() => setHidden(true)} title="Hide until next start">✕</button>
    </span>
  )
}

function ScreenSelector() {
  const screens = useScreens(s => s.screens)
  const order = useScreens(s => s.order)
  const activeId = useScreens(s => s.activeId)
  const { setActive, createScreen, createFromTemplate, renameScreen, duplicateScreen, deleteScreen } = useScreens.getState()
  const [open, setOpen] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [name, setName] = useState('')
  const active = activeId ? screens[activeId] : null

  const startRename = (id: string) => { setRenaming(id); setName(screens[id]?.name ?? '') }
  const commitRename = () => { if (renaming) renameScreen(renaming, name); setRenaming(null) }

  return (
    <div style={{ position: 'relative' }}>
      <button className="btn" onClick={() => setOpen(o => !o)} title="Screens">
        <span className="faint">Screen</span> <b>{active?.name ?? '—'}</b> <span className="faint">▾</span>
      </button>
      <Menu open={open} onClose={() => { setOpen(false); setRenaming(null) }} style={{ minWidth: 240 }}>
        <MenuHead>Screens</MenuHead>
        {order.map(id => {
          const s = screens[id]
          if (!s) return null
          if (renaming === id) {
            return (
              <div key={id} className="row" style={{ padding: '4px 6px' }}>
                <input className="input sm" autoFocus value={name} onChange={e => setName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setRenaming(null) }} style={{ flex: 1 }} />
                <button className="btn sm primary" onClick={commitRename}>OK</button>
              </div>
            )
          }
          return (
            <div key={id} className="row" style={{ gap: 2 }}>
              <button className={`menu-item${id === activeId ? ' on' : ''}`} style={{ flex: 1 }} onClick={() => { setActive(id); setOpen(false) }}>
                {s.name}{s.locked && <span className="k">🔒</span>}
              </button>
              <button className="wf-ctl" title="Rename" onClick={() => startRename(id)}>✎</button>
              <button className="wf-ctl" title="Duplicate" onClick={() => { duplicateScreen(id); setOpen(false) }}>⧉</button>
              <button className="wf-ctl close" title="Delete" onClick={() => { if (confirm(`Delete screen "${s.name}"?`)) deleteScreen(id) }}>✕</button>
            </div>
          )
        })}
        <MenuSep />
        <MenuItem icon="+" onClick={() => { const n = prompt('New screen name', 'New screen'); if (n != null) { createScreen(n); setOpen(false) } }}>New screen</MenuItem>
        <MenuHead>Starter layouts (added as a new screen)</MenuHead>
        {(Object.keys(SCREEN_TEMPLATES) as ScreenTemplate[]).map(k => (
          <MenuItem key={k} icon="▦" onClick={() => { createFromTemplate(k); setOpen(false) }}>
            <span title={SCREEN_TEMPLATES[k].desc}>{SCREEN_TEMPLATES[k].label}</span>
          </MenuItem>
        ))}
      </Menu>
    </div>
  )
}

/** Things used rarely, out of the way so the bar fits at 1280px. */
function MoreMenu() {
  const [open, setOpen] = useState(false)
  const activeId = useScreens(s => s.activeId)
  const theme = useSettings(s => s.theme)
  const setTheme = useSettings(s => s.setTheme)
  const setMenuHidden = useSettings(s => s.setMenuHidden)
  const close = () => setOpen(false)
  return (
    <div style={{ position: 'relative' }}>
      <button className={`btn icon${open ? ' on' : ''}`} title="More: save layout, theme, hide this bar, fullscreen" aria-label="More" onClick={() => setOpen(o => !o)}>⋯</button>
      <Menu open={open} onClose={close} right style={{ minWidth: 220 }}>
        <MenuItem icon="💾" disabled={!activeId} onClick={() => {
          close(); if (!activeId) return
          const n = prompt('Save layout as', ''); if (n && n.trim()) useScreens.getState().duplicateScreen(activeId, n)
        }}>Save layout as new screen…</MenuItem>
        <MenuSep />
        <MenuHead>Theme</MenuHead>
        {THEMES.map(th => (
          <MenuItem key={th.key} icon={th.short} on={theme === th.key} onClick={() => { setTheme(th.key); close() }}>{th.label}</MenuItem>
        ))}
        <MenuSep />
        <MenuItem icon="▴" onClick={() => { setMenuHidden(true); close() }}>Hide this bar</MenuItem>
        <MenuItem icon="⛶" onClick={() => { close(); if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen?.() }}>Fullscreen</MenuItem>
      </Menu>
    </div>
  )
}

export function AddWindowMenu({ open, onClose }: { open: boolean; onClose(): void }) {
  const addWindow = useScreens(s => s.addWindow)
  return (
    <Menu open={open} onClose={onClose} style={{ minWidth: 200 }}>
      <MenuHead>Add window</MenuHead>
      {WINDOW_ORDER.map(t => (
        <MenuItem key={t} icon={WINDOW_ICONS[t]} onClick={() => { addWindow(t); onClose() }}>{WINDOW_TITLES[t]}</MenuItem>
      ))}
    </Menu>
  )
}

export function TopBar() {
  const [clock, setClock] = useState(nowET)
  const [addOpen, setAddOpen] = useState(false)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [audioOn, setAudioOn] = useState(audioReady())
  const activeId = useScreens(s => s.activeId)
  const locked = useScreens(s => (s.activeId ? s.screens[s.activeId]?.locked : false) ?? false)
  const saveState = useScreens(s => s.saveState)
  const setLocked = useScreens(s => s.setLocked)
  const menuHidden = useSettings(s => s.menuHidden)
  const setMenuHidden = useSettings(s => s.setMenuHidden)
  const globalMute = useSettings(s => s.globalMute)
  const setGlobalMute = useSettings(s => s.setGlobalMute)
  const { data: clockInfo } = usePoll(api.clock, 5000)

  useEffect(() => { const t = setInterval(() => setClock(nowET()), 1000); return () => clearInterval(t) }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setAddOpen(o => !o) }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'l') { e.preventDefault(); setLocked(!locked) }
      if ((e.ctrlKey || e.metaKey) && e.key === ',') { e.preventDefault(); setCfgOpen(o => !o) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [locked, setLocked])

  if (menuHidden) {
    return <div className="topbar hidden" title="Show menu" onClick={() => setMenuHidden(false)} />
  }

  const regime = (clockInfo?.regime ?? 'neutral').toLowerCase()
  const session = clockInfo?.session

  return (
    <header className="topbar">
      <div className="brand"><span className="brand-mark" /><span className="brand-name">EDGE SCANNER</span></div>
      <ScreenSelector />
      <div style={{ position: 'relative' }}>
        <button className="btn primary" onClick={() => setAddOpen(o => !o)} title="Add window (Ctrl+K)">+ Add Window</button>
        <AddWindowMenu open={addOpen} onClose={() => setAddOpen(false)} />
      </div>
      <button className={`btn icon${locked ? ' on' : ''}`} title={locked ? 'Unlock layout (Ctrl+L)' : 'Lock layout (Ctrl+L)'} onClick={() => setLocked(!locked)} disabled={!activeId}>
        {locked ? '🔒' : '🔓'}
      </button>
      <button className="btn" title="Config: setups, rankings and universe filters (Ctrl+,)" onClick={() => setCfgOpen(true)}>⚙ Config</button>
      {cfgOpen && <SetupsPanel onClose={() => setCfgOpen(false)} />}
      {/* Only when there is something to say: layouts save automatically. */}
      {saveState !== 'idle' && (
        <span className={saveState === 'error' ? 'down' : 'faint'} style={{ fontSize: 10.5 }}
          title={saveState === 'error' ? 'Layout could not be saved to the scanner' : 'Layout changes save automatically'}>
          {saveState === 'saving' ? 'saving…' : 'save failed'}
        </span>
      )}
      <span className="flex-spacer" />
      <FeedDots />
      <DataChip info={clockInfo} />
      <UpdateBadge />
      <span className="sep" />
      {clockInfo?.replay && <span className="chip static" style={{ color: 'var(--link-purple)', borderColor: 'var(--link-purple)' }} title="Replaying a past session">REPLAY {clockInfo.replay.date}</span>}
      {session && <span className={`chip static ${session === 'rth' ? 'up' : ''}`} title="Session">{session.toUpperCase()}</span>}
      <span className={`regime ${regime}`} title="SPY regime"><span className="dot" style={{ background: 'currentColor' }} />{regime}</span>
      <span className="clock">{clock}<span className="clock-tz">ET</span></span>
      <span className="sep" />
      {!audioOn && (
        <button className="btn icon attn" title="Enable sound: browsers need one click before alerts can play sound or speak" aria-label="Enable sound"
          onClick={async () => { setAudioOn(await unlockAudio()); playTone('ping') }}>🔈</button>
      )}
      <button className={`btn icon${globalMute ? ' on' : ''}`} title={globalMute ? 'Unmute all' : 'Mute all'} onClick={() => setGlobalMute(!globalMute)}>{globalMute ? '🔇' : '🔔'}</button>
      <MoreMenu />
    </header>
  )
}
