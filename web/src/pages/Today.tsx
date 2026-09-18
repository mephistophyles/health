// The screen the app opens to: what is for dinner, what today's session is, and — from
// Friday noon — what still has to happen before the next period starts.
import { useState } from 'react'
import type { Ctx } from '../App'
import type { Checklist, PlanMeal, Plans, Recipe } from '../api'
import { api } from '../api'
import {
  Card,
  Empty,
  Notice,
  Pill,
  Spinner,
  dayLabel,
  modeLabel,
  todayIso,
  useAction,
  useLoad,
} from '../components'
import { MealFeedbackForm } from './Feedback'

export default function Today({
  ctx,
  checklist,
  reloadChecklist,
}: {
  ctx: Ctx
  checklist: Checklist | null
  reloadChecklist: () => void
}) {
  const today = todayIso()
  const periodId = ctx.period?.id ?? null
  const plans = useLoad<Plans>(periodId ? `/api/periods/${periodId}/plans` : null)
  const meals = useLoad<PlanMeal[]>(periodId ? `/api/periods/${periodId}/meals` : null)
  const suggestions = useLoad<any[]>('/api/profile-suggestions')

  if (!ctx.period) {
    return (
      <Card>
        <h2>No period yet</h2>
        <p className="muted">Set up the first planning period to get started.</p>
        <button className="primary" onClick={() => ctx.goTo('plan')}>Set up a period</button>
      </Card>
    )
  }

  const mealPlan = plans.data?.meals.accepted
  const fitnessPlan = plans.data?.fitness.accepted
  const dinner = meals.data?.find((meal) => meal.day === today && meal.meal === 'dinner')
  const lunch = meals.data?.find((meal) => meal.day === today && meal.meal === 'lunch')
  const session = fitnessPlan?.content.sessions.find((item) => item.date === today)
  const recipe = mealPlan?.content.recipes.find(
    (item) => item.key === mealPlan.content.days.find((day) => day.date === today)?.dinner.recipe_key,
  )

  return (
    <>
      {checklist?.show && (
        <ChecklistCard ctx={ctx} checklist={checklist} onGo={() => ctx.goTo('plan')} />
      )}

      <Card>
        <div className="row between">
          <h2 style={{ margin: 0 }}>{dayLabel(today)}</h2>
          <Pill>{ctx.period.start_date} → {ctx.period.end_date}</Pill>
        </div>
      </Card>

      <Card>
        <h3>Dinner</h3>
        {plans.loading ? (
          <Spinner label="Loading the plan" />
        ) : !mealPlan ? (
          <Empty>No meal plan has been accepted for this period yet.</Empty>
        ) : !dinner ? (
          <Empty>Nothing planned for today.</Empty>
        ) : (
          <DinnerToday
            meal={dinner}
            recipe={recipe}
            ctx={ctx}
            onChanged={() => {
              meals.reload()
              reloadChecklist()
            }}
          />
        )}
        {lunch && lunch.mode !== 'none' && (
          <p className="small muted" style={{ marginTop: 8 }}>
            Lunch: {modeLabel(lunch.mode)}
            {lunch.leftover_of ? ` from ${dayLabel(lunch.leftover_of)}` : ''}
          </p>
        )}
      </Card>

      <Card>
        <h3>Training</h3>
        {!fitnessPlan ? (
          <Empty>No training plan has been accepted for this period yet.</Empty>
        ) : !session ? (
          <Empty>Nothing planned today. {fitnessPlan.content.intent} week.</Empty>
        ) : (
          <SessionToday planId={fitnessPlan.id} session={session} day={today} />
        )}
      </Card>

      {!!suggestions.data?.length && (
        <Card>
          <h3>Suggested profile updates</h3>
          {suggestions.data.map((suggestion) => (
            <SuggestionRow
              key={suggestion.id}
              suggestion={suggestion}
              onDone={() => suggestions.reload()}
            />
          ))}
        </Card>
      )}
    </>
  )
}

