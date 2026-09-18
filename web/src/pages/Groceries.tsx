// The grocery list, used one-handed in a shop. Every decision here follows from that:
// whole lines are tap targets, ticks apply before the network answers, and nothing moves
// under the thumb once it has been ticked.
import { useEffect, useMemo, useState } from 'react'
import type { Ctx } from '../App'
import type { GroceryItem, GroceryTrip, Page, Period, Plans } from '../api'
import { ApiError, api } from '../api'
import {
  Card,
  Empty,
  Field,
  Notice,
  Pill,
  Spinner,
  dayLabel,
  shortDay,
  todayIso,
  useAction,
  useLoad,
} from '../components'

type Groceries = { trips: GroceryTrip[] }

export default function Groceries({ ctx }: { ctx: Ctx }) {
  const [periodId, setPeriodId] = useState(ctx.period?.id ?? '')
  const [tripId, setTripId] = useState('')
  const [tickError, setTickError] = useState('')

  const periods = useLoad<Page<Period>>('/api/periods?limit=20')
  const groceries = useLoad<Groceries>(periodId ? `/api/periods/${periodId}/groceries` : null)
  const settings = useLoad<Record<string, any>>('/api/settings')

  const trips = groceries.data?.trips ?? []

  // Default to the trip you are most likely standing in a shop for: the closest to today.
  useEffect(() => {
    if (!trips.length) {
      setTripId('')
      return
    }
    if (trips.some((trip) => trip.id === tripId)) return
    const today = todayIso()
    const closest = trips.reduce((best, trip) =>
      Math.abs(distanceInDays(trip.trip_date, today)) < Math.abs(distanceInDays(best.trip_date, today))
        ? trip
        : best,
    )
    setTripId(closest.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groceries.data])

  const trip = trips.find((candidate) => candidate.id === tripId) ?? trips[0] ?? null

  const patchItem = (itemId: string, checked: boolean) =>
    groceries.setData((current) =>
      current
        ? {
            trips: current.trips.map((each) => {
              if (!each.items.some((item) => item.id === itemId)) return each
              const items = each.items.map((item) => (item.id === itemId ? { ...item, checked } : item))
              return { ...each, items, remaining: items.filter((item) => !item.checked).length }
            }),
          }
        : current,
    )

  const toggle = async (item: GroceryItem) => {
    const next = !item.checked
    patchItem(item.id, next)
    setTickError('')
    try {
      await api.patch(`/api/grocery-items/${item.id}`, { checked: next })
    } catch (problem) {
      // A tick that waits for the network is worse than a tick that occasionally backs out.
      patchItem(item.id, item.checked)
      setTickError(problem instanceof ApiError ? problem.message : String(problem))
    }
  }

  const categories: string[] = Array.isArray(settings.data?.grocery_categories)
    ? settings.data!.grocery_categories.filter((value: unknown) => typeof value === 'string')
    : []

  return (
    <>
      <Card>
        <div className="row between" style={{ gap: 8 }}>
          <h2 style={{ margin: 0 }}>Shopping</h2>
          <button className="small" onClick={() => groceries.reload()} disabled={groceries.loading}>
            Refresh
          </button>
        </div>
        <p className="tiny muted" style={{ marginBottom: 8 }}>
          Ticks are shared between both phones. Refresh to pick up the other one.
        </p>
        {(periods.data?.items.length ?? 0) > 1 && (
          <Field label="Period">
            <select value={periodId} onChange={(event) => setPeriodId(event.target.value)}>
              {periods.data!.items.map((period) => (
                <option key={period.id} value={period.id}>
                  {dayLabel(period.start_date)} → {dayLabel(period.end_date)}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Notice>{groceries.error || periods.error}</Notice>
        <Notice>{tickError}</Notice>
      </Card>

      {!periodId ? (
        <Card>
          <Empty>No planning period yet.</Empty>
          <button className="primary" onClick={() => ctx.goTo('plan')}>Set one up</button>
        </Card>
      ) : groceries.loading && !groceries.data ? (
        <Card>
          <Spinner label="Loading the list" />
        </Card>
      ) : !trips.length ? (
        <NoTrips ctx={ctx} periodId={periodId} onDone={() => groceries.reload()} />
      ) : (
        <>
          <Card>
            <div className="row wrap" style={{ gap: 6 }}>
              {trips.map((each) => (
                <button
                  key={each.id}
                  className="chip"
                  aria-pressed={each.id === trip?.id}
                  onClick={() => setTripId(each.id)}
                >
                  {shortDay(each.trip_date)}
                  {each.store ? ` · ${each.store}` : ''} · {each.remaining} left
                </button>
              ))}
            </div>
          </Card>

          {trip && (
            <TripList
              trip={trip}
              periodId={periodId}
              categories={categories}
              onToggle={toggle}
              onChanged={() => groceries.reload()}
            />
          )}
        </>
      )}
    </>
  )
}

function NoTrips({ ctx, periodId, onDone }: { ctx: Ctx; periodId: string; onDone: () => void }) {
  const { run, busy, error } = useAction(onDone)
  return (
    <Card>
      <h3>Nothing to shop for</h3>
      <p className="small muted">
        This period has no shopping trips, or no meal plan has been accepted yet — the list is
        computed from the accepted plan, so there is nothing to compute from.
      </p>
      <Notice>{error}</Notice>
      <div className="row wrap" style={{ gap: 8 }}>
        <button className="primary" onClick={() => ctx.goTo('plan')}>Go to Plan</button>
        <button
          disabled={busy}
          onClick={() => run(() => api.post(`/api/periods/${periodId}/groceries/recompute`))}
        >
          {busy ? 'Working…' : 'Recompute from the plan'}
        </button>
      </div>
    </Card>
  )
}

function TripList({
  trip,
  periodId,
  categories,
  onToggle,
  onChanged,
}: {
  trip: GroceryTrip
  periodId: string
  categories: string[]
  onToggle: (item: GroceryItem) => void
  onChanged: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [showSources, setShowSources] = useState(false)
  const { run, busy, error } = useAction(onChanged)

  // Only worth a fetch once someone asks where a line came from.
  const plans = useLoad<Plans>(showSources ? `/api/periods/${periodId}/plans` : null)
  const titleById = useMemo(() => {
    const map = new Map<string, string>()
    const plan = plans.data?.meals.accepted ?? plans.data?.meals.draft
    for (const recipe of plan?.content.recipes ?? []) {
      if (recipe.library_id) map.set(recipe.library_id, recipe.title)
    }
    return map
  }, [plans.data])

  // The server has already walked the store for us; grouping must not disturb that order.
  const groups: { category: string; items: GroceryItem[] }[] = []
  for (const item of trip.items) {
    const last = groups[groups.length - 1]
    if (last && last.category === item.category) last.items.push(item)
    else groups.push({ category: item.category, items: [item] })
  }

  return (
    <Card>
      <div className="row between" style={{ gap: 8 }}>
        <h3 style={{ margin: 0 }}>
          {dayLabel(trip.trip_date)}
          {trip.store ? ` · ${trip.store}` : ''}
        </h3>
        <Pill kind={trip.remaining ? 'warn' : 'accent'}>
          {trip.remaining} of {trip.items.length} left
        </Pill>
      </div>

      <Notice>{error}</Notice>

      {!trip.items.length ? (
        <Empty>No lines on this trip yet.</Empty>
      ) : (
        groups.map((group, index) => (
          <div key={`${group.category}-${index}`}>
            <p className="category">{group.category}</p>
            {group.items.map((item) => (
              <Line
                key={item.id}
                item={item}
                sources={showSources ? item.recipe_ids.map((id) => titleById.get(id) ?? 'a recipe') : null}
                onToggle={() => onToggle(item)}
                onDelete={
                  item.manual
                    ? () => run(() => api.del(`/api/grocery-items/${item.id}`))
                    : undefined
                }
                busy={busy}
              />
            ))}
          </div>
        ))
      )}

      <div className="row wrap" style={{ gap: 8, marginTop: 14 }}>
        <button className="small" onClick={() => setAdding((open) => !open)}>
          {adding ? 'Cancel' : 'Add a line'}
        </button>
        <button className="small ghost" onClick={() => setShowSources((shown) => !shown)}>
          {showSources ? 'Hide sources' : 'Where did these come from?'}
        </button>
      </div>

      {adding && (
        <AddLine
          tripId={trip.id}
          categories={categories.length ? categories : [...new Set(trip.items.map((i) => i.category))]}
          onAdded={() => {
            setAdding(false)
            onChanged()
          }}
        />
      )}

      <Recompute periodId={periodId} onDone={onChanged} />
    </Card>
  )
}

function Line({
  item,
  sources,
  onToggle,
  onDelete,
  busy,
}: {
  item: GroceryItem
  sources: string[] | null
  onToggle: () => void
  onDelete?: () => void
  busy: boolean
}) {
  return (
    <div>
      <button className="tick" aria-pressed={item.checked} onClick={onToggle}>
        <span className="box" aria-hidden>{item.checked ? '✓' : ''}</span>
        <span className="grow label">
          {item.check_pantry ? (
            <span className="muted">check pantry — </span>
          ) : (
            <strong>{[item.qty ?? '', item.unit].filter(Boolean).join(' ')} </strong>
          )}
          {item.name}
          {item.manual && <span className="tiny muted"> · added by hand</span>}
        </span>
        {item.check_pantry && <Pill>pantry</Pill>}
      </button>
      {sources && !item.check_pantry && (
        <p className="tiny muted" style={{ margin: '0 0 6px 36px' }}>
          {item.manual
            ? 'Added by hand.'
            : sources.length
              ? `For ${sources.join(', ')}.`
              : 'From the plan.'}
        </p>
      )}
      {onDelete && (
        <div style={{ paddingLeft: 36, marginBottom: 6 }}>
          <button className="small ghost danger" disabled={busy} onClick={onDelete}>
            Remove
          </button>
        </div>
      )}
    </div>
  )
}

function AddLine({
  tripId,
  categories,
  onAdded,
}: {
  tripId: string
  categories: string[]
  onAdded: () => void
}) {
  const [name, setName] = useState('')
  const [qty, setQty] = useState('')
  const [unit, setUnit] = useState('')
  const [category, setCategory] = useState(categories[0] ?? 'Other')
  const { run, busy, error } = useAction(onAdded)

  return (
    <div className="card tight" style={{ marginTop: 10 }}>
      <Notice>{error}</Notice>
      <Field label="Item">
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Coffee" />
      </Field>
      <div className="field-row">
        <Field label="Quantity">
          <input
            value={qty}
            inputMode="decimal"
            onChange={(event) => setQty(event.target.value)}
            placeholder="2"
          />
        </Field>
        <Field label="Unit">
          <input value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="lb" />
        </Field>
      </div>
      <Field label="Category" hint="Sets where it lands in the walk round the shop.">
        <select value={category} onChange={(event) => setCategory(event.target.value)}>
          {(categories.includes(category) ? categories : [category, ...categories]).map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
          {!categories.includes('Other') && <option value="Other">Other</option>}
        </select>
      </Field>
      <button
        className="primary"
        disabled={busy || !name.trim()}
        onClick={() =>
          run(() =>
            api.post(`/api/trips/${tripId}/items`, {
              name: name.trim(),
              qty: qty.trim() ? Number(qty) : null,
              unit: unit.trim(),
              category,
            }),
          )
        }
      >
        {busy ? 'Adding…' : 'Add'}
      </button>
      <p className="tiny muted" style={{ marginTop: 8, marginBottom: 0 }}>
        Hand-added lines survive a recompute, and only they can be removed.
      </p>
    </div>
  )
}

function Recompute({ periodId, onDone }: { periodId: string; onDone: () => void }) {
  const { run, busy, error } = useAction(onDone)
  return (
    <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
      <Notice>{error}</Notice>
      <button
        className="small"
        disabled={busy}
        onClick={() => run(() => api.post(`/api/periods/${periodId}/groceries/recompute`))}
      >
        {busy ? 'Recomputing…' : 'Recompute from the plan'}
      </button>
      <p className="tiny muted" style={{ marginTop: 6, marginBottom: 0 }}>
        Rebuilds the list from the accepted plan. Ticks on lines that have not changed are kept.
      </p>
    </div>
  )
}

function distanceInDays(iso: string, from: string): number {
  return (Date.parse(`${iso}T12:00:00`) - Date.parse(`${from}T12:00:00`)) / 86_400_000
}
