"""Assembling the context each loop is given (PRD 11).

Two rules shape this module. The meal loop sees the household, but of each adult's
fitness it sees only the derived training summary. Each fitness loop sees one adult and
nothing of the other.
"""
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection

from health.domain.library import (
    DIDNT_WORK_LIMIT,
    WORKED_LIMIT,
    DigestEntry,
    LibraryDigest,
)
from health.domain.validation import COOK_TIME_LIMITS, FitnessContext, MealContext


def settings_map(connection: Connection, household_id: str) -> dict[str, Any]:
    rows = connection.execute(
        'SELECT key, value FROM setting WHERE household_id = %s', (household_id,)
    ).fetchall()
    return {row['key']: row['value'] for row in rows}


def get_period(connection: Connection, period_id: UUID) -> dict[str, Any]:
    row = connection.execute('SELECT * FROM period WHERE id = %s', (period_id,)).fetchone()
    if row is None:
        raise ValueError('period not found')
    return row


def previous_period(connection: Connection, period: dict[str, Any]) -> dict[str, Any] | None:
    return connection.execute(
        'SELECT * FROM period WHERE start_date < %s ORDER BY start_date DESC LIMIT 1',
        (period['start_date'],),
    ).fetchone()


def day_rows(connection: Connection, period_id: UUID) -> list[dict[str, Any]]:
    return connection.execute(
        'SELECT day, dinner_mode, lunch_mode, cook_time FROM period_day'
        ' WHERE period_id = %s ORDER BY day',
        (period_id,),
    ).fetchall()


def members_for_meals(
    connection: Connection, household_id: str, today: date | None = None
) -> list[dict[str, Any]]:
    """Profiles for the meal loop: shared fields plus meal goals. No fitness goals."""
    today = today or datetime.now(UTC).date()
    rows = connection.execute(
        """SELECT m.slug, m.name, m.kind, m.birth_month, m.notes, m.presentation_rules,
                  m.likes, m.dislikes, m.exclusions,
                  (SELECT g.statement FROM goal g
                    WHERE g.member_id = m.id AND g.kind = 'meal' AND g.active_to IS NULL
                    ORDER BY g.active_from DESC LIMIT 1) AS meal_goal
             FROM member m WHERE m.household_id = %s ORDER BY m.sort_order, m.name""",
        (household_id,),
    ).fetchall()
    members = []
    for row in rows:
        member = {
            'slug': row['slug'],
            'name': row['name'],
            'kind': row['kind'],
            'notes': row['notes'],
            'likes': row['likes'],
            'dislikes': row['dislikes'],
            'exclusions': row['exclusions'],
            'presentation_rules': row['presentation_rules'],
        }
        if row['birth_month']:
            months = (today.year - row['birth_month'].year) * 12 + (
                today.month - row['birth_month'].month
            )
            member['age'] = f'{months // 12}y' if months >= 24 else f'{months} months'
        if row['meal_goal']:
            member['goal'] = row['meal_goal']
        members.append(member)
    return members


