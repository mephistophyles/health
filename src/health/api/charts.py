"""JSON endpoints behind the progress views (PRD 9).

Each returns plain points; the front end draws them. Private charts are scoped to the
caller by row security, so there is nothing to filter here.
"""
from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from health.deps import get_connection, get_identity
from health.identity import Identity

router = APIRouter(prefix='/api/charts', tags=['charts'])

MIN_POINTS = 3


def _series(points: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """Under three points there is no trend to draw, so the UI shows a list instead."""
    return {'points': points, 'enough_for_chart': len(points) >= MIN_POINTS, **extra}


@router.get('/weight')
def weight(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    days: int = Query(365, ge=7, le=3650),
) -> dict[str, Any]:
    points = connection.execute(
        """SELECT metric_date AS date, value,
                  round(avg(value) OVER (ORDER BY metric_date ROWS BETWEEN 6 PRECEDING
                                         AND CURRENT ROW), 2) AS rolling_7
             FROM metric
            WHERE member_id = %s AND kind = 'weight'
              AND metric_date >= CURRENT_DATE - make_interval(days => %s)
            ORDER BY metric_date""",
        (identity.member_id, days),
    ).fetchall()
    goal = connection.execute(
        """SELECT target FROM goal WHERE member_id = %s AND kind = 'fitness' AND active_to IS NULL
            ORDER BY active_from DESC LIMIT 1""",
        (identity.member_id,),
    ).fetchone()
    return _series(points, goal=goal['target'] if goal else None, unit='lb')


@router.get('/mood')
def mood(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    weeks: int = Query(52, ge=4, le=260),
) -> dict[str, Any]:
    points = connection.execute(
        """SELECT date_trunc('week', metric_date)::date AS date, kind,
                  round(avg(value), 2) AS value
             FROM metric
            WHERE member_id = %s AND kind IN ('mood', 'energy')
              AND metric_date >= CURRENT_DATE - make_interval(weeks => %s)
            GROUP BY 1, 2 ORDER BY 1""",
        (identity.member_id, weeks),
    ).fetchall()
    return _series(points)


@router.get('/adherence')
def adherence(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    limit: int = Query(12, ge=1, le=52),
) -> dict[str, Any]:
    """Sessions planned against sessions with any log or a completed mark, per period."""
    points = connection.execute(
        """SELECT p.start_date AS date,
                  jsonb_array_length(COALESCE(pl.content->'sessions', '[]'::jsonb)) AS planned,
                  (SELECT count(DISTINCT w.log_date) FROM workout_log w
                    WHERE w.member_id = %s AND w.status <> 'skipped'
                      AND w.log_date BETWEEN p.start_date AND p.end_date) AS completed
             FROM period p
             JOIN plan pl ON pl.period_id = p.id AND pl.kind = 'fitness'
                         AND pl.status = 'accepted' AND pl.owner_member_id = %s
            ORDER BY p.start_date DESC LIMIT %s""",
        (identity.member_id, identity.member_id, limit),
    ).fetchall()
    return _series(list(reversed(points)))


@router.get('/strength')
def strength(
    exercise: str = Query(..., min_length=1),
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Top set and an Epley estimate of a one-rep max, per session, for one exercise."""
    points = connection.execute(
        """SELECT log_date AS date,
                  max((s->>'weight_lb')::numeric) AS top_weight,
                  round(max((s->>'weight_lb')::numeric
                        * (1 + COALESCE((s->>'reps')::numeric, 1) / 30)), 1) AS estimated_1rm
             FROM workout_log w, jsonb_array_elements(w.sets) AS s
            WHERE w.member_id = %s AND w.name ILIKE %s AND w.status <> 'skipped'
              AND s->>'weight_lb' IS NOT NULL
            GROUP BY log_date ORDER BY log_date""",
        (identity.member_id, f'%{exercise}%'),
    ).fetchall()
    return _series(points, exercise=exercise, unit='lb')


@router.get('/strength/exercises')
def strength_exercises(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> list[dict[str, Any]]:
    return connection.execute(
        """SELECT name, count(*) AS sessions FROM workout_log
            WHERE member_id = %s GROUP BY name HAVING count(*) >= 2
            ORDER BY count(*) DESC, name LIMIT 50""",
        (identity.member_id,),
    ).fetchall()


@router.get('/running')
def running(
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    weeks: int = Query(52, ge=4, le=260),
) -> dict[str, Any]:
    weekly = connection.execute(
        """SELECT date_trunc('week', activity_date)::date AS date,
                  round(sum(distance_mi), 2) AS distance_mi
             FROM activity
            WHERE member_id = %s AND type ILIKE 'run%%' AND distance_mi IS NOT NULL
              AND activity_date >= CURRENT_DATE - make_interval(weeks => %s)
            GROUP BY 1 ORDER BY 1""",
        (identity.member_id, weeks),
    ).fetchall()
    runs = connection.execute(
        """SELECT activity_date AS date, distance_mi, duration_min,
                  CASE WHEN distance_mi > 0 THEN round(duration_min / distance_mi, 2) END
                    AS pace_min_per_mi
             FROM activity
            WHERE member_id = %s AND type ILIKE 'run%%' AND distance_mi IS NOT NULL
              AND duration_min IS NOT NULL
              AND activity_date >= CURRENT_DATE - make_interval(weeks => %s)
            ORDER BY activity_date""",
        (identity.member_id, weeks),
    ).fetchall()
    return {**_series(weekly), 'runs': runs}


@router.get('/meal-enjoyment')
def meal_enjoyment(
    connection: Connection = Depends(get_connection), limit: int = Query(12, ge=1, le=52)
) -> dict[str, Any]:
    points = connection.execute(
        """SELECT p.start_date AS date, m.slug, m.name, round(avg(f.enjoyment), 2) AS value
             FROM meal_feedback f
             JOIN plan_meal pm ON pm.id = f.plan_meal_id
             JOIN plan pl ON pl.id = pm.plan_id AND pl.status = 'accepted'
             JOIN period p ON p.id = pl.period_id
             JOIN member m ON m.id = f.member_id
            WHERE f.enjoyment IS NOT NULL
            GROUP BY p.start_date, m.slug, m.name
            ORDER BY p.start_date DESC LIMIT %s""",
        (limit * 4,),
    ).fetchall()
    return _series(list(reversed(points)))


@router.get('/kid-acceptance')
def kid_acceptance(
    connection: Connection = Depends(get_connection), limit: int = Query(12, ge=1, le=52)
) -> dict[str, Any]:
    points = connection.execute(
        """SELECT p.start_date AS date, m.slug, m.name,
                  round(100.0 * count(*) FILTER (WHERE f.ate = 'yes') / NULLIF(count(f.ate), 0), 0)
                    AS ate_rate
             FROM meal_feedback f
             JOIN plan_meal pm ON pm.id = f.plan_meal_id
             JOIN plan pl ON pl.id = pm.plan_id AND pl.status = 'accepted'
             JOIN period p ON p.id = pl.period_id
             JOIN member m ON m.id = f.member_id AND m.kind = 'child'
            GROUP BY p.start_date, m.slug, m.name
            ORDER BY p.start_date DESC LIMIT %s""",
        (limit * 2,),
    ).fetchall()
    return _series(list(reversed(points)))


@router.get('/cooking')
def cooking(
    connection: Connection = Depends(get_connection), limit: int = Query(12, ge=1, le=52)
) -> dict[str, Any]:
    points = connection.execute(
        """SELECT p.start_date AS date,
                  count(*) FILTER (WHERE pm.mode = 'cook') AS planned,
                  count(*) FILTER (WHERE pm.mode = 'cook' AND NOT pm.skipped) AS cooked
             FROM plan_meal pm
             JOIN plan pl ON pl.id = pm.plan_id AND pl.status = 'accepted' AND pl.kind = 'meals'
             JOIN period p ON p.id = pl.period_id
            WHERE pm.meal = 'dinner'
            GROUP BY p.start_date ORDER BY p.start_date DESC LIMIT %s""",
        (limit,),
    ).fetchall()
    return _series(list(reversed(points)))
