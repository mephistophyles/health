// The planning workbench for one period: the inputs, the Generate button, and the review
// of whatever came back (PRD 3 and 4). Nothing here runs on its own.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Ctx } from '../App'
import type {
  Availability,
  CookTime,
  DinnerMode,
  FitnessPlanContent,
  Job,
  LunchMode,
  MealPlanContent,
  Page,
  Period,
  PeriodDay,
  PeriodDetail,
  Plan,
  Plans,
  Trip,
} from '../api'
import { ApiError, api } from '../api'
import {
  COOK_TIMES,
  Card,
  Chips,
  DINNER_MODES,
  Empty,
  Field,
  LUNCH_MODES,
  Notice,
  Pill,
  Sheet,
  Spinner,
  dayLabel,
  modeLabel,
  shortDay,
  useAction,
  useLoad,
} from '../components'
import { RecipeDetail, describe } from './Today'
import { MealFeedbackList, PeriodFeedback } from './Feedback'

const JOB_LABELS: Record<string, string> = {
  'meals.generate': 'Meal plan',
  'fitness.generate': 'Your training plan',
  'meals.revise': 'Meal revision',
  'fitness.revise': 'Training revision',
  'profile.suggest': 'Profile suggestions',
}

// Used until /api/settings answers; the household can edit the real list in Settings.
const FALLBACK_CHIPS = {
  meals: ['lighter', 'quicker', 'more kid-friendly', 'cheaper', 'different protein'],
  fitness: ['easier', 'harder', 'shorter', 'less equipment', 'joint-friendly'],
}

const SESSION_MINUTES = [30, 45, 60]
const SESSION_TYPES = ['strength', 'run', 'mobility', 'class']

function isWeekday(iso: string) {
  const weekday = new Date(`${iso}T12:00:00`).getDay()
  return weekday >= 1 && weekday <= 5
}

/** Polls the job list while anything is in flight, and tells the page when it all settles. */
function useJobs(periodId: string | null, onSettled: () => void) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState('')
  const settled = useRef(onSettled)
  settled.current = onSettled

  const refresh = useCallback(async () => {
    if (!periodId) {
      setJobs([])
      return
    }
    try {
      setJobs(await api.get<Job[]>(`/api/periods/${periodId}/jobs`))
      setError('')
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : String(problem))
    }
  }, [periodId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const running = jobs.some((job) => job.status === 'queued' || job.status === 'running')

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(refresh, 3000)
    return () => window.clearInterval(timer)
  }, [running, refresh])

  const wasRunning = useRef(false)
  useEffect(() => {
    // A draft only exists once the worker has written it, so reload on the falling edge.
    if (wasRunning.current && !running) settled.current()
    wasRunning.current = running
  }, [running])

  return { jobs, refresh, running, error }
}

