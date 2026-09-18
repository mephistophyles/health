"""The background worker.

A generation takes 30-120 seconds, so the UI queues a job and polls. The queue is a
Postgres table read by an in-process worker: single-digit concurrency needs nothing more.
One loop failing leaves the other loops' drafts untouched (PRD 4).
"""
import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import UniqueViolation

from health.database import Database
from health.domain import paths
from health.domain.contracts import (
    FitnessPlan,
    MealPlan,
    ProfileSuggestions,
    ReviseResult,
)
from health.domain.validation import validate_fitness_plan, validate_meal_plan
from health.llm import context as ctx
from health.llm.client import LlmError, run_task
from health.settings import Settings

log = logging.getLogger('health.jobs')

GENERATE_MEALS = 'meals.generate'
GENERATE_FITNESS = 'fitness.generate'
REVISE_MEALS = 'meals.revise'
REVISE_FITNESS = 'fitness.revise'
SUGGEST_PROFILE = 'profile.suggest'

NO_COOKING = {'takeout', 'eating_out', 'away'}


def enqueue(
    connection: Connection,
    household_id: str,
    kind: str,
    period_id: UUID,
    requested_by: UUID,
    owner_member_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Queue one loop, once.

    A unique index enforces one live job per loop, so pressing Generate twice queues
    nothing twice. The duplicate is detected by the database rather than by a prior SELECT,
    because a job belonging to the other adult is not one this connection can read.
    Returns None when a job was already queued or is already running.
    """
    try:
        with connection.transaction():
            return connection.execute(
                """INSERT INTO job (household_id, period_id, kind, owner_member_id,
                                    requested_by, payload)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    household_id, period_id, kind, owner_member_id, requested_by,
                    json.dumps(payload or {}),
                ),
            ).fetchone()
    except UniqueViolation:
        return None


def _store_plan(
    connection: Connection,
    household_id: str,
    period_id: UUID,
    kind: str,
    owner_member_id: UUID | None,
    content: dict,
    task: str,
    model: str,
    prompt_version_id: str,
) -> None:
    """Write the draft. A previous draft for the same slot is replaced, never kept (PRD 4)."""
    connection.execute(
        """DELETE FROM plan WHERE period_id = %s AND kind = %s
            AND owner_member_id IS NOT DISTINCT FROM %s AND status = 'draft'""",
        (period_id, kind, owner_member_id),
    )
    connection.execute(
        """INSERT INTO plan (household_id, period_id, kind, owner_member_id, status, content,
                             origin, task, model, prompt_version_id)
           VALUES (%s, %s, %s, %s, 'draft', %s, 'generated', %s, %s, %s)""",
        (
            household_id,
            period_id,
            kind,
            owner_member_id,
            json.dumps(content, default=str),
            task,
            model,
            prompt_version_id,
        ),
    )


def _draft(connection: Connection, period_id: UUID, kind: str, owner: UUID | None) -> dict:
    row = connection.execute(
        """SELECT * FROM plan WHERE period_id = %s AND kind = %s
            AND owner_member_id IS NOT DISTINCT FROM %s AND status = 'draft'""",
        (period_id, kind, owner),
    ).fetchone()
    if row is None:
        raise LlmError('There is no draft to revise. Generate one first.')
    return row


def run_fitness_generate(
    connection: Connection, settings: Settings, household_id: str, job: dict
) -> str:
    period_id, member_id = job['period_id'], job['owner_member_id']
    context = ctx.fitness_generation_context(connection, household_id, period_id, member_id)
    validation = ctx.fitness_validation_context(connection, period_id, member_id)
    if not validation.available_days:
        return 'No training days were marked available, so no sessions were planned.'

    result = run_task(
        connection,
        settings,
        household_id,
        GENERATE_FITNESS,
        context,
        FitnessPlan,
        post_validate=lambda plan: [str(p) for p in validate_fitness_plan(plan, validation)],
    )
    plan: FitnessPlan = result.value
    _store_plan(
        connection, household_id, period_id, 'fitness', member_id, plan.model_dump(),
        GENERATE_FITNESS, result.model, result.prompt_version_id,
    )
    _publish_summary(connection, household_id, period_id, member_id, plan)
    return plan.summary


