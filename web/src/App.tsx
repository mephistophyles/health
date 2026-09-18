import { useEffect, useMemo, useState } from 'react'
import type { Checklist, Me, Member, Period } from './api'
import { Notice, Spinner, useLoad } from './components'
import Today from './pages/Today'
import PlanPage from './pages/Plan'
import Groceries from './pages/Groceries'
import Library from './pages/Library'
import Progress from './pages/Progress'
import SettingsPage from './pages/Settings'

export type Ctx = {
  me: Me
  members: Member[]
  period: Period | null
  reloadPeriod: () => void
  reloadMembers: () => void
  goTo: (tab: TabKey) => void
}

const TABS = [
  { key: 'today', label: 'Today', glyph: '☀' },
  { key: 'plan', label: 'Plan', glyph: '☰' },
  { key: 'shop', label: 'Shop', glyph: '🛒' },
  { key: 'library', label: 'Library', glyph: '📖' },
  { key: 'progress', label: 'Progress', glyph: '📈' },
  { key: 'settings', label: 'Settings', glyph: '⚙' },
] as const

export type TabKey = (typeof TABS)[number]['key']

function currentTab(): TabKey {
  const hash = window.location.hash.replace('#', '')
  return (TABS.find((tab) => tab.key === hash)?.key ?? 'today') as TabKey
}

export default function App() {
  const [tab, setTab] = useState<TabKey>(currentTab)
  const me = useLoad<Me>('/api/me')
  const members = useLoad<Member[]>(me.data ? '/api/members' : null)
  const period = useLoad<Period | null>(me.data ? '/api/periods/current' : null)
  const checklist = useLoad<Checklist>(me.data ? '/api/checklist' : null)

  useEffect(() => {
    const onHash = () => setTab(currentTab())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const goTo = (next: TabKey) => {
    window.location.hash = next
    setTab(next)
    window.scrollTo(0, 0)
  }

  const context: Ctx | null = useMemo(
    () =>
      me.data && members.data
        ? {
            me: me.data,
            members: members.data,
            period: period.data ?? null,
            reloadPeriod: () => {
              period.reload()
              checklist.reload()
            },
            reloadMembers: members.reload,
            goTo,
          }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [me.data, members.data, period.data],
  )

  if (me.error) {
    return (
      <main style={{ padding: 24 }}>
        <h1>Health</h1>
        <Notice>
          {me.error.includes('Unknown identity')
            ? 'This login is not set up for the household yet. An admin needs to add it.'
            : me.error}
        </Notice>
      </main>
    )
  }
  if (!context) {
    return (
      <main style={{ padding: 24 }}>
        <Spinner label="Loading" />
      </main>
    )
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1 style={{ margin: 0 }}>Health</h1>
        <span className="who">{context.me.display_name}</span>
      </header>

      <nav className="tabs" aria-label="Sections">
        {TABS.map((item) => (
          <button
            key={item.key}
            aria-current={tab === item.key ? 'page' : undefined}
            onClick={() => goTo(item.key)}
          >
            <span className="glyph" aria-hidden>{item.glyph}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <main>
        {tab === 'today' && (
          <Today ctx={context} checklist={checklist.data} reloadChecklist={checklist.reload} />
        )}
        {tab === 'plan' && <PlanPage ctx={context} onChanged={checklist.reload} />}
        {tab === 'shop' && <Groceries ctx={context} />}
        {tab === 'library' && <Library ctx={context} />}
        {tab === 'progress' && <Progress ctx={context} />}
        {tab === 'settings' && <SettingsPage ctx={context} />}
      </main>
    </div>
  )
}
