CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Row-level security context. Both return NULL when unset, so every policy below
-- fails closed rather than open when a connection forgets to set its context.
CREATE FUNCTION app_household() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.household_id', true), '')::uuid $$;
CREATE FUNCTION app_member() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.member_id', true), '')::uuid $$;

-- The background worker must see every queued job, including another member's private
-- fitness job. Only the worker sets `app.worker`; request connections never do.
CREATE FUNCTION app_is_worker() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.worker', true), '') = 'on' $$;

CREATE TYPE member_kind AS ENUM ('adult', 'child');
CREATE TYPE goal_kind AS ENUM ('meal', 'fitness');
CREATE TYPE period_status AS ENUM ('planning', 'active', 'closed');
CREATE TYPE dinner_mode AS ENUM ('cook', 'leftovers', 'takeout', 'eating_out', 'away');
CREATE TYPE lunch_mode AS ENUM ('none', 'leftovers', 'prep');
CREATE TYPE cook_time AS ENUM ('quick', 'normal', 'project');
CREATE TYPE meal_slot AS ENUM ('dinner', 'lunch');
CREATE TYPE plan_kind AS ENUM ('meals', 'fitness');
CREATE TYPE plan_status AS ENUM ('draft', 'accepted');
CREATE TYPE recipe_verdict AS ENUM ('staple', 'worked', 'needs_tweak', 'didnt_work', 'untried');
CREATE TYPE portion_signal AS ENUM ('too_little', 'right', 'too_much');
CREATE TYPE ate_signal AS ENUM ('yes', 'partly', 'no');
CREATE TYPE repeat_signal AS ENUM ('yes', 'with_tweaks', 'no');
CREATE TYPE session_status AS ENUM ('completed', 'partly', 'skipped');
CREATE TYPE metric_kind AS ENUM ('weight', 'mood', 'energy');
CREATE TYPE suggestion_status AS ENUM ('pending', 'accepted', 'dismissed');
CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed');
CREATE TYPE llm_call_status AS ENUM ('ok', 'invalid_json', 'repaired', 'error', 'timeout', 'blocked');

-- Members are the four people planned for. Users are the subset who log in.
CREATE TABLE member (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  kind member_kind NOT NULL,
  birth_month DATE,
  notes TEXT NOT NULL DEFAULT '',
  presentation_rules JSONB NOT NULL DEFAULT '{"toggles": [], "notes": ""}'::jsonb,
  likes TEXT[] NOT NULL DEFAULT '{}',
  dislikes TEXT[] NOT NULL DEFAULT '{}',
  exclusions TEXT[] NOT NULL DEFAULT '{}',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, slug)
);

CREATE TABLE app_user (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  auth_user TEXT NOT NULL,
  display_name TEXT NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE RESTRICT,
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, auth_user)
);

-- Goals are versioned: a change closes the old row and opens a new one.
-- Fitness goals are private to their member; meal goals are shared with the household,
-- because the meal loop plans for all four people at once.
CREATE TABLE goal (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  kind goal_kind NOT NULL,
  statement TEXT NOT NULL,
  target TEXT,
  target_date DATE,
  constraints TEXT NOT NULL DEFAULT '',
  active_from DATE NOT NULL DEFAULT CURRENT_DATE,
  active_to DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX goal_active_idx ON goal (household_id, member_id, kind) WHERE active_to IS NULL;

CREATE TABLE equipment_profile (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  items TEXT[] NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, name)
);

CREATE TABLE period (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  status period_status NOT NULL DEFAULT 'planning',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date - start_date BETWEEN 4 AND 13),
  UNIQUE (household_id, start_date)
);

CREATE TABLE period_day (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID NOT NULL REFERENCES period(id) ON DELETE CASCADE,
  day DATE NOT NULL,
  dinner_mode dinner_mode NOT NULL DEFAULT 'cook',
  lunch_mode lunch_mode NOT NULL DEFAULT 'none',
  cook_time cook_time NOT NULL DEFAULT 'normal',
  UNIQUE (period_id, day)
);

