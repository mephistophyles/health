# AGENTS.md

Conventions for this project. Reference for Claude Code, hermes, and any other agent working in this repo. Symlink `CLAUDE.md → AGENTS.md` so both resolve here.

This is a **template**, shipped into every new repo. The Hard Rules and Platform sections should stay identical everywhere; app-specific sections will diverge and that's fine. When platform conventions change, update the template and copy forward.

---

## What this platform is

A private network of personal apps and pages on one EC2 box, reachable only over Tailscale. Not public. Not multi-tenant. A household, not an organisation.

- **Auth is network-level.** Reaching anything means being on the tailnet. Never build login pages, session management, password resets, or user tables.
- **Small scale.** Single-digit concurrent requests. No caching layers, no queues, no horizontal scaling.
- **Data is precious and hard to regenerate.** Prefer append-only, prefer soft deletes, make migrations reversible.
- **Everything is disposable except the data.** Containers are cattle; the Postgres volume is not.
- **Builds happen in GitHub Actions, never on the box.** The box only pulls images. Build weight is free; box weight is not.

---

## Decision: app or artifact?

Ask this first. Getting it wrong means building the thing twice.

> **Shared or relational state → app. No state, or single-user scratch → artifact.**

Not complexity. Not effort. State.

| It needs… | Build |
|---|---|
| Another person to see or edit the same data | **app** |
| Relational queries, joins, history you'll query later | **app** |
| A scheduled job, or to run when nobody's looking | **app** |
| A secret, or an external API with credentials | **app** |
| Nothing persisted, or a personal scratchpad | **artifact** |

Worked example: a chore list looks like a trivial checklist, but two people tick items on the same list. That's shared state — it's an app.

**Default to artifact when the rule allows.** No container, no migrations, no deploy pipeline. Small is the point.

---

## Two kinds of artifact

Split by who authored it, not by ambition.

**Authored mini-app** — you or Claude Code wrote it. Lives in the artifacts repo, gets **its own hostname on day one** (`<slug>.madebyphil.com`), served as static files. Promotion later means nginx changes from `root` to `proxy_pass` and **the URL never moves**. Give it its own hostname even when that feels like overkill.

**Dropped artifact** — an agent generated it. Lives at `artifacts.madebyphil.com/<ulid>`, immutable, sandboxed origin. The separate origin is load-bearing: an artifact served from the launchpad's origin could read its storage and call its APIs with your session. Revisions are new artifacts with a `supersedes` pointer, never edits.

---

# Part 1 — Building an artifact

- **Single self-contained HTML file.** No bundler, no external CSS/JS.
- **No external network requests** except the KV endpoint on the same origin. CSP blocks the rest, so a CDN import silently fails. Inline what you need.
- **2 MB cap.**
- **Degrade gracefully** — KV is a convenience, not a dependency. An artifact that shows an error screen when KV is unreachable is broken.
- **Never put secrets in an artifact.** It's readable HTML. An artifact that needs a secret is an app.
- **Never touch Postgres.** Not read-only, not "just one table." If it needs a table, it's an app.
- Respect `prefers-color-scheme`, work down to 380px, no analytics or external beacons.
- Read an existing artifact before writing a new one. Consistency across the launchpad matters more than any individual artifact's ambition.

Artifacts register the same way apps do — see below — with `"kind": "artifact"`.

---

# Part 2 — Building an app

## Repo layout

```
.
  AGENTS.md            this file (CLAUDE.md symlinks here)
  README.md            what it does, what it owns, required SSM parameters
  LICENSE              from day one — see Public-readiness
  Dockerfile
  docker-compose.yml
  manifest.json        launchpad registration — NO url field
  migrations/          numbered, each with a matching .down.sql
  src/
  .github/
    workflows/deploy.yml
    dependabot.yml
```

## The deploy contract

CI does three things: build `linux/arm64`, push to GHCR tagged with the commit SHA, copy two files to the box and call one command.

```bash
scp docker-compose.yml manifest.json deploy@home-apps:/opt/home/apps/<slug>/
ssh deploy@home-apps "/opt/home/bin/deploy.sh <slug> <sha>"
```

**CI does not handle secrets, migrations, health checks, rollback, or launchpad registration.** `/opt/home/bin/deploy.sh` owns all of it, so behaviour stays identical across every repo. Don't reimplement any of it in a workflow.