def _training_summaries(connection: Connection, period_id: UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT m.slug, m.name, t.summary FROM training_summary t
             JOIN member m ON m.id = t.member_id
            WHERE t.period_id = %s ORDER BY m.sort_order""",
        (period_id,),
    ).fetchall()
    return [
        {'member': row['slug'], 'name': row['name'], **(row['summary'] or {})} for row in rows
    ]


def _season(connection: Connection, household_id: str, start: date, end: date) -> dict[int, list]:
    months = sorted({start.month, end.month, ((start.month % 12) + 1) if start.month != end.month else start.month})
    rows = connection.execute(
        'SELECT month, items FROM seasonal_produce WHERE household_id = %s AND month = ANY(%s)',
        (household_id, months),
    ).fetchall()
    return {row['month']: row['items'] for row in rows}


def recent_dinner_titles(
    connection: Connection, household_id: str, before: date, weeks: int
) -> list[str]:
    """Dinners served inside the repeat window, so the loop does not serve them again."""
    rows = connection.execute(
        """SELECT DISTINCT r.title FROM plan_meal pm
             JOIN plan p ON p.id = pm.plan_id AND p.status = 'accepted' AND p.kind = 'meals'
             JOIN recipe r ON r.id = pm.recipe_id
            WHERE pm.household_id = %s AND pm.meal = 'dinner' AND NOT pm.skipped
              AND pm.day >= %s AND pm.day < %s""",
        (household_id, before - timedelta(weeks=weeks), before),
    ).fetchall()
    return [row['title'] for row in rows]


def active_rejections(connection: Connection, household_id: str, kind: str) -> list[dict[str, str]]:
    rows = connection.execute(
        """SELECT summary, reason FROM rejection
            WHERE household_id = %s AND kind = %s AND NOT cleared AND expires_at >= CURRENT_DATE
            ORDER BY created_at DESC LIMIT 100""",
        (household_id, kind),
    ).fetchall()
    return [{'summary': row['summary'], 'reason': row['reason']} for row in rows]


def library_digest(connection: Connection, household_id: str, recent: list[str]) -> LibraryDigest:
    """Staples, the best of what worked, and recent failures — never the whole library."""
    rows = connection.execute(
        """SELECT r.id, r.title, r.tags, r.staple, r.tweak_notes, r.last_served,
                  COALESCE(r.verdict_override, r.verdict) AS verdict,
                  (SELECT AVG(f.enjoyment) FROM meal_feedback f
                     JOIN plan_meal pm ON pm.id = f.plan_meal_id
                    WHERE pm.recipe_id = r.id) AS average_enjoyment
             FROM recipe r
            WHERE r.household_id = %s AND NOT r.archived""",
        (household_id,),
    ).fetchall()

    def entry(row) -> DigestEntry:
        return DigestEntry(
            title=row['title'],
            tags=list(row['tags'] or []),
            average_enjoyment=round(float(row['average_enjoyment']), 2)
            if row['average_enjoyment'] is not None
            else None,
            last_served=row['last_served'].isoformat() if row['last_served'] else None,
            tweak_notes=row['tweak_notes'],
        )

    staples = [entry(r) for r in rows if r['staple'] or r['verdict'] == 'staple']
    worked = sorted(
        (entry(r) for r in rows if r['verdict'] == 'worked'),
        key=lambda e: (-(e.average_enjoyment or 0), e.title),
    )[:WORKED_LIMIT]
    didnt = sorted(
        (entry(r) for r in rows if r['verdict'] == 'didnt_work'),
        key=lambda e: (e.last_served or '', e.title),
        reverse=True,
    )[:DIDNT_WORK_LIMIT]
    return LibraryDigest(
        staples=staples,
        worked=worked,
        didnt_work=didnt,
        recent_dinners=sorted(recent),
        rejections=[
            DigestEntry(title=r['summary'], reason=r['reason'])
            for r in active_rejections(connection, household_id, 'meals')
        ],
    )


def meal_feedback(connection: Connection, period_id: UUID | None) -> dict[str, Any]:
    if period_id is None:
        return {}
    period = connection.execute(
        "SELECT fields FROM period_feedback WHERE period_id = %s AND kind = 'meals'",
        (period_id,),
    ).fetchone()
    meals = connection.execute(
        """SELECT pm.day, pm.meal, r.title,
                  jsonb_agg(jsonb_build_object('member', m.slug, 'enjoyment', f.enjoyment,
                    'portion', f.portion, 'ate', f.ate, 'presentation', f.presentation_note))
                    FILTER (WHERE f.id IS NOT NULL) AS per_person,
                  mr.effort, mr.repeat_choice, mr.notes
             FROM plan_meal pm
             JOIN plan p ON p.id = pm.plan_id AND p.status = 'accepted' AND p.kind = 'meals'
             LEFT JOIN recipe r ON r.id = pm.recipe_id
             LEFT JOIN meal_feedback f ON f.plan_meal_id = pm.id
             LEFT JOIN member m ON m.id = f.member_id
             LEFT JOIN meal_review mr ON mr.plan_meal_id = pm.id
            WHERE p.period_id = %s
            GROUP BY pm.day, pm.meal, r.title, mr.effort, mr.repeat_choice, mr.notes
            ORDER BY pm.day""",
        (period_id,),
    ).fetchall()
    return {
        'period': period['fields'] if period else {},
        'meals': [
            {k: v for k, v in row.items() if v not in (None, [])}
            for row in meals
            if row['per_person'] or row['repeat_choice'] or row['notes']
        ],
    }


def meal_generation_context(
    connection: Connection, household_id: str, period_id: UUID
) -> dict[str, Any]:
    period = get_period(connection, period_id)
    previous = previous_period(connection, period)
    config = settings_map(connection, household_id)
    planning = config.get('planning_defaults', {})
    repeat_weeks = int(planning.get('repeat_weeks', 3))

    days = [
        {
            'date': row['day'].isoformat(),
            'weekday': row['day'].strftime('%a'),
            'dinner': row['dinner_mode'],
            'lunch': row['lunch_mode'],
            'cooking_time': row['cook_time'],
        }
        for row in day_rows(connection, period_id)
    ]
    trips = connection.execute(
        'SELECT trip_date, store FROM shopping_trip WHERE period_id = %s ORDER BY trip_date',
        (period_id,),
    ).fetchall()

    last_plan = None
    if previous:
        row = connection.execute(
            "SELECT content FROM plan WHERE period_id = %s AND kind = 'meals' AND status = 'accepted'",
            (previous['id'],),
        ).fetchone()
        last_plan = row['content'] if row else None

    recent = recent_dinner_titles(connection, household_id, period['start_date'], repeat_weeks)
    digest = library_digest(connection, household_id, recent)

    return {
        'period': {
            'start': period['start_date'].isoformat(),
            'end': period['end_date'].isoformat(),
            'notes': period['notes'],
            'days': days,
        },
        'shopping_trips': [
            {'date': row['trip_date'].isoformat(), 'store': row['store'] or ''} for row in trips
        ],
        'members': members_for_meals(connection, household_id),
        'training_summaries': _training_summaries(connection, period_id),
        'last_plan': last_plan,
        'feedback': meal_feedback(connection, previous['id'] if previous else None),
        'library_digest': digest.as_dict(),
        'rejections': digest.as_dict()['rejections'],
        'recent_dinners': recent,
        'season': _season(connection, household_id, period['start_date'], period['end_date']),
        'staples': config.get('staples', []),
    }


def meal_validation_context(
    connection: Connection, household_id: str, period_id: UUID
) -> MealContext:
    period = get_period(connection, period_id)
    config = settings_map(connection, household_id)
    planning = config.get('planning_defaults', {})
    rows = day_rows(connection, period_id)

    def norm(title: str) -> str:
        return ' '.join(title.lower().split())

    staples = connection.execute(
        "SELECT title FROM recipe WHERE household_id = %s"
        " AND (staple OR COALESCE(verdict_override, verdict) = 'staple')",
        (household_id,),
    ).fetchall()
    recent = recent_dinner_titles(
        connection, household_id, period['start_date'], int(planning.get('repeat_weeks', 3))
    )
    slugs = connection.execute(
        'SELECT slug FROM member WHERE household_id = %s', (household_id,)
    ).fetchall()

    return MealContext(
        start=period['start_date'],
        end=period['end_date'],
        day_modes={row['day']: row['dinner_mode'] for row in rows},
        lunch_modes={row['day']: row['lunch_mode'] for row in rows},
        cook_times={row['day']: row['cook_time'] for row in rows},
        recent_dinner_titles={norm(t) for t in recent},
        rejected_titles={
            norm(r['summary']) for r in active_rejections(connection, household_id, 'meals')
        },
        staple_titles={norm(row['title']) for row in staples},
        member_slugs={row['slug'] for row in slugs},
        cook_time_limits=config.get('cook_time_limits', dict(COOK_TIME_LIMITS)),
    )


def _availability(connection: Connection, period_id: UUID, member_id: UUID) -> dict[str, Any]:
    row = connection.execute(
        'SELECT days, travel, session_minutes, session_types FROM training_availability'
        ' WHERE period_id = %s AND member_id = %s',
        (period_id, member_id),
    ).fetchone()
    if row is None:
        return {'days': [], 'travel': False, 'session_minutes': 45, 'session_types': []}
    return dict(row)


def _weight_trend(connection: Connection, member_id: UUID) -> str:
    """A four-week direction only. Raw metrics never leave this function."""
    rows = connection.execute(
        """SELECT metric_date, value FROM metric
            WHERE member_id = %s AND kind = 'weight' AND metric_date >= CURRENT_DATE - 28
            ORDER BY metric_date""",
        (member_id,),
    ).fetchall()
    if len(rows) < 2:
        return 'not enough entries to show a trend'
    change = float(rows[-1]['value']) - float(rows[0]['value'])
    if abs(change) < 1:
        return 'steady over four weeks'
    return f'{"down" if change < 0 else "up"} {abs(change):.1f} lb over four weeks'


def fitness_generation_context(
    connection: Connection, household_id: str, period_id: UUID, member_id: UUID
) -> dict[str, Any]:
    period = get_period(connection, period_id)
    previous = previous_period(connection, period)
    availability = _availability(connection, period_id, member_id)

    goal = connection.execute(
        """SELECT statement, target, target_date, constraints FROM goal
            WHERE member_id = %s AND kind = 'fitness' AND active_to IS NULL
            ORDER BY active_from DESC LIMIT 1""",
        (member_id,),
    ).fetchone()
    profile_name = 'travel' if availability['travel'] else 'home'
    equipment = connection.execute(
        'SELECT name, items, notes FROM equipment_profile WHERE member_id = %s AND name = %s',
        (member_id, profile_name),
    ).fetchone()

    last_plan = None
    if previous:
        row = connection.execute(
            "SELECT content FROM plan WHERE period_id = %s AND kind = 'fitness'"
            " AND owner_member_id = %s AND status = 'accepted'",
            (previous['id'], member_id),
        ).fetchone()
        last_plan = row['content'] if row else None

    logs = connection.execute(
        """SELECT log_date, exercise_key, name, sets, alternative_used, status, difficulty
             FROM workout_log WHERE member_id = %s AND log_date >= %s ORDER BY log_date""",
        (member_id, period['start_date'] - timedelta(days=28)),
    ).fetchall()
    activities = connection.execute(
        """SELECT activity_date, type, duration_min, distance_mi FROM activity
            WHERE member_id = %s AND activity_date >= %s ORDER BY activity_date""",
        (member_id, period['start_date'] - timedelta(days=28)),
    ).fetchall()
    period_fb = None
    if previous:
        row = connection.execute(
            "SELECT fields FROM period_feedback WHERE period_id = %s AND kind = 'fitness'"
            ' AND member_id = %s',
            (previous['id'], member_id),
        ).fetchone()
        period_fb = row['fields'] if row else None

    return {
        'period': {
            'start': period['start_date'].isoformat(),
            'end': period['end_date'].isoformat(),
            'notes': period['notes'],
        },
        'available_days': [d.isoformat() for d in availability['days']],
        'travel': availability['travel'],
        'session_minutes': availability['session_minutes'],
        'session_types': availability['session_types'],
        'goal': dict(goal) if goal else {'statement': 'No goal set yet.', 'constraints': ''},
        'equipment_profile': dict(equipment) if equipment else {'name': profile_name, 'items': []},
        'last_plan': last_plan,
        'feedback': {
            'period': period_fb or {},
            'logs': [dict(row) for row in logs],
            'activities': [dict(row) for row in activities],
            'has_logs': bool(logs),
        },
        'recent_metrics_trend': _weight_trend(connection, member_id),
    }


def fitness_validation_context(
    connection: Connection, period_id: UUID, member_id: UUID
) -> FitnessContext:
    period = get_period(connection, period_id)
    availability = _availability(connection, period_id, member_id)
    return FitnessContext(
        start=period['start_date'],
        end=period['end_date'],
        available_days=set(availability['days']),
    )


def as_json(value: Any) -> str:
    return json.dumps(value, default=str, indent=2)