function ChecklistCard({
  ctx,
  checklist,
  onGo,
}: {
  ctx: Ctx
  checklist: Checklist
  onGo: () => void
}) {
  return (
    <Card>
      <div className="row between">
        <h2 style={{ margin: 0 }}>This Friday</h2>
        <Pill kind="warn">{checklist.items.filter((item) => !item.done).length} left</Pill>
      </div>
      <ul className="checklist">
        {checklist.items.map((item) => (
          <li key={item.key} className={item.done ? 'done' : ''}>
            <span className="mark" aria-hidden>{item.done ? '✓' : '○'}</span>
            <span className="grow">
              {item.label}
              {item.detail && <span className="tiny muted"> · {item.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
      <button className="primary" onClick={onGo}>Plan the next period</button>
      {ctx.period?.status === 'planning' && (
        <p className="tiny muted" style={{ marginTop: 8 }}>
          Nothing generates on its own — press Generate when the inputs are right.
        </p>
      )}
    </Card>
  )
}

function DinnerToday({
  meal,
  recipe,
  ctx,
  onChanged,
}: {
  meal: PlanMeal
  recipe: Recipe | undefined
  ctx: Ctx
  onChanged: () => void
}) {
  const [rating, setRating] = useState(false)

  if (meal.mode !== 'cook' && meal.mode !== 'leftovers' && meal.mode !== 'prep') {
    return (
      <p>
        <strong>{modeLabel(meal.mode)}</strong>
        <span className="muted"> — nothing to cook.</span>
      </p>
    )
  }

  return (
    <>
      <div className="row between" style={{ alignItems: 'flex-start' }}>
        <div className="grow">
          <strong>{meal.title ?? modeLabel(meal.mode)}</strong>
          {meal.mode === 'leftovers' && meal.leftover_of && (
            <div className="small muted">Leftovers from {dayLabel(meal.leftover_of)}</div>
          )}
          {recipe && (
            <div className="small muted">
              {recipe.active_min} min hands-on · {recipe.total_min} min total
            </div>
          )}
        </div>
        {meal.skipped && <Pill kind="warn">Skipped</Pill>}
      </div>

      {recipe && <RecipeDetail recipe={recipe} ctx={ctx} />}

      <div className="row wrap" style={{ marginTop: 10, gap: 8 }}>
        <button className="primary small" onClick={() => setRating((open) => !open)}>
          {rating ? 'Hide rating' : 'Rate this meal'}
        </button>
      </div>

      {rating && (
        <div style={{ marginTop: 12 }}>
          <MealFeedbackForm meal={meal} members={ctx.members} onSaved={onChanged} />
        </div>
      )}
    </>
  )
}

export function RecipeDetail({ recipe, ctx }: { recipe: Recipe; ctx: Ctx }) {
  const nameFor = (slug: string) =>
    ctx.members.find((member) => member.slug === slug)?.name ?? slug

  return (
    <>
      {recipe.description && <p className="small" style={{ marginTop: 8 }}>{recipe.description}</p>}

      <details>
        <summary className="small">Ingredients ({recipe.ingredients.length})</summary>
        <ul className="small" style={{ margin: 0, paddingLeft: 20 }}>
          {recipe.ingredients.map((ingredient, index) => (
            <li key={index}>
              {ingredient.qty ? `${ingredient.qty} ` : ''}
              {ingredient.unit} {ingredient.name}
            </li>
          ))}
        </ul>
      </details>

      <details>
        <summary className="small">Method ({recipe.steps.length} steps)</summary>
        <ol className="small" style={{ margin: 0, paddingLeft: 20 }}>
          {recipe.steps.map((step, index) => (
            <li key={index}>
              {step.text}
              {step.make_ahead && <Pill>make ahead</Pill>}
            </li>
          ))}
        </ol>
      </details>

      {!!recipe.kid_plates.length && (
        <details>
          <summary className="small">Kid plates</summary>
          {recipe.kid_plates.map((plate) => (
            <p key={plate.member} className="small">
              <strong>{nameFor(plate.member)}:</strong> {plate.serve}
              {plate.fallback && <em className="muted"> Fallback: {plate.fallback}</em>}
            </p>
          ))}
        </details>
      )}

      {!!recipe.portions.length && (
        <details>
          <summary className="small">Portions</summary>
          {recipe.portions.map((portion) => (
            <p key={portion.member} className="small">
              <strong>{nameFor(portion.member)}:</strong> {portion.guidance}
            </p>
          ))}
        </details>
      )}

      {!!recipe.feeds.length && (
        <p className="tiny muted">
          Makes extra: {recipe.feeds.map((feed) => `${dayLabel(feed.date)} ${feed.meal} ×${feed.servings}`).join(', ')}
        </p>
      )}
    </>
  )
}

function SessionToday({
  planId,
  session,
  day,
}: {
  planId: string
  session: Plans['fitness']['accepted'] extends null ? never : any
  day: string
}) {
  const [logged, setLogged] = useState(false)
  const { run, busy, error } = useAction()

  return (
    <>
      <div className="row between">
        <strong>{session.focus}</strong>
        <Pill>{session.duration_min} min</Pill>
      </div>
      <Notice>{error}</Notice>

      {!!session.warmup.length && (
        <p className="small muted">Warm-up: {session.warmup.join(' · ')}</p>
      )}

      <table>
        <thead>
          <tr>
            <th>Exercise</th>
            <th className="right">Prescribed</th>
          </tr>
        </thead>
        <tbody>
          {session.exercises.map((exercise: any) => (
            <tr key={exercise.key}>
              <td>
                {exercise.name}
                {exercise.cues && <div className="tiny muted">{exercise.cues}</div>}
                {!!exercise.alternatives?.length && (
                  <div className="tiny muted">
                    or {exercise.alternatives.map((alt: any) => `${alt.name} (${alt.reason})`).join(', ')}
                  </div>
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

      <div className="row wrap" style={{ marginTop: 10, gap: 8 }}>
        <button
          className="primary"
          disabled={busy || logged}
          onClick={() =>
            run(async () => {
              await api.post('/api/workouts/session-done', { plan_id: planId, log_date: day })
              setLogged(true)
            })
          }
        >
          {logged ? 'Logged ✓' : 'Done as prescribed'}
        </button>
        <button
          disabled={busy || logged}
          onClick={() =>
            run(async () => {
              await api.post('/api/workouts/session-done', {
                plan_id: planId,
                log_date: day,
                status: 'partly',
              })
              setLogged(true)
            })
          }
        >
          Partly
        </button>
        <button
          disabled={busy || logged}
          onClick={() =>
            run(async () => {
              await api.post('/api/workouts/session-done', {
                plan_id: planId,
                log_date: day,
                status: 'skipped',
              })
              setLogged(true)
            })
          }
        >
          Skipped
        </button>
      </div>
    </>
  )
}

export function describe(exercise: any): string {
  if (exercise.distance_mi) return `${exercise.distance_mi} mi${exercise.pace ? ` @ ${exercise.pace}` : ''}`
  if (exercise.duration_s) return `${Math.round(exercise.duration_s / 60)} min`
  const sets = exercise.sets ? `${exercise.sets}×` : ''
  const reps = exercise.reps ?? ''
  const weight = exercise.weight_lb ? ` @ ${exercise.weight_lb} lb` : ''
  return `${sets}${reps}${weight}` || '—'
}

function SuggestionRow({ suggestion, onDone }: { suggestion: any; onDone: () => void }) {
  const { run, busy } = useAction(onDone)
  return (
    <div className="card tight">
      <p style={{ marginBottom: 4 }}>
        <strong>{suggestion.name}:</strong> {suggestion.change}
      </p>
      {suggestion.evidence && <p className="tiny muted">{suggestion.evidence}</p>}
      <div className="row wrap" style={{ gap: 8 }}>
        <button
          className="small primary"
          disabled={busy}
          onClick={() =>
            run(() =>
              api.post(`/api/profile-suggestions/${suggestion.id}`, {
                accept: true,
                apply_to: 'presentation_rules',
              }),
            )
          }
        >
          Add to their rules
        </button>
        <button
          className="small"
          disabled={busy}
          onClick={() =>
            run(() =>
              api.post(`/api/profile-suggestions/${suggestion.id}`, {
                accept: true,
                apply_to: 'notes',
              }),
            )
          }
        >
          Add to notes
        </button>
        <button
          className="small ghost"
          disabled={busy}
          onClick={() => run(() => api.post(`/api/profile-suggestions/${suggestion.id}`, { accept: false }))}
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
