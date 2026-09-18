// Small shared pieces. Nothing here knows about a particular page.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, api } from './api'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`}>{children}</div>
}

export function Notice({ kind = 'error', children }: { kind?: 'error' | 'warn' | 'ok'; children: ReactNode }) {
  if (!children) return null
  return <div className={`notice ${kind}`}>{children}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row small muted" style={{ gap: 8 }}>
      <span className="spinner" aria-hidden />
      {label}
    </span>
  )
}

export function Pill({ kind = '', children }: { kind?: string; children: ReactNode }) {
  return <span className={`pill ${kind}`}>{children}</span>
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: string
}) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <div className="tiny muted" style={{ marginTop: 3 }}>{hint}</div>}
    </div>
  )
}

export function Chips({
  options,
  selected,
  onToggle,
}: {
  options: string[]
  selected: string[]
  onToggle: (value: string) => void
}) {
  return (
    <div className="row wrap" style={{ gap: 6 }}>
      {options.map((option) => (
        <button
          key={option}
          type="button"
          className="chip"
          aria-pressed={selected.includes(option)}
          onClick={() => onToggle(option)}
        >
          {option}
        </button>
      ))}
    </div>
  )
}

export function Sheet({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [onClose])

  return (
    <div className="sheet-backdrop" onClick={onClose} role="presentation">
      <div className="sheet" onClick={(event) => event.stopPropagation()} role="dialog" aria-label={title}>
        <div className="row between" style={{ marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost small" onClick={onClose}>Close</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** Load once, with a refresh handle. `deps` re-runs it the way useEffect would. */
export function useLoad<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(Boolean(path))
  const alive = useRef(true)

  const reload = useCallback(async () => {
    if (!path) {
      setData(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const result = await api.get<T>(path)
      if (alive.current) {
        setData(result)
        setError('')
      }
    } catch (problem) {
      if (alive.current) setError(problem instanceof ApiError ? problem.message : String(problem))
    } finally {
      if (alive.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  useEffect(() => {
    alive.current = true
    reload()
    return () => {
      alive.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps])

  return { data, error, loading, reload, setData }
}

/** Wraps a mutation so every page reports failure the same way. */
export function useAction(onDone?: () => void | Promise<void>) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = useCallback(
    async (work: () => Promise<unknown>) => {
      setBusy(true)
      setError('')
      try {
        await work()
        await onDone?.()
        return true
      } catch (problem) {
        setError(problem instanceof ApiError ? problem.message : String(problem))
        return false
      } finally {
        setBusy(false)
      }
    },
    [onDone],
  )

  return { run, busy, error, setError }
}

export const WEEKDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export function dayLabel(iso: string) {
  const date = new Date(`${iso}T12:00:00`)
  return `${WEEKDAY[date.getDay()]} ${date.getDate()} ${date.toLocaleString('en', { month: 'short' })}`
}

export function shortDay(iso: string) {
  const date = new Date(`${iso}T12:00:00`)
  return `${WEEKDAY[date.getDay()]} ${date.getDate()}`
}

export function todayIso(): string {
  // The household clock is America/New_York; the server agrees.
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
}

export const DINNER_MODES: { value: string; label: string }[] = [
  { value: 'cook', label: 'Cook' },
  { value: 'leftovers', label: 'Leftovers' },
  { value: 'takeout', label: 'Takeout' },
  { value: 'eating_out', label: 'Eating out' },
  { value: 'away', label: 'Away' },
]

export const LUNCH_MODES = [
  { value: 'none', label: 'Not planned' },
  { value: 'leftovers', label: 'Leftovers' },
  { value: 'prep', label: 'Prep' },
]

export const COOK_TIMES = [
  { value: 'quick', label: 'Quick (≤30 min)' },
  { value: 'normal', label: 'Normal' },
  { value: 'project', label: 'Project' },
]

export const VERDICTS: Record<string, { label: string; kind: string }> = {
  staple: { label: 'Staple', kind: 'accent' },
  worked: { label: 'Worked', kind: 'accent' },
  needs_tweak: { label: 'Needs tweak', kind: 'warn' },
  didnt_work: { label: "Didn't work", kind: 'danger' },
  untried: { label: 'Untried', kind: '' },
}

export function modeLabel(mode: string) {
  return DINNER_MODES.find((m) => m.value === mode)?.label ?? mode
}
