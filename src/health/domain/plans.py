"""Turning an accepted plan document into the rows the rest of the app queries."""
from dataclasses import dataclass
from datetime import date

from health.domain.contracts import MealPlan
from health.domain.groceries import IngredientLine, RecipeNeed


@dataclass(frozen=True)
class PlannedMeal:
    day: date
    meal: str
    mode: str
    recipe_key: str | None
    leftover_of: date | None
    servings: float | None


def planned_meals(plan: MealPlan) -> list[PlannedMeal]:
    """One row per served meal. Days with no lunch planned produce no lunch row."""
    rows: list[PlannedMeal] = []
    for day in plan.days:
        day_date = date.fromisoformat(day.date)
        rows.append(
            PlannedMeal(
                day=day_date,
                meal='dinner',
                mode=day.dinner.mode,
                recipe_key=day.dinner.recipe_key,
                leftover_of=date.fromisoformat(day.dinner.leftover_of)
                if day.dinner.leftover_of
                else None,
                servings=None,
            )
        )
        if day.lunch.mode != 'none':
            rows.append(
                PlannedMeal(
                    day=day_date,
                    meal='lunch',
                    mode=day.lunch.mode,
                    recipe_key=day.lunch.recipe_key,
                    leftover_of=date.fromisoformat(day.lunch.leftover_of)
                    if day.lunch.leftover_of
                    else None,
                    servings=None,
                )
            )
    return rows


def recipe_needs(plan: MealPlan, recipe_ids: dict[str, str]) -> list[RecipeNeed]:
    """What has to be bought, and for which night's cooking.

    Ingredients are needed on the day the recipe is cooked, not on the day leftovers from
    it are eaten, so a cook-double meal shops once.
    """
    cook_dates: dict[str, date] = {}
    for day in plan.days:
        if day.dinner.mode == 'cook' and day.dinner.recipe_key:
            cook_dates.setdefault(day.dinner.recipe_key, date.fromisoformat(day.date))
        if day.lunch.mode == 'prep' and day.lunch.recipe_key:
            cook_dates.setdefault(day.lunch.recipe_key, date.fromisoformat(day.date))

    needs: list[RecipeNeed] = []
    for recipe in plan.recipes:
        cook_date = cook_dates.get(recipe.key)
        if cook_date is None:
            continue  # A recipe nothing cooks buys nothing.
        needs.append(
            RecipeNeed(
                recipe_id=recipe_ids.get(recipe.key, recipe.key),
                cook_date=cook_date,
                ingredients=[
                    IngredientLine(
                        name=ingredient.name,
                        qty=ingredient.qty,
                        unit=ingredient.unit,
                        category=ingredient.category,
                        perishability=ingredient.perishability,
                    )
                    for ingredient in recipe.ingredients
                ],
            )
        )
    return needs


def cook_double_links(plan: MealPlan) -> dict[str, list[str]]:
    """Which later meals each cooked recipe feeds, for the 'Tue: makes Wed lunch x2' line."""
    links: dict[str, list[str]] = {}
    for recipe in plan.recipes:
        for feed in recipe.feeds:
            links.setdefault(recipe.key, []).append(
                f'{feed.date} {feed.meal} x{feed.servings:g}'
            )
    return links