CREATE TABLE shopping_trip (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID NOT NULL REFERENCES period(id) ON DELETE CASCADE,
  trip_date DATE NOT NULL,
  store TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (period_id, trip_date, store)
);

CREATE TABLE training_availability (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID NOT NULL REFERENCES period(id) ON DELETE CASCADE,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  days DATE[] NOT NULL DEFAULT '{}',
  travel BOOLEAN NOT NULL DEFAULT FALSE,
  session_minutes INTEGER NOT NULL DEFAULT 45,
  session_types TEXT[] NOT NULL DEFAULT '{}',
  UNIQUE (period_id, member_id)
);

CREATE TABLE prompt_version (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  task TEXT NOT NULL,
  version INTEGER NOT NULL,
  system_prompt TEXT NOT NULL,
  user_template TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'seed',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, task, version)
);

CREATE TABLE llm_task_config (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  task TEXT NOT NULL,
  model TEXT NOT NULL,
  fallback_model TEXT,
  temperature NUMERIC(3,2) NOT NULL DEFAULT 0.70,
  max_tokens INTEGER NOT NULL DEFAULT 8000,
  timeout_s INTEGER NOT NULL DEFAULT 180,
  active_prompt_version_id UUID REFERENCES prompt_version(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, task)
);

-- The plan of record. Only drafts and the accepted version exist; superseded drafts
-- are overwritten in place, per PRD 4.
CREATE TABLE plan (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID NOT NULL REFERENCES period(id) ON DELETE CASCADE,
  kind plan_kind NOT NULL,
  owner_member_id UUID REFERENCES member(id) ON DELETE CASCADE,
  status plan_status NOT NULL DEFAULT 'draft',
  content JSONB NOT NULL,
  origin TEXT NOT NULL DEFAULT 'generated',
  task TEXT,
  model TEXT,
  prompt_version_id UUID REFERENCES prompt_version(id),
  accepted_by UUID REFERENCES member(id),
  accepted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((kind = 'meals' AND owner_member_id IS NULL) OR (kind = 'fitness' AND owner_member_id IS NOT NULL))
);
CREATE UNIQUE INDEX plan_one_per_slot_idx ON plan
  (period_id, kind, COALESCE(owner_member_id, '00000000-0000-0000-0000-000000000000'::uuid), status);

-- The derived, metric-free training summary each fitness loop hands to the meal loop.
-- Household-scoped because the meal loop must read both adults' summaries, but no read
-- endpoint returns another member's row: it is planning input, not a shared view.
CREATE TABLE training_summary (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID NOT NULL REFERENCES period(id) ON DELETE CASCADE,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (period_id, member_id)
);

