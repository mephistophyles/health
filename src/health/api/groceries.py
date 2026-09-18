"""Grocery lists: one per shopping trip, tickable in the store on a phone."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from psycopg import Connection
from pydantic import BaseModel

from health.api.common import one
from health.deps import get_connection
from health.services import list_grocery_items, recompute_groceries
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['groceries'])


class ManualLine(BaseModel):
    name: str
    qty: float | None = None
    unit: str = ''
    category: str = 'Other'
    check_pantry: bool = False


class ItemPatch(BaseModel):
    checked: bool | None = None
    name: str | None = None
    qty: float | None = None
    unit: str | None = None
    category: str | None = None
    check_pantry: bool | None = None


@router.get('/periods/{period_id}/groceries')
def get_groceries(
    period_id: UUID, connection: Connection = Depends(get_connection)
) -> dict[str, Any]:
    """Grouped by trip, then by category, in the order the store is walked."""
    trips = connection.execute(
        'SELECT id, trip_date, store FROM shopping_trip WHERE period_id = %s ORDER BY trip_date',
        (period_id,),
    ).fetchall()
    items = list_grocery_items(connection, period_id)
    by_trip: dict[UUID, list[dict[str, Any]]] = {trip['id']: [] for trip in trips}
    for item in items:
        by_trip.setdefault(item['trip_id'], []).append(item)
    return {
        'trips': [
            {**trip, 'items': by_trip.get(trip['id'], []),
             'remaining': sum(1 for i in by_trip.get(trip['id'], []) if not i['checked'])}
            for trip in trips
        ]
    }


@router.post('/periods/{period_id}/groceries/recompute')
def recompute(
    period_id: UUID,
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    recompute_groceries(connection, settings.household_id, period_id)
    return get_groceries(period_id, connection)


@router.patch('/grocery-items/{item_id}')
def update_item(
    item_id: UUID, patch: ItemPatch, connection: Connection = Depends(get_connection)
) -> dict[str, Any]:
    """Ticking is shared: both phones see the same list on refresh."""
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        return one(
            connection, 'SELECT * FROM grocery_item WHERE id = %s', (item_id,), 'No such item'
        )
    assignments = ', '.join(f'{name} = %s' for name in fields)
    return one(
        connection,
        f'UPDATE grocery_item SET {assignments}, updated_at = now() WHERE id = %s RETURNING *',
        (*fields.values(), item_id),
        'No such item',
    )


@router.post('/trips/{trip_id}/items', status_code=201)
def add_manual_line(
    trip_id: UUID,
    line: ManualLine,
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """A manual line survives every recompute; it traces back to a person, not a recipe."""
    return one(
        connection,
        """INSERT INTO grocery_item (household_id, trip_id, name, qty, unit, category,
                                     check_pantry, manual, line_key, sort_order)
           VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, 9000) RETURNING *""",
        (
            settings.household_id, trip_id, line.name, line.qty, line.unit, line.category,
            line.check_pantry, f'manual|{line.name.lower()}|{line.unit.lower()}',
        ),
        'Could not add that line',
    )


@router.delete('/grocery-items/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: UUID, connection: Connection = Depends(get_connection)) -> None:
    """Only manual lines can be deleted; computed lines come back on the next recompute."""
    connection.execute('DELETE FROM grocery_item WHERE id = %s AND manual', (item_id,))
