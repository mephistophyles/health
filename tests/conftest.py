"""Database-backed fixtures.

These tests need a real Postgres because what they prove — row security, the accept
pipeline, grocery recomputation — lives in the database, not in Python. Set
`TEST_DATABASE_URL` to run them; without it they skip.
"""
import os
import uuid
from pathlib import Path
from tempfile import mkdtemp

import pytest

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL')
HOUSEHOLD_ID = '11111111-1111-1111-1111-111111111111'
ADULT_ONE = 'one@example.com'
ADULT_TWO = 'two@example.com'


def _fake_web_dist() -> Path:
    """A stand-in for the built frontend, so the startup asset check passes."""
    root = Path(mkdtemp())
    (root / 'assets').mkdir()
    (root / 'index.html').write_text('<!doctype html><title>Health</title>')
    return root


# Set before anything imports health.main, which resolves these at import time.
os.environ.setdefault('WEB_DIST_PATH', str(_fake_web_dist()))
os.environ.setdefault('HOUSEHOLD_ID', HOUSEHOLD_ID)
os.environ.setdefault('AUTH_MODE', 'proxy')
os.environ.setdefault('DATABASE_URL', TEST_DATABASE_URL or 'postgresql://unused/unused')
os.environ['JOB_WORKER_ENABLED'] = 'false'

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations'


@pytest.fixture(scope='session')
def database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip('TEST_DATABASE_URL is not set')
    return TEST_DATABASE_URL


@pytest.fixture
def schema(database_url, monkeypatch):
    """A throwaway schema per test, so nothing leaks between them."""
    import psycopg

    from health.settings import get_settings

    name = f'test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA {name}')

    url = f'{database_url}?options=-csearch_path%3D{name}'
    with psycopg.connect(url) as connection:
        for path in sorted(
            p for p in MIGRATIONS.glob('[0-9]*.sql') if not p.name.endswith('.down.sql')
        ):
            connection.execute(path.read_text())
        # A non-owner role, because a table's owner bypasses its own row-security policies.
        # This is what proves the policies actually refuse rows rather than merely existing.
        connection.execute(
            "DO $$ BEGIN CREATE ROLE health_rls_probe NOLOGIN;"
            ' EXCEPTION WHEN duplicate_object THEN NULL; END $$'
        )
        connection.execute(f'GRANT USAGE ON SCHEMA {name} TO health_rls_probe')
        connection.execute(
            f'GRANT SELECT ON ALL TABLES IN SCHEMA {name} TO health_rls_probe'
        )
        connection.commit()

    monkeypatch.setenv('DATABASE_URL', url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA {name} CASCADE')


@pytest.fixture
def app_settings(schema):
    from health.settings import get_settings

    return get_settings()


@pytest.fixture
def database(app_settings):
    from health.database import Database

    db = Database(app_settings)
    db.open()
    yield db
    db.close()


@pytest.fixture
def household(database, monkeypatch):
    """Two adults with logins, two children, plus settings, produce and seed prompts."""
    from health import seed

    monkeypatch.setenv(
        'SEED_USERS',
        f'[{{"auth_user": "{ADULT_ONE}", "display_name": "Adult one", "member": "adult-1",'
        f' "is_admin": true}},'
        f' {{"auth_user": "{ADULT_TWO}", "display_name": "Adult two", "member": "adult-2"}}]',
    )
    with database.worker_connection() as connection:
        seed.run(connection, HOUSEHOLD_ID)
    with database.worker_connection() as connection:
        rows = connection.execute('SELECT id, slug FROM member').fetchall()
    return {row['slug']: row['id'] for row in rows}


@pytest.fixture
def client(household, database):
    """A TestClient factory: `client(ADULT_TWO)` makes requests as that person.

    The app's lifespan is bypassed so tests share the fixture's pool and the background
    worker never starts — no test should be able to make an outbound model call.
    """
    from fastapi.testclient import TestClient

    from health import main

    main.app.state.database = database

    def _as(auth_user: str) -> TestClient:
        return TestClient(main.app, headers={'X-Auth-User': auth_user})

    return _as