export default function PlanPage({ ctx, onChanged }: { ctx: Ctx; onChanged: () => void }) {
  const [periodId, setPeriodId] = useState<string | null>(ctx.period?.id ?? null)
  const periods = useLoad<Page<Period>>('/api/periods?limit=20')
  const detail = useLoad<PeriodDetail>(periodId ? `/api/periods/${periodId}` : null)
  const plans = useLoad<Plans>(periodId ? `/api/periods/${periodId}/plans` : null)
  const settings = useLoad<Record<string, unknown>>('/api/settings')
  const jobs = useJobs(periodId, () => plans.reload())
  const create = useAction()

  useEffect(() => {
    if (!periodId && ctx.period) setPeriodId(ctx.period.id)
  }, [ctx.period, periodId])

  const chips = (settings.data?.revise_chips as Partial<typeof FALLBACK_CHIPS> | undefined) ?? {}
  const mealChips = chips.meals ?? FALLBACK_CHIPS.meals
  const fitnessChips = chips.fitness ?? FALLBACK_CHIPS.fitness

  const mealPlan = plans.data?.meals.draft ?? plans.data?.meals.accepted ?? null
  const fitnessPlan = plans.data?.fitness.draft ?? plans.data?.fitness.accepted ?? null
  const inputsDefault = detail.data?.status === 'planning'
  const [inputsOpen, setInputsOpen] = useState<boolean | null>(null)
  const showInputs = inputsOpen ?? inputsDefault

  return (
    <>
      <Card>
        <div className="row between" style={{ alignItems: 'flex-start' }}>
          <h2 style={{ margin: 0 }}>Plan a period</h2>
          {detail.data && <Pill>{detail.data.status}</Pill>}
        </div>
        <Notice>{periods.error || create.error}</Notice>

        <Field label="Period">
          <select
            value={periodId ?? ''}
            onChange={(event) => setPeriodId(event.target.value || null)}
          >
            {!periodId && <option value="">Choose a period</option>}
            {periods.data?.items.map((period) => (
              <option key={period.id} value={period.id}>
                {dayLabel(period.start_date)} → {dayLabel(period.end_date)} · {period.status}
              </option>
            ))}
          </select>
        </Field>

        <button
          disabled={create.busy}
          onClick={() =>
            create.run(async () => {
              const period = await api.post<Period>('/api/periods', {})
              setPeriodId(period.id)
              setInputsOpen(true)
              await periods.reload()
              ctx.reloadPeriod()
            })
          }
        >
          {create.busy ? 'Starting…' : 'Start next period'}
        </button>
      </Card>

      {!periodId ? (
        <Card>
          <Empty>No period selected yet. Start the next one to begin planning.</Empty>
        </Card>
      ) : detail.loading && !detail.data ? (
        <Card>
          <Spinner label="Loading the period" />
        </Card>
      ) : !detail.data ? (
        <Card>
          <Notice>{detail.error}</Notice>
        </Card>
      ) : (
        <>
          <Card>
            <div className="row between">
              <h3 style={{ margin: 0 }}>Inputs</h3>
              <button className="small ghost" onClick={() => setInputsOpen(!showInputs)}>
                {showInputs ? 'Hide' : 'Edit'}
              </button>
            </div>
            {showInputs ? (
              <PeriodInputs
                period={detail.data}
                me={ctx.me.display_name}
                onSaved={() => {
                  detail.reload()
                  onChanged()
                }}
              />
            ) : (
              <p className="small muted" style={{ margin: 0 }}>
                {detail.data.days.length} days · {detail.data.trips.length} shopping trip(s) ·
                {' '}
                {detail.data.availability?.days.length ?? 0} training day(s) for you
              </p>
            )}
          </Card>

          <GenerateCard periodId={periodId} jobs={jobs} />

          <Card>
            <div className="row between">
              <h3 style={{ margin: 0 }}>Meal plan</h3>
              {mealPlan && <Pill kind={mealPlan.status === 'accepted' ? 'accent' : ''}>
                {mealPlan.status}
              </Pill>}
            </div>
            {plans.loading && !plans.data ? (
              <Spinner label="Loading plans" />
            ) : !mealPlan ? (
              <Empty>Nothing generated yet. Press Generate when the inputs are right.</Empty>
            ) : (
              <MealPlanReview
                plan={mealPlan}
                periodId={periodId}
                ctx={ctx}
                chipOptions={mealChips}
                onQueued={() => jobs.refresh()}
                onChanged={() => {
                  plans.reload()
                  onChanged()
                }}
              />
            )}
          </Card>

          <Card>
            <div className="row between">
              <h3 style={{ margin: 0 }}>Your training plan</h3>
              <Pill kind="accent">Private</Pill>
            </div>
            <p className="tiny muted">
              Only you can see or accept this; the other adult plans their own.
            </p>
            {plans.loading && !plans.data ? (
              <Spinner label="Loading plans" />
            ) : !fitnessPlan ? (
              <Empty>Nothing generated yet.</Empty>
            ) : (
              <FitnessPlanReview
                plan={fitnessPlan}
                periodId={periodId}
                chipOptions={fitnessChips}
                onQueued={() => jobs.refresh()}
                onChanged={() => {
                  plans.reload()
                  onChanged()
                }}
              />
            )}
          </Card>

          <PeriodFeedback periodId={periodId} me={ctx.me} onSaved={onChanged} />
          <MealFeedbackList periodId={periodId} members={ctx.members} />
        </>
      )}
    </>
  )
}

