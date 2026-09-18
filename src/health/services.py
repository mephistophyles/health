"""Accepting a plan, and everything that has to stay consistent with it.

Acceptance is the moment a JSONB document becomes queryable rows: meals, library recipes
and grocery lists. Every one of these runs again after an edit, so the plan of record and
the shopping list never drift apart.
"""
import json
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import Connection

from health.domain import groceries as grocery_domain
from health.domain.contracts import MealPlan
from health.domain.library import RecipeStats, derive_verdict
from health.domain.plans import planned_meals, recipe_needs


def _settings(connection: Connection, household_id: str) -> dict[str, Any]:
    rows = connection.execute(
        'SELECT key, value FROM setting WHERE household_id = %s', (household_id,)
    ).fetchall()
    return {row['key']: row['value'] for row in rows}


def upsert_recipes(connection: Connection, household_id: str, plan: MealPlan) -> dict[str, UUID]:
    """Every accepted recipe joins the shared library, keyed by title within the household."""
    ids: dict[str, UUID] = {}
    for recipe in plan.recipes:
        content = recipe.model_dump()
        existing = None
        if recipe.library_id:
            existing = connection.execute(
                'SELECT id FROM recipe WHERE id = %s', (recipe.library_id,)
            ).fetchone()
        if existing is None:
            existing = connection.execute(
                'SELECT id FROM recipe WHERE household_id = %s AND lower(title) = lower(%s)',
                (household_id, recipe.title),
            ).fetchone()
        if existing:
            row = connection.execute(
                'UPDATE recipe SET content = %s, tags = %s, updated_at = now() WHERE id = %s'
                ' RETURNING id',
                (json.dumps(content, default=str), recipe.tags, existing['id']),
            ).fetchone()
        else:
            row = connection.execute(
                'INSERT INTO recipe (household_id, title, content, tags) VALUES (%s, %s, %s, %s)'
                ' RETURNING id',
                (household_id, recipe.title, json.dumps(content, default=str), recipe.tags),
            ).fetchone()
        ids[recipe.key] = row['id']
    return ids


