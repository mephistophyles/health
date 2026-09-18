// Entry first, charts second: weight and mood get typed in most days, the graphs get
// looked at now and then (PRD 7, 9).
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Ctx } from '../App'
import type { ChartSeries, Page } from '../api'
import { api } from '../api'
import { BarChart, GroupedLines, LineChart } from '../Chart'
import {
  Card,
  Empty,
  Field,
  Notice,
  Pill,
  Spinner,
  shortDay,
  todayIso,
  useAction,
  useLoad,
} from '../components'

type Metric = { id: string; metric_date: string; kind: string; value: number; note: string }
type Activity = {
  id: string
  activity_date: string
  type: string
  duration_min: number | null
  distance_mi: number | null
  notes: string
}
type Workout = { id: string; log_date: string; name: string; status: string }
type ExerciseChoice = { name: string; sessions: number }

const SCORES = [1, 2, 3, 4, 5]

export default function Progress({ ctx }: { ctx: Ctx }) {
  const metrics = useLoad<Metric[]>('/api/metrics?limit=30')
  const activities = useLoad<Page<Activity>>('/api/activities?limit=10')
  const workouts = useLoad<Page<Workout>>('/api/workouts?limit=10')

  const refreshEntries = () => {
    metrics.reload()
    activities.reload()
    workouts.reload()
  }

  return (
    <>
      <QuickEntry metrics={metrics.data ?? []} onSaved={refreshEntries} />
      <ActivityEntry onSaved={refreshEntries} />
      <RecentEntries
        metrics={metrics.data ?? []}
        activities={activities.data?.items ?? []}
        workouts={workouts.data?.items ?? []}
        loading={metrics.loading || activities.loading || workouts.loading}
        error={metrics.error || activities.error || workouts.error}
        onChanged={refreshEntries}
      />

      <h2 style={{ marginTop: 20 }}>Your charts</h2>
      <p className="small muted">Only you can see these. {ctx.me.display_name} only.</p>
      <WeightCard />
      <MoodCard />
      <AdherenceCard />
      <StrengthCard />
      <RunningCard />

      <h2 style={{ marginTop: 20 }}>Household charts</h2>
      <p className="small muted">Everyone in the house sees these.</p>
      <MealEnjoymentCard />
      <KidAcceptanceCard />
      <CookingCard />
    </>
  )
}

// --- quick entry --------------------------------------------------------------------

function QuickEntry({ metrics, onSaved }: { metrics: Metric[]; onSaved: () => void }) {
  const [day, setDay] = useState(todayIso())
  const [weight, setWeight] = useState('')
  const [mood, setMood] = useState<number | null>(null)
  const [energy, setEnergy] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState('')
  const { run, busy, error } = useAction(onSaved)

  // Re-entering a day should show what is already there rather than a blank form.
  const onDay = (next: string) => {
    setDay(next)
    setSaved('')
    const existing = (kind: string) => metrics.find((m) => m.metric_date === next && m.kind === kind)
    setWeight(existing('weight') ? String(existing('weight')!.value) : '')
    setMood(existing('mood')?.value ?? null)
    setEnergy(existing('energy')?.value ?? null)
    setNote('')
  }

  const save = () => {
    setSaved('')
    return run(async () => {
      const entries: { kind: string; value: number }[] = []
      const pounds = Number(weight)
      if (weight.trim() && Number.isFinite(pounds)) entries.push({ kind: 'weight', value: pounds })
      if (mood != null) entries.push({ kind: 'mood', value: mood })
      if (energy != null) entries.push({ kind: 'energy', value: energy })
      for (const entry of entries) {
        await api.put('/api/metrics', { metric_date: day, ...entry, note })
      }
      setSaved(
        entries.length
          ? `Saved ${entries.length} ${entries.length === 1 ? 'entry' : 'entries'}.`
          : 'Nothing filled in yet.',
      )
      setNote('')
    })
  }

  return (
    <Card>
      <div className="row between">
        <h2 style={{ margin: 0 }}>Today's numbers</h2>
        <Pill>Private</Pill>
      </div>
      <p className="small muted">Weight, mood and energy are yours alone; nobody else sees them.</p>
      <Notice>{error}</Notice>
      {saved && <Notice kind="ok">{saved}</Notice>}

      <div className="field-row">
        <Field label="Day">
          <input type="date" value={day} onChange={(event) => onDay(event.target.value)} />
        </Field>
        <Field label="Weight (lb)">
          <input
            type="number"
            inputMode="decimal"
            step="0.1"
            value={weight}
            placeholder="—"
            onChange={(event) => setWeight(event.target.value)}
          />
        </Field>
      </div>

      <ScoreRow label="Mood" hint="1 low, 5 great" value={mood} onChange={setMood} />
      <ScoreRow label="Energy" hint="1 flat, 5 strong" value={energy} onChange={setEnergy} />

      <Field label="Note (optional)">
        <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Slept badly" />
      </Field>

      <button className="primary" disabled={busy} onClick={save}>
        {busy ? 'Saving…' : 'Save'}
      </button>
    </Card>
  )
}

function ScoreRow({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint: string
  value: number | null
  onChange: (next: number | null) => void
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="row wrap" style={{ gap: 6 }}>
        {SCORES.map((score) => (
          <button
            key={score}
            type="button"
            className="chip"
            style={{ minWidth: 44, minHeight: 44 }}
            aria-pressed={value === score}
            onClick={() => onChange(value === score ? null : score)}
          >
            {score}
          </button>
        ))}
      </div>
    </Field>
  )
}

function ActivityEntry({ onSaved }: { onSaved: () => void }) {
  const [day, setDay] = useState(todayIso())
  const [type, setType] = useState('run')
  const [minutes, setMinutes] = useState('')
  const [miles, setMiles] = useState('')
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)
  const { run, busy, error } = useAction(onSaved)

  return (
    <Card>
      <h3>Log another activity</h3>
      <p className="small muted">
        Anything outside the planned session — a hike, a run, whatever else. Private to you.
      </p>
      <Notice>{error}</Notice>
      {saved && <Notice kind="ok">Logged.</Notice>}

      <div className="field-row">
        <Field label="Day">
          <input type="date" value={day} onChange={(event) => setDay(event.target.value)} />
        </Field>
        <Field label="Type">
          <select value={type} onChange={(event) => setType(event.target.value)}>
            <option value="run">Run</option>
            <option value="hike">Hike</option>
            <option value="other">Other</option>
          </select>
        </Field>
      </div>
      <div className="field-row">
        <Field label="Duration (min)">
          <input
            type="number"
            inputMode="numeric"
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
          />
        </Field>
        <Field label="Distance (mi)" hint="Optional">
          <input
            type="number"
            inputMode="decimal"
            step="0.01"
            value={miles}
            onChange={(event) => setMiles(event.target.value)}
          />
        </Field>
      </div>
      <Field label="Notes (optional)">
        <input value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>

      <button
        className="primary"
        disabled={busy}
        onClick={() =>
          run(async () => {
            await api.post('/api/activities', {
              activity_date: day,
              type,
              duration_min: minutes.trim() ? Number(minutes) : null,
              distance_mi: miles.trim() ? Number(miles) : null,
              notes,
            })
            setSaved(true)
            setMinutes('')
            setMiles('')
            setNotes('')
          })
        }
      >
        {busy ? 'Saving…' : 'Log activity'}
      </button>
    </Card>
  )
}

function RecentEntries({
  metrics,
  activities,
  workouts,
  loading,
  error,
  onChanged,
}: {
  metrics: Metric[]
  activities: Activity[]
  workouts: Workout[]
  loading: boolean
  error: string
  onChanged: () => void
}) {
  const { run, busy, error: deleteError } = useAction(onChanged)

  const rows = useMemo(() => {
    const merged = [
      ...metrics.slice(0, 8).map((metric) => ({
        id: metric.id,
        date: metric.metric_date,
        what: `${metric.kind === 'weight' ? 'Weight' : metric.kind === 'mood' ? 'Mood' : 'Energy'} ${metric.value}${metric.kind === 'weight' ? ' lb' : ''}`,
        deletable: false,
      })),
      ...activities.map((activity) => ({
        id: activity.id,
        date: activity.activity_date,
        what: [
          activity.type,
          activity.distance_mi ? `${activity.distance_mi} mi` : '',
          activity.duration_min ? `${activity.duration_min} min` : '',
        ]
          .filter(Boolean)
          .join(' · '),
        deletable: false,
      })),
      ...workouts.map((workout) => ({
        id: workout.id,
        date: workout.log_date,
        what: `${workout.name}${workout.status === 'completed' ? '' : ` (${workout.status})`}`,
        deletable: true,
      })),
    ]
    return merged.sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12)
  }, [metrics, activities, workouts])

  return (
    <Card>
      <h3>Last few entries</h3>
      <Notice>{error || deleteError}</Notice>
      {loading ? (
        <Spinner label="Loading" />
      ) : !rows.length ? (
        <Empty>Nothing logged yet.</Empty>
      ) : (
        <table>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="small muted" style={{ whiteSpace: 'nowrap' }}>{shortDay(row.date)}</td>
                <td>{row.what}</td>
                <td className="right">
                  {row.deletable && (
                    <button
                      className="small ghost danger"
                      disabled={busy}
                      onClick={() => run(() => api.del(`/api/workouts/${row.id}`))}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

// --- charts -------------------------------------------------------------------------

function ChartCard({
  title,
  explain,
  state,
  children,
}: {
  title: string
  explain: string
  state: { loading: boolean; error: string }
  children: ReactNode
}) {
  return (
    <Card>
      <h3>{title}</h3>
      <p className="small muted">{explain}</p>
      <Notice>{state.error}</Notice>
      {state.loading ? <Spinner label="Loading" /> : children}
    </Card>
  )
}

/** Goals are free text ("175 lb", "under 170"), so a goal line only appears when a number falls out. */
function goalNumber(goal: unknown): number | null {
  if (typeof goal === 'number') return goal
  if (typeof goal !== 'string') return null
  const match = goal.match(/-?\d+(\.\d+)?/)
  return match ? Number(match[0]) : null
}

function WeightCard() {
  const chart = useLoad<ChartSeries & { goal: string | null }>('/api/charts/weight')
  return (
    <ChartCard
      title="Body weight"
      explain="Every weigh-in, with a 7-day average through the noise and your goal as a dashed line."
      state={chart}
    >
      {chart.data && (
        <LineChart
          series={chart.data}
          lines={[
            { key: 'value', label: 'Weight' },
            { key: 'rolling_7', label: '7-day average', secondary: true },
          ]}
          goal={goalNumber(chart.data.goal)}
          unit="lb"
        />
      )}
    </ChartCard>
  )
}

function MoodCard() {
  const chart = useLoad<ChartSeries>('/api/charts/mood')
  // The endpoint tags each point with `kind`; GroupedLines splits on `name`, so give it one.
  const named = useMemo(() => {
    if (!chart.data) return null
    return {
      ...chart.data,
      points: chart.data.points.map((point: any) => ({
        ...point,
        name: point.kind === 'mood' ? 'Mood' : 'Energy',
      })),
    }
  }, [chart.data])

  return (
    <ChartCard
      title="Mood and energy"
      explain="Weekly averages of your 1–5 entries, one line each."
      state={chart}
    >
      {named && <GroupedLines series={named} valueKey="value" label="Mood and energy" />}
    </ChartCard>
  )
}

function AdherenceCard() {
  const chart = useLoad<ChartSeries>('/api/charts/adherence')
  return (
    <ChartCard
      title="Adherence"
      explain="Sessions the plan asked for against sessions you actually logged, per period."
      state={chart}
    >
      {chart.data && (
        <BarChart
          series={chart.data}
          bars={[
            { key: 'planned', label: 'Planned', ghost: true },
            { key: 'completed', label: 'Completed' },
          ]}
        />
      )}
    </ChartCard>
  )
}

function StrengthCard() {
  const choices = useLoad<ExerciseChoice[]>('/api/charts/strength/exercises')
  const [exercise, setExercise] = useState('')
  const picked = exercise || choices.data?.[0]?.name || ''
  const chart = useLoad<ChartSeries>(
    picked ? `/api/charts/strength?exercise=${encodeURIComponent(picked)}` : null,
  )

  return (
    <ChartCard
      title="Strength"
      explain="For one exercise: your top set, and an estimated one-rep max from the reps you did."
      state={{ loading: choices.loading || chart.loading, error: choices.error || chart.error }}
    >
      {!choices.data?.length ? (
        <Empty>No exercise has been logged twice yet.</Empty>
      ) : (
        <>
          <Field label="Exercise">
            <select value={picked} onChange={(event) => setExercise(event.target.value)}>
              {choices.data.map((choice) => (
                <option key={choice.name} value={choice.name}>
                  {choice.name} ({choice.sessions})
                </option>
              ))}
            </select>
          </Field>
          {chart.data && (
            <LineChart
              series={chart.data}
              lines={[
                { key: 'estimated_1rm', label: 'Est. 1RM' },
                { key: 'top_weight', label: 'Top set', secondary: true },
              ]}
              unit="lb"
            />
          )}
        </>
      )}
    </ChartCard>
  )
}

type Run = {
  date: string
  distance_mi: number
  duration_min: number
  pace_min_per_mi: number | null
}

function RunningCard() {
  const chart = useLoad<ChartSeries & { runs: Run[] }>('/api/charts/running')
  const runs = chart.data?.runs ?? []

  return (
    <ChartCard
      title="Running"
      explain="Miles per week, and the pace of each individual run underneath."
      state={chart}
    >
      {chart.data && <BarChart series={chart.data} bars={[{ key: 'distance_mi', label: 'Miles' }]} />}
      {!runs.length ? (
        <Empty>No runs logged yet.</Empty>
      ) : (
        <table style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Run</th>
              <th className="right">Distance</th>
              <th className="right">Pace</th>
            </tr>
          </thead>
          <tbody>
            {runs
              .slice()
              .reverse()
              .slice(0, 10)
              .map((run) => (
                <tr key={`${run.date}-${run.distance_mi}`}>
                  <td>{shortDay(run.date)}</td>
                  <td className="right">{run.distance_mi} mi</td>
                  <td className="right">{run.pace_min_per_mi ? `${paceLabel(run.pace_min_per_mi)}/mi` : '—'}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </ChartCard>
  )
}

/** The API returns decimal minutes; runners read 8:30, not 8.5. */
function paceLabel(minutes: number): string {
  const whole = Math.floor(minutes)
  const seconds = Math.round((minutes - whole) * 60)
  return `${whole}:${String(seconds).padStart(2, '0')}`
}

function MealEnjoymentCard() {
  const chart = useLoad<ChartSeries>('/api/charts/meal-enjoyment')
  return (
    <ChartCard
      title="Meal enjoyment"
      explain="Average 1–5 rating per period, one line per person. Shared with the household."
      state={chart}
    >
      {chart.data && <GroupedLines series={chart.data} valueKey="value" label="Meal enjoyment" />}
    </ChartCard>
  )
}

function KidAcceptanceCard() {
  const chart = useLoad<ChartSeries>('/api/charts/kid-acceptance')
  return (
    <ChartCard
      title="Kid acceptance"
      explain="How often each child ate what was served, as a percentage per period. Shared."
      state={chart}
    >
      {chart.data && <GroupedLines series={chart.data} valueKey="ate_rate" label="Kid acceptance" />}
    </ChartCard>
  )
}

function CookingCard() {
  const chart = useLoad<ChartSeries>('/api/charts/cooking')
  return (
    <ChartCard
      title="Cooking: plan versus reality"
      explain="Dinners planned as a cook night against the ones that actually got cooked. Shared."
      state={chart}
    >
      {chart.data && (
        <BarChart
          series={chart.data}
          bars={[
            { key: 'planned', label: 'Planned cook nights', ghost: true },
            { key: 'cooked', label: 'Actually cooked' },
          ]}
        />
      )}
    </ChartCard>
  )
}