// --- inputs -----------------------------------------------------------------------

function PeriodInputs({
  period,
  me,
  onSaved,
}: {
  period: PeriodDetail
  me: string
  onSaved: () => void
}) {
  return (
    <>
      <DatesForm period={period} onSaved={onSaved} />
      <DaysEditor period={period} onSaved={onSaved} />
      <TripsEditor period={period} onSaved={onSaved} />
      <AvailabilityEditor period={period} me={me} onSaved={onSaved} />
    </>
  )
}

function DatesForm({ period, onSaved }: { period: PeriodDetail; onSaved: () => void }) {
  const [start, setStart] = useState(period.start_date)
  const [end, setEnd] = useState(period.end_date)
  const [notes, setNotes] = useState(period.notes)
  const { run, busy, error } = useAction(onSaved)
  const dirty = start !== period.start_date || end !== period.end_date || notes !== period.notes

  return (
    <div style={{ marginTop: 10 }}>
      <Notice>{error}</Notice>
      <div className="field-row">
        <Field label="Start">
          <input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
        </Field>
        <Field label="End">
          <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
        </Field>
      </div>
      <Field label="Notes" hint="Anything the plan should know: guests, late nights, travel.">
        <textarea
          value={notes}
          placeholder="Guests Saturday; someone home late Tuesday"
          onChange={(event) => setNotes(event.target.value)}
        />
      </Field>
      <button
        disabled={busy || !dirty}
        onClick={() =>
          run(() =>
            api.patch(`/api/periods/${period.id}`, { start_date: start, end_date: end, notes }),
          )
        }
      >
        {busy ? 'Saving…' : dirty ? 'Save dates and notes' : 'No changes'}
      </button>
    </div>
  )
}