`deploy.sh` in order: fetches `/home/shared/*` and `/home/<slug>/*` from SSM into `.env.runtime` → pulls the image → runs the `migrate` service if the `migration` profile defines one → starts `app` → waits for its healthcheck → **rolls back to the previous SHA if it doesn't become healthy** → registers `manifest.json` only after health passes.

## `docker-compose.yml`

```yaml
name: month-close                       # MUST equal the slug

services:
  app:
    image: ghcr.io/<owner>/<repo>:${TAG:-latest}
    restart: unless-stopped
    env_file: [.env.runtime]            # generated by deploy.sh; never committed
    networks: [homenet]
    ports:
      - "127.0.0.1:8000:8000"           # 127.0.0.1 prefix is mandatory
    mem_limit: 512m
    healthcheck:                        # REQUIRED — deploys are gated on it
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 30s

  migrate:
    image: ghcr.io/<owner>/<repo>:${TAG:-latest}   # SAME tag as app
    command: ["python", "-m", "<pkg>.migrate"]
    env_file: [.env.runtime]
    networks: [homenet]
    profiles: [migration]                # name must be exactly "migration"
    restart: "no"

networks:
  homenet:
    external: true
```

Details that have each caused a real failure:

- **The healthcheck command must exist in the image.** `python:*-slim` has no `curl`. Use a runtime already present.
- **`start_period` must exceed real cold-start time.** Too short and `deploy.sh` rolls back a working app.
- **The profile must be named `migration` and the service `migrate`.** `deploy.sh` looks for both by name, and services behind an inactive profile are invisible to `docker compose config --services` — which is why the profile name matters, not just the service name.
- **`migrate` uses the same image tag as `app`**, so migrations match the code being deployed.
- **`127.0.0.1:` prefix.** Docker's port publishing writes iptables rules directly and bypasses UFW. Omit it and the app is on the public internet at its raw port.
- **port 8000 conflict.** The example uses port 8000, but that one is already in use, new apps can use port 8000 within the container, but must expose an unused port for production deployment.

## `manifest.json`

```json
{
  "slug": "month-close",
  "name": "Monthly close",
  "kind": "app",
  "description": "Categorised ledger, month-end view, and trends",
  "group": "household",
  "access": ["me", "household"],
  "status": "active"
}
```

- **No `url` field.** `deploy.sh` derives `https://<slug>.<PLATFORM_DOMAIN>` and injects it along with `version` and `deployed`. Hardcoding the hostname would leak it into a repo that may go public. An explicit `url` still wins, for entries not following the `<slug>.<domain>` convention.
- **Never `"url": ""`.** Empty string isn't null, so it survives the fallback and the launchpad then skips the entry for failing its https check. Omit the field entirely.
- `slug` must be unique platform-wide — it's the registry filename, and a collision silently overwrites another entry.
- **`access` is display only.** It decides whose tile renders, never who may reach the URL. Never read it to make an authorisation decision.
- `status: retired` hides the tile while reserving the slug. Prefer it to deleting.

## `.github/workflows/deploy.yml`

```yaml
name: Build and deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build-and-deploy:
    runs-on: ubuntu-24.04-arm        # native arm64; QEMU cross-build is painfully slow
    environment: production          # REQUIRED — see below
    permissions:
      contents: read
      packages: write
      id-token: write                # REQUIRED for Tailscale workload identity
    steps:
      - uses: actions/checkout@v7

      # tests here — but do NOT build the frontend if the Dockerfile does

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/arm64
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - uses: tailscale/github-action@v4
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          audience: ${{ secrets.TS_AUDIENCE }}
          tags: tag:ci

      - name: Deploy
        run: |
          scp -o StrictHostKeyChecking=accept-new docker-compose.yml manifest.json \
            deploy@home-apps:/opt/home/apps/<slug>/
          ssh -o StrictHostKeyChecking=accept-new deploy@home-apps \
            "/opt/home/bin/deploy.sh <slug> ${{ github.sha }}"
```

