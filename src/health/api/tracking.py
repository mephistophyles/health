"""Feedback and tracking (PRD 7).

Every input here is optional and meant to be fast: a meal rating under 30 seconds, a
workout 'done as prescribed' in one tap. Fitness entries are private to their owner.
"""
import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from psycopg import Connection
from pydantic import BaseModel, Field

from health.api.common import bad_request, one, page_params
from health.deps import get_connection, get_identity
from health.identity import Identity
from health.services import refresh_recipe_stats
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['tracking'])


class MealFeedbackIn(BaseModel):
    member_id: UUID
    enjoyment: int | None = Field(None, ge=1, le=5)
    portion: Literal['too_little', 'right', 'too_much'] | None = None
    ate: Literal['yes', 'partly', 'no'] | None = None
    presentation_note: str = ''


class MealReviewIn(BaseModel):
    effort: str | None = None
    repeat_choice: Literal['yes', 'with_tweaks', 'no'] | None = None
    notes: str = ''


class PeriodFeedbackIn(BaseModel):
    kind: Literal['meals', 'fitness']
    fields: dict[str, Any] = Field(default_factory=dict)


class WorkoutLogIn(BaseModel):
    log_date: date
    plan_id: UUID | None = None
    exercise_key: str
    name: str
    sets: list[dict[str, Any]] = Field(default_factory=list)
    alternative_used: str | None = None
    status: Literal['completed', 'partly', 'skipped'] = 'completed'
    difficulty: str | None = None


class SessionDoneIn(BaseModel):
    """One tap: the whole session went as prescribed."""

    plan_id: UUID
    log_date: date
    status: Literal['completed', 'partly', 'skipped'] = 'completed'
    difficulty: str | None = None


class ActivityIn(BaseModel):
    activity_date: date
    type: str
    duration_min: int | None = None
    distance_mi: float | None = None
    notes: str = ''


class MetricIn(BaseModel):
    metric_date: date
    kind: Literal['weight', 'mood', 'energy']
    value: float
    note: str = ''


@router.get('/periods/{period_id}/meals')
def list_meals(
    period_id: UUID, connection: Connection = Depends(get_connection)
) -> list[dict[str, Any]]:
    """The accepted plan's meals with whatever feedback has been entered so far."""
    return connection.execute(
        """SELECT pm.id, pm.day, pm.meal, pm.mode, pm.skipped, pm.leftover_of,
                  r.id AS recipe_id, r.title,
                  COALESCE(jsonb_agg(jsonb_build_object('member_id', f.member_id,
                    'slug', m.slug, 'enjoyment', f.enjoyment, 'portion', f.portion,
                    'ate', f.ate, 'presentation_note', f.presentation_note))
                    FILTER (WHERE f.id IS NOT NULL), '[]'::jsonb) AS feedback,
                  to_jsonb(mr.*) - 'id' - 'household_id' AS review
             FROM plan_meal pm
             JOIN plan p ON p.id = pm.plan_id AND p.kind = 'meals' AND p.status = 'accepted'
             LEFT JOIN recipe r ON r.id = pm.recipe_id
             LEFT JOIN meal_feedback f ON f.plan_meal_id = pm.id
             LEFT JOIN member m ON m.id = f.member_id
             LEFT JOIN meal_review mr ON mr.plan_meal_id = pm.id
            WHERE p.period_id = %s
            GROUP BY pm.id, pm.day, pm.meal, pm.mode, pm.skipped, pm.leftover_of, r.id, r.title,
                     mr.*
            ORDER BY pm.day, pm.meal""",
        (period_id,),
    ).fetchall()