function DaysEditor({ period, onSaved }: { period: PeriodDetail; onSaved: () => void }) {
  const [days, setDays] = useState<PeriodDay[]>(period.days)
  const { run, busy, error } = useAction(onSaved)

  const change = (index: number, patch: Partial<PeriodDay>) => {
    const next = days.map((day, position) => (position === index ? { ...day, ...patch } : day))
    setDays(next)
    run(() => api.put(`/api/periods/${period.id}/days`, next))
  }

  return (
    <div style={{ marginTop: 16 }}>
      <div className="row between">
        <h3 style={{ margin: 0 }}>Each day</h3>
        {busy && <Spinner />}
      </div>
      <Notice>{error}</Notice>
      {days.map((day, index) => (
        <div key={day.day} className="card tight">
          <strong className="small">{dayLabel(day.day)}</strong>
          <div className="field-row" style={{ marginTop: 6 }}>
            <Field label="Dinner">
              <select
                value={day.dinner_mode}
                onChange={(event) =>
                  change(index, { dinner_mode: event.target.value as DinnerMode })
                }
              >
                {DINNER_MODES.map((mode) => (
                  <option key={mode.value} value={mode.value}>{mode.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Lunch">
              <select
                value={day.lunch_mode}
                onChange={(event) => change(index, { lunch_mode: event.target.value as LunchMode })}
              >
                {LUNCH_MODES.map((mode) => (
                  <option key={mode.value} value={mode.value}>{mode.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Cooking time">
              <select
                value={day.cook_time}
                onChange={(event) => change(index, { cook_time: event.target.value as CookTime })}
              >
                {COOK_TIMES.map((time) => (
                  <option key={time.value} value={time.value}>{time.label}</option>
                ))}
              </select>
            </Field>
          </div>
        </div>
      ))}
    </div>
  )
}

function TripsEditor({ period, onSaved }: { period: PeriodDetail; onSaved: () => void }) {
  const [date, setDate] = useState(period.start_date)
  const [store, setStore] = useState('')
  const { run, busy, error } = useAction(onSaved)

  return (
    <div style={{ marginTop: 16 }}>
      <h3>Shopping trips</h3>
      <p className="tiny muted">The grocery list is split across these.</p>
      <Notice>{error}</Notice>
      {!period.trips.length && <Empty>No trips yet — add at least one.</Empty>}
      {period.trips.map((trip) => (
        <TripRow key={trip.id} trip={trip} onSaved={onSaved} />
      ))}
      <div className="field-row" style={{ marginTop: 10 }}>
        <Field label="New trip date">
          <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        </Field>
        <Field label="Store (optional)">
          <input
            value={store}
            placeholder="Market Basket"
            onChange={(event) => setStore(event.target.value)}
          />
        </Field>
      </div>
      <button
        disabled={busy || !date}
        onClick={() =>
          run(async () => {
            await api.post(`/api/periods/${period.id}/trips`, {
              trip_date: date,
              store: store || null,
            })
            setStore('')
          })
        }
      >
        Add trip
      </button>
    </div>
  )
}

function TripRow({ trip, onSaved }: { trip: Trip; onSaved: () => void }) {
  const [date, setDate] = useState(trip.trip_date)
  const [store, setStore] = useState(trip.store ?? '')
  const { run, busy, error } = useAction(onSaved)
  const dirty = date !== trip.trip_date || store !== (trip.store ?? '')

  return (
    <div className="card tight">
      <Notice>{error}</Notice>
      <div className="field-row">
        <Field label="Date">
          <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        </Field>
        <Field label="Store">
          <input
            value={store}
            placeholder="Any store"
            onChange={(event) => setStore(event.target.value)}
          />
        </Field>
      </div>
      <div className="row wrap" style={{ gap: 8 }}>
        <button
          className="small"
          disabled={busy || !dirty}
          onClick={() =>
            run(() => api.patch(`/api/trips/${trip.id}`, { trip_date: date, store: store || null }))
          }
        >
          {dirty ? 'Move trip' : 'No changes'}
        </button>
        <button
          className="small ghost danger"
          disabled={busy}
          onClick={() => run(() => api.del(`/api/trips/${trip.id}`))}
        >
          Delete
        </button>
      </div>
    </div>
  )
}

function AvailabilityEditor({
  period,
  me,
  onSaved,
}: {
  period: PeriodDetail
  me: string
  onSaved: () => void
}) {
  const saved: Availability = period.availability ?? {
    days: [],
    travel: false,
    session_minutes: 45,
    session_types: [],
  }
  const [days, setDays] = useState<string[]>(saved.days)
  const [travel, setTravel] = useState(saved.travel)
  const [minutes, setMinutes] = useState(saved.session_minutes)
  const [types, setTypes] = useState<string[]>(saved.session_types)
  const { run, busy, error } = useAction(onSaved)

  // Weekends are never planned, so they are not offered at all (PRD 6).
  const options = period.days.map((day) => day.day).filter(isWeekday)

  return (
    <div style={{ marginTop: 16 }}>
      <div className="row between">
        <h3 style={{ margin: 0 }}>Your training availability</h3>
        <Pill kind="accent">Private</Pill>
      </div>
      <p className="tiny muted">
        {me} only. The other adult sets their own, and neither sees the other's.
      </p>
      <Notice>{error}</Notice>

      <Field label="Days you can train" hint="Weekends are never planned.">
        <div className="row wrap" style={{ gap: 6 }}>
          {options.map((day) => (
            <button
              key={day}
              type="button"
              className={days.includes(day) ? 'primary' : ''}
              style={{ padding: '0 12px' }}
              aria-pressed={days.includes(day)}
              onClick={() =>
                setDays(days.includes(day) ? days.filter((other) => other !== day) : [...days, day])
              }
            >
              {shortDay(day)}
            </button>
          ))}
        </div>
      </Field>

      <Field label="Travelling this period" hint="Switches you to the travel equipment profile.">
        <button
          type="button"
          className={travel ? 'primary' : ''}
          aria-pressed={travel}
          onClick={() => setTravel(!travel)}
        >
          {travel ? 'Away — hotel gym, bodyweight, running' : 'Home'}
        </button>
      </Field>

      <Field label="Session length">
        <select value={minutes} onChange={(event) => setMinutes(Number(event.target.value))}>
          {SESSION_MINUTES.map((option) => (
            <option key={option} value={option}>{option} min</option>
          ))}
        </select>
      </Field>

      <Field label="Session types you want">
        <Chips
          options={SESSION_TYPES}
          selected={types}
          onToggle={(type) =>
            setTypes(types.includes(type) ? types.filter((other) => other !== type) : [...types, type])
          }
        />
      </Field>

      <button
        className="primary"
        disabled={busy}
        onClick={() =>
          run(() =>
            api.put(`/api/periods/${period.id}/availability`, {
              days,
              travel,
              session_minutes: minutes,
              session_types: types,
            }),
          )
        }
      >
        {busy ? 'Saving…' : 'Save my availability'}
      </button>
    </div>
  )
}

// --- generate ---------------------------------------------------------------------

function GenerateCard({
  periodId,
  jobs,
}: {
  periodId: string
  jobs: ReturnType<typeof useJobs>
}) {
  const { run, busy, error } = useAction(() => jobs.refresh())

  const generate = (loops: string[], ownFitnessOnly = false) =>
    run(() =>
      api.post(`/api/periods/${periodId}/generate`, { loops, own_fitness_only: ownFitnessOnly }),
    )

  return (
    <Card>
      <h3>Generate</h3>
      <p className="tiny muted">Nothing generates on its own. A failed loop leaves the others alone.</p>
      <Notice>{error || jobs.error}</Notice>

      <div className="row wrap" style={{ gap: 8 }}>
        <button
          className="primary"
          disabled={busy || jobs.running}
          onClick={() => generate(['meals', 'fitness'])}
        >
          {jobs.running ? 'Working…' : 'Generate'}
        </button>
        <button disabled={busy || jobs.running} onClick={() => generate(['meals'])}>
          Meals only
        </button>
        <button disabled={busy || jobs.running} onClick={() => generate(['fitness'], true)}>
          My training only
        </button>
      </div>

      {!!jobs.jobs.length && (
        <div style={{ marginTop: 12 }}>
          {jobs.jobs.map((job) => (
            <JobRow key={job.id} job={job} onRetried={() => jobs.refresh()} />
          ))}
        </div>
      )}
    </Card>
  )
}

function JobRow({ job, onRetried }: { job: Job; onRetried: () => void }) {
  const { run, busy, error } = useAction(onRetried)
  const label = JOB_LABELS[job.kind] ?? job.kind
  const kind =
    job.status === 'failed' ? 'danger' : job.status === 'succeeded' ? 'accent' : 'warn'

  return (
    <div className="card tight">
      <div className="row between">
        <span className="grow">{label}</span>
        {job.status === 'running' || job.status === 'queued' ? (
          <Spinner label={job.status} />
        ) : (
          <Pill kind={kind}>{job.status}</Pill>
        )}
      </div>
      {job.detail && <p className="tiny muted" style={{ margin: '4px 0 0' }}>{job.detail}</p>}
      <Notice>{error}</Notice>
      {job.status === 'failed' && (
        <button
          className="small"
          style={{ marginTop: 8 }}
          disabled={busy}
          onClick={() => run(() => api.post(`/api/jobs/${job.id}/retry`))}
        >
          Retry {label.toLowerCase()}
        </button>
      )}
    </div>
  )
}

// --- review sheets ----------------------------------------------------------------

function ReviseSheet({
  title,
  options,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  title: string
  options: string[]
  busy: boolean
  error: string
  onClose: () => void
  onSubmit: (feedback: string, chips: string[]) => void
}) {
  const [feedback, setFeedback] = useState('')
  const [chips, setChips] = useState<string[]>([])

  return (
    <Sheet title={title} onClose={onClose}>
      <Notice>{error}</Notice>
      <Field label="What should change?">
        <textarea
          value={feedback}
          autoFocus
          placeholder="Too much prep for a Tuesday"
          onChange={(event) => setFeedback(event.target.value)}
        />
      </Field>
      <Field label="Or tap a shortcut">
        <Chips
          options={options}
          selected={chips}
          onToggle={(chip) =>
            setChips(chips.includes(chip) ? chips.filter((other) => other !== chip) : [...chips, chip])
          }
        />
      </Field>
      <button
        className="primary"
        disabled={busy || (!feedback.trim() && !chips.length)}
        onClick={() => onSubmit(feedback, chips)}
      >
        {busy ? 'Sending…' : 'Revise'}
      </button>
    </Sheet>
  )
}

function RejectSheet({
  title,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  title: string
  busy: boolean
  error: string
  onClose: () => void
  onSubmit: (reason: string) => void
}) {
  const [reason, setReason] = useState('')

  return (
    <Sheet title={title} onClose={onClose}>
      <Notice>{error}</Notice>
      <Field label="Why? (optional)" hint="Kept on the rejection list so it is not suggested again.">
        <textarea
          value={reason}
          autoFocus
          placeholder="Nobody here likes fennel"
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
      <button className="primary" disabled={busy} onClick={() => onSubmit(reason)}>
        {busy ? 'Removing…' : 'Reject it'}
      </button>
    </Sheet>
  )
}

/** Accept / revise whole / write my own — the actions that apply to a plan as a whole. */
function PlanActions({
  plan,
  periodId,
  kind,
  chipOptions,
  onQueued,
  onChanged,
}: {
  plan: Plan<unknown>
  periodId: string
  kind: 'meals' | 'fitness'
  chipOptions: string[]
  onQueued: () => void
  onChanged: () => void
}) {
  const [revising, setRevising] = useState(false)
  const [confirmManual, setConfirmManual] = useState(false)
  const { run, busy, error } = useAction()

  return (
    <>
      <Notice>{error}</Notice>
      <div className="row wrap" style={{ gap: 8, marginTop: 10 }}>
        {plan.status === 'draft' && (
          <button
            className="primary"
            disabled={busy}
            onClick={() => run(async () => {
              await api.post(`/api/plans/${plan.id}/accept`)
              onChanged()
            })}
          >
            Accept this plan
          </button>
        )}
        {plan.status === 'draft' && (
          <button disabled={busy} onClick={() => setRevising(true)}>
            Revise the whole plan
          </button>
        )}
        <button className="ghost" disabled={busy} onClick={() => setConfirmManual(true)}>
          Write my own
        </button>
      </div>

      {confirmManual && (
        <Notice kind="warn">
          Writing your own discards the current draft. It cannot be brought back.
          <div className="row wrap" style={{ gap: 8, marginTop: 8 }}>
            <button
              className="small"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.post(`/api/periods/${periodId}/plans/manual?kind=${kind}`)
                  setConfirmManual(false)
                  onChanged()
                })
              }
            >
              Discard and start blank
            </button>
            <button className="small ghost" onClick={() => setConfirmManual(false)}>
              Keep the draft
            </button>
          </div>
        </Notice>
      )}

      {revising && (
        <ReviseSheet
          title="Revise the whole plan"
          options={chipOptions}
          busy={busy}
          error={error}
          onClose={() => setRevising(false)}
          onSubmit={(feedback, chips) =>
            run(async () => {
              await api.post(`/api/plans/${plan.id}/revise`, {
                target: 'plan',
                feedback_text: feedback,
                chips,
              })
              setRevising(false)
              onQueued()
            })
          }
        />
      )}
    </>
  )
}

// --- meal plan --------------------------------------------------------------------

function MealPlanReview({
  plan,
  periodId,
  ctx,
  chipOptions,
  onQueued,
  onChanged,
}: {
  plan: Plan<MealPlanContent>
  periodId: string
  ctx: Ctx
  chipOptions: string[]
  onQueued: () => void
  onChanged: () => void
}) {
  const [revising, setRevising] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState<string | null>(null)
  const { run, busy, error } = useAction()
  const draft = plan.status === 'draft'

  return (
    <>
      {plan.content.summary && <p className="small">{plan.content.summary}</p>}
      <p className="tiny muted">
        {plan.origin === 'manual' ? 'Written by hand' : `Generated${plan.model ? ` by ${plan.model}` : ''}`}
      </p>
      <Notice>{error}</Notice>

      {!plan.content.days.length && <Empty>This plan has no days yet.</Empty>}

      {plan.content.days.map((day, index) => {
        const recipe = plan.content.recipes.find((item) => item.key === day.dinner.recipe_key)
        const target = `days[${index}].dinner`
        return (
          <div key={day.date} className="card tight">
            <div className="row between" style={{ alignItems: 'flex-start' }}>
              <div className="grow">
                <strong>{dayLabel(day.date)}</strong>
                <div className="small muted">{modeLabel(day.dinner.mode)}</div>
              </div>
              {recipe && <Pill>{recipe.active_min}/{recipe.total_min} min</Pill>}
            </div>

            {recipe ? (
              <>
                <p style={{ margin: '6px 0 0' }}>{recipe.title}</p>
                {!!recipe.feeds.length && (
                  <p className="tiny muted" style={{ margin: '2px 0 0' }}>
                    {recipe.feeds
                      .map((feed) => `makes ${shortDay(feed.date)} ${feed.meal} ×${feed.servings}`)
                      .join(' · ')}
                  </p>
                )}
                <RecipeDetail recipe={recipe} ctx={ctx} />
              </>
            ) : day.dinner.leftover_of ? (
              <p className="small muted" style={{ margin: '6px 0 0' }}>
                Leftovers from {dayLabel(day.dinner.leftover_of)}
              </p>
            ) : (
              <p className="small muted" style={{ margin: '6px 0 0' }}>Nothing to cook.</p>
            )}

            {day.lunch.mode !== 'none' && (
              <p className="tiny muted" style={{ margin: '4px 0 0' }}>
                Lunch: {modeLabel(day.lunch.mode)}
                {day.lunch.leftover_of ? ` from ${dayLabel(day.lunch.leftover_of)}` : ''}
              </p>
            )}

            {draft && (
              <div className="row wrap" style={{ gap: 8, marginTop: 8 }}>
                <button className="small" disabled={busy} onClick={() => setRevising(target)}>
                  Revise
                </button>
                <button className="small ghost" disabled={busy} onClick={() => setRejecting(target)}>
                  Reject
                </button>
              </div>
            )}
          </div>
        )
      })}

      <PlanActions
        plan={plan}
        periodId={periodId}
        kind="meals"
        chipOptions={chipOptions}
        onQueued={onQueued}
        onChanged={onChanged}
      />

      {revising && (
        <ReviseSheet
          title="Revise this dinner"
          options={chipOptions}
          busy={busy}
          error={error}
          onClose={() => setRevising(null)}
          onSubmit={(feedback, chips) =>
            run(async () => {
              await api.post(`/api/plans/${plan.id}/revise`, {
                target: revising,
                feedback_text: feedback,
                chips,
              })
              setRevising(null)
              onQueued()
            })
          }
        />
      )}

      {rejecting && (
        <RejectSheet
          title="Reject this dinner"
          busy={busy}
          error={error}
          onClose={() => setRejecting(null)}
          onSubmit={(reason) =>
            run(async () => {
              await api.post(`/api/plans/${plan.id}/reject-item`, { target: rejecting, reason })
              setRejecting(null)
              onChanged()
            })
          }
        />
      )}
    </>
  )
}

// --- fitness plan -----------------------------------------------------------------

function FitnessPlanReview({
  plan,
  periodId,
  chipOptions,
  onQueued,
  onChanged,
}: {
  plan: Plan<FitnessPlanContent>
  periodId: string
  chipOptions: string[]
  onQueued: () => void
  onChanged: () => void
}) {
  // The revise target is either `sessions[N]` or an exercise key; the server resolves both.
  const [revising, setRevising] = useState<{ target: string; title: string } | null>(null)
  const [rejecting, setRejecting] = useState<{ target: string; title: string } | null>(null)
  const { run, busy, error } = useAction()
  const draft = plan.status === 'draft'

  return (
    <>
      <div className="row between">
        <Pill kind="accent">{plan.content.intent} week</Pill>
        <span className="tiny muted">
          {plan.origin === 'manual' ? 'Written by hand' : plan.model ?? 'Generated'}
        </span>
      </div>
      {plan.content.summary && <p className="small" style={{ marginTop: 8 }}>{plan.content.summary}</p>}
      <Notice>{error}</Notice>

      {!plan.content.sessions.length && <Empty>No sessions in this plan yet.</Empty>}

      {plan.content.sessions.map((session, index) => (
        <div key={session.date} className="card tight">
          <div className="row between">
            <strong>{shortDay(session.date)} — {session.focus}</strong>
            <Pill>{session.duration_min} min</Pill>
          </div>

          {!!session.warmup.length && (
            <p className="small muted" style={{ margin: '6px 0 0' }}>
              Warm-up: {session.warmup.join(' · ')}
            </p>
          )}

          <table>
            <thead>
              <tr>
                <th>Exercise</th>
                <th className="right">Prescribed</th>
              </tr>
            </thead>
            <tbody>
              {session.exercises.map((exercise) => (
                <tr key={exercise.key}>
                  <td>
                    {exercise.name}
                    {exercise.cues && <div className="tiny muted">{exercise.cues}</div>}
                    {!!exercise.alternatives.length && (
                      <div className="tiny muted">
                        or {exercise.alternatives
                          .map((alternative) => `${alternative.name} (${alternative.reason})`)
                          .join(', ')}
                      </div>
                    )}
                    {draft && (
                      <button
                        className="small ghost"
                        style={{ marginTop: 4 }}
                        disabled={busy}
                        onClick={() =>
                          setRevising({ target: exercise.key, title: `Revise ${exercise.name}` })
                        }
                      >
                        Revise
                      </button>
                    )}
                  </td>
                  <td className="right small">
                    {describe(exercise)}
                    {exercise.rpe ? <div className="tiny muted">RPE {exercise.rpe}</div> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {!!session.cooldown.length && (
            <p className="small muted">Cool-down: {session.cooldown.join(' · ')}</p>
          )}

          {draft && (
            <div className="row wrap" style={{ gap: 8 }}>
              <button
                className="small"
                disabled={busy}
                onClick={() =>
                  setRevising({ target: `sessions[${index}]`, title: 'Revise this session' })
                }
              >
                Revise session
              </button>
              <button
                className="small ghost"
                disabled={busy}
                onClick={() =>
                  setRejecting({ target: `sessions[${index}]`, title: 'Reject this session' })
                }
              >
                Reject session
              </button>
            </div>
          )}
        </div>
      ))}

      <PlanActions
        plan={plan}
        periodId={periodId}
        kind="fitness"
        chipOptions={chipOptions}
        onQueued={onQueued}
        onChanged={onChanged}
      />

      {revising && (
        <ReviseSheet
          title={revising.title}
          options={chipOptions}
          busy={busy}
          error={error}
          onClose={() => setRevising(null)}
          onSubmit={(feedback, chips) =>
            run(async () => {
              await api.post(`/api/plans/${plan.id}/revise`, {
                target: revising.target,
                feedback_text: feedback,
                chips,
              })
              setRevising(null)
              onQueued()
            })
          }
        />
      )}

      {rejecting && (
        <RejectSheet
          title={rejecting.title}
          busy={busy}
          error={error}
          onClose={() => setRejecting(null)}
          onSubmit={(reason) =>
            run(async () => {
              await api.post(`/api/plans/${plan.id}/reject-item`, {
                target: rejecting.target,
                reason,
              })
              setRejecting(null)
              onChanged()
            })
          }
        />
      )}
    </>
  )
}
