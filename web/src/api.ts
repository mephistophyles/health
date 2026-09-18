// One place that talks to the API. Everything else calls these.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: any) => d.msg).join(', ')
    } catch {
      // A non-JSON error body is still an error; the status line is enough.
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

// --- shapes shared across pages ---------------------------------------------------

export type Me = {
  auth_user: string
  display_name: string
  member_id: string
  member_slug: string
  is_admin: boolean
  auth_mode: string
}

export type Member = {
  id: string
  slug: string
  name: string
  kind: 'adult' | 'child'
  birth_month: string | null
  notes: string
  presentation_rules: { toggles: string[]; notes: string }
  likes: string[]
  dislikes: string[]
  exclusions: string[]
  is_user: boolean
}

export type DinnerMode = 'cook' | 'leftovers' | 'takeout' | 'eating_out' | 'away'
export type LunchMode = 'none' | 'leftovers' | 'prep'
export type CookTime = 'quick' | 'normal' | 'project'

export type PeriodDay = {
  day: string
  dinner_mode: DinnerMode
  lunch_mode: LunchMode
  cook_time: CookTime
}

export type Trip = { id: string; trip_date: string; store: string | null }

export type Availability = {
  days: string[]
  travel: boolean
  session_minutes: number
  session_types: string[]
}

export type Period = {
  id: string
  start_date: string
  end_date: string
  notes: string
  status: 'planning' | 'active' | 'closed'
}

export type PeriodDetail = Period & {
  days: PeriodDay[]
  trips: Trip[]
  availability: Availability | null
}

export type Ingredient = {
  name: string
  qty: number | null
  unit: string
  category: string
  perishability: 'fresh' | 'keeps' | 'pantry'
}

export type Recipe = {
  key: string
  library_id: string | null
  title: string
  description: string
  active_min: number
  total_min: number
  servings: number
  feeds: { date: string; meal: 'dinner' | 'lunch'; servings: number }[]
  ingredients: Ingredient[]
  steps: { text: string; make_ahead: boolean }[]
  portions: { member: string; guidance: string }[]
  kid_plates: { member: string; serve: string; fallback: string }[]
  tags: string[]
}

export type MealPlanContent = {
  summary: string
  days: {
    date: string
    dinner: { mode: DinnerMode; recipe_key: string | null; leftover_of: string | null }
    lunch: { mode: LunchMode; recipe_key: string | null; leftover_of: string | null }
  }[]
  recipes: Recipe[]
  profile_suggestions: { member: string; change: string; evidence: string }[]
}

export type Exercise = {
  key: string
  name: string
  sets: number | null
  reps: number | null
  duration_s: number | null
  distance_mi: number | null
  pace: string | null
  weight_lb: number | null
  rest_s: number | null
  rpe: number | null
  cues: string
  alternatives: { name: string; reason: string; sets: number | null; reps: number | null }[]
}

export type FitnessPlanContent = {
  intent: 'Build' | 'Repeat' | 'Deload'
  summary: string
  summary_for_meals: { training_days: string[]; notes: string }
  sessions: {
    date: string
    focus: string
    duration_min: number
    warmup: string[]
    cooldown: string[]
    exercises: Exercise[]
  }[]
}

export type Plan<T> = {
  id: string
  period_id: string
  kind: 'meals' | 'fitness'
  owner_member_id: string | null
  status: 'draft' | 'accepted'
  content: T
  origin: string
  model: string | null
  accepted_at: string | null
  updated_at: string
}

export type Job = {
  id: string
  kind: string
  owner_member_id: string | null
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  detail: string
  created_at: string
  finished_at: string | null
}

export type Plans = {
  meals: { draft: Plan<MealPlanContent> | null; accepted: Plan<MealPlanContent> | null }
  fitness: { draft: Plan<FitnessPlanContent> | null; accepted: Plan<FitnessPlanContent> | null }
  jobs: Job[]
}

export type GroceryItem = {
  id: string
  trip_id: string
  name: string
  qty: number | null
  unit: string
  category: string
  recipe_ids: string[]
  check_pantry: boolean
  checked: boolean
  manual: boolean
}

export type GroceryTrip = Trip & { items: GroceryItem[]; remaining: number }

export type ChecklistItem = { key: string; label: string; done: boolean; detail: string }

export type Checklist = {
  show: boolean
  now: string
  active_period: Period | null
  target_period: Period | null
  items: ChecklistItem[]
}

export type LibraryRecipe = {
  id: string
  title: string
  description: string | null
  tags: string[]
  staple: boolean
  tweak_notes: string
  times_served: number
  last_served: string | null
  archived: boolean
  verdict: 'staple' | 'worked' | 'needs_tweak' | 'didnt_work' | 'untried'
  verdict_is_manual: boolean
  average_enjoyment: number | null
  total_min: number | null
}

export type PlanMeal = {
  id: string
  day: string
  meal: 'dinner' | 'lunch'
  mode: string
  skipped: boolean
  leftover_of: string | null
  recipe_id: string | null
  title: string | null
  feedback: {
    member_id: string
    slug: string
    enjoyment: number | null
    portion: string | null
    ate: string | null
    presentation_note: string
  }[]
  review: { effort: string | null; repeat_choice: string | null; notes: string } | null
}

export type Page<T> = { items: T[]; total: number; limit: number; offset: number }

export type ChartSeries = { points: any[]; enough_for_chart: boolean; [key: string]: any }