CREATE TABLE recipe (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  title TEXT NOT NULL,
  content JSONB NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  verdict recipe_verdict NOT NULL DEFAULT 'untried',
  verdict_override recipe_verdict,
  tweak_notes TEXT NOT NULL DEFAULT '',
  staple BOOLEAN NOT NULL DEFAULT FALSE,
  times_served INTEGER NOT NULL DEFAULT 0,
  last_served DATE,
  archived BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX recipe_last_served_idx ON recipe (household_id, last_served DESC NULLS LAST);

CREATE TABLE plan_meal (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  plan_id UUID NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
  day DATE NOT NULL,
  meal meal_slot NOT NULL,
  mode TEXT NOT NULL,
  recipe_id UUID REFERENCES recipe(id) ON DELETE SET NULL,
  recipe_key TEXT,
  leftover_of DATE,
  servings NUMERIC(6,2),
  skipped BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (plan_id, day, meal)
);
CREATE INDEX plan_meal_day_idx ON plan_meal (household_id, day);

CREATE TABLE grocery_item (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  trip_id UUID NOT NULL REFERENCES shopping_trip(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  qty NUMERIC(10,3),
  unit TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'Other',
  recipe_ids UUID[] NOT NULL DEFAULT '{}',
  check_pantry BOOLEAN NOT NULL DEFAULT FALSE,
  checked BOOLEAN NOT NULL DEFAULT FALSE,
  manual BOOLEAN NOT NULL DEFAULT FALSE,
  line_key TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX grocery_item_trip_idx ON grocery_item (trip_id);

CREATE TABLE meal_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  plan_meal_id UUID NOT NULL REFERENCES plan_meal(id) ON DELETE CASCADE,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  enjoyment SMALLINT CHECK (enjoyment BETWEEN 1 AND 5),
  portion portion_signal,
  ate ate_signal,
  presentation_note TEXT NOT NULL DEFAULT '',
  entered_by UUID REFERENCES member(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (plan_meal_id, member_id)
);

CREATE TABLE meal_review (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  plan_meal_id UUID NOT NULL REFERENCES plan_meal(id) ON DELETE CASCADE,
  effort TEXT,
  repeat_choice repeat_signal,
  notes TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (plan_meal_id)
);

CREATE TABLE period_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID NOT NULL REFERENCES period(id) ON DELETE CASCADE,
  kind plan_kind NOT NULL,
  member_id UUID REFERENCES member(id) ON DELETE CASCADE,
  fields JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((kind = 'meals' AND member_id IS NULL) OR (kind = 'fitness' AND member_id IS NOT NULL)),
  UNIQUE NULLS NOT DISTINCT (period_id, kind, member_id)
);

CREATE TABLE rejection (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  kind plan_kind NOT NULL,
  summary TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  rejected_by UUID REFERENCES member(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at DATE NOT NULL,
  cleared BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX rejection_active_idx ON rejection (household_id, kind, expires_at) WHERE NOT cleared;

CREATE TABLE workout_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  log_date DATE NOT NULL,
  plan_id UUID REFERENCES plan(id) ON DELETE SET NULL,
  exercise_key TEXT NOT NULL,
  name TEXT NOT NULL,
  sets JSONB NOT NULL DEFAULT '[]'::jsonb,
  alternative_used TEXT,
  status session_status NOT NULL DEFAULT 'completed',
  difficulty TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workout_log_member_date_idx ON workout_log (member_id, log_date DESC);

CREATE TABLE activity (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  activity_date DATE NOT NULL,
  type TEXT NOT NULL,
  duration_min INTEGER,
  distance_mi NUMERIC(6,2),
  notes TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX activity_member_date_idx ON activity (member_id, activity_date DESC);

CREATE TABLE metric (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  metric_date DATE NOT NULL,
  kind metric_kind NOT NULL,
  value NUMERIC(8,2) NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (member_id, metric_date, kind)
);
CREATE INDEX metric_member_kind_idx ON metric (member_id, kind, metric_date DESC);

CREATE TABLE profile_suggestion (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  member_id UUID NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  change TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '',
  status suggestion_status NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE setting (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  key TEXT NOT NULL,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, key)
);

CREATE TABLE seasonal_produce (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  month SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
  items TEXT[] NOT NULL DEFAULT '{}',
  UNIQUE (household_id, month)
);

-- Operational log. Request and response bodies are pruned with the row after 30 days.
CREATE TABLE llm_call (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  task TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version_id UUID REFERENCES prompt_version(id),
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  status llm_call_status NOT NULL,
  error TEXT,
  request JSONB,
  response JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX llm_call_created_idx ON llm_call (household_id, created_at DESC);

-- Generation runs as a background job because a loop takes 30-120s.
CREATE TABLE job (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  period_id UUID REFERENCES period(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  owner_member_id UUID REFERENCES member(id) ON DELETE CASCADE,
  requested_by UUID REFERENCES member(id),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status job_status NOT NULL DEFAULT 'queued',
  detail TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX job_queue_idx ON job (status, created_at) WHERE status IN ('queued', 'running');
-- One live job per loop, so pressing Generate twice queues nothing twice.
CREATE UNIQUE INDEX job_active_slot_idx ON job
  (period_id, kind, COALESCE(owner_member_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE status IN ('queued', 'running');
CREATE INDEX job_period_idx ON job (household_id, period_id, created_at DESC);

ALTER TABLE member ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE goal ENABLE ROW LEVEL SECURITY;
ALTER TABLE equipment_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE period ENABLE ROW LEVEL SECURITY;
ALTER TABLE period_day ENABLE ROW LEVEL SECURITY;
ALTER TABLE shopping_trip ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_availability ENABLE ROW LEVEL SECURITY;
ALTER TABLE prompt_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_task_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_summary ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_meal ENABLE ROW LEVEL SECURITY;
ALTER TABLE grocery_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE meal_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE meal_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE period_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE rejection ENABLE ROW LEVEL SECURITY;
ALTER TABLE workout_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_suggestion ENABLE ROW LEVEL SECURITY;
ALTER TABLE setting ENABLE ROW LEVEL SECURITY;
ALTER TABLE seasonal_produce ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_call ENABLE ROW LEVEL SECURITY;
ALTER TABLE job ENABLE ROW LEVEL SECURITY;

-- Shared: both users see the same rows.
CREATE POLICY household_scope ON member USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON app_user USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON period USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON period_day USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON shopping_trip USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON prompt_version USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON llm_task_config USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON training_summary USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON recipe USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON grocery_item USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON meal_feedback USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON meal_review USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON rejection USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON profile_suggestion USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON setting USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON seasonal_produce USING (household_id = app_household()) WITH CHECK (household_id = app_household());
CREATE POLICY household_scope ON llm_call USING (household_id = app_household()) WITH CHECK (household_id = app_household());

-- Private: only the owning member's rows are visible, and only to that member.
CREATE POLICY owner_scope ON equipment_profile
  USING (household_id = app_household() AND member_id = app_member())
  WITH CHECK (household_id = app_household() AND member_id = app_member());
CREATE POLICY owner_scope ON training_availability
  USING (household_id = app_household() AND member_id = app_member())
  WITH CHECK (household_id = app_household() AND member_id = app_member());
CREATE POLICY owner_scope ON workout_log
  USING (household_id = app_household() AND member_id = app_member())
  WITH CHECK (household_id = app_household() AND member_id = app_member());
CREATE POLICY owner_scope ON activity
  USING (household_id = app_household() AND member_id = app_member())
  WITH CHECK (household_id = app_household() AND member_id = app_member());
CREATE POLICY owner_scope ON metric
  USING (household_id = app_household() AND member_id = app_member())
  WITH CHECK (household_id = app_household() AND member_id = app_member());

-- Mixed: meal rows are shared, fitness rows are private to their owner.
CREATE POLICY mixed_scope ON goal
  USING (household_id = app_household() AND (kind = 'meal' OR member_id = app_member()))
  WITH CHECK (household_id = app_household() AND (kind = 'meal' OR member_id = app_member()));
CREATE POLICY mixed_scope ON plan
  USING (household_id = app_household() AND (owner_member_id IS NULL OR owner_member_id = app_member()))
  WITH CHECK (household_id = app_household() AND (owner_member_id IS NULL OR owner_member_id = app_member()));
CREATE POLICY mixed_scope ON period_feedback
  USING (household_id = app_household() AND (member_id IS NULL OR member_id = app_member()))
  WITH CHECK (household_id = app_household() AND (member_id IS NULL OR member_id = app_member()));
-- Reading a job is restricted to its owner, because a fitness job's `detail` carries that
-- adult's plan summary. Queueing one for the other adult is allowed, since pressing
-- Generate has to start both fitness loops for the meal loop to have both summaries.
CREATE POLICY mixed_scope ON job
  USING (household_id = app_household()
         AND (app_is_worker() OR owner_member_id IS NULL OR owner_member_id = app_member()))
  WITH CHECK (household_id = app_household()
         AND (app_is_worker() OR owner_member_id IS NULL OR owner_member_id = app_member()
              OR requested_by = app_member()));

-- plan_meal rows only ever belong to a meal plan, which is shared.
CREATE POLICY household_scope ON plan_meal USING (household_id = app_household()) WITH CHECK (household_id = app_household());
