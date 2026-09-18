"""Create the household's starting rows. Idempotent: safe to run on every deploy.

Members and users come from `SEED_MEMBERS` / `SEED_USERS` when set, so no real name or
login address ever lands in this repository. Without them, neutral placeholders are
created and renamed in the app.
"""
import json
import os
from typing import Any

from psycopg import Connection, connect

from health import seed_data
from health.llm.seed_prompts import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL, SEEDS, TASKS
from health.settings import get_settings


def _members_spec() -> list[dict[str, Any]]:
    raw = os.environ.get('SEED_MEMBERS')
    return json.loads(raw) if raw else seed_data.DEFAULT_MEMBERS


def _users_spec() -> list[dict[str, Any]]:
    """[{"auth_user": "...", "display_name": "...", "member": "adult-1", "is_admin": true}]"""
    raw = os.environ.get('SEED_USERS')
    if raw:
        return json.loads(raw)
    settings = get_settings()
    if settings.auth_mode == 'none':
        return [
            {
                'auth_user': settings.single_user_id,
                'display_name': 'You',
                'member': 'adult-1',
                'is_admin': True,
            }
        ]
    return []


def seed_members(connection: Connection, household_id: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for spec in _members_spec():
        row = connection.execute(
            """INSERT INTO member (household_id, slug, name, kind, notes, presentation_rules,
                                   likes, dislikes, exclusions, sort_order)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (household_id, slug) DO UPDATE SET slug = EXCLUDED.slug
               RETURNING id""",
            (
                household_id,
                spec['slug'],
                spec['name'],
                spec['kind'],
                spec.get('notes', ''),
                json.dumps(spec.get('presentation_rules', {'toggles': [], 'notes': ''})),
                spec.get('likes', []),
                spec.get('dislikes', []),
                spec.get('exclusions', []),
                spec.get('sort_order', 0),
            ),
        ).fetchone()
        ids[spec['slug']] = row['id']
    return ids


def seed_users(connection: Connection, household_id: str, members: dict[str, str]) -> None:
    for spec in _users_spec():
        member_id = members.get(spec.get('member', ''))
        if member_id is None:
            continue
        connection.execute(
            """INSERT INTO app_user (household_id, auth_user, display_name, member_id, is_admin)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (household_id, auth_user) DO UPDATE
                 SET display_name = EXCLUDED.display_name,
                     member_id = EXCLUDED.member_id,
                     is_admin = EXCLUDED.is_admin""",
            (
                household_id,
                spec['auth_user'],
                spec.get('display_name', spec['auth_user']),
                member_id,
                bool(spec.get('is_admin', False)),
            ),
        )


def seed_goals_and_equipment(
    connection: Connection, household_id: str, members: dict[str, str]
) -> None:
    for slug, goal in seed_data.DEFAULT_GOALS.items():
        member_id = members.get(slug)
        if member_id is None:
            continue
        existing = connection.execute(
            'SELECT 1 FROM goal WHERE member_id = %s AND kind = %s', (member_id, goal['kind'])
        ).fetchone()
        if existing:
            continue
        connection.execute(
            """INSERT INTO goal (household_id, member_id, kind, statement, constraints)
               VALUES (%s, %s, %s, %s, %s)""",
            (household_id, member_id, goal['kind'], goal['statement'], goal['constraints']),
        )

    adult_slugs = [s['slug'] for s in _members_spec() if s.get('kind') == 'adult']
    for slug in adult_slugs:
        member_id = members.get(slug)
        if member_id is None:
            continue
        for name, profile in seed_data.DEFAULT_EQUIPMENT.items():
            connection.execute(
                """INSERT INTO equipment_profile (household_id, member_id, name, items, notes)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (member_id, name) DO NOTHING""",
                (household_id, member_id, name, profile['items'], profile['notes']),
            )


def seed_settings(connection: Connection, household_id: str) -> None:
    values = {
        'grocery_categories': seed_data.GROCERY_CATEGORIES,
        'staples': seed_data.STAPLES,
        'planning_defaults': seed_data.PLANNING_DEFAULTS,
        'cook_time_limits': seed_data.COOK_TIME_LIMITS,
        'monthly_spend_cap_usd': seed_data.MONTHLY_SPEND_CAP_USD,
        'revise_chips': seed_data.REVISE_CHIPS,
    }
    for key, value in values.items():
        connection.execute(
            """INSERT INTO setting (household_id, key, value) VALUES (%s, %s, %s)
               ON CONFLICT (household_id, key) DO NOTHING""",
            (household_id, key, json.dumps(value)),
        )

    for month, items in seed_data.SEASONAL_PRODUCE.items():
        connection.execute(
            """INSERT INTO seasonal_produce (household_id, month, items) VALUES (%s, %s, %s)
               ON CONFLICT (household_id, month) DO NOTHING""",
            (household_id, month, items),
        )


def seed_prompts(connection: Connection, household_id: str) -> None:
    for task in TASKS:
        system_prompt, user_template = SEEDS[task]
        row = connection.execute(
            'SELECT id FROM prompt_version WHERE household_id = %s AND task = %s AND version = 1',
            (household_id, task),
        ).fetchone()
        if row is None:
            row = connection.execute(
                """INSERT INTO prompt_version
                     (household_id, task, version, system_prompt, user_template)
                   VALUES (%s, %s, 1, %s, %s) RETURNING id""",
                (household_id, task, system_prompt, user_template),
            ).fetchone()
        connection.execute(
            """INSERT INTO llm_task_config
                 (household_id, task, model, fallback_model, active_prompt_version_id)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (household_id, task) DO NOTHING""",
            (household_id, task, DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL, row['id']),
        )


def run(connection: Connection, household_id: str) -> None:
    connection.execute("SELECT set_config('app.household_id', %s, true)", (household_id,))
    members = seed_members(connection, household_id)
    seed_users(connection, household_id, members)
    seed_goals_and_equipment(connection, household_id, members)
    seed_settings(connection, household_id)
    seed_prompts(connection, household_id)


def main() -> None:
    from psycopg.rows import dict_row

    settings = get_settings()
    with connect(str(settings.database_url), row_factory=dict_row) as connection:
        run(connection, settings.household_id)
        connection.commit()
    print('seed complete', flush=True)


if __name__ == '__main__':
    main()