- **Repo secrets: `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE`.** That's all. Tailscale uses workload identity federation, so there's no static credential; and Tailscale SSH authorises the connection from the tailnet ACL, so no SSH key is needed either.
- **`environment: production` is load-bearing.** The federated identity's subject is registered as `repo:<owner>@*/<repo>@*:environment:production`. Without this line GitHub mints a `ref`-based subject instead and the token exchange fails with a 403 that reads like a credential problem. Note GitHub may append numeric owner and repo IDs to the subject — match with `@*`.
- **`id-token: write`** or the action can't request a JWT.
- **Don't build the frontend twice.** If the Dockerfile has a build stage, delete the equivalent step from the workflow.

To debug a claim mismatch, decode the actual subject rather than guessing:

```yaml
      - run: |
          TOKEN=$(curl -sH "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
            "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=tailscale" | jq -r '.value')
          echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | jq '{sub, repository, environment}'
```

Remove it once resolved — don't leave token claims printing into build logs.

## `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: { interval: "monthly" }
  - package-ecosystem: "docker"
    directory: "/"
    schedule: { interval: "monthly" }
```

Every version pinned in this file will be wrong eventually. Action majors and base images drift, and library majors drop default exports. Let Dependabot carry it.

## Dockerfile

- **Copy the lockfile.** `npm ci` refuses to run without `package-lock.json`, and a selective `COPY package.json …` that omits it fails inside the image while working fine on the runner. Copy manifest and lockfile together in their own layer for caching, then sources.
- **Set data paths as `ENV`.** If the app finds built assets or other non-code files at runtime, set an explicit `ENV` here so both `app` and `migrate` inherit it.
- **`EXPOSE` is documentation**; the compose `ports:` mapping is what actually publishes.

---

## Runtime contract

- **Config comes from the environment.** Never read a config file, never hardcode.
- **Never write a `.env` file** — not for local convenience, not as an example with dummy values that get filled in and committed. Secrets live in SSM; document parameter names in `README.md` and let a human create them.

### Never compute a runtime path from the module's location

Worth stating on its own, because it fails silently and only in the container.

```python
# WRONG — resolves differently once the package is pip-installed
web_dist = Path(__file__).parents[2] / 'web-dist'
```

The app is installed into site-packages while assets are copied to `/app/…`, so relative resolution lands somewhere that doesn't exist. It works locally because you run from the source tree.

```python
# RIGHT — explicit, configurable, identical everywhere
WEB_DIST = Path(os.environ.get("WEB_DIST_PATH", "/app/web-dist"))
```

### Fail loudly at startup; never silently skip registration

```python
# WRONG — missing assets produce a bare API and mystery 404s
if web_dist.exists():
    app.mount('/assets', StaticFiles(directory=web_dist / 'assets'))
    ...
```

```python
# RIGHT — the container fails its healthcheck and deploy.sh rolls back
if not WEB_DIST.exists():
    raise RuntimeError(f"WEB_DIST not found at {WEB_DIST}")