def write_plan_meals(
    connection: Connection, household_id: str, plan_id: UUID, plan: MealPlan,
    recipe_ids: dict[str, UUID],
) -> None:
    """Rewrite this plan's meal rows, keeping any `skipped` mark already made."""
    skipped = {
        (row['day'], row['meal'])
        for row in connection.execute(
            'SELECT day, meal FROM plan_meal WHERE plan_id = %s AND skipped', (plan_id,)
        ).fetchall()
    }
    connection.execute('DELETE FROM plan_meal WHERE plan_id = %s', (plan_id,))
    for meal in planned_meals(plan):
        connection.execute(
            """INSERT INTO plan_meal (household_id, plan_id, day, meal, mode, recipe_id,
                                      recipe_key, leftover_of, skipped)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                household_id, plan_id, meal.day, meal.meal, meal.mode,
                recipe_ids.get(meal.recipe_key) if meal.recipe_key else None,
                meal.recipe_key, meal.leftover_of, (meal.day, meal.meal) in skipped,
            ),
        )


def recompute_groceries(
    connection: Connection, household_id: str, period_id: UUID
) -> list[dict[str, Any]]:
    """Rebuild every trip's list from the accepted plan, carrying ticks forward (PRD 5)."""
    plan = connection.execute(
        "SELECT content FROM plan WHERE period_id = %s AND kind = 'meals' AND status = 'accepted'",
        (period_id,),
    ).fetchone()
    trips = connection.execute(
        'SELECT id, trip_date FROM shopping_trip WHERE period_id = %s ORDER BY trip_date',
        (period_id,),
    ).fetchall()
    trip_ids = [row['id'] for row in trips]
    if not trip_ids:
        return []

    existing = {
        (str(row['trip_id']), row['line_key']): row['checked']
        for row in connection.execute(
            'SELECT trip_id, line_key, checked FROM grocery_item'
            ' WHERE trip_id = ANY(%s) AND NOT manual',
            (trip_ids,),
        ).fetchall()
    }
    connection.execute(
        'DELETE FROM grocery_item WHERE trip_id = ANY(%s) AND NOT manual', (trip_ids,)
    )
    if plan is None:
        return []

    parsed = MealPlan.model_validate(plan['content'])
    recipe_ids = {
        row['recipe_key']: row['recipe_id']
        for row in connection.execute(
            """SELECT DISTINCT pm.recipe_key, pm.recipe_id FROM plan_meal pm
                 JOIN plan p ON p.id = pm.plan_id
                WHERE p.period_id = %s AND p.kind = 'meals' AND p.status = 'accepted'
                  AND pm.recipe_key IS NOT NULL""",
            (period_id,),
        ).fetchall()
    }
    config = _settings(connection, household_id)
    lines = grocery_domain.build_lines(
        recipe_needs(parsed, {k: str(v) for k, v in recipe_ids.items() if v}),
        [grocery_domain.Trip(id=str(row['id']), trip_date=row['trip_date']) for row in trips],
        staples=set(config.get('staples', [])),
        category_order=config.get('grocery_categories', []),
    )
    for line, checked in grocery_domain.merge_ticks(lines, existing):
        connection.execute(
            """INSERT INTO grocery_item (household_id, trip_id, name, qty, unit, category,
                                         recipe_ids, check_pantry, checked, line_key, sort_order)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                household_id, line.trip_id, line.name, line.qty, line.unit, line.category,
                [UUID(rid) for rid in line.recipe_ids], line.check_pantry, checked,
                line.line_key, line.sort_order,
            ),
        )
    return list_grocery_items(connection, period_id)


def list_grocery_items(connection: Connection, period_id: UUID) -> list[dict[str, Any]]:
    return connection.execute(
        """SELECT g.*, t.trip_date, t.store FROM grocery_item g
             JOIN shopping_trip t ON t.id = g.trip_id
            WHERE t.period_id = %s ORDER BY t.trip_date, g.sort_order, g.name""",
        (period_id,),
    ).fetchall()


def refresh_recipe_stats(connection: Connection, household_id: str, recipe_id: UUID) -> None:
    """Recompute the library verdict from feedback. A hand-set verdict always wins."""
    row = connection.execute(
        """SELECT count(DISTINCT pm.id) FILTER (WHERE NOT pm.skipped) AS times_served,
                  max(pm.day) FILTER (WHERE NOT pm.skipped) AS last_served,
                  avg(f.enjoyment) AS enjoyment,
                  count(DISTINCT pm.id) FILTER (WHERE mr.repeat_choice = 'yes') AS repeat_yes,
                  count(DISTINCT pm.id) FILTER (WHERE mr.repeat_choice = 'with_tweaks')
                    AS repeat_tweaks,
                  count(DISTINCT pm.id) FILTER (WHERE mr.repeat_choice = 'no') AS repeat_no
             FROM plan_meal pm
             JOIN plan p ON p.id = pm.plan_id AND p.status = 'accepted'
             LEFT JOIN meal_feedback f ON f.plan_meal_id = pm.id
             LEFT JOIN meal_review mr ON mr.plan_meal_id = pm.id
            WHERE pm.recipe_id = %s""",
        (recipe_id,),
    ).fetchone()
    staple = connection.execute(
        'SELECT staple FROM recipe WHERE id = %s', (recipe_id,)
    ).fetchone()
    stats = RecipeStats(
        times_served=row['times_served'] or 0,
        average_enjoyment=float(row['enjoyment']) if row['enjoyment'] is not None else None,
        repeat_yes=row['repeat_yes'] or 0,
        repeat_with_tweaks=row['repeat_tweaks'] or 0,
        repeat_no=row['repeat_no'] or 0,
    )
    connection.execute(
        'UPDATE recipe SET verdict = %s, times_served = %s, last_served = %s, updated_at = now()'
        ' WHERE id = %s',
        (
            derive_verdict(stats, staple=bool(staple and staple['staple'])),
            stats.times_served,
            row['last_served'],
            recipe_id,
        ),
    )


def accept_meal_plan(
    connection: Connection, household_id: str, plan: dict[str, Any], accepted_by: UUID
) -> dict[str, Any]:
    parsed = MealPlan.model_validate(plan['content'])
    connection.execute(
        """DELETE FROM plan WHERE period_id = %s AND kind = 'meals' AND status = 'accepted'
            AND id <> %s""",
        (plan['period_id'], plan['id']),
    )
    accepted = connection.execute(
        "UPDATE plan SET status = 'accepted', accepted_by = %s, accepted_at = now(),"
        ' updated_at = now() WHERE id = %s RETURNING *',
        (accepted_by, plan['id']),
    ).fetchone()

    recipe_ids = upsert_recipes(connection, household_id, parsed)
    write_plan_meals(connection, household_id, accepted['id'], parsed, recipe_ids)
    for recipe_id in recipe_ids.values():
        refresh_recipe_stats(connection, household_id, recipe_id)
    recompute_groceries(connection, household_id, accepted['period_id'])
    connection.execute(
        "UPDATE period SET status = 'active', updated_at = now() WHERE id = %s AND status = 'planning'",
        (accepted['period_id'],),
    )
    return accepted


def accept_fitness_plan(
    connection: Connection, plan: dict[str, Any], accepted_by: UUID
) -> dict[str, Any]:
    connection.execute(
        """DELETE FROM plan WHERE period_id = %s AND kind = 'fitness'
            AND owner_member_id = %s AND status = 'accepted' AND id <> %s""",
        (plan['period_id'], plan['owner_member_id'], plan['id']),
    )
    return connection.execute(
        "UPDATE plan SET status = 'accepted', accepted_by = %s, accepted_at = now(),"
        ' updated_at = now() WHERE id = %s RETURNING *',
        (accepted_by, plan['id']),
    ).fetchone()


def record_rejection(
    connection: Connection, household_id: str, kind: str, summary: str, reason: str,
    member_id: UUID, weeks: int,
) -> dict[str, Any]:
    return connection.execute(
        """INSERT INTO rejection (household_id, kind, summary, reason, rejected_by, expires_at)
           VALUES (%s, %s, %s, %s, %s, CURRENT_DATE + %s) RETURNING *""",
        (household_id, kind, summary, reason, member_id, weeks * 7),
    ).fetchone()


def prune_llm_calls(connection: Connection, household_id: str, days: int) -> int:
    """Prompts and responses are kept for debugging for this window only (PRD 4)."""
    row = connection.execute(
        "DELETE FROM llm_call WHERE household_id = %s AND created_at < now() - make_interval(days => %s)"
        ' RETURNING 1',
        (household_id, days),
    ).fetchall()
    return len(row)


def today_in(tz) -> date:
    from datetime import datetime

    return datetime.now(tz).date()
