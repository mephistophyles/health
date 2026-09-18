// The library is the household's memory: what has been cooked, how it went, and what
// must never be suggested again. Both halves live here because they answer the same
// question from opposite ends.
import { useEffect, useMemo, useState } from 'react'
import type { Ctx } from '../App'
import type { LibraryRecipe, Page, Period, Plans, Recipe } from '../api'
import { api } from '../api'
import {
  Card,
  Empty,
  Field,
  Notice,
  Pill,
  Sheet,
  Spinner,
  VERDICTS,
  dayLabel,
  useAction,
  useLoad,
} from '../components'
import { RecipeDetail } from './Today'

type PerPerson = {
  slug: string
  name: string
  enjoyment: number | null
  ate_yes: number
  entries: number
}

type FullRecipe = {
  id: string
  title: string
  content: Recipe
  tags: string[]
  staple: boolean
  tweak_notes: string
  archived: boolean
  times_served: number
  last_served: string | null
  verdict: string
  verdict_override: string | null
  per_person: PerPerson[]
}

type Rejection = {
  id: string
  kind: 'meals' | 'fitness'
  summary: string
  reason: string
  rejected_by_name: string | null
  created_at: string
  expires_at: string
  cleared: boolean
}

const PAGE_SIZE = 20

export default function Library({ ctx }: { ctx: Ctx }) {
  const [section, setSection] = useState<'recipes' | 'rejected'>('recipes')

  return (
    <>
      <Card>
        <div className="row wrap" style={{ gap: 6 }}>
          <button
            className="chip"
            aria-pressed={section === 'recipes'}
            onClick={() => setSection('recipes')}
          >
            Recipes
          </button>
          <button
            className="chip"
            aria-pressed={section === 'rejected'}
            onClick={() => setSection('rejected')}
          >
            Rejected ideas
          </button>
        </div>
      </Card>

      {section === 'recipes' ? <Recipes ctx={ctx} /> : <Rejections />}
    </>
  )
}

function Recipes({ ctx }: { ctx: Ctx }) {
  const [verdict, setVerdict] = useState('')
  const [tag, setTag] = useState('')
  const [typed, setTyped] = useState('')
  const [search, setSearch] = useState('')
  const [includeArchived, setIncludeArchived] = useState(false)
  const [offset, setOffset] = useState(0)
  const [openId, setOpenId] = useState<string | null>(null)

  // Typing in a search box should not be one request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setSearch(typed.trim()), 300)
    return () => clearTimeout(timer)
  }, [typed])

  useEffect(() => setOffset(0), [verdict, tag, search, includeArchived])

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
  if (verdict) query.set('verdict', verdict)
  if (tag) query.set('tag', tag)
  if (search) query.set('search', search)
  if (includeArchived) query.set('include_archived', 'true')

  const recipes = useLoad<Page<LibraryRecipe>>(`/api/recipes?${query}`)
  const tags = useLoad<{ tag: string; n: number }[]>('/api/library/tags')

  const total = recipes.data?.total ?? 0
  const shown = recipes.data?.items ?? []

  return (
    <>
      <Card>
        <Field label="Search">
          <input
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="Title contains…"
            type="search"
          />
        </Field>

        <label>Verdict</label>
        <div className="row wrap" style={{ gap: 6, marginBottom: 12 }}>
          <button className="chip" aria-pressed={!verdict} onClick={() => setVerdict('')}>
            Any
          </button>
          {Object.entries(VERDICTS).map(([value, meta]) => (
            <button
              key={value}
              className="chip"
              aria-pressed={verdict === value}
              onClick={() => setVerdict(verdict === value ? '' : value)}
            >
              {meta.label}
            </button>
          ))}
        </div>

        {!!tags.data?.length && (
          <>
            <label>Tag</label>
            <div className="row wrap" style={{ gap: 6, marginBottom: 12 }}>
              <button className="chip" aria-pressed={!tag} onClick={() => setTag('')}>
                Any
              </button>
              {tags.data.map((entry) => (
                <button
                  key={entry.tag}
                  className="chip"
                  aria-pressed={tag === entry.tag}
                  onClick={() => setTag(tag === entry.tag ? '' : entry.tag)}
                >
                  {entry.tag} · {entry.n}
                </button>
              ))}
            </div>
          </>
        )}

        <button
          className="chip"
          aria-pressed={includeArchived}
          onClick={() => setIncludeArchived((on) => !on)}
        >
          Include archived
        </button>

        <Notice>{recipes.error}</Notice>
      </Card>

      <Card>
        {recipes.loading && !recipes.data ? (
          <Spinner label="Loading the library" />
        ) : !shown.length ? (
          <Empty>Nothing matches those filters.</Empty>
        ) : (
          <>
            <p className="tiny muted">
              {offset + 1}–{offset + shown.length} of {total}
            </p>
            {shown.map((recipe) => (
              <RecipeRow key={recipe.id} recipe={recipe} onOpen={() => setOpenId(recipe.id)} />
            ))}
            <div className="row between" style={{ marginTop: 12 }}>
              <button
                className="small"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                ← Newer
              </button>
              <button
                className="small"
                disabled={offset + shown.length >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Older →
              </button>
            </div>
          </>
        )}
      </Card>

      {openId && (
        <Detail
          ctx={ctx}
          recipeId={openId}
          onClose={() => setOpenId(null)}
          onChanged={() => recipes.reload()}
        />
      )}
    </>
  )
}

