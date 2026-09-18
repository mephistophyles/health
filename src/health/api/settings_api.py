"""Settings, model and prompt configuration (PRD 10). Admin only.

Nothing about models or prompts is hard-coded beyond the seed values: every save creates a
new prompt version, plans record the version that produced them, and any version can be
made active again.
"""
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg import Connection
from pydantic import BaseModel, Field

from health.api.common import bad_request, one
from health.deps import get_connection, get_identity
from health.identity import Identity
from health.llm import context as ctx
from health.llm.client import load_task_config, month_spend, render, spend_cap
from health.llm.seed_prompts import TASKS
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['settings'])


class SettingIn(BaseModel):
    value: Any


class TaskConfigIn(BaseModel):
    model: str | None = None
    fallback_model: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=256, le=200_000)
    timeout_s: int | None = Field(None, ge=10, le=600)
    active_prompt_version_id: UUID | None = None


class PromptIn(BaseModel):
    system_prompt: str
    user_template: str


class ProduceIn(BaseModel):
    items: list[str]


@router.get('/settings')
def list_settings(connection: Connection = Depends(get_connection)) -> dict[str, Any]:
    rows = connection.execute('SELECT key, value, updated_at FROM setting ORDER BY key').fetchall()
    return {row['key']: row['value'] for row in rows}


@router.put('/settings/{key}')
def set_setting(
    key: str,
    body: SettingIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    identity.require_admin()
    return one(
        connection,
        """INSERT INTO setting (household_id, key, value) VALUES (%s, %s, %s)
           ON CONFLICT (household_id, key) DO UPDATE
             SET value = EXCLUDED.value, updated_at = now() RETURNING *""",
        (settings.household_id, key, json.dumps(body.value)),
        'Could not save that setting',
    )


@router.get('/seasonal-produce')
def list_produce(connection: Connection = Depends(get_connection)) -> list[dict[str, Any]]:
    return connection.execute(
        'SELECT month, items FROM seasonal_produce ORDER BY month'
    ).fetchall()


@router.put('/seasonal-produce/{month}')
def set_produce(
    month: int,
    body: ProduceIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    identity.require_admin()
    if not 1 <= month <= 12:
        raise bad_request('Month must be 1-12')
    return one(
        connection,
        """INSERT INTO seasonal_produce (household_id, month, items) VALUES (%s, %s, %s)
           ON CONFLICT (household_id, month) DO UPDATE SET items = EXCLUDED.items RETURNING *""",
        (settings.household_id, month, body.items),
        'Could not save that month',
    )


@router.get('/llm-tasks')
def list_tasks(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    identity.require_admin()
    rows = connection.execute(
        """SELECT c.*, p.version AS prompt_version, p.system_prompt, p.user_template
             FROM llm_task_config c
             LEFT JOIN prompt_version p ON p.id = c.active_prompt_version_id
            ORDER BY c.task"""
    ).fetchall()
    return {
        'tasks': rows,
        'known_tasks': TASKS,
        'spend': {
            'month_to_date': month_spend(connection, settings.household_id),
            'cap': spend_cap(connection, settings.household_id),
        },
    }


@router.patch('/llm-tasks/{task}')
def update_task(
    task: str,
    body: TaskConfigIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    identity.require_admin()
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise bad_request('Nothing to update')
    assignments = ', '.join(f'{name} = %s' for name in fields)
    return one(
        connection,
        f'UPDATE llm_task_config SET {assignments}, updated_at = now() WHERE task = %s RETURNING *',
        (*fields.values(), task),
        f'No configuration for {task}',
    )


@router.get('/llm-tasks/{task}/prompts')
def list_prompt_versions(
    task: str,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    identity.require_admin()
    return connection.execute(
        'SELECT * FROM prompt_version WHERE task = %s ORDER BY version DESC LIMIT %s',
        (task, limit),
    ).fetchall()


@router.post('/llm-tasks/{task}/prompts', status_code=201)
def save_prompt(
    task: str,
    body: PromptIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Every save is a new version, and it becomes the active one. Nothing is overwritten."""
    identity.require_admin()
    version = connection.execute(
        'SELECT COALESCE(max(version), 0) + 1 AS next FROM prompt_version WHERE task = %s',
        (task,),
    ).fetchone()['next']
    row = one(
        connection,
        """INSERT INTO prompt_version (household_id, task, version, system_prompt, user_template,
                                       created_by)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
        (
            settings.household_id, task, version, body.system_prompt, body.user_template,
            identity.display_name,
        ),
        'Could not save that prompt',
    )
    connection.execute(
        'UPDATE llm_task_config SET active_prompt_version_id = %s, updated_at = now()'
        ' WHERE task = %s',
        (row['id'], task),
    )
    return row


@router.post('/llm-tasks/{task}/preview')
def preview_prompt(
    task: str,
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """The fully assembled prompt for a real period, without calling the model (PRD 10)."""
    identity.require_admin()
    config = load_task_config(connection, settings.household_id, task)
    if task.startswith('fitness'):
        context = ctx.fitness_generation_context(
            connection, settings.household_id, period_id, identity.member_id
        )
        from health.domain.contracts import FitnessPlan as Model
    elif task.startswith('meals'):
        context = ctx.meal_generation_context(connection, settings.household_id, period_id)
        from health.domain.contracts import MealPlan as Model
    else:
        context = {
            'members': ctx.members_for_meals(connection, settings.household_id),
            'feedback': ctx.meal_feedback(connection, period_id),
        }
        from health.domain.contracts import ProfileSuggestions as Model

    schema = json.dumps(Model.model_json_schema())
    return {
        'task': task,
        'model': config.model,
        'fallback_model': config.fallback_model,
        'system_prompt': config.system_prompt,
        'user_prompt': render(config.user_template, {**context, 'schema': schema}),
    }


@router.get('/llm-calls')
def list_calls(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """The operational log. Prompts and responses are pruned with the row after 30 days."""
    identity.require_admin()
    total = connection.execute('SELECT count(*) AS n FROM llm_call').fetchone()['n']
    rows = connection.execute(
        """SELECT id, task, model, tokens_in, tokens_out, cost_usd, latency_ms, status, error,
                  created_at
             FROM llm_call ORDER BY created_at DESC LIMIT %s OFFSET %s""",
        (limit, offset),
    ).fetchall()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/llm-calls/{call_id}')
def get_call(
    call_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    identity.require_admin()
    return one(connection, 'SELECT * FROM llm_call WHERE id = %s', (call_id,), 'No such call')
