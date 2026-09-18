"""Period setup: dates, per-day modes, shopping trips, training availability, and the
Friday checklist that opens the week (PRD 3)."""
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg import Connection
from pydantic import BaseModel, Field

from health.api.common import bad_request, one, page_params
from health.deps import get_connection, get_identity
from health.domain import periods as domain
from health.identity import Identity
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['periods'])


class DayIn(BaseModel):
    day: date
    dinner_mode: Literal['cook', 'leftovers', 'takeout', 'eating_out', 'away']
    lunch_mode: Literal['none', 'leftovers', 'prep'] = 'none'
    cook_time: Literal['quick', 'normal', 'project'] = 'normal'


class PeriodIn(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    notes: str = ''


class PeriodPatch(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None
    status: Literal['planning', 'active', 'closed'] | None = None


class TripIn(BaseModel):
    trip_date: date
    store: str | None = None


class AvailabilityIn(BaseModel):
    days: list[date] = Field(default_factory=list)
    travel: bool = False
    session_minutes: int = 45
    session_types: list[str] = Field(default_factory=list)


def _settings_value(connection: Connection, household_id: str, key: str, default: Any) -> Any:
    row = connection.execute(
        'SELECT value FROM setting WHERE household_id = %s AND key = %s', (household_id, key)
    ).fetchone()
    return row['value'] if row else default


@router.get('/periods')
def list_periods(
    connection: Connection = Depends(get_connection),
    paging: tuple[int, int] = Depends(page_params),
) -> dict[str, Any]:
    limit, offset = paging
    total = connection.execute('SELECT count(*) AS n FROM period').fetchone()['n']
    rows = connection.execute(
        'SELECT * FROM period ORDER BY start_date DESC LIMIT %s OFFSET %s', (limit, offset)
    ).fetchall()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/periods/current')
def current_period(
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any] | None:
    """The period covering today, else the newest one that is still being planned."""
    today = datetime.now(settings.tz).date()
    row = connection.execute(
        'SELECT * FROM period WHERE start_date <= %s AND end_date >= %s ORDER BY start_date DESC'
        ' LIMIT 1',
        (today, today),
    ).fetchone()
    if row:
        return row
    return connection.execute(
        'SELECT * FROM period ORDER BY start_date DESC LIMIT 1'
    ).fetchone()


@router.post('/periods', status_code=201)
def create_period(
    body: PeriodIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Create the next period with sensible defaults already filled in."""
    household_id = settings.household_id
    planning = _settings_value(connection, household_id, 'planning_defaults', {})
    today = datetime.now(settings.tz).date()
    previous = connection.execute(
        'SELECT end_date FROM period ORDER BY start_date DESC LIMIT 1'
    ).fetchone()

    start, end = domain.next_period_dates(
        previous['end_date'] if previous else None,
        today,
        int(planning.get('period_length_days', domain.DEFAULT_LENGTH_DAYS)),
    )
    start = body.start_date or start
    end = body.end_date or end
    length = (end - start).days + 1
    if not domain.MIN_LENGTH_DAYS <= length <= domain.MAX_LENGTH_DAYS:
        raise bad_request(
            f'A period is {domain.MIN_LENGTH_DAYS}-{domain.MAX_LENGTH_DAYS} days; that is {length}'
        )

    period = one(
        connection,
        'INSERT INTO period (household_id, start_date, end_date, notes) VALUES (%s, %s, %s, %s)'
        ' ON CONFLICT (household_id, start_date) DO NOTHING RETURNING *',
        (household_id, start, end, body.notes),
        f'A period already starts on {start}',
    )
    _fill_defaults(connection, household_id, period, planning, identity)
    return period


def _fill_defaults(
    connection: Connection,
    household_id: str,
    period: dict[str, Any],
    planning: dict[str, Any],
    identity: Identity,
) -> None:
    start, end = period['start_date'], period['end_date']
    for day in domain.period_days(start, end):
        connection.execute(
            """INSERT INTO period_day (household_id, period_id, day, dinner_mode, lunch_mode,
                                       cook_time)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (period_id, day) DO NOTHING""",
            (
                household_id,
                period['id'],
                day,
                planning.get('default_dinner_mode', 'cook'),
                planning.get('default_lunch_mode', 'none'),
                planning.get('default_cook_time', 'normal'),
            ),
        )
    for trip_date in domain.default_shopping_trips(start, end):
        connection.execute(
            'INSERT INTO shopping_trip (household_id, period_id, trip_date) VALUES (%s, %s, %s)'
            ' ON CONFLICT DO NOTHING',
            (household_id, period['id'], trip_date),
        )
    # Only the caller's own availability is seeded; the other adult sets their own.
    connection.execute(
        """INSERT INTO training_availability (household_id, period_id, member_id, days)
           VALUES (%s, %s, %s, %s) ON CONFLICT (period_id, member_id) DO NOTHING""",
        (household_id, period['id'], identity.member_id, domain.default_training_days(start, end)),
    )


@router.get('/periods/{period_id}')
def get_period(
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    period = one(connection, 'SELECT * FROM period WHERE id = %s', (period_id,), 'No such period')
    days = connection.execute(
        'SELECT day, dinner_mode, lunch_mode, cook_time FROM period_day WHERE period_id = %s'
        ' ORDER BY day',
        (period_id,),
    ).fetchall()
    trips = connection.execute(
        'SELECT id, trip_date, store FROM shopping_trip WHERE period_id = %s ORDER BY trip_date',
        (period_id,),
    ).fetchall()
    availability = connection.execute(
        'SELECT * FROM training_availability WHERE period_id = %s AND member_id = %s',
        (period_id, identity.member_id),
    ).fetchone()
    return {**period, 'days': days, 'trips': trips, 'availability': availability}


@router.patch('/periods/{period_id}')
def update_period(
    period_id: UUID,
    body: PeriodPatch,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise bad_request('Nothing to update')
    assignments = ', '.join(f'{name} = %s' for name in fields)
    period = one(
        connection,
        f'UPDATE period SET {assignments}, updated_at = now() WHERE id = %s RETURNING *',
        (*fields.values(), period_id),
        'No such period',
    )
    if 'start_date' in fields or 'end_date' in fields:
        # Days outside the new range go; days inside it that are missing are added.
        connection.execute(
            'DELETE FROM period_day WHERE period_id = %s AND (day < %s OR day > %s)',
            (period_id, period['start_date'], period['end_date']),
        )
        planning = _settings_value(connection, settings.household_id, 'planning_defaults', {})
        _fill_defaults(connection, settings.household_id, period, planning, identity)
    return period


@router.put('/periods/{period_id}/days')
def set_days(
    period_id: UUID,
    days: list[DayIn],
    connection: Connection = Depends(get_connection),
) -> list[dict[str, Any]]:
    for day in days:
        connection.execute(
            """UPDATE period_day SET dinner_mode = %s, lunch_mode = %s, cook_time = %s
                WHERE period_id = %s AND day = %s""",
            (day.dinner_mode, day.lunch_mode, day.cook_time, period_id, day.day),
        )
    return connection.execute(
        'SELECT day, dinner_mode, lunch_mode, cook_time FROM period_day WHERE period_id = %s'
        ' ORDER BY day',
        (period_id,),
    ).fetchall()


@router.post('/periods/{period_id}/trips', status_code=201)
def add_trip(
    period_id: UUID,
    trip: TripIn,
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return one(
        connection,
        'INSERT INTO shopping_trip (household_id, period_id, trip_date, store)'
        ' VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *',
        (settings.household_id, period_id, trip.trip_date, trip.store),
        'That trip already exists',
    )


@router.patch('/trips/{trip_id}')
def move_trip(
    trip_id: UUID, trip: TripIn, connection: Connection = Depends(get_connection)
) -> dict[str, Any]:
    return one(
        connection,
        'UPDATE shopping_trip SET trip_date = %s, store = %s WHERE id = %s RETURNING *',
        (trip.trip_date, trip.store, trip_id),
        'No such trip',
    )


@router.delete('/trips/{trip_id}', status_code=204)
def delete_trip(trip_id: UUID, connection: Connection = Depends(get_connection)) -> None:
    connection.execute('DELETE FROM shopping_trip WHERE id = %s', (trip_id,))


@router.put('/periods/{period_id}/availability')
def set_availability(
    period_id: UUID,
    body: AvailabilityIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Each adult sets their own. Weekends are never planned, so they are dropped here."""
    weekdays = [day for day in body.days if day.weekday() < 5]
    return one(
        connection,
        """INSERT INTO training_availability (household_id, period_id, member_id, days, travel,
                                              session_minutes, session_types)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (period_id, member_id) DO UPDATE
             SET days = EXCLUDED.days, travel = EXCLUDED.travel,
                 session_minutes = EXCLUDED.session_minutes,
                 session_types = EXCLUDED.session_types
           RETURNING *""",
        (
            settings.household_id, period_id, identity.member_id, weekdays, body.travel,
            body.session_minutes, body.session_types,
        ),
        'Could not save availability',
    )


@router.get('/checklist')
def checklist(
    period_id: UUID | None = Query(None),
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """What still has to happen before the next period can start (PRD 3)."""
    now = datetime.now(settings.tz)
    ending = connection.execute(
        'SELECT * FROM period WHERE end_date >= %s - 14 ORDER BY start_date DESC LIMIT 2',
        (now.date(),),
    ).fetchall()
    active = next((p for p in ending if p['start_date'] <= now.date() <= p['end_date']), None)
    upcoming = next((p for p in ending if p['start_date'] > now.date()), None)
    target = connection.execute(
        'SELECT * FROM period WHERE id = %s', (period_id,)
    ).fetchone() if period_id else (upcoming or active)

    items = []
    if active:
        meals_feedback = connection.execute(
            "SELECT 1 FROM period_feedback WHERE period_id = %s AND kind = 'meals'",
            (active['id'],),
        ).fetchone()
        fitness_feedback = connection.execute(
            "SELECT 1 FROM period_feedback WHERE period_id = %s AND kind = 'fitness'"
            ' AND member_id = %s',
            (active['id'], identity.member_id),
        ).fetchone()
        items.append(
            {'key': 'meal_feedback', 'label': 'Meal feedback for this period',
             'done': bool(meals_feedback), 'detail': ''}
        )
        items.append(
            {'key': 'fitness_feedback', 'label': 'Your training feedback',
             'done': bool(fitness_feedback), 'detail': ''}
        )

    if target:
        trips = connection.execute(
            'SELECT count(*) AS n FROM shopping_trip WHERE period_id = %s', (target['id'],)
        ).fetchone()['n']
        availability = connection.execute(
            'SELECT 1 FROM training_availability WHERE period_id = %s AND member_id = %s',
            (target['id'], identity.member_id),
        ).fetchone()
        plans = connection.execute(
            "SELECT kind, owner_member_id, status FROM plan WHERE period_id = %s",
            (target['id'],),
        ).fetchall()
        meals_accepted = any(
            p['kind'] == 'meals' and p['status'] == 'accepted' for p in plans
        )
        mine_accepted = any(
            p['kind'] == 'fitness' and p['status'] == 'accepted'
            and p['owner_member_id'] == identity.member_id
            for p in plans
        )
        items += [
            {'key': 'inputs', 'label': 'Next period set up',
             'done': trips > 0 and bool(availability),
             'detail': f'{trips} shopping trip(s)'},
            {'key': 'meals_accepted', 'label': 'Meal plan accepted', 'done': meals_accepted,
             'detail': ''},
            {'key': 'fitness_accepted', 'label': 'Your training plan accepted',
             'done': mine_accepted, 'detail': ''},
        ]

    show = domain.is_planning_window(now, active['end_date'] if active else None) and not all(
        item['done'] for item in items
    )
    return {
        'show': show,
        'now': now.isoformat(),
        'active_period': active,
        'target_period': target,
        'items': items,
    }