def _publish_summary(
    connection: Connection, household_id: str, period_id: UUID, member_id: UUID, plan: FitnessPlan
) -> None:
    """Hand the meal loop the derived summary — training days and appetite, nothing else."""
    connection.execute(
        """INSERT INTO training_summary (household_id, period_id, member_id, summary)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (period_id, member_id) DO UPDATE
             SET summary = EXCLUDED.summary, updated_at = now()""",
        (
            household_id,
            period_id,
            member_id,
            json.dumps(plan.summary_for_meals.model_dump(), default=str),
        ),
    )


def run_meals_generate(
    connection: Connection, settings: Settings, household_id: str, job: dict
) -> str:
    period_id = job['period_id']
    modes = connection.execute(
        'SELECT dinner_mode FROM period_day WHERE period_id = %s', (period_id,)
    ).fetchall()
    if modes and all(row['dinner_mode'] in NO_COOKING for row in modes):
        return 'No cooking is planned this period, so no meal plan or grocery list was made.'

    context = ctx.meal_generation_context(connection, household_id, period_id)
    validation = ctx.meal_validation_context(connection, household_id, period_id)
    result = run_task(
        connection,
        settings,
        household_id,
        GENERATE_MEALS,
        context,
        MealPlan,
        post_validate=lambda plan: [str(p) for p in validate_meal_plan(plan, validation)],
    )
    plan: MealPlan = result.value
    _store_plan(
        connection, household_id, period_id, 'meals', None, plan.model_dump(),
        GENERATE_MEALS, result.model, result.prompt_version_id,
    )
    _store_suggestions(connection, household_id, plan)
    return plan.summary


def _store_suggestions(
    connection: Connection, household_id: str, plan: MealPlan | ProfileSuggestions
) -> None:
    for suggestion in plan.profile_suggestions:
        member = connection.execute(
            'SELECT id FROM member WHERE household_id = %s AND slug = %s',
            (household_id, suggestion.member),
        ).fetchone()
        if member is None:
            continue
        connection.execute(
            """INSERT INTO profile_suggestion (household_id, member_id, change, evidence)
               SELECT %s, %s, %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM profile_suggestion
                                   WHERE member_id = %s AND change = %s AND status = 'pending')""",
            (
                household_id, member['id'], suggestion.change, suggestion.evidence,
                member['id'], suggestion.change,
            ),
        )


def _apply_revision(draft: dict, target: str, result: ReviseResult) -> tuple[dict, list[str]]:
    """Apply only what the revision was asked to change; refuse everything else."""
    document = json.loads(json.dumps(draft))
    refused: list[str] = []
    for replacement in result.replacements:
        if target == 'plan' and replacement.target == 'plan':
            return replacement.value, refused
        if not paths.is_within(replacement.target, target):
            refused.append(replacement.target)
            continue
        try:
            paths.set_value(document, replacement.target, replacement.value)
        except (KeyError, IndexError, ValueError):
            refused.append(replacement.target)
    return document, refused


