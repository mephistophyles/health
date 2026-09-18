"""Generating, reviewing, revising and accepting plans (PRD 4).

Generation never happens on its own: it runs on a button press, as a background job, and
one loop failing leaves the others' drafts alone.
"""
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Connection
from pydantic import BaseModel, Field

from health.api.common import bad_request, one
from health.deps import get_connection, get_identity
from health.domain import paths
from health.domain.contracts import FitnessPlan, MealPlan
from health.domain.validation import validate_fitness_plan, validate_meal_plan
from health.identity import Identity
from health.llm import context as ctx
from health.llm import jobs
from health.services import accept_fitness_plan, accept_meal_plan, record_rejection
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['plans'])

LOOPS = Literal['meals', 'fitness']


class GenerateIn(BaseModel):
    loops: list[LOOPS] = Field(default_factory=lambda: ['meals', 'fitness'])
    # "Regenerate my fitness only" leaves the other adult's plan alone.
    own_fitness_only: bool = False


class ReviseIn(BaseModel):
    target: str = 'plan'
    feedback_text: str = ''
    chips: list[str] = Field(default_factory=list)


class RejectIn(BaseModel):
    target: str
    reason: str = ''


class ContentIn(BaseModel):
    content: dict[str, Any]


def _plan(connection: Connection, plan_id: UUID, identity: Identity) -> dict[str, Any]:
    """A fitness plan belonging to the other adult is not found, because it is not theirs."""
    return one(
        connection,
        'SELECT * FROM plan WHERE id = %s AND (owner_member_id IS NULL OR owner_member_id = %s)',
        (plan_id, identity.member_id),
        'No such plan',
    )


def _validate(connection: Connection, household_id: str, plan: dict, strict: bool) -> list[str]:
    """Structure always; the repeat window and rejection list only for generated plans."""
    if plan['kind'] == 'meals':
        parsed = MealPlan.model_validate(plan['content'])
        context = ctx.meal_validation_context(connection, household_id, plan['period_id'])
        if not strict:
            context.recent_dinner_titles = set()
            context.rejected_titles = set()
        return [str(p) for p in validate_meal_plan(parsed, context)]
    parsed = FitnessPlan.model_validate(plan['content'])
    context = ctx.fitness_validation_context(
        connection, plan['period_id'], plan['owner_member_id']
    )
    return [str(p) for p in validate_fitness_plan(parsed, context)]


