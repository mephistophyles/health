from typing import Any
from uuid import UUID

from fastapi import HTTPException, Query, status
from psycopg import Connection
from pydantic import BaseModel

MAX_PAGE = 200


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


def page_params(
    limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0)
) -> tuple[int, int]:
    """Every list endpoint paginates, however small the household is."""
    return limit, offset


def one(connection: Connection, sql: str, params: tuple, missing: str) -> dict[str, Any]:
    row = connection.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, missing)
    return row


def member_by_slug(connection: Connection, household_id: str, slug: str) -> UUID:
    return one(
        connection,
        'SELECT id FROM member WHERE household_id = %s AND slug = %s',
        (household_id, slug),
        f'No member {slug!r}',
    )['id']


def bad_request(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)
