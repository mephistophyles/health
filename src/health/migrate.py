"""Apply each numbered SQL migration exactly once, before the new container starts."""
import os
from pathlib import Path

from psycopg import connect
from psycopg.rows import dict_row

from health import seed
from health.settings import get_settings

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS app_schema_migrations (
  filename TEXT PRIMARY KEY,
  household_id UUID NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

POLICY = """
DO $$ BEGIN
  CREATE POLICY migration_scope ON app_schema_migrations
    USING (household_id = current_setting('app.household_id', true)::uuid)
    WITH CHECK (household_id = current_setting('app.household_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN NULL; END $$
"""


def main() -> None:
    settings = get_settings()
    directory = Path(os.environ.get('MIGRATIONS_PATH', settings.migrations_path))
    migrations = sorted(p for p in directory.glob('[0-9]*.sql') if not p.name.endswith('.down.sql'))
    with connect(str(settings.database_url), autocommit=False, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(BOOTSTRAP)
            cursor.execute('ALTER TABLE app_schema_migrations ENABLE ROW LEVEL SECURITY')
            cursor.execute(POLICY)
            cursor.execute(
                "SELECT set_config('app.household_id', %s, true)", (settings.household_id,)
            )
            for migration in migrations:
                cursor.execute(
                    'SELECT 1 FROM app_schema_migrations WHERE filename = %s', (migration.name,)
                )
                if cursor.fetchone():
                    continue
                print(f'applying {migration.name}', flush=True)
                cursor.execute(migration.read_text())
                cursor.execute(
                    'INSERT INTO app_schema_migrations (filename, household_id) VALUES (%s, %s)',
                    (migration.name, settings.household_id),
                )
        seed.run(connection, settings.household_id)
        connection.commit()
    print('migrations up to date', flush=True)


if __name__ == '__main__':
    main()