function RecipeRow({ recipe, onOpen }: { recipe: LibraryRecipe; onOpen: () => void }) {
  const verdict = VERDICTS[recipe.verdict] ?? { label: recipe.verdict, kind: '' }
  return (
    <button
      className="tick"
      onClick={onOpen}
      style={{ alignItems: 'flex-start', paddingTop: 10, paddingBottom: 10 }}
    >
      <span className="grow">
        <span className="row between" style={{ gap: 8 }}>
          <strong className="grow">{recipe.title}</strong>
          <Pill kind={verdict.kind}>{verdict.label}</Pill>
        </span>
        <span className="tiny muted" style={{ display: 'block' }}>
          {recipe.average_enjoyment !== null ? `${recipe.average_enjoyment}/5 · ` : ''}
          served {recipe.times_served}×
          {recipe.last_served ? ` · last ${dayLabel(recipe.last_served)}` : ''}
          {recipe.total_min ? ` · ${recipe.total_min} min` : ''}
          {recipe.archived ? ' · archived' : ''}
        </span>
        {!!recipe.tags.length && (
          <span className="tiny muted" style={{ display: 'block' }}>{recipe.tags.join(' · ')}</span>
        )}
      </span>
    </button>
  )
}

function Detail({
  ctx,
  recipeId,
  onClose,
  onChanged,
}: {
  ctx: Ctx
  recipeId: string
  onClose: () => void
  onChanged: () => void
}) {
  const detail = useLoad<FullRecipe>(`/api/recipes/${recipeId}`)
  const [notes, setNotes] = useState<string | null>(null)
  const { run, busy, error } = useAction(async () => {
    await detail.reload()
    onChanged()
  })

  const recipe = detail.data
  const verdict = recipe ? VERDICTS[recipe.verdict] ?? { label: recipe.verdict, kind: '' } : null

  return (
    <Sheet title={recipe?.title ?? 'Recipe'} onClose={onClose}>
      {!recipe ? (
        detail.error ? <Notice>{detail.error}</Notice> : <Spinner label="Loading" />
      ) : (
        <>
          <div className="row wrap" style={{ gap: 8 }}>
            {verdict && <Pill kind={verdict.kind}>{verdict.label}</Pill>}
            {recipe.verdict_override && <Pill>set by hand</Pill>}
            {recipe.staple && <Pill kind="accent">Staple</Pill>}
            {recipe.archived && <Pill kind="warn">Archived</Pill>}
          </div>

          <p className="tiny muted" style={{ marginTop: 8 }}>
            Served {recipe.times_served}×
            {recipe.last_served ? ` · last ${dayLabel(recipe.last_served)}` : ''}
          </p>

          <Notice>{error}</Notice>

          <RecipeDetail recipe={recipe.content} ctx={ctx} />

          {!!recipe.per_person.length && (
            <>
              <h3 style={{ marginTop: 14 }}>How it went, per person</h3>
              <table>
                <thead>
                  <tr>
                    <th>Person</th>
                    <th className="right">Enjoyment</th>
                    <th className="right">Ate it</th>
                  </tr>
                </thead>
                <tbody>
                  {recipe.per_person.map((row) => (
                    <tr key={row.slug}>
                      <td>{row.name}</td>
                      <td className="right">{row.enjoyment ?? '—'}</td>
                      <td className="right">
                        {row.ate_yes}/{row.entries}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h3 style={{ marginTop: 14 }}>Verdict</h3>
          <div className="row wrap" style={{ gap: 6 }}>
            {Object.entries(VERDICTS).map(([value, meta]) => (
              <button
                key={value}
                className="chip"
                aria-pressed={recipe.verdict === value}
                disabled={busy}
                onClick={() => run(() => api.patch(`/api/recipes/${recipeId}`, { verdict_override: value }))}
              >
                {meta.label}
              </button>
            ))}
          </div>
          <p className="tiny muted" style={{ marginTop: 6 }}>
            Setting it by hand overrides the verdict derived from feedback, for good.
          </p>

          <Field
            label="Tweak notes"
            hint="Carried forward: the planner applies these the next time this is planned."
          >
            <textarea
              value={notes ?? recipe.tweak_notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="less spice; serve rice separately"
            />
          </Field>
          <button
            className="small"
            disabled={busy || notes === null || notes === recipe.tweak_notes}
            onClick={() =>
              run(async () => {
                await api.patch(`/api/recipes/${recipeId}`, { tweak_notes: notes })
                setNotes(null)
              })
            }
          >
            Save notes
          </button>

          <PlanThis ctx={ctx} recipeId={recipeId} />

          <div className="row wrap" style={{ gap: 8, marginTop: 16 }}>
            <button
              className="small"
              disabled={busy}
              onClick={() => run(() => api.patch(`/api/recipes/${recipeId}`, { staple: !recipe.staple }))}
            >
              {recipe.staple ? 'Not a staple' : 'Mark as staple'}
            </button>
            <button
              className="small ghost danger"
              disabled={busy}
              onClick={() =>
                run(() => api.patch(`/api/recipes/${recipeId}`, { archived: !recipe.archived }))
              }
            >
              {recipe.archived ? 'Unarchive' : 'Archive'}
            </button>
          </div>
          <p className="tiny muted" style={{ marginTop: 6 }}>
            A staple can repeat inside the repeat window; archiving keeps the history but takes it
            out of the library the planner sees.
          </p>
        </>
      )}
    </Sheet>
  )
}

function PlanThis({ ctx, recipeId }: { ctx: Ctx; recipeId: string }) {
  const [periodId, setPeriodId] = useState(ctx.period?.id ?? '')
  const [day, setDay] = useState('')
  const [meal, setMeal] = useState<'dinner' | 'lunch'>('dinner')
  const [done, setDone] = useState(false)

  const periods = useLoad<Page<Period>>('/api/periods?limit=20')
  const plans = useLoad<Plans>(periodId ? `/api/periods/${periodId}/plans` : null)
  const { run, busy, error } = useAction()

  // A draft is the natural target; falling back to the accepted plan edits this week's plan.
  const plan = plans.data?.meals.draft ?? plans.data?.meals.accepted ?? null
  const days = useMemo(() => plan?.content.days.map((entry) => entry.date) ?? [], [plan])

  useEffect(() => {
    if (days.length && !days.includes(day)) setDay(days[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days])

  return (
    <div style={{ marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
      <h3>Plan this</h3>
      {(periods.data?.items.length ?? 0) > 1 && (
        <Field label="Period">
          <select
            value={periodId}
            onChange={(event) => {
              setPeriodId(event.target.value)
              setDone(false)
            }}
          >
            {periods.data!.items.map((period) => (
              <option key={period.id} value={period.id}>
                {dayLabel(period.start_date)} → {dayLabel(period.end_date)}
              </option>
            ))}
          </select>
        </Field>
      )}

      {plans.loading ? (
        <Spinner label="Looking for a plan" />
      ) : !plan ? (
        <p className="small muted">
          This period has no meal plan to drop it into yet. Generate or start one on the Plan tab.
        </p>
      ) : (
        <>
          <p className="tiny muted">
            Going into the {plan.status === 'draft' ? 'draft' : 'accepted'} plan.
          </p>
          <div className="field-row">
            <Field label="Day">
              <select value={day} onChange={(event) => setDay(event.target.value)}>
                {days.map((date) => (
                  <option key={date} value={date}>{dayLabel(date)}</option>
                ))}
              </select>
            </Field>
            <Field label="Meal">
              <select
                value={meal}
                onChange={(event) => setMeal(event.target.value as 'dinner' | 'lunch')}
              >
                <option value="dinner">Dinner</option>
                <option value="lunch">Lunch</option>
              </select>
            </Field>
          </div>
          <Notice>{error}</Notice>
          <button
            className="primary"
            disabled={busy || !day}
            onClick={() =>
              run(async () => {
                await api.post(`/api/recipes/${recipeId}/plan-this`, {
                  plan_id: plan.id,
                  day,
                  meal,
                })
                setDone(true)
              })
            }
          >
            {busy ? 'Adding…' : done ? 'Added ✓' : 'Add to the plan'}
          </button>
          {done && (
            <p className="tiny muted" style={{ marginTop: 6 }}>
              Added. Recompute the grocery list on the Shop tab so the ingredients appear.
            </p>
          )}
        </>
      )}
    </div>
  )
}

function Rejections() {
  const [kind, setKind] = useState('')
  const rejections = useLoad<Rejection[]>(kind ? `/api/rejections?kind=${kind}` : '/api/rejections')
  const { run, busy, error } = useAction(() => rejections.reload())

  // The endpoint returns cleared rows too; a cleared rejection is no longer in force.
  const live = (rejections.data ?? []).filter((row) => !row.cleared)

  return (
    <Card>
      <h2>Rejected ideas</h2>
      <p className="small muted">
        These go to the planner as "do not suggest" until they expire, reasons included, so it
        learns the pattern rather than avoiding the one dish.
      </p>

      <div className="row wrap" style={{ gap: 6, marginBottom: 12 }}>
        {[
          { value: '', label: 'All' },
          { value: 'meals', label: 'Meals' },
          { value: 'fitness', label: 'Fitness' },
        ].map((option) => (
          <button
            key={option.value || 'all'}
            className="chip"
            aria-pressed={kind === option.value}
            onClick={() => setKind(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      <Notice>{rejections.error || error}</Notice>

      {rejections.loading && !rejections.data ? (
        <Spinner label="Loading" />
      ) : !live.length ? (
        <Empty>Nothing on the do-not-suggest list.</Empty>
      ) : (
        live.map((row) => (
          <div key={row.id} className="card tight">
            <div className="row between" style={{ gap: 8, alignItems: 'flex-start' }}>
              <strong className="grow">{row.summary}</strong>
              <Pill>{row.kind}</Pill>
            </div>
            {row.reason && <p className="small" style={{ margin: '4px 0' }}>{row.reason}</p>}
            <p className="tiny muted" style={{ marginBottom: 6 }}>
              {row.rejected_by_name ?? 'Someone'} · {dayLabel(row.created_at.slice(0, 10))} · expires{' '}
              {dayLabel(row.expires_at)}
            </p>
            <button
              className="small ghost"
              disabled={busy}
              onClick={() => run(() => api.del(`/api/rejections/${row.id}`))}
            >
              Clear
            </button>
          </div>
        ))
      )}
    </Card>
  )
}