def _run_revise(
    connection: Connection, settings: Settings, household_id: str, job: dict, kind: str
) -> str:
    period_id, owner = job['period_id'], job['owner_member_id']
    payload = job['payload'] or {}
    target = payload.get('target', 'plan')
    row = _draft(connection, period_id, kind, owner)
    draft = row['content']

    if kind == 'fitness':
        base = ctx.fitness_generation_context(connection, household_id, period_id, owner)
        validation_context = ctx.fitness_validation_context(connection, period_id, owner)
        if not target.startswith(('sessions', 'plan')):
            resolved = paths.exercise_target(draft, target)
            target = resolved or target
        model_type, validate = FitnessPlan, validate_fitness_plan
        task = REVISE_FITNESS
    else:
        base = ctx.meal_generation_context(connection, household_id, period_id)
        validation_context = ctx.meal_validation_context(connection, household_id, period_id)
        model_type, validate = MealPlan, validate_meal_plan
        task = REVISE_MEALS

    context = {
        **base,
        'draft': draft,
        'target': target,
        'feedback_text': payload.get('feedback_text', ''),
        'chips': payload.get('chips', []),
    }
    result = run_task(
        connection, settings, household_id, task, context, ReviseResult,
        schema=ReviseResult.model_json_schema(),
    )
    revised, refused = _apply_revision(draft, target, result.value)

    parsed = model_type.model_validate(revised)
    problems = validate(parsed, validation_context)
    if problems:
        raise LlmError(
            'The revision did not fit the plan:\n' + '\n'.join(str(p) for p in problems[:10])
        )

    connection.execute(
        'UPDATE plan SET content = %s, task = %s, model = %s, prompt_version_id = %s,'
        ' updated_at = now() WHERE id = %s',
        (
            json.dumps(parsed.model_dump(), default=str), task, result.model,
            result.prompt_version_id, row['id'],
        ),
    )
    if kind == 'fitness':
        _publish_summary(connection, household_id, period_id, owner, parsed)

    note = result.value.note or 'Revised.'
    if refused:
        note += f' ({len(refused)} change(s) outside {target} were refused.)'
    return note


def run_profile_suggest(
    connection: Connection, settings: Settings, household_id: str, job: dict
) -> str:
    context = {
        'members': ctx.members_for_meals(connection, household_id),
        'feedback': ctx.meal_feedback(connection, job['period_id']),
    }
    result = run_task(
        connection, settings, household_id, SUGGEST_PROFILE, context, ProfileSuggestions
    )
    _store_suggestions(connection, household_id, result.value)
    return f'{len(result.value.profile_suggestions)} suggestion(s) proposed.'


HANDLERS = {
    GENERATE_FITNESS: run_fitness_generate,
    GENERATE_MEALS: run_meals_generate,
    REVISE_FITNESS: lambda c, s, h, j: _run_revise(c, s, h, j, 'fitness'),
    REVISE_MEALS: lambda c, s, h, j: _run_revise(c, s, h, j, 'meals'),
    SUGGEST_PROFILE: run_profile_suggest,
}


def claim_next(database: Database) -> dict | None:
    with database.queue_connection() as connection:
        row = connection.execute(
            """SELECT * FROM job WHERE status = 'queued'
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE job SET status = 'running', started_at = now(), attempts = attempts + 1"
            ' WHERE id = %s',
            (row['id'],),
        )
        return row


def finish(database: Database, job_id: UUID, status: str, detail: str) -> None:
    with database.queue_connection() as connection:
        connection.execute(
            'UPDATE job SET status = %s, detail = %s, finished_at = now() WHERE id = %s',
            (status, detail[:2000], job_id),
        )


def run_job(database: Database, settings: Settings, job: dict) -> None:
    handler = HANDLERS.get(job['kind'])
    if handler is None:
        finish(database, job['id'], 'failed', f'Unknown job kind {job["kind"]}')
        return
    try:
        with database.worker_connection(job['owner_member_id']) as connection:
            detail = handler(connection, settings, database.household_id, job)
        finish(database, job['id'], 'succeeded', detail or '')
    except LlmError as error:
        finish(database, job['id'], 'failed', str(error))
    except Exception as error:  # one bad job must not stop the worker
        log.exception('job %s failed', job['id'])
        finish(database, job['id'], 'failed', f'Unexpected error: {error}')


async def worker_loop(database: Database, settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            job = await asyncio.to_thread(claim_next, database)
            if job is None:
                await asyncio.wait_for(stop.wait(), timeout=settings.job_poll_seconds)
                continue
            await asyncio.to_thread(run_job, database, settings, job)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception:  # the worker outlives any single failure
            log.exception('worker loop error')
            await asyncio.sleep(settings.job_poll_seconds)