@router.put('/plan-meals/{plan_meal_id}/feedback')
def set_meal_feedback(
    plan_meal_id: UUID,
    body: MealFeedbackIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Either user enters feedback for any household member; it is shared data."""
    row = one(
        connection,
        """INSERT INTO meal_feedback (household_id, plan_meal_id, member_id, enjoyment, portion,
                                      ate, presentation_note, entered_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (plan_meal_id, member_id) DO UPDATE
             SET enjoyment = EXCLUDED.enjoyment, portion = EXCLUDED.portion, ate = EXCLUDED.ate,
                 presentation_note = EXCLUDED.presentation_note, entered_by = EXCLUDED.entered_by,
                 updated_at = now()
           RETURNING *""",
        (
            settings.household_id, plan_meal_id, body.member_id, body.enjoyment, body.portion,
            body.ate, body.presentation_note, identity.member_id,
        ),
        'No such meal',
    )
    _refresh_for_meal(connection, settings.household_id, plan_meal_id)
    return row


@router.put('/plan-meals/{plan_meal_id}/review')
def set_meal_review(
    plan_meal_id: UUID,
    body: MealReviewIn,
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    row = one(
        connection,
        """INSERT INTO meal_review (household_id, plan_meal_id, effort, repeat_choice, notes)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (plan_meal_id) DO UPDATE
             SET effort = EXCLUDED.effort, repeat_choice = EXCLUDED.repeat_choice,
                 notes = EXCLUDED.notes, updated_at = now()
           RETURNING *""",
        (settings.household_id, plan_meal_id, body.effort, body.repeat_choice, body.notes),
        'No such meal',
    )
    _refresh_for_meal(connection, settings.household_id, plan_meal_id)
    return row


def _refresh_for_meal(connection: Connection, household_id: str, plan_meal_id: UUID) -> None:
    meal = connection.execute(
        'SELECT recipe_id FROM plan_meal WHERE id = %s', (plan_meal_id,)
    ).fetchone()
    if meal and meal['recipe_id']:
        refresh_recipe_stats(connection, household_id, meal['recipe_id'])


@router.get('/periods/{period_id}/feedback')
def get_period_feedback(
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> list[dict[str, Any]]:
    """Meal feedback is shared; another member's fitness feedback is never returned."""
    return connection.execute(
        'SELECT * FROM period_feedback WHERE period_id = %s'
        ' AND (member_id IS NULL OR member_id = %s)',
        (period_id, identity.member_id),
    ).fetchall()


@router.put('/periods/{period_id}/feedback')
def set_period_feedback(
    period_id: UUID,
    body: PeriodFeedbackIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    member_id = identity.member_id if body.kind == 'fitness' else None
    return one(
        connection,
        """INSERT INTO period_feedback (household_id, period_id, kind, member_id, fields)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (period_id, kind, member_id) DO UPDATE
             SET fields = EXCLUDED.fields, updated_at = now()
           RETURNING *""",
        (settings.household_id, period_id, body.kind, member_id, json.dumps(body.fields)),
        'Could not save that feedback',
    )


@router.get('/workouts')
def list_workouts(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    paging: tuple[int, int] = Depends(page_params),
    since: date | None = None,
) -> dict[str, Any]:
    limit, offset = paging
    total = connection.execute(
        'SELECT count(*) AS n FROM workout_log WHERE member_id = %s'
        ' AND (%s::date IS NULL OR log_date >= %s)',
        (identity.member_id, since, since),
    ).fetchone()['n']
    rows = connection.execute(
        'SELECT * FROM workout_log WHERE member_id = %s AND (%s::date IS NULL OR log_date >= %s)'
        ' ORDER BY log_date DESC, created_at DESC LIMIT %s OFFSET %s',
        (identity.member_id, since, since, limit, offset),
    ).fetchall()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.post('/workouts', status_code=201)
def log_workout(
    body: WorkoutLogIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return one(
        connection,
        """INSERT INTO workout_log (household_id, member_id, log_date, plan_id, exercise_key,
                                    name, sets, alternative_used, status, difficulty)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (
            settings.household_id, identity.member_id, body.log_date, body.plan_id,
            body.exercise_key, body.name, json.dumps(body.sets), body.alternative_used,
            body.status, body.difficulty,
        ),
        'Could not save that log',
    )


@router.post('/workouts/session-done', status_code=201)
def log_session(
    body: SessionDoneIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """One tap logs every exercise in that day's session exactly as prescribed."""
    plan = one(
        connection,
        "SELECT * FROM plan WHERE id = %s AND kind = 'fitness'",
        (body.plan_id,),
        'No such plan',
    )
    identity.require_owner(plan['owner_member_id'])
    session = next(
        (
            s
            for s in plan['content'].get('sessions', [])
            if s.get('date') == body.log_date.isoformat()
        ),
        None,
    )
    if session is None:
        raise bad_request(f'No session is planned for {body.log_date}')

    connection.execute(
        'DELETE FROM workout_log WHERE member_id = %s AND log_date = %s AND plan_id = %s',
        (identity.member_id, body.log_date, body.plan_id),
    )
    logged = []
    for exercise in session.get('exercises', []):
        sets = [
            {
                'reps': exercise.get('reps'),
                'weight_lb': exercise.get('weight_lb'),
                'duration_s': exercise.get('duration_s'),
                'distance_mi': exercise.get('distance_mi'),
                'as_prescribed': True,
            }
            for _ in range(exercise.get('sets') or 1)
        ]
        logged.append(
            one(
                connection,
                """INSERT INTO workout_log (household_id, member_id, log_date, plan_id,
                                            exercise_key, name, sets, status, difficulty)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    settings.household_id, identity.member_id, body.log_date, body.plan_id,
                    exercise.get('key', ''), exercise.get('name', ''), json.dumps(sets),
                    body.status, body.difficulty,
                ),
                'Could not save that log',
            )
        )
    return logged


@router.delete('/workouts/{log_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_workout(
    log_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> None:
    connection.execute(
        'DELETE FROM workout_log WHERE id = %s AND member_id = %s', (log_id, identity.member_id)
    )


@router.get('/activities')
def list_activities(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    paging: tuple[int, int] = Depends(page_params),
) -> dict[str, Any]:
    limit, offset = paging
    total = connection.execute(
        'SELECT count(*) AS n FROM activity WHERE member_id = %s', (identity.member_id,)
    ).fetchone()['n']
    rows = connection.execute(
        'SELECT * FROM activity WHERE member_id = %s ORDER BY activity_date DESC LIMIT %s OFFSET %s',
        (identity.member_id, limit, offset),
    ).fetchall()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.post('/activities', status_code=201)
def log_activity(
    body: ActivityIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return one(
        connection,
        """INSERT INTO activity (household_id, member_id, activity_date, type, duration_min,
                                 distance_mi, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (
            settings.household_id, identity.member_id, body.activity_date, body.type,
            body.duration_min, body.distance_mi, body.notes,
        ),
        'Could not save that activity',
    )


@router.get('/metrics')
def list_metrics(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    kind: Literal['weight', 'mood', 'energy'] | None = None,
    since: date | None = None,
    limit: int = Query(365, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return connection.execute(
        'SELECT * FROM metric WHERE member_id = %s AND (%s::text IS NULL OR kind = %s::metric_kind)'
        ' AND (%s::date IS NULL OR metric_date >= %s) ORDER BY metric_date DESC LIMIT %s',
        (identity.member_id, kind, kind, since, since, limit),
    ).fetchall()


@router.put('/metrics')
def set_metric(
    body: MetricIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return one(
        connection,
        """INSERT INTO metric (household_id, member_id, metric_date, kind, value, note)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (member_id, metric_date, kind) DO UPDATE
             SET value = EXCLUDED.value, note = EXCLUDED.note
           RETURNING *""",
        (
            settings.household_id, identity.member_id, body.metric_date, body.kind, body.value,
            body.note,
        ),
        'Could not save that entry',
    )