```

A silent conditional turns a deploy-time failure into a runtime mystery hours later.

### Healthcheck

- **Expose `/healthz`** returning 200 when the app can actually serve — including a database check, and ideally asserting static assets mounted.
- A healthcheck that only proves the process is listening will pass while the app serves nothing useful. The deploy gate is only as good as this endpoint.

### Other

- **Set `mem_limit`.** One leaking process must not OOM Postgres.
- **Log to stdout as JSON.** No log files, no rotation logic.
- **Handle SIGTERM** — finish in-flight requests, close the pool. Deploys wait 10s before SIGKILL.
- Every outbound call gets a timeout. No exceptions.
- Stream LLM responses when user-facing.
- Never log request or response bodies from an LLM or mail API — they contain personal data.

---

## Secrets and configuration

Two SSM namespaces merge into `.env.runtime`, shared written first so an app-specific parameter of the same name wins:

| Path | Contents |
|---|---|
| `/home/shared/*` | platform-wide values every app gets — `HOUSEHOLD_ID`, … |
| `/home/<slug>/*` | this app's own configuration and secrets |

The last path segment becomes the variable name: `/home/month-close/DATABASE_URL` → `DATABASE_URL`.

- **`HOUSEHOLD_ID` comes from `/home/shared/`.** Never create a per-app copy — apps with different values silently see none of each other's data.
- `SecureString` for secrets, `String` for non-secret config like `AUTH_MODE`.
- **A config-only change needs a reload, not just a parameter write.** `.env.runtime` is regenerated at deploy and the container reads it at start:
  ```bash
  sudo -u deploy /opt/home/bin/reload-env.sh <slug>
  ```
- **AWS CLI write failures are non-fatal in a shell.** After any `put-parameter` or `delete-parameter`, read back rather than trusting the exit:
  ```bash
  aws ssm get-parameters-by-path --path /home/<slug> --recursive \
    --query 'Parameters[].Name' --output text
  ```

---

## Identity and roles

The proxy resolves the caller and sets two headers:

```
X-Auth-User: liz@example.com
X-Auth-Role: viewer
```

- **`AUTH_MODE` selects behaviour.** `proxy` reads the headers; `none` runs single-user. **Default to `none`** — forward-auth doesn't exist yet, so `proxy` means every API call is unauthenticated and returns 403 while static routes still return 200. That asymmetry is the signature of this misconfiguration.
- Keep `AUTH_MODE=none` in SSM until forward-auth is live and a second person is on the tailnet. Adding a household member is the trigger to flip it, not a date.
- **Trust the headers only because the app can't be reached except through nginx.** That holds only while the app binds `127.0.0.1` and nginx *sets* the headers rather than forwarding them. Never accept a client-supplied `X-Auth-User`.
- **Never build your own login.** If you're writing a session table, stop.
- **Roles gate actions; RLS gates rows.** A `viewer` may see the whole household ledger (RLS permits it) but cannot upload a CSV (the role forbids it).
- Unknown user or unknown role → 403. Never default-allow.
- Document which roles this app expects in `README.md`.

---

## Database

This app owns exactly one Postgres database, reached via `DATABASE_URL`. It's a database on a shared server, not a private server.

- **Never connect to another app's database.** If you need another app's data, a human adds an explicit `GRANT SELECT` on specific tables, recorded in the platform's `core/grants.sql`. Note the dependency in `README.md`.
- **Every table gets RLS enabled and a policy.** Not optional, not a later hardening pass.

The reason is specific to this platform: apps here are largely agent-written, and a query that forgets `WHERE user_id = ?` is a realistic mistake. RLS means the database refuses those rows regardless of what the query says.

```sql
CREATE TABLE ledger_entries (
  id           BIGSERIAL PRIMARY KEY,
  household_id UUID NOT NULL,
  user_id      TEXT NOT NULL,      -- who created it
  ...
);
ALTER TABLE ledger_entries ENABLE ROW LEVEL SECURITY;
```

| Mode | Example | Policy |
|---|---|---|
| **Shared** — same rows, both edit | chores, ledger, meal plan | `USING (household_id = current_setting('app.household_id')::uuid)` |
| **Complementary** — own rows, joint rollups | nutrition logs | `USING (user_id = current_setting('app.user_id'))` plus `SECURITY DEFINER` views returning aggregates only |
| **Private** — never cross-visible | fitness, writing coach | `USING (user_id = current_setting('app.user_id'))` |

Complementary needs the most care: default to `user_id`, and expose household views returning **aggregates, not rows**, so "how did we both do this week" works without either person reading the other's entries.

Set context per request from the identity headers, never from user input:

```sql
SET LOCAL app.user_id = 'liz@example.com';
SET LOCAL app.household_id = '...';
```

Migrations are numbered, forward with a matching `.down.sql`, and run before the new container starts. One that can't be rolled back needs a comment saying why.

---

## Public-readiness

This repo may become public. These rules keep that a one-step move instead of a history rewrite, and they apply **from the first commit**.

- **No secret ever enters the history.** Not in code, comments, fixtures, commit messages, or a file later deleted. `git rm` doesn't remove it from history. If one lands, rotate it and assume it's public.
- **No hardcoded platform hostnames.** Base URLs come from env (`PUBLIC_BASE_URL` via SSM at runtime, never a build-time constant). No literal `*.madebyphil.com` in source or in `manifest.json`.
- **Auth mode is configurable.** A public deployment has no forward-auth in front of it, so an app that hard-depends on `X-Auth-User` can't be published without surgery.
- **No household member emails or personal data** in code, fixtures, or seed data. Use `user@example.com`.
- **No real financial, health, or personal records in fixtures.** Generate synthetic data.
- **`LICENSE` and a real `README.md` from day one**, written for someone who doesn't know this platform.
- Keep commit messages professional. They're published too.

Assume every commit is public the moment you write it.

---

## Adding this app to the platform

An agent can do 1–4. The rest need a human on the box.

1. `Dockerfile`, `docker-compose.yml`, `manifest.json`, `migrations/`
2. `.github/workflows/deploy.yml` and `dependabot.yml`
3. `README.md` listing required SSM parameters and expected roles
4. `LICENSE`
5. **Human:** `sudo -u deploy /opt/home/bin/new-db.sh <slug>`
6. **Human:** SSM parameters under `/home/<slug>/` — including `AUTH_MODE`
7. **Human:** `mkdir -p /opt/home/apps/<slug>` owned by `deploy`
8. **Human:** nginx config + DNS A record pointing at the box's tailnet IP
9. **Human:** repo secrets `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`, and a `production` environment in GitHub
10. **Human:** Tailscale federated identity subject covering this repo

If you're an agent and you've finished 1–4, **say so explicitly and list what a human still needs to do.** Don't imply the app is live.

---

## Hard rules

Not preferences. Violating one is a defect regardless of how well the rest works. **Keep this section identical across every repo.**

1. **No secrets in the repo or its history.**
2. **No `ports:` without a `127.0.0.1:` prefix.**
3. **No table without RLS enabled and a policy.**
4. **No connecting to another app's database without an explicit reviewed grant.**
5. **No trusting a client-supplied identity header.**
6. **No login pages, session tables, or password handling.**
7. **No writes to the host filesystem from application code.** Postgres or object storage.
8. **No unbounded queries.** Every list endpoint paginates. "It's only us" is how a 400k-row table ends up in a browser.
9. **No hardcoded platform hostnames, no `url` in `manifest.json`, no real personal data.**
10. **No runtime path computed from `__file__` or equivalent.** Env var with an explicit default.
11. **No silent conditional around route or asset registration.** Fail at startup.
12. **No artifact touching Postgres.** If it needs a table, it's an app.
13. **No new dependency without justification** in the PR description.

---

## Anti-patterns

**Building auth.** The network and the proxy already answered this. A login page is dead code with a vulnerability surface.

**Reaching for a queue or a cache.** Single-digit concurrency. `setInterval` or a cron entry is the right answer.

**Storing files on disk.** Container filesystems vanish on deploy.

**Reimplementing deploy logic in the workflow.** Migrations, health gating, rollback, and registration live in `deploy.sh` so they can't drift across repos. A workflow that does them itself will diverge and won't receive fixes.

**Reading a manifest or registry to make an authorisation decision.** Those are display manifests. Authorisation is the ACL, the proxy, and RLS.

**Sharing code by copying it between repos.** If two apps genuinely need the same module, publish it as a package. Copies drift and the second copy never gets the bug fix.

**Building an app because the artifact felt too small.** Check the state rule again. If nothing is shared and nothing is relational, the artifact was right.

**A "temporary" hardcoded value.** It won't be temporary. SSM or a repo constant; nothing in between.

---

## Operating on the box

For humans — agents have no box access.

**Everything under `/opt/home/apps/` runs as `deploy`.** Those directories and `.env.runtime` are `deploy:deploy 600`; running as `ubuntu` either fails on permissions or creates files the next deploy can't read.

```bash
sudo -u deploy /opt/home/bin/fetch-env.sh <slug>
sudo -u deploy /opt/home/bin/reload-env.sh <slug>
sudo -u deploy bash -c 'cd /opt/home/apps/<slug> && docker compose logs -f app'
```

Diagnosing a deploy:

```bash
cat /opt/home/registry/<slug>.json          # written last — absent means the health gate failed
sudo -u deploy bash -c 'cd /opt/home/apps/<slug> && docker compose ps'
sudo nginx -T 2>/dev/null | grep -c "<slug>.madebyphil.com"   # 0 means the config never loaded
```

`nginx -t` passing proves nothing about a file that was never symlinked — always pair it with `nginx -T | grep`.

Manual rollback:

```bash
sudo -u deploy bash -c 'cd /opt/home/apps/<slug> && echo "TAG=<older-sha>" > .env && docker compose up -d'
```