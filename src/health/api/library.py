"""The meal library: every accepted recipe, its verdict, and what to do next with it."""
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg import Connection
from pydantic import BaseModel

from health.api.common import bad_request, one, page_params
from health.deps import get_connection
from health.domain.contracts import MealPlan, Recipe
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['library'])

Verdict = Literal['staple', 'worked', 'needs_tweak', 'didnt_work', 'untried']


class RecipeIn(BaseModel):
    """A recipe entered by hand. Same shape the model has to produce."""

    recipe: Recipe
    staple: bool = False
    tweak_notes: str = ''


class RecipePatch(BaseModel):
    verdict_override: Verdict | None = None
    tweak_notes: str | None = None
    staple: bool | None = None
    archived: bool | None = None
    tags: list[str] | None = None


class PlanThis(BaseModel):
    plan_id: UUID
    day: str
    meal: Literal['dinner', 'lunch'] = 'dinner'


@router.get('/recipes')
def list_recipes(
    connection: Connection = Depends(get_connection),
    paging: tuple[int, int] = Depends(page_params),
    verdict: Verdict | None = None,
    tag: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    limit, offset = paging
    where = ['(%s OR NOT archived)']
    params: list[Any] = [include_archived]
    if verdict:
        where.append('COALESCE(verdict_override, verdict) = %s')
        params.append(verdict)
    if tag:
        where.append('%s = ANY(tags)')
        params.append(tag)
    if search:
        where.append('title ILIKE %s')
        params.append(f'%{search}%')
    clause = ' AND '.join(where)

    total = connection.execute(
        f'SELECT count(*) AS n FROM recipe WHERE {clause}', tuple(params)
    ).fetchone()['n']
    rows = connection.execute(
        f"""SELECT r.id, r.title, r.tags, r.staple, r.tweak_notes, r.times_served, r.last_served,
                   r.archived, COALESCE(r.verdict_override, r.verdict) AS verdict,
                   r.verdict_override IS NOT NULL AS verdict_is_manual,
                   (SELECT round(avg(f.enjoyment), 2) FROM meal_feedback f
                      JOIN plan_meal pm ON pm.id = f.plan_meal_id
                     WHERE pm.recipe_id = r.id) AS average_enjoyment,
                   r.content->>'description' AS description,
                   (r.content->>'total_min')::int AS total_min
              FROM recipe r WHERE {clause}
             ORDER BY r.staple DESC, r.last_served DESC NULLS LAST, r.title
             LIMIT %s OFFSET %s""",
        (*params, limit, offset),
    ).fetchall()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/recipes/{recipe_id}')
def get_recipe(recipe_id: UUID, connection: Connection = Depends(get_connection)) -> dict[str, Any]:
    recipe = one(connection, 'SELECT * FROM recipe WHERE id = %s', (recipe_id,), 'No such recipe')
    per_person = connection.execute(
        """SELECT m.slug, m.name, round(avg(f.enjoyment), 2) AS enjoyment,
                  count(*) FILTER (WHERE f.ate = 'yes') AS ate_yes, count(f.id) AS entries
             FROM meal_feedback f
             JOIN plan_meal pm ON pm.id = f.plan_meal_id
             JOIN member m ON m.id = f.member_id
            WHERE pm.recipe_id = %s GROUP BY m.slug, m.name ORDER BY m.slug""",
        (recipe_id,),
    ).fetchall()
    return {**recipe, 'per_person': per_person}


@router.post('/recipes', status_code=201)
def create_recipe(
    body: RecipeIn,
    connection: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return one(
        connection,
        """INSERT INTO recipe (household_id, title, content, tags, staple, tweak_notes)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
        (
            settings.household_id, body.recipe.title,
            json.dumps(body.recipe.model_dump(), default=str), body.recipe.tags,
            body.staple, body.tweak_notes,
        ),
        'Could not save that recipe',
    )


@router.put('/recipes/{recipe_id}')
def replace_recipe(
    recipe_id: UUID, body: RecipeIn, connection: Connection = Depends(get_connection)
) -> dict[str, Any]:
    return one(
        connection,
        'UPDATE recipe SET title = %s, content = %s, tags = %s, updated_at = now()'
        ' WHERE id = %s RETURNING *',
        (
            body.recipe.title, json.dumps(body.recipe.model_dump(), default=str),
            body.recipe.tags, recipe_id,
        ),
        'No such recipe',
    )


@router.patch('/recipes/{recipe_id}')
def update_recipe(
    recipe_id: UUID, patch: RecipePatch, connection: Connection = Depends(get_connection)
) -> dict[str, Any]:
    """A hand-set verdict overrides the derived one for good."""
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        raise bad_request('Nothing to update')
    assignments = ', '.join(f'{name} = %s' for name in fields)
    return one(
        connection,
        f'UPDATE recipe SET {assignments}, updated_at = now() WHERE id = %s RETURNING *',
        (*fields.values(), recipe_id),
        'No such recipe',
    )


@router.post('/recipes/{recipe_id}/plan-this')
def plan_this(
    recipe_id: UUID,
    body: PlanThis,
    connection: Connection = Depends(get_connection),
) -> dict[str, Any]:
    """Drop a library recipe straight into a draft or hand-written plan (PRD 8)."""
    recipe = one(connection, 'SELECT * FROM recipe WHERE id = %s', (recipe_id,), 'No such recipe')
    plan = one(connection, 'SELECT * FROM plan WHERE id = %s', (body.plan_id,), 'No such plan')
    if plan['kind'] != 'meals':
        raise bad_request('Recipes go into a meal plan')

    content = json.loads(json.dumps(plan['content'], default=str))
    key = f'lib-{str(recipe_id)[:8]}'
    entry = {**recipe['content'], 'key': key, 'library_id': str(recipe_id)}

    recipes = [r for r in content.get('recipes', []) if r.get('key') != key]
    recipes.append(entry)
    content['recipes'] = recipes
    for day in content['days']:
        if day['date'] == body.day:
            slot = day[body.meal]
            slot['mode'] = 'cook' if body.meal == 'dinner' else 'prep'
            slot['recipe_key'] = key
            slot['leftover_of'] = None
            break
    else:
        raise bad_request(f'{body.day} is not in this plan')

    MealPlan.model_validate(content)  # The edited document still has to be a plan.
    return one(
        connection,
        'UPDATE plan SET content = %s, updated_at = now() WHERE id = %s RETURNING *',
        (json.dumps(content, default=str), body.plan_id),
        'No such plan',
    )


@router.get('/library/tags')
def list_tags(
    connection: Connection = Depends(get_connection), limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    return connection.execute(
        'SELECT tag, count(*) AS n FROM recipe, unnest(tags) AS tag'
        ' WHERE NOT archived GROUP BY tag ORDER BY n DESC, tag LIMIT %s',
        (limit,),
    ).fetchall()
