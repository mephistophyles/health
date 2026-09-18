from collections.abc import Generator

from fastapi import Depends, Request
from psycopg import Connection

from health.database import Database
from health.identity import Identity, auth_user_from_request
from health.settings import Settings, get_settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_identity(
    request: Request,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
) -> Identity:
    cached = getattr(request.state, 'identity', None)
    if cached is not None:
        return cached
    auth_user = auth_user_from_request(request, settings.auth_mode, settings.single_user_id)
    identity = database.resolve_identity(auth_user)
    request.state.identity = identity
    return identity


def get_connection(
    database: Database = Depends(get_database),
    identity: Identity = Depends(get_identity),
) -> Generator[Connection, None, None]:
    with database.request_connection(identity) as connection:
        yield connection