@router.get('/periods/{period_id}/plans')
def list_plans(
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Meal plans for the household; of the fitness plans, only the caller's own."""
    rows = connection.execute(
        'SELECT * FROM plan WHERE period_id = %s'
        ' AND (owner_member_id IS NULL OR owner_member_id = %s) ORDER BY kind, status',
        (period_id, identity.member_id),
    ).fetchall()
    running = connection.execute(
        """SELECT id, kind, owner_member_id, status, detail, created_at, finished_at
             FROM job WHERE period_id = %s AND (owner_member_id IS NULL OR owner_member_id = %s)
            ORDER BY created_at DESC LIMIT 20""",
        (period_id, identity.member_id),
    ).fetchall()
    meal_rows = [r for r in rows if r['kind'] == 'meals']
    fitness_rows = [r for r in rows if r['owner_member_id'] == identity.member_id]
    return {
        'meals': {
            'draft': next((r for r in meal_rows if r['status'] == 'draft'), None),
            'accepted': next((r for r in meal_rows if r['status'] == 'accepted'), None),
        },
        'fitness': {
            'draft': next((r for r in fitness_rows if r['status'] == 'draft'), None),
            'accepted': next((r for r in fitness_rows if r['status'] == 'accepted'), None),
        },
        'jobs': running,
    }


@router.post('/periods/{period_id}/generate', status_code=202)
def generate(
    period_id: UUID,
    body: GenerateIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Queue the loops. Fitness runs first; meals waits for the training summaries.

    Both adults' fitness loops are queued, because the meal loop needs both training
    summaries. Each runs scoped to its own member and its draft is only ever visible to
    that member — the caller sees their own job here, and nothing of the other's.
    """
    one(connection, 'SELECT id FROM period WHERE id = %s', (period_id,), 'No such period')
    queued = []
    if 'fitness' in body.loops:
        owners = (
            [identity.member_id]
            if body.own_fitness_only
            else [
                row['id']
                for row in connection.execute(
                    "SELECT id FROM member WHERE kind = 'adult' ORDER BY sort_order"
                ).fetchall()
            ]
        )
        for owner in owners:
            job = jobs.enqueue(
                connection, settings.household_id, jobs.GENERATE_FITNESS, period_id,
                identity.member_id, owner_member_id=owner,
            )
            if owner == identity.member_id:
                queued.append(job)
    if 'meals' in body.loops:
        queued.append(
            jobs.enqueue(
                connection, settings.household_id, jobs.GENERATE_MEALS, period_id,
                identity.member_id,
            )
        )
    return {'jobs': queued}


@router.get('/periods/{period_id}/jobs')
def list_jobs(
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> list[dict[str, Any]]:
    return connection.execute(
        """SELECT id, kind, owner_member_id, status, detail, attempts, created_at, finished_at
             FROM job WHERE period_id = %s AND (owner_member_id IS NULL OR owner_member_id = %s)
            ORDER BY created_at DESC LIMIT 20""",
        (period_id, identity.member_id),
    ).fetchall()


@router.post('/jobs/{job_id}/retry', status_code=202)
def retry_job(
    job_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    return one(
        connection,
        "UPDATE job SET status = 'queued', detail = '', finished_at = NULL WHERE id = %s"
        " AND status = 'failed' AND (owner_member_id IS NULL OR owner_member_id = %s) RETURNING *",
        (job_id, identity.member_id),
        'That job is not waiting to be retried',
    )


@router.post('/plans/{plan_id}/accept')
def accept(
    plan_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Meals: either user. Fitness: the owner only (PRD 2)."""
    plan = _plan(connection, plan_id, identity)
    if plan['status'] == 'accepted':
        return plan
    if plan['kind'] == 'fitness':
        identity.require_owner(plan['owner_member_id'])

    problems = _validate(connection, settings.household_id, plan, strict=False)
    if problems:
        raise bad_request('This plan cannot be accepted yet:\n' + '\n'.join(problems[:10]))

    if plan['kind'] == 'meals':
        return accept_meal_plan(connection, settings.household_id, plan, identity.member_id)
    return accept_fitness_plan(connection, plan, identity.member_id)


@router.put('/plans/{plan_id}/content')
def edit_plan(
    plan_id: UUID,
    body: ContentIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """A manual edit. No LLM call, and an accepted plan stays accepted (PRD 4)."""
    plan = _plan(connection, plan_id, identity)
    if plan['kind'] == 'fitness':
        identity.require_owner(plan['owner_member_id'])

    candidate = {**plan, 'content': body.content}
    problems = _validate(connection, settings.household_id, candidate, strict=False)
    if problems:
        raise bad_request('That edit does not fit the period:\n' + '\n'.join(problems[:10]))

    updated = one(
        connection,
        "UPDATE plan SET content = %s, origin = 'manual', updated_at = now() WHERE id = %s"
        ' RETURNING *',
        (json.dumps(body.content, default=str), plan_id),
        'No such plan',
    )
    if updated['status'] == 'accepted' and updated['kind'] == 'meals':
        # Keep the rows and the shopping list consistent with what was just edited.
        accept_meal_plan(connection, settings.household_id, updated, identity.member_id)
    return updated


@router.post('/periods/{period_id}/plans/manual', status_code=201)
def write_own(
    period_id: UUID,
    kind: LOOPS,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Discard the draft and start one by hand, empty but shaped (PRD 4)."""
    owner = identity.member_id if kind == 'fitness' else None
    days = connection.execute(
        'SELECT day, dinner_mode, lunch_mode FROM period_day WHERE period_id = %s ORDER BY day',
        (period_id,),
    ).fetchall()
    if kind == 'meals':
        content = MealPlan(
            summary='Written by hand',
            days=[
                {
                    'date': row['day'].isoformat(),
                    'dinner': {'mode': row['dinner_mode']},
                    'lunch': {'mode': row['lunch_mode']},
                }
                for row in days
            ],
        ).model_dump()
    else:
        content = FitnessPlan(
            intent='Repeat', summary='Written by hand',
            summary_for_meals={'training_days': [], 'notes': ''}, sessions=[],
        ).model_dump()

    connection.execute(
        """DELETE FROM plan WHERE period_id = %s AND kind = %s
            AND owner_member_id IS NOT DISTINCT FROM %s AND status = 'draft'""",
        (period_id, kind, owner),
    )
    return one(
        connection,
        """INSERT INTO plan (household_id, period_id, kind, owner_member_id, status, content,
                             origin)
           VALUES (%s, %s, %s, %s, 'draft', %s, 'manual') RETURNING *""",
        (settings.household_id, period_id, kind, owner, json.dumps(content, default=str)),
        'Could not create the plan',
    )


@router.post('/plans/{plan_id}/revise', status_code=202)
def revise(
    plan_id: UUID,
    body: ReviseIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """The minor loop: the model replaces the target and leaves everything else alone."""
    plan = _plan(connection, plan_id, identity)
    if plan['kind'] == 'fitness':
        identity.require_owner(plan['owner_member_id'])
    if plan['status'] != 'draft':
        raise bad_request('Only a draft can be revised. Regenerate to reopen an accepted plan.')

    kind = jobs.REVISE_MEALS if plan['kind'] == 'meals' else jobs.REVISE_FITNESS
    job = jobs.enqueue(
        connection, settings.household_id, kind, plan['period_id'], identity.member_id,
        owner_member_id=plan['owner_member_id'],
        payload={'target': body.target, 'feedback_text': body.feedback_text, 'chips': body.chips},
    )
    return {'job': job}


@router.post('/plans/{plan_id}/reject-item')
def reject_item(
    plan_id: UUID,
    body: RejectIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Remove an item and remember why, so future generations learn from it (PRD 8)."""
    plan = _plan(connection, plan_id, identity)
    if plan['kind'] == 'fitness':
        identity.require_owner(plan['owner_member_id'])

    content = json.loads(json.dumps(plan['content'], default=str))
    try:
        rejected = paths.get(content, body.target)
    except (KeyError, IndexError, ValueError) as error:
        raise bad_request(f'{body.target} is not part of this plan') from error

    summary = _describe(content, body.target, rejected, plan['kind'])
    planning = connection.execute(
        "SELECT value FROM setting WHERE household_id = %s AND key = 'planning_defaults'",
        (settings.household_id,),
    ).fetchone()
    weeks = int((planning['value'] if planning else {}).get('rejection_weeks', 12))
    rejection = record_rejection(
        connection, settings.household_id, plan['kind'], summary, body.reason,
        identity.member_id, weeks,
    )

    if plan['kind'] == 'meals' and body.target.endswith('.dinner'):
        paths.set_value(content, f'{body.target}.recipe_key', None)
    elif plan['kind'] == 'fitness':
        _remove(content, body.target)

    updated = one(
        connection,
        'UPDATE plan SET content = %s, updated_at = now() WHERE id = %s RETURNING *',
        (json.dumps(content, default=str), plan_id),
        'No such plan',
    )
    return {'plan': updated, 'rejection': rejection}


def _describe(content: dict, target: str, value: Any, kind: str) -> str:
    """What the rejection list should call this, so the next generation can avoid it."""
    if isinstance(value, dict):
        if value.get('recipe_key'):
            for recipe in content.get('recipes', []):
                if recipe.get('key') == value['recipe_key']:
                    return recipe.get('title', value['recipe_key'])
        if value.get('title'):
            return value['title']
        if value.get('name'):
            return value['name']
        if value.get('focus'):
            return value['focus']
    return f'{kind} item at {target}'


def _remove(content: dict, target: str) -> None:
    parts = paths.parse(target)
    parent = content
    for part in parts[:-1]:
        parent = parent[part]
    last = parts[-1]
    if isinstance(parent, list) and isinstance(last, int):
        parent.pop(last)
    else:
        parent[last] = None


@router.get('/rejections')
def list_rejections(
    kind: LOOPS | None = None,
    include_cleared: bool = False,
    connection: Connection = Depends(get_connection),
) -> list[dict[str, Any]]:
    """Clearing a rejection is a soft delete, so cleared rows are hidden unless asked for."""
    return connection.execute(
        """SELECT r.*, m.name AS rejected_by_name FROM rejection r
             LEFT JOIN member m ON m.id = r.rejected_by
            WHERE (%s::text IS NULL OR r.kind = %s::plan_kind)
              AND (%s OR NOT r.cleared)
            ORDER BY r.created_at DESC LIMIT 200""",
        (kind, kind, include_cleared),
    ).fetchall()


@router.delete('/rejections/{rejection_id}', status_code=status.HTTP_204_NO_CONTENT)
def clear_rejection(
    rejection_id: UUID, connection: Connection = Depends(get_connection)
) -> None:
    connection.execute('UPDATE rejection SET cleared = TRUE WHERE id = %s', (rejection_id,))


@router.post('/periods/{period_id}/suggest-profile-updates', status_code=202)
def suggest_profile_updates(
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    job = jobs.enqueue(
        connection, settings.household_id, jobs.SUGGEST_PROFILE, period_id, identity.member_id
    )
    return {'job': job}


@router.post('/plan-meals/{plan_meal_id}/skip')
def toggle_skipped(
    plan_meal_id: UUID,
    skipped: bool = True,
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """A light post-acceptance edit: it updates the plan without reopening it (PRD 4)."""
    row = one(
        connection,
        'UPDATE plan_meal SET skipped = %s WHERE id = %s RETURNING *',
        (skipped, plan_meal_id),
        'No such meal',
    )
    if row['recipe_id']:
        from health.services import refresh_recipe_stats

        refresh_recipe_stats(connection, settings.household_id, row['recipe_id'])
    return row


@router.get('/plans/{plan_id}/problems')
def plan_problems(
    plan_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    plan = _plan(connection, plan_id, identity)
    try:
        return {'problems': _validate(connection, settings.household_id, plan, strict=False)}
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - a malformed draft is a report, not a 500
        return {'problems': [str(error)]}
