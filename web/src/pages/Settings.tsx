// Everything the planner reads but never decides for itself: who is in the house, what
// they are working towards, how the shop is walked, and which model writes the plan
// (PRD 2, 10). Long page, so every section is a closed <details> until it is wanted.
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Ctx } from '../App'
import type { Member, Page, Period } from '../api'
import { api } from '../api'
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
  Spinner,
  useAction,
  useLoad,
} from '../components'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

// Starting points, not a closed list: presentation rules are whatever the child needs.
const RULE_TOGGLES = [
  'nothing touching',
  'sauce on the side',
  'cut into strips',
  'served whole',
  'no visible green flecks',
  'plain portion set aside before saucing',
  'dip on the side',
  'served cool, not hot',
]

// A shortlist for the fingers; the field stays free text because comparing models is the point.
const COMMON_MODELS = [
  'anthropic/claude-sonnet-4.5',
  'openai/gpt-5',
  'google/gemini-2.5-pro',
  'deepseek/deepseek-chat',
]

export default function SettingsPage({ ctx }: { ctx: Ctx }) {
  return (
    <>
      <Section title="Household members" defaultOpen>
        <MembersSection members={ctx.members} onSaved={ctx.reloadMembers} />
      </Section>

      <Section title="Goals">
        <GoalsSection ctx={ctx} />
      </Section>

      <Section title="My equipment">
        <EquipmentSection memberId={ctx.me.member_id} />
      </Section>

      <Section title="Planning, staples and categories">
        <PlanningSection />
      </Section>

      <Section title="Seasonal produce">
        <ProduceSection />
      </Section>

      {ctx.me.is_admin && (
        <>
          <Section title="Models and prompts">
            <TasksSection ctx={ctx} />
          </Section>
          <Section title="Model call log">
            <CallLogSection />
          </Section>
        </>
      )}
    </>
  )
}

function Section({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  return (
    <Card>
      <details open={defaultOpen}>
        <summary>
          <strong>{title}</strong>
        </summary>
        {children}
      </details>
    </Card>
  )
}

function listToText(items: string[] | null | undefined) {
  return (items ?? []).join(', ')
}

function textToList(text: string) {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

// --- 1. members ---------------------------------------------------------------------

function MembersSection({ members, onSaved }: { members: Member[]; onSaved: () => void }) {
  return (
    <>
      <p className="small muted">
        Exclusions are absolute — the planner will never put them in a meal. Dislikes are strong
        preferences it avoids but may still use when something else makes the dish worth it.
      </p>
      {members.map((member) => (
        <MemberForm key={member.id} member={member} onSaved={onSaved} />
      ))}
    </>
  )
}

function MemberForm({ member, onSaved }: { member: Member; onSaved: () => void }) {
  const [name, setName] = useState(member.name)
  const [birthMonth, setBirthMonth] = useState(member.birth_month ?? '')
  const [notes, setNotes] = useState(member.notes)
  const [likes, setLikes] = useState(listToText(member.likes))
  const [dislikes, setDislikes] = useState(listToText(member.dislikes))
  const [exclusions, setExclusions] = useState(listToText(member.exclusions))
  const [toggles, setToggles] = useState<string[]>(member.presentation_rules?.toggles ?? [])
  const [ruleNotes, setRuleNotes] = useState(member.presentation_rules?.notes ?? '')
  const [extraRule, setExtraRule] = useState('')
  const [saved, setSaved] = useState(false)
  // A renamed member or a changed plate rule shows up all over the app, so refresh the
  // shared list rather than leaving the rest of the session looking at stale profiles.
  const { run, busy, error } = useAction(onSaved)

  const toggle = (value: string) =>
    setToggles((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )

  return (
    <details className="card tight" style={{ marginBottom: 10 }}>
      <summary>
        {member.name} <Pill>{member.kind}</Pill>
      </summary>
      <Notice>{error}</Notice>
      {saved && <Notice kind="ok">Saved.</Notice>}

      <div className="field-row">
        <Field label="Name">
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Birth month" hint="Used for age-appropriate portions">
          <input
            type="date"
            value={birthMonth}
            onChange={(event) => setBirthMonth(event.target.value)}
          />
        </Field>
      </div>

      <Field label="Notes" hint="Anything the planner should keep in mind">
        <textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
      <Field label="Likes" hint="Comma separated">
        <input value={likes} onChange={(event) => setLikes(event.target.value)} />
      </Field>
      <Field label="Dislikes" hint="Comma separated — strong preferences, avoided where possible">
        <input value={dislikes} onChange={(event) => setDislikes(event.target.value)} />
      </Field>
      <Field label="Exclusions" hint="Comma separated — never used, no exceptions">
        <input value={exclusions} onChange={(event) => setExclusions(event.target.value)} />
      </Field>

      {member.kind === 'child' && (
        <>
          <Field label="Presentation rules" hint="How food has to arrive on the plate">
            <Chips
              options={Array.from(new Set([...RULE_TOGGLES, ...toggles]))}
              selected={toggles}
              onToggle={toggle}
            />
          </Field>
          <div className="row" style={{ marginBottom: 12, gap: 8 }}>
            <input
              className="grow"
              value={extraRule}
              placeholder="Add another rule"
              onChange={(event) => setExtraRule(event.target.value)}
            />
            <button
              type="button"
              onClick={() => {
                const rule = extraRule.trim()
                if (rule && !toggles.includes(rule)) setToggles([...toggles, rule])
                setExtraRule('')
              }}
            >
              Add
            </button>
          </div>
          <Field label="Presentation notes">
            <textarea value={ruleNotes} onChange={(event) => setRuleNotes(event.target.value)} />
          </Field>
        </>
      )}

      <button
        className="primary"
        disabled={busy}
        onClick={() =>
          run(async () => {
            await api.patch(`/api/members/${member.id}`, {
              name,
              birth_month: birthMonth || null,
              notes,
              likes: textToList(likes),
              dislikes: textToList(dislikes),
              exclusions: textToList(exclusions),
              presentation_rules: { toggles, notes: ruleNotes },
            })
            setSaved(true)
          })
        }
      >
        {busy ? 'Saving…' : 'Save profile'}
      </button>
    </details>
  )
}

// --- 2. goals -----------------------------------------------------------------------

type Goal = {
  id: string
  kind: 'meal' | 'fitness'
  statement: string
  target: string | null
  target_date: string | null
  constraints: string
  active_from: string
  active_to: string | null
}

function GoalsSection({ ctx }: { ctx: Ctx }) {
  return (
    <>
      <p className="small muted">
        Saving a goal opens a new record and closes the old one — history is kept, so the planner
        can see what changed and when. Meal goals are shared; a fitness goal can only be set by the
        person it belongs to.
      </p>
      {ctx.members.map((member) => (
        <MemberGoals key={member.id} member={member} isMe={member.id === ctx.me.member_id} />
      ))}
    </>
  )
}

function MemberGoals({ member, isMe }: { member: Member; isMe: boolean }) {
  const goals = useLoad<Goal[]>(`/api/members/${member.id}/goals`)
  const active = (goals.data ?? []).filter((goal) => !goal.active_to)
  const past = (goals.data ?? []).filter((goal) => goal.active_to)

  return (
    <details className="card tight" style={{ marginBottom: 10 }}>
      <summary>{member.name}</summary>
      <Notice>{goals.error}</Notice>
      {goals.loading ? (
        <Spinner label="Loading goals" />
      ) : (
        <>
          {!active.length ? (
            <Empty>No goal set.</Empty>
          ) : (
            active.map((goal) => (
              <p key={goal.id} className="small">
                <Pill kind="accent">{goal.kind}</Pill> {goal.statement}
                {goal.target && <span className="muted"> · target {goal.target}</span>}
                {goal.target_date && <span className="muted"> by {goal.target_date}</span>}
                {goal.constraints && <div className="tiny muted">{goal.constraints}</div>}
              </p>
            ))
          )}

          {!!past.length && (
            <details>
              <summary className="small">History ({past.length})</summary>
              <table>
                <tbody>
                  {past.map((goal) => (
                    <tr key={goal.id}>
                      <td className="tiny muted" style={{ whiteSpace: 'nowrap' }}>
                        {goal.active_from} → {goal.active_to}
                      </td>
                      <td className="small">
                        {goal.kind}: {goal.statement}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          <NewGoalForm memberId={member.id} allowFitness={isMe} onSaved={goals.reload} />
        </>
      )}
    </details>
  )
}

function NewGoalForm({
  memberId,
  allowFitness,
  onSaved,
}: {
  memberId: string
  allowFitness: boolean
  onSaved: () => void
}) {
  const [kind, setKind] = useState<'meal' | 'fitness'>('meal')
  const [statement, setStatement] = useState('')
  const [target, setTarget] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const [constraints, setConstraints] = useState('')
  const { run, busy, error } = useAction(onSaved)

  return (
    <details>
      <summary className="small">Add a goal</summary>
      <Notice>{error}</Notice>
      {allowFitness ? (
        <Field label="Kind">
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as 'meal' | 'fitness')}
          >
            <option value="meal">Meal</option>
            <option value="fitness">Fitness</option>
          </select>
        </Field>
      ) : (
        <p className="tiny muted">Meal goal. Fitness goals are set by their owner only.</p>
      )}
      <Field label="Statement">
        <input
          value={statement}
          onChange={(event) => setStatement(event.target.value)}
          placeholder="Eat more protein at lunch"
        />
      </Field>
      <div className="field-row">
        <Field label="Target" hint="Optional, free text">
          <input value={target} onChange={(event) => setTarget(event.target.value)} placeholder="175 lb" />
        </Field>
        <Field label="Target date" hint="Optional">
          <input
            type="date"
            value={targetDate}
            onChange={(event) => setTargetDate(event.target.value)}
          />
        </Field>
      </div>
      <Field label="Constraints" hint="Injuries, medication, anything limiting">
        <textarea value={constraints} onChange={(event) => setConstraints(event.target.value)} />
      </Field>
      <button
        className="primary"
        disabled={busy || !statement.trim()}
        onClick={() =>
          run(async () => {
            await api.post(`/api/members/${memberId}/goals`, {
              kind: allowFitness ? kind : 'meal',
              statement,
              target: target || null,
              target_date: targetDate || null,
              constraints,
            })
            setStatement('')
            setTarget('')
            setTargetDate('')
            setConstraints('')
          })
        }
      >
        {busy ? 'Saving…' : 'Save as new goal'}
      </button>
    </details>
  )
}

// --- 3. equipment -------------------------------------------------------------------

type EquipmentProfile = { name: string; items: string[]; notes: string }

function EquipmentSection({ memberId }: { memberId: string }) {
  const profiles = useLoad<EquipmentProfile[]>(`/api/members/${memberId}/equipment`)
  const find = (name: string) => profiles.data?.find((profile) => profile.name === name)

  return (
    <>
      <p className="small muted">
        Two kits, and the travel flag on a planning period decides which one the planner is handed.
        Only you can see or change these.
      </p>
      <Notice>{profiles.error}</Notice>
      {profiles.loading ? (
        <Spinner label="Loading equipment" />
      ) : (
        <>
          <EquipmentForm
            memberId={memberId}
            name="home"
            label="At home"
            profile={find('home')}
            onSaved={profiles.reload}
          />
          <EquipmentForm
            memberId={memberId}
            name="travel"
            label="Travelling"
            profile={find('travel')}
            onSaved={profiles.reload}
          />
        </>
      )}
    </>
  )
}

function EquipmentForm({
  memberId,
  name,
  label,
  profile,
  onSaved,
}: {
  memberId: string
  name: string
  label: string
  profile: EquipmentProfile | undefined
  onSaved: () => void
}) {
  const [items, setItems] = useState(listToText(profile?.items))
  const [notes, setNotes] = useState(profile?.notes ?? '')
  const { run, busy, error } = useAction(onSaved)

  return (
    <div className="card tight">
      <h3>{label}</h3>
      <Notice>{error}</Notice>
      <Field label="Equipment" hint="Comma separated">
        <textarea value={items} onChange={(event) => setItems(event.target.value)} />
      </Field>
      <Field label="Notes">
        <input value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
      <button
        className="primary"
        disabled={busy}
        onClick={() =>
          run(() =>
            api.put(`/api/members/${memberId}/equipment/${name}`, {
              items: textToList(items),
              notes,
            }),
          )
        }
      >
        {busy ? 'Saving…' : `Save ${label.toLowerCase()}`}
      </button>
    </div>
  )
}

// --- 4. planning defaults, staples, categories --------------------------------------

type SettingsMap = Record<string, any>

function PlanningSection() {
  const settings = useLoad<SettingsMap>('/api/settings')
  if (settings.loading) return <Spinner label="Loading settings" />
  if (settings.error) return <Notice>{settings.error}</Notice>
  if (!settings.data) return <Empty>No settings found.</Empty>

  return (
    <>
      <PlanningDefaults value={settings.data.planning_defaults ?? {}} onSaved={settings.reload} />
      <StaplesEditor value={settings.data.staples ?? []} onSaved={settings.reload} />
      <CategoryOrder value={settings.data.grocery_categories ?? []} onSaved={settings.reload} />
      <SpendCap value={settings.data.monthly_spend_cap_usd ?? 20} onSaved={settings.reload} />
      <CookTimeLimits value={settings.data.cook_time_limits ?? {}} onSaved={settings.reload} />
    </>
  )
}

function useSetting(key: string, onSaved: () => void) {
  const { run, busy, error } = useAction(onSaved)
  return { save: (value: unknown) => run(() => api.put(`/api/settings/${key}`, { value })), busy, error }
}

function PlanningDefaults({ value, onSaved }: { value: SettingsMap; onSaved: () => void }) {
  const [draft, setDraft] = useState<SettingsMap>(value)
  const { save, busy, error } = useSetting('planning_defaults', onSaved)
  const set = (key: string, next: unknown) => setDraft({ ...draft, [key]: next })

  return (
    <div className="card tight">
      <h3>Planning defaults</h3>
      <p className="tiny muted">What a new period starts out as before anyone edits it.</p>
      <Notice>{error}</Notice>
      <div className="field-row">
        <Field label="Period length (days)">
          <input
            type="number"
            inputMode="numeric"
            value={draft.period_length_days ?? 7}
            onChange={(event) => set('period_length_days', Number(event.target.value))}
          />
        </Field>
        <Field label="Repeat window (weeks)" hint="A dinner is not repeated inside this">
          <input
            type="number"
            inputMode="numeric"
            value={draft.repeat_weeks ?? 3}
            onChange={(event) => set('repeat_weeks', Number(event.target.value))}
          />
        </Field>
        <Field label="Rejection window (weeks)" hint="A rejected idea stays out this long">
          <input
            type="number"
            inputMode="numeric"
            value={draft.rejection_weeks ?? 12}
            onChange={(event) => set('rejection_weeks', Number(event.target.value))}
          />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Default dinner mode">
          <select
            value={draft.default_dinner_mode ?? 'cook'}
            onChange={(event) => set('default_dinner_mode', event.target.value)}
          >
            {DINNER_MODES.map((mode) => (
              <option key={mode.value} value={mode.value}>{mode.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Default lunch mode">
          <select
            value={draft.default_lunch_mode ?? 'none'}
            onChange={(event) => set('default_lunch_mode', event.target.value)}
          >
            {LUNCH_MODES.map((mode) => (
              <option key={mode.value} value={mode.value}>{mode.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Default cook time">
          <select
            value={draft.default_cook_time ?? 'normal'}
            onChange={(event) => set('default_cook_time', event.target.value)}
          >
            {COOK_TIMES.map((time) => (
              <option key={time.value} value={time.value}>{time.label}</option>
            ))}
          </select>
        </Field>
      </div>
      <button className="primary" disabled={busy} onClick={() => save(draft)}>
        {busy ? 'Saving…' : 'Save defaults'}
      </button>
    </div>
  )
}

function StaplesEditor({ value, onSaved }: { value: string[]; onSaved: () => void }) {
  const [text, setText] = useState(listToText(value))
  const { save, busy, error } = useSetting('staples', onSaved)

  return (
    <div className="card tight">
      <h3>Staples</h3>
      <p className="tiny muted">
        An ingredient on this list lands on the grocery list as "check pantry" rather than "buy".
      </p>
      <Notice>{error}</Notice>
      <Field label="Staples" hint="Comma separated">
        <textarea value={text} onChange={(event) => setText(event.target.value)} />
      </Field>
      <button className="primary" disabled={busy} onClick={() => save(textToList(text))}>
        {busy ? 'Saving…' : 'Save staples'}
      </button>
    </div>
  )
}

function CategoryOrder({ value, onSaved }: { value: string[]; onSaved: () => void }) {
  const [order, setOrder] = useState<string[]>(value)
  const [added, setAdded] = useState('')
  const { save, busy, error } = useSetting('grocery_categories', onSaved)

  const move = (index: number, by: number) => {
    const next = [...order]
    const target = index + by
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setOrder(next)
  }

  return (
    <div className="card tight">
      <h3>Grocery categories</h3>
      <p className="tiny muted">
        This is the order the shop is walked, so put them in the order you actually reach them.
      </p>
      <Notice>{error}</Notice>
      <table>
        <tbody>
          {order.map((category, index) => (
            <tr key={category}>
              <td>{category}</td>
              <td className="right" style={{ whiteSpace: 'nowrap' }}>
                <button
                  className="small ghost"
                  aria-label={`Move ${category} up`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>{' '}
                <button
                  className="small ghost"
                  aria-label={`Move ${category} down`}
                  disabled={index === order.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>{' '}
                <button
                  className="small ghost danger"
                  aria-label={`Remove ${category}`}
                  onClick={() => setOrder(order.filter((item) => item !== category))}
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ margin: '10px 0', gap: 8 }}>
        <input
          className="grow"
          value={added}
          placeholder="Add a category"
          onChange={(event) => setAdded(event.target.value)}
        />
        <button
          onClick={() => {
            const name = added.trim()
            if (name && !order.includes(name)) setOrder([...order, name])
            setAdded('')
          }}
        >
          Add
        </button>
      </div>
      <button className="primary" disabled={busy} onClick={() => save(order)}>
        {busy ? 'Saving…' : 'Save order'}
      </button>
    </div>
  )
}

function SpendCap({ value, onSaved }: { value: number; onSaved: () => void }) {
  const [cap, setCap] = useState(String(value))
  const { save, busy, error } = useSetting('monthly_spend_cap_usd', onSaved)

  return (
    <div className="card tight">
      <h3>Monthly model spend cap</h3>
      <p className="tiny muted">Generation stops once the month's spend reaches this.</p>
      <Notice>{error}</Notice>
      <Field label="Cap (USD)">
        <input
          type="number"
          inputMode="decimal"
          value={cap}
          onChange={(event) => setCap(event.target.value)}
        />
      </Field>
      <button className="primary" disabled={busy} onClick={() => save(Number(cap))}>
        {busy ? 'Saving…' : 'Save cap'}
      </button>
    </div>
  )
}

function CookTimeLimits({ value, onSaved }: { value: SettingsMap; onSaved: () => void }) {
  const [draft, setDraft] = useState<SettingsMap>(value)
  const { save, busy, error } = useSetting('cook_time_limits', onSaved)

  return (
    <div className="card tight">
      <h3>Cook time limits</h3>
      <p className="tiny muted">
        Total minutes a recipe may take under each setting. Leave a box empty for no limit.
      </p>
      <Notice>{error}</Notice>
      <div className="field-row">
        {COOK_TIMES.map((time) => (
          <Field key={time.value} label={time.label}>
            <input
              type="number"
              inputMode="numeric"
              value={draft[time.value] ?? ''}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  [time.value]: event.target.value === '' ? null : Number(event.target.value),
                })
              }
            />
          </Field>
        ))}
      </div>
      <button className="primary" disabled={busy} onClick={() => save(draft)}>
        {busy ? 'Saving…' : 'Save limits'}
      </button>
    </div>
  )
}

// --- 5. seasonal produce -------------------------------------------------------------

type ProduceRow = { month: number; items: string[] }

function ProduceSection() {
  const produce = useLoad<ProduceRow[]>('/api/seasonal-produce')

  return (
    <>
      <p className="small muted">
        What is worth buying locally, month by month. The meal planner leans on the current month.
      </p>
      <Notice>{produce.error}</Notice>
      {produce.loading ? (
        <Spinner label="Loading produce" />
      ) : (
        MONTHS.map((name, index) => (
          <ProduceRowForm
            key={name}
            month={index + 1}
            name={name}
            items={produce.data?.find((row) => row.month === index + 1)?.items ?? []}
          />
        ))
      )}
    </>
  )
}

function ProduceRowForm({ month, name, items }: { month: number; name: string; items: string[] }) {
  const [text, setText] = useState(listToText(items))
  const [saved, setSaved] = useState(false)
  const { run, busy, error } = useAction()

  return (
    <div style={{ marginBottom: 10 }}>
      <Notice>{error}</Notice>
      <Field label={name}>
        <div className="row" style={{ gap: 8 }}>
          <input
            className="grow"
            value={text}
            onChange={(event) => {
              setText(event.target.value)
              setSaved(false)
            }}
          />
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api.put(`/api/seasonal-produce/${month}`, { items: textToList(text) })
                setSaved(true)
              })
            }
          >
            {saved ? '✓' : 'Save'}
          </button>
        </div>
      </Field>
    </div>
  )
}

// --- 6. models and prompts (admin) ---------------------------------------------------

type TaskConfig = {
  task: string
  model: string
  fallback_model: string | null
  temperature: number
  max_tokens: number
  timeout_s: number
  active_prompt_version_id: string | null
  prompt_version: number | null
  system_prompt: string | null
  user_template: string | null
}

type TasksResponse = {
  tasks: TaskConfig[]
  known_tasks: string[]
  spend: { month_to_date: number; cap: number }
}

function TasksSection({ ctx }: { ctx: Ctx }) {
  const tasks = useLoad<TasksResponse>('/api/llm-tasks')
  const periods = useLoad<Page<Period>>('/api/periods?limit=20')
  const spend = tasks.data?.spend
  const spent = Number(spend?.month_to_date ?? 0)
  const cap = Number(spend?.cap ?? 0)
  const blocked = cap > 0 && spent >= cap

  return (
    <>
      <Notice>{tasks.error}</Notice>
      {spend && (
        <Notice kind={blocked ? 'error' : spent > cap * 0.8 ? 'warn' : 'ok'}>
          ${spent.toFixed(2)} of ${cap.toFixed(2)} spent this month.
          {blocked ? ' Generation is blocked until the cap is raised or the month rolls over.' : ''}
        </Notice>
      )}
      {tasks.loading ? (
        <Spinner label="Loading tasks" />
      ) : (
        (tasks.data?.tasks ?? []).map((task) => (
          <TaskCard
            key={task.task}
            task={task}
            periods={periods.data?.items ?? []}
            currentPeriod={ctx.period}
            onSaved={tasks.reload}
          />
        ))
      )}
      {!!tasks.data && tasks.data.known_tasks.length > tasks.data.tasks.length && (
        <p className="tiny muted">
          Not yet configured: {tasks.data.known_tasks
            .filter((known) => !tasks.data!.tasks.some((task) => task.task === known))
            .join(', ')}
        </p>
      )}
    </>
  )
}

function TaskCard({
  task,
  periods,
  currentPeriod,
  onSaved,
}: {
  task: TaskConfig
  periods: Period[]
  currentPeriod: Period | null
  onSaved: () => void
}) {
  const [model, setModel] = useState(task.model)
  const [fallback, setFallback] = useState(task.fallback_model ?? '')
  const [temperature, setTemperature] = useState(String(task.temperature))
  const [maxTokens, setMaxTokens] = useState(String(task.max_tokens))
  const [timeoutS, setTimeoutS] = useState(String(task.timeout_s))
  const config = useAction(onSaved)

  return (
    <details className="card tight" style={{ marginBottom: 10 }}>
      <summary>
        <span className="grow">{task.task}</span>
        {task.prompt_version != null && <Pill>v{task.prompt_version}</Pill>}
      </summary>
      <Notice>{config.error}</Notice>

      <Field label="Model" hint="An OpenRouter model slug — type any of them">
        <input value={model} onChange={(event) => setModel(event.target.value)} />
      </Field>
      <div className="row wrap" style={{ gap: 6, marginBottom: 12 }}>
        {COMMON_MODELS.map((slug) => (
          <button key={slug} type="button" className="chip" onClick={() => setModel(slug)}>
            {slug}
          </button>
        ))}
      </div>

      <Field label="Fallback model" hint="Used only when the first one errors. Blank for none.">
        <input value={fallback} onChange={(event) => setFallback(event.target.value)} />
      </Field>
      <div className="field-row">
        <Field label="Temperature">
          <input
            type="number"
            inputMode="decimal"
            step="0.1"
            value={temperature}
            onChange={(event) => setTemperature(event.target.value)}
          />
        </Field>
        <Field label="Max tokens">
          <input
            type="number"
            inputMode="numeric"
            value={maxTokens}
            onChange={(event) => setMaxTokens(event.target.value)}
          />
        </Field>
        <Field label="Timeout (s)">
          <input
            type="number"
            inputMode="numeric"
            value={timeoutS}
            onChange={(event) => setTimeoutS(event.target.value)}
          />
        </Field>
      </div>
      <button
        className="primary"
        disabled={config.busy}
        onClick={() =>
          config.run(() =>
            api.patch(`/api/llm-tasks/${task.task}`, {
              model,
              fallback_model: fallback || null,
              temperature: Number(temperature),
              max_tokens: Number(maxTokens),
              timeout_s: Number(timeoutS),
            }),
          )
        }
      >
        {config.busy ? 'Saving…' : 'Save configuration'}
      </button>

      <PromptEditor task={task} onSaved={onSaved} />
      <PromptVersions task={task} onSaved={onSaved} />
      <PreviewPrompt
        task={task.task}
        periods={periods}
        currentPeriod={currentPeriod}
      />
    </details>
  )
}

function PromptEditor({ task, onSaved }: { task: TaskConfig; onSaved: () => void }) {
  const [systemPrompt, setSystemPrompt] = useState(task.system_prompt ?? '')
  const [userTemplate, setUserTemplate] = useState(task.user_template ?? '')
  const { run, busy, error } = useAction(onSaved)

  return (
    <details>
      <summary className="small">Prompt</summary>
      <p className="tiny muted">
        Saving writes a new version and makes it active. Nothing is overwritten, so an earlier
        version can always be brought back.
      </p>
      <Notice>{error}</Notice>
      <Field label="System prompt">
        <textarea
          rows={10}
          value={systemPrompt}
          onChange={(event) => setSystemPrompt(event.target.value)}
        />
      </Field>
      <Field label="User template" hint="{{variables}} are filled in by the app">
        <textarea
          rows={10}
          value={userTemplate}
          onChange={(event) => setUserTemplate(event.target.value)}
        />
      </Field>
      <button
        className="primary"
        disabled={busy}
        onClick={() =>
          run(() =>
            api.post(`/api/llm-tasks/${task.task}/prompts`, {
              system_prompt: systemPrompt,
              user_template: userTemplate,
            }),
          )
        }
      >
        {busy ? 'Saving…' : 'Save as new version'}
      </button>
    </details>
  )
}

type PromptVersion = {
  id: string
  version: number
  created_at: string
  created_by: string
}

function PromptVersions({ task, onSaved }: { task: TaskConfig; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const versions = useLoad<PromptVersion[]>(open ? `/api/llm-tasks/${task.task}/prompts` : null)
  const { run, busy, error } = useAction(async () => {
    await versions.reload()
    onSaved()
  })

  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="small">Versions</summary>
      <Notice>{versions.error || error}</Notice>
      {versions.loading ? (
        <Spinner label="Loading versions" />
      ) : !versions.data?.length ? (
        <Empty>No saved versions yet.</Empty>
      ) : (
        <table>
          <tbody>
            {versions.data.map((version) => (
              <tr key={version.id}>
                <td>
                  v{version.version}
                  <div className="tiny muted">
                    {version.created_at?.slice(0, 10)} · {version.created_by}
                  </div>
                </td>
                <td className="right">
                  {version.id === task.active_prompt_version_id ? (
                    <Pill kind="accent">Active</Pill>
                  ) : (
                    <button
                      className="small"
                      disabled={busy}
                      onClick={() =>
                        run(() =>
                          api.patch(`/api/llm-tasks/${task.task}`, {
                            active_prompt_version_id: version.id,
                          }),
                        )
                      }
                    >
                      Roll back
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  )
}

type Preview = { model: string; fallback_model: string | null; system_prompt: string; user_prompt: string }

function PreviewPrompt({
  task,
  periods,
  currentPeriod,
}: {
  task: string
  periods: Period[]
  currentPeriod: Period | null
}) {
  const [periodId, setPeriodId] = useState(currentPeriod?.id ?? '')
  const [preview, setPreview] = useState<Preview | null>(null)
  const { run, busy, error } = useAction()

  // The period list arrives after the first render, so fall back to its newest entry.
  useEffect(() => {
    if (!periodId && periods.length) setPeriodId(periods[0].id)
  }, [periodId, periods])

  return (
    <details>
      <summary className="small">Preview</summary>
      <p className="tiny muted">
        Assembles the whole prompt for a real period. The model is not called and nothing is spent.
      </p>
      <Notice>{error}</Notice>
      <Field label="Period">
        <select value={periodId} onChange={(event) => setPeriodId(event.target.value)}>
          {periods.map((period) => (
            <option key={period.id} value={period.id}>
              {period.start_date} → {period.end_date}
            </option>
          ))}
        </select>
      </Field>
      <button
        disabled={busy || !periodId}
        onClick={() =>
          run(async () => {
            const result = await api.post<Preview>(
              `/api/llm-tasks/${task}/preview?period_id=${periodId}`,
            )
            setPreview(result)
          })
        }
      >
        {busy ? 'Assembling…' : 'Preview prompt'}
      </button>
      {preview && (
        <>
          <p className="tiny muted" style={{ marginTop: 10 }}>
            {preview.model}
            {preview.fallback_model ? ` (fallback ${preview.fallback_model})` : ''}
          </p>
          <Field label="System">
            <textarea rows={8} readOnly value={preview.system_prompt} />
          </Field>
          <Field label="User">
            <textarea rows={14} readOnly value={preview.user_prompt} />
          </Field>
        </>
      )}
    </details>
  )
}

// --- 7. call log (admin) -------------------------------------------------------------

type Call = {
  id: string
  task: string
  model: string
  tokens_in: number | null
  tokens_out: number | null
  cost_usd: number | null
  latency_ms: number | null
  status: string
  error: string | null
  created_at: string
}

const PAGE = 25

function CallLogSection() {
  const [offset, setOffset] = useState(0)
  const calls = useLoad<Page<Call>>(`/api/llm-calls?limit=${PAGE}&offset=${offset}`)
  const total = calls.data?.total ?? 0
  const rows = useMemo(() => calls.data?.items ?? [], [calls.data])

  return (
    <>
      <p className="small muted">
        Every model call, newest first. Rows — including the prompt and response — are pruned after
        30 days.
      </p>
      <Notice>{calls.error}</Notice>
      {calls.loading ? (
        <Spinner label="Loading calls" />
      ) : !rows.length ? (
        <Empty>No calls logged.</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Task</th>
              <th className="right">Tokens</th>
              <th className="right">Cost</th>
              <th className="right">Time</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((call) => (
              <tr key={call.id}>
                <td>
                  {call.task}
                  <div className="tiny muted">
                    {call.model} · {call.created_at?.slice(0, 16).replace('T', ' ')}
                  </div>
                  {call.status !== 'ok' && (
                    <Pill kind="danger">{call.error ? `${call.status}: ${call.error}` : call.status}</Pill>
                  )}
                </td>
                <td className="right small">
                  {(call.tokens_in ?? 0) + (call.tokens_out ?? 0)}
                </td>
                <td className="right small">
                  {call.cost_usd != null ? `$${Number(call.cost_usd).toFixed(3)}` : '—'}
                </td>
                <td className="right small">
                  {call.latency_ms != null ? `${(call.latency_ms / 1000).toFixed(1)}s` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row between" style={{ marginTop: 10 }}>
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          Newer
        </button>
        <span className="tiny muted">
          {total ? `${offset + 1}–${Math.min(offset + PAGE, total)} of ${total}` : ''}
        </span>
        <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
          Older
        </button>
      </div>
    </>
  )
}
