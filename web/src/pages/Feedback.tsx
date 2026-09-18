// Feedback, everywhere it is asked for (PRD 7). Everything here is optional and has to be
// fast: a tap saves, so nobody has to find a submit button at the dinner table.
import { useState } from 'react'
import type { Me, Member, PlanMeal } from '../api'
import { api } from '../api'
import { Card, Empty, Notice, Pill, Spinner, dayLabel, useAction, useLoad } from '../components'

type Choice = { value: string; label: string }

const PORTIONS: Choice[] = [
  { value: 'too_little', label: 'Too little' },
  { value: 'right', label: 'Right' },
  { value: 'too_much', label: 'Too much' },
]

const ATE: Choice[] = [
  { value: 'yes', label: 'Yes' },
  { value: 'partly', label: 'Partly' },
  { value: 'no', label: 'No' },
]

const EFFORT: Choice[] = [
  { value: 'less', label: 'Less' },
  { value: 'as_expected', label: 'As expected' },
  { value: 'more', label: 'More' },
]

const REPEAT: Choice[] = [
  { value: 'yes', label: 'Yes' },
  { value: 'with_tweaks', label: 'With tweaks' },
  { value: 'no', label: 'No' },
]

const SCORES = [1, 2, 3, 4, 5]

/** A row of full-height buttons. `.chip` is prettier but too small for a one-handed tap. */
function Picker({
  label,
  options,
  value,
  onPick,
}: {
  label: string
  options: Choice[]
  value: string | null
  onPick: (next: string | null) => void
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <div className="row wrap" style={{ gap: 6 }}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={value === option.value ? 'primary grow' : 'grow'}
            style={{ padding: '0 10px' }}
            aria-pressed={value === option.value}
            // Tapping the chosen one again clears it, so a mis-tap is not permanent.
            onClick={() => onPick(value === option.value ? null : option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function Score({
  label,
  value,
  onPick,
}: {
  label: string
  value: number | null
  onPick: (next: number | null) => void
}) {
  return (
    <Picker
      label={label}
      options={SCORES.map((score) => ({ value: String(score), label: String(score) }))}
      value={value === null ? null : String(value)}
      onPick={(next) => onPick(next === null ? null : Number(next))}
    />
  )
}

type MemberEntry = {
  enjoyment: number | null
  portion: string | null
  ate: string | null
  presentation_note: string
}

const EMPTY_ENTRY: MemberEntry = { enjoyment: null, portion: null, ate: null, presentation_note: '' }

export function MealFeedbackForm({
  meal,
  members,
  onSaved,
}: {
  meal: PlanMeal
  members: Member[]
  onSaved?: () => void
}) {
  const [entries, setEntries] = useState<Record<string, MemberEntry>>(() =>
    Object.fromEntries(
      members.map((member) => {
        const saved = meal.feedback.find((row) => row.member_id === member.id)
        return [
          member.id,
          saved
            ? {
                enjoyment: saved.enjoyment,
                portion: saved.portion,
                ate: saved.ate,
                presentation_note: saved.presentation_note ?? '',
              }
            : { ...EMPTY_ENTRY },
        ]
      }),
    ),
  )
  const [review, setReview] = useState(() => ({
    effort: meal.review?.effort ?? null,
    repeat_choice: meal.review?.repeat_choice ?? null,
    notes: meal.review?.notes ?? '',
  }))
  const { run, busy, error } = useAction(onSaved)

  const saveMember = (memberId: string, next: MemberEntry) => {
    setEntries((current) => ({ ...current, [memberId]: next }))
    run(() => api.put(`/api/plan-meals/${meal.id}/feedback`, { member_id: memberId, ...next }))
  }

  const saveReview = (next: typeof review) => {
    setReview(next)
    run(() => api.put(`/api/plan-meals/${meal.id}/review`, next))
  }

  return (
    <div className="stack">
      <Notice>{error}</Notice>

      {members.map((member) => {
        const entry = entries[member.id] ?? EMPTY_ENTRY
        return (
          <div key={member.id} className="card tight">
            <div className="row between">
              <strong>{member.name}</strong>
              {busy && <Spinner />}
            </div>
            <Score
              label="Enjoyment"
              value={entry.enjoyment}
              onPick={(enjoyment) => saveMember(member.id, { ...entry, enjoyment })}
            />
            <Picker
              label="Portion"
              options={PORTIONS}
              value={entry.portion}
              onPick={(portion) => saveMember(member.id, { ...entry, portion })}
            />
            <Picker
              label="Ate it"
              options={ATE}
              value={entry.ate}
              onPick={(ate) => saveMember(member.id, { ...entry, ate })}
            />
            {member.kind === 'child' && (
              <div className="field" style={{ marginBottom: 0 }}>
                <label htmlFor={`note-${meal.id}-${member.id}`}>
                  What worked or failed in presentation
                </label>
                <textarea
                  id={`note-${meal.id}-${member.id}`}
                  value={entry.presentation_note}
                  placeholder="Sauce on the side worked; refused the mixed plate"
                  onChange={(event) =>
                    setEntries((current) => ({
                      ...current,
                      [member.id]: { ...entry, presentation_note: event.target.value },
                    }))
                  }
                  // Free text saves when they look away rather than on every keystroke.
                  onBlur={() => saveMember(member.id, entries[member.id] ?? entry)}
                />
              </div>
            )}
          </div>
        )
      })}

      <div className="card tight">
        <strong>The meal itself</strong>
        <Picker
          label="Effort vs expected"
          options={EFFORT}
          value={review.effort}
          onPick={(effort) => saveReview({ ...review, effort })}
        />
        <Picker
          label="Make it again?"
          options={REPEAT}
          value={review.repeat_choice}
          onPick={(repeat_choice) => saveReview({ ...review, repeat_choice })}
        />
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`review-${meal.id}`}>Notes</label>
          <textarea
            id={`review-${meal.id}`}
            value={review.notes}
            placeholder="Halve the chilli next time"
            onChange={(event) => setReview({ ...review, notes: event.target.value })}
            onBlur={() => saveReview(review)}
          />
        </div>
      </div>
    </div>
  )
}

export function MealFeedbackList({ periodId, members }: { periodId: string; members: Member[] }) {
  const meals = useLoad<PlanMeal[]>(periodId ? `/api/periods/${periodId}/meals` : null)
  const [open, setOpen] = useState<string | null>(null)
  const { run, busy, error } = useAction(() => meals.reload())

  if (meals.loading) return <Spinner label="Loading meals" />

  // Nothing was eaten on a takeout, eating-out or away day, so there is nothing to rate.
  const served = (meals.data ?? []).filter((meal) =>
    ['cook', 'leftovers', 'prep'].includes(meal.mode),
  )

  return (
    <Card>
      <h3>Meals this period</h3>
      <Notice>{meals.error || error}</Notice>
      {!served.length ? (
        <Empty>No meals have been served from an accepted plan yet.</Empty>
      ) : (
        served.map((meal) => {
          const rated = meal.feedback.filter((row) => row.enjoyment !== null).length
          return (
            <div key={meal.id} className="card tight">
              <div className="row between" style={{ alignItems: 'flex-start' }}>
                <button
                  className="ghost grow"
                  style={{ textAlign: 'left', padding: 0, border: 0 }}
                  aria-expanded={open === meal.id}
                  onClick={() => setOpen(open === meal.id ? null : meal.id)}
                >
                  <span className="small muted">{dayLabel(meal.day)} {meal.meal}</span>
                  <br />
                  {meal.title ?? meal.mode}
                </button>
                <div className="row" style={{ gap: 6 }}>
                  {meal.skipped && <Pill kind="warn">Skipped</Pill>}
                  {rated ? (
                    <Pill kind="accent">{rated} rated</Pill>
                  ) : (
                    <Pill>No feedback</Pill>
                  )}
                </div>
              </div>

              <div className="row wrap" style={{ gap: 8, marginTop: 6 }}>
                <button
                  className="small"
                  disabled={busy}
                  onClick={() =>
                    run(() => api.post(`/api/plan-meals/${meal.id}/skip?skipped=${!meal.skipped}`))
                  }
                >
                  {meal.skipped ? 'Not skipped after all' : 'We skipped this'}
                </button>
                <button className="small" onClick={() => setOpen(open === meal.id ? null : meal.id)}>
                  {open === meal.id ? 'Hide' : rated ? 'Edit feedback' : 'Add feedback'}
                </button>
              </div>

              {open === meal.id && (
                <div style={{ marginTop: 10 }}>
                  <MealFeedbackForm
                    meal={meal}
                    members={members}
                    onSaved={() => meals.reload()}
                  />
                </div>
              )}
            </div>
          )
        })
      )}
    </Card>
  )
}

type PeriodFeedbackRow = {
  kind: 'meals' | 'fitness'
  member_id: string | null
  fields: Record<string, unknown>
}

function text(fields: Record<string, unknown> | undefined, key: string): string {
  const value = fields?.[key]
  return typeof value === 'string' ? value : ''
}

function score(fields: Record<string, unknown> | undefined, key: string): number | null {
  const value = fields?.[key]
  return typeof value === 'number' ? value : null
}

export function PeriodFeedback({
  periodId,
  me,
  onSaved,
}: {
  periodId: string
  me: Me
  onSaved?: () => void
}) {
  const saved = useLoad<PeriodFeedbackRow[]>(periodId ? `/api/periods/${periodId}/feedback` : null)

  if (saved.loading) return <Spinner label="Loading feedback" />

  const meals = saved.data?.find((row) => row.kind === 'meals')?.fields
  const fitness = saved.data?.find((row) => row.kind === 'fitness')?.fields

  return (
    <>
      <Card>
        <h3>How the meals went</h3>
        <p className="tiny muted">
          Shared with the household, and the main input to the next meal plan.
        </p>
        <Notice>{saved.error}</Notice>
        <MealsPeriodForm
          periodId={periodId}
          fields={meals}
          onSaved={() => onSaved?.()}
        />
      </Card>

      <Card>
        <div className="row between">
          <h3 style={{ margin: 0 }}>How your training went</h3>
          <Pill kind="accent">Private</Pill>
        </div>
        <p className="tiny muted">
          Only {me.display_name} can see this. It shapes the next training plan.
        </p>
        <FitnessPeriodForm
          periodId={periodId}
          fields={fitness}
          onSaved={() => onSaved?.()}
        />
      </Card>
    </>
  )
}

function MealsPeriodForm({
  periodId,
  fields,
  onSaved,
}: {
  periodId: string
  fields: Record<string, unknown> | undefined
  onSaved: () => void
}) {
  const [overall, setOverall] = useState<number | null>(score(fields, 'overall'))
  const [change, setChange] = useState(text(fields, 'change'))
  const [wasted, setWasted] = useState(text(fields, 'wasted'))
  const [done, setDone] = useState(false)
  const { run, busy, error } = useAction(onSaved)

  const save = () =>
    run(async () => {
      await api.put(`/api/periods/${periodId}/feedback`, {
        kind: 'meals',
        fields: { overall, change, wasted },
      })
      setDone(true)
    })

  return (
    <>
      <Notice>{error}</Notice>
      <Score label="Overall" value={overall} onPick={setOverall} />
      <div className="field">
        <label htmlFor="meals-change">What to change next time</label>
        <textarea
          id="meals-change"
          value={change}
          placeholder="Fewer long cooks midweek"
          onChange={(event) => setChange(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="meals-wasted">What went unused or wasted</label>
        <textarea
          id="meals-wasted"
          value={wasted}
          placeholder="Half the salad greens"
          onChange={(event) => setWasted(event.target.value)}
        />
      </div>
      <button className="primary" disabled={busy} onClick={save}>
        {done ? 'Saved ✓' : 'Save meal feedback'}
      </button>
    </>
  )
}

function FitnessPeriodForm({
  periodId,
  fields,
  onSaved,
}: {
  periodId: string
  fields: Record<string, unknown> | undefined
  onSaved: () => void
}) {
  const [energy, setEnergy] = useState<number | null>(score(fields, 'energy'))
  const [soreness, setSoreness] = useState(text(fields, 'soreness'))
  const [change, setChange] = useState(text(fields, 'change'))
  const [done, setDone] = useState(false)
  const { run, busy, error } = useAction(onSaved)

  const save = () =>
    run(async () => {
      await api.put(`/api/periods/${periodId}/feedback`, {
        kind: 'fitness',
        fields: { energy, soreness, change },
      })
      setDone(true)
    })

  return (
    <>
      <Notice>{error}</Notice>
      <Score label="Energy" value={energy} onPick={setEnergy} />
      <div className="field">
        <label htmlFor="fitness-soreness">Soreness or pain — where, and how bad</label>
        <textarea
          id="fitness-soreness"
          value={soreness}
          placeholder="Left knee, mild, only on the stairs"
          onChange={(event) => setSoreness(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="fitness-change">What to change</label>
        <textarea
          id="fitness-change"
          value={change}
          placeholder="Shorter sessions, more mobility"
          onChange={(event) => setChange(event.target.value)}
        />
      </div>
      <button className="primary" disabled={busy} onClick={save}>
        {done ? 'Saved ✓' : 'Save training feedback'}
      </button>
    </>
  )
}
