# Health

A weekly planner for one household: dinners for everyone, and a private training plan for
each adult. One button produces a draft; the household reviews it, revises it, or writes
its own; and the accepted plan becomes a shopping list, a set of recipes, and a week of
sessions. What the household thought of last week feeds into next week.

It is a small private app. It assumes it is reachable only by the people it plans for.

## What it owns

- **Planning periods** — 5 to 14 days, with a dinner and lunch mode per day, a cooking-time
  budget per day, shopping trips, and each adult's training availability.
- **Meal plans and recipes** — one entry per day, recipes with portions and per-child plate
  instructions, and a growing library with a verdict on every meal that has been served.
- **Grocery lists** — computed by the app from the accepted recipes, one per shopping trip,
  tickable on a phone, shared between both users.
- **Training plans** — Monday to Friday, per adult, private to that adult, with workout
  logs, activities, and body metrics.
- **Feedback** — per meal, per person, per period, and the progress charts drawn from it.

## How the pieces fit

```
period inputs ──┬─> fitness loop (per adult, private) ──> training summary ─┐
                │                                                           │
                └───────────────────────────────────────> meal loop <───────┘
                                                              │
                                        accepted plan ──> plan_meal rows
                                                     └──> library recipes
                                                     └──> grocery lists
```

Four rules shape the code:

1. **The model never writes to the database.** Every response is parsed into a Pydantic
   model (`domain/contracts.py`), then checked against the period it claims to plan
   (`domain/validation.py`), and only then stored. A response that fails either gate gets
   one repair retry with the errors handed back, and after that the loop fails and the
   other loops keep their drafts.
2. **The grocery list is computed, never generated.** `domain/groceries.py` is plain,
   unit-tested Python. It runs again after every edit, and ticks survive on lines that did
   not change.
3. **Each adult's fitness data is private.** Only a derived, metric-free summary crosses
   into the shared meal context — training days and a note about appetite, never a weight.
   Privacy is enforced in the repository layer, with row-level security behind it, and
   `tests/test_privacy.py` proves both directions.
4. **Nothing generates on its own.** Generation runs on a button press, as a background
   job, under a monthly spend cap.

## Running it locally

Requires Python 3.12+, Node 22+, and a Postgres 15 or later.

```bash
uv sync --all-groups
npm install && npm run build            # writes web-dist/

export DATABASE_URL=postgresql://localhost/health
export HOUSEHOLD_ID=$(uuidgen | tr 'A-Z' 'a-z')
export WEB_DIST_PATH=$PWD/web-dist
export MIGRATIONS_PATH=$PWD/migrations
export AUTH_MODE=none

uv run python -m health.migrate         # migrates, then seeds
uv run uvicorn health.main:app --reload
```

`npm run dev` runs the frontend with hot reload, proxying `/api` to port 8000.

### Tests

```bash
uv run pytest                                    # unit tests only
docker run -d --name health-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=health_test \
  -p 55433:5432 postgres:18-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55433/health_test uv run pytest
```

Without `TEST_DATABASE_URL` the database-backed tests skip and the pure domain tests still
run. CI sets it, so the privacy and accept-pipeline tests run on every push.

## Configuration

All configuration comes from the environment. Nothing is read from a file and nothing is
hardcoded.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | This app's own database. |
| `HOUSEHOLD_ID` | yes | From `/home/shared/` — never a per-app copy. |
| `AUTH_MODE` | yes | `proxy` reads `X-Auth-User`; `none` runs single-user as `SINGLE_USER_ID`. |
| `SINGLE_USER_ID` | no | The login used when `AUTH_MODE=none`. |
| `OPENROUTER_API_KEY` | for generation | Read from SSM. Without it the app works; generation does not. |
| `OPENROUTER_BASE_URL` | no | Defaults to the public endpoint. |
| `WEB_DIST_PATH` | no | Defaults to `/app/web-dist`; the app refuses to start without it. |
| `MIGRATIONS_PATH` | no | Defaults to `/app/migrations`. |
| `TIMEZONE` | no | Defaults to `America/New_York`. |
| `SEED_MEMBERS` | no | JSON, see below. |
| `SEED_USERS` | no | JSON, see below. |
| `JOB_WORKER_ENABLED` | no | `false` disables the in-process worker. |

Required SSM parameters under `/home/health/`:

```
/home/health/DATABASE_URL          SecureString
/home/health/AUTH_MODE             String
/home/health/OPENROUTER_API_KEY    SecureString
/home/health/SEED_USERS            SecureString   (contains login addresses)
/home/health/SEED_MEMBERS          SecureString   (optional; contains names)
```

`HOUSEHOLD_ID` comes from `/home/shared/`.

### Seeding the household

No real name or login address appears in this repository. The migration runner seeds four
placeholder members (`adult-1`, `adult-2`, `child-1`, `child-2`) that are renamed in the
app, and creates no users at all unless told who they are:

```json
SEED_USERS=[{"auth_user":"someone@example.com","display_name":"...","member":"adult-1","is_admin":true},
            {"auth_user":"other@example.com","display_name":"...","member":"adult-2"}]
```

`SEED_MEMBERS` optionally replaces the placeholder profiles with the real ones in the same
shape as `seed_data.DEFAULT_MEMBERS`. Both are read only at seed time and both are
idempotent, so a deploy re-running them changes nothing.

Health details — dietary needs, medication context, a child's food rules — belong in the
app, entered by the household, not in seed data or fixtures.

### Roles

There are no roles beyond one flag. `app_user.is_admin` gates model, prompt and household
settings; everything else is either shared between both users or private to one member.
An identity that is not in `app_user` gets a 403.

## Notes on two deliberate decisions

**LLM request and response bodies are stored in `llm_call` for 30 days**, then pruned by a
background task. This is a considered exception to the platform's "never log LLM bodies"
rule: debugging a bad plan is impossible without seeing the prompt that produced it. They
are stored in this app's own database, never written to stdout, and never leave the box.

**Row-level security is enabled with a policy on every table, but the repository layer is
the enforced gate.** A table's owner bypasses its own policies, so if the app connects as
the database owner, RLS is a second line rather than the first. `tests/test_privacy.py`
tests both: every private endpoint through the API, and the policies themselves through a
non-owner role. Pointing `DATABASE_URL` at a non-owner role would make the policies bite
directly, and nothing in the app depends on being the owner at runtime.

## Deploying

CI builds `linux/arm64`, pushes to GHCR, copies `docker-compose.yml` and `manifest.json` to
the box, and calls `/opt/home/bin/deploy.sh`, which owns secrets, migrations, health gating,
rollback and launchpad registration. The container listens on 8000 and is published on
`127.0.0.1:8010`.

## Licence

MIT. See `LICENSE`.
