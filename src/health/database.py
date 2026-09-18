from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from fastapi import HTTPException, status
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from health.identity import Identity
from health.settings import Settings


class Database:
    """Connection pool that stamps every transaction with its row-security context.

    Nothing reads or writes without going through one of these context managers, so
    `app.household_id` and `app.member_id` are always set before the first statement.
    """

    def __init__(self, settings: Settings) -> None:
        self.pool = ConnectionPool(str(settings.database_url), open=False, min_size=1, max_size=8)
        self.household_id = settings.household_id

    def open(self) -> None:
        self.pool.open()

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def _scoped(
        self, member_id: UUID | None, worker: bool = False
    ) -> Generator[Connection, None, None]:
        with self.pool.connection() as connection, connection.transaction():
            connection.row_factory = dict_row
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.household_id', %s, true),"
                    "       set_config('app.member_id', %s, true),"
                    "       set_config('app.worker', %s, true)",
                    (self.household_id, str(member_id) if member_id else '', 'on' if worker else ''),
                )
            yield connection

    @contextmanager
    def request_connection(self, identity: Identity) -> Generator[Connection, None, None]:
        """A connection scoped to the caller. Private rows of other members are invisible."""
        with self._scoped(identity.member_id) as connection:
            yield connection

    @contextmanager
    def worker_connection(self, member_id: UUID | None = None) -> Generator[Connection, None, None]:
        """A connection for the background worker, scoped to the member a job runs for."""
        with self._scoped(member_id) as connection:
            yield connection

    @contextmanager
    def queue_connection(self) -> Generator[Connection, None, None]:
        """Sees the whole job queue, including another member's private fitness job.

        Only the queue reader gets this. It reads job rows, never the private data a job
        goes on to touch, which is read through a member-scoped connection instead.
        """
        with self._scoped(None, worker=True) as connection:
            yield connection

    def resolve_identity(self, auth_user: str) -> Identity:
        with self._scoped(None) as connection:
            row = connection.execute(
                'SELECT u.auth_user, u.display_name, u.is_admin, m.id AS member_id, m.slug'
                '  FROM app_user u JOIN member m ON m.id = u.member_id'
                ' WHERE u.auth_user = %s',
                (auth_user,),
            ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, 'Unknown identity')
        return Identity(
            auth_user=row['auth_user'],
            member_id=row['member_id'],
            member_slug=row['slug'],
            display_name=row['display_name'],
            is_admin=row['is_admin'],
        )
