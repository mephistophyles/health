"""Grocery aggregation and trip assignment.

The LLM never writes a grocery list. It is computed here from the accepted recipes, so
every edit or revision leaves it consistent (PRD 4, 5). Pure functions, no database.
"""
from dataclasses import dataclass, field
from datetime import date

FRESH = 'fresh'


@dataclass(frozen=True)
class Trip:
    id: str
    trip_date: date


@dataclass(frozen=True)
class IngredientLine:
    name: str
    qty: float | None = None
    unit: str = ''
    category: str = 'Other'
    perishability: str = 'keeps'


@dataclass(frozen=True)
class RecipeNeed:
    """One cook event: a recipe, the day it is cooked, and what it needs that day."""

    recipe_id: str
    cook_date: date
    ingredients: list[IngredientLine] = field(default_factory=list)


@dataclass(frozen=True)
class GroceryLine:
    trip_id: str
    line_key: str
    name: str
    qty: float | None
    unit: str
    category: str
    recipe_ids: list[str]
    check_pantry: bool
    sort_order: int = 0


def normalize(name: str) -> str:
    return ' '.join(name.lower().split())


def normalize_unit(unit: str) -> str:
    return ' '.join(unit.lower().split()).rstrip('.')


def assign_trip(trips: list[Trip], needed_on: date) -> Trip | None:
    """The latest trip on or before the day the ingredient is needed.

    If every trip falls after that day the ingredient still has to be bought, so it goes
    on the earliest trip and is simply late. Returning None only happens with no trips.
    """
    if not trips:
        return None
    ordered = sorted(trips, key=lambda trip: (trip.trip_date, trip.id))
    eligible = [trip for trip in ordered if trip.trip_date <= needed_on]
    return eligible[-1] if eligible else ordered[0]


def _sum_quantities(quantities: list[float | None]) -> float | None:
    known = [q for q in quantities if q is not None]
    if not known:
        return None
    return round(sum(known), 3)


def build_lines(
    needs: list[RecipeNeed],
    trips: list[Trip],
    staples: set[str] | None = None,
    category_order: list[str] | None = None,
) -> list[GroceryLine]:
    """Aggregate recipe ingredients into one tickable list per shopping trip.

    Non-fresh items are bought once, on the latest trip before the first day they are
    needed. Fresh items are bought per cook day, on the trip closest before that day, so
    they are in the house for the shortest possible time.

    Same name and same unit are summed. Different units stay separate lines under the
    same name; v1 converts nothing.
    """
    staple_names = {normalize(s) for s in (staples or set())}

    # (trip, name, unit) -> quantities, display name, category, source recipes
    buckets: dict[tuple[str, str, str], dict] = {}
    # Non-fresh items need the earliest day across every use before a trip can be chosen.
    first_needed: dict[tuple[str, str], date] = {}
    for need in needs:
        for ingredient in need.ingredients:
            if ingredient.perishability == FRESH:
                continue
            key = (normalize(ingredient.name), normalize_unit(ingredient.unit))
            if key not in first_needed or need.cook_date < first_needed[key]:
                first_needed[key] = need.cook_date

    for need in sorted(needs, key=lambda n: (n.cook_date, n.recipe_id)):
        for ingredient in need.ingredients:
            name_key = normalize(ingredient.name)
            unit_key = normalize_unit(ingredient.unit)
            fresh = ingredient.perishability == FRESH
            needed_on = need.cook_date if fresh else first_needed[(name_key, unit_key)]
            trip = assign_trip(trips, needed_on)
            if trip is None:
                continue
            bucket_key = (trip.id, name_key, unit_key)
            bucket = buckets.setdefault(
                bucket_key,
                {
                    'name': ingredient.name.strip(),
                    'unit': ingredient.unit.strip(),
                    'category': ingredient.category or 'Other',
                    'quantities': [],
                    'recipe_ids': [],
                },
            )
            bucket['quantities'].append(ingredient.qty)
            if need.recipe_id not in bucket['recipe_ids']:
                bucket['recipe_ids'].append(need.recipe_id)

    order = [c.lower() for c in (category_order or [])]

    def category_rank(category: str) -> int:
        lowered = category.lower()
        return order.index(lowered) if lowered in order else len(order)

    lines = [
        GroceryLine(
            trip_id=trip_id,
            line_key=f'{name_key}|{unit_key}',
            name=bucket['name'],
            qty=_sum_quantities(bucket['quantities']),
            unit=bucket['unit'],
            category=bucket['category'],
            recipe_ids=bucket['recipe_ids'],
            check_pantry=name_key in staple_names,
        )
        for (trip_id, name_key, unit_key), bucket in buckets.items()
    ]
    lines.sort(key=lambda line: (line.trip_id, category_rank(line.category), normalize(line.name)))
    return [
        GroceryLine(**{**line.__dict__, 'sort_order': index}) for index, line in enumerate(lines)
    ]


def merge_ticks(
    new_lines: list[GroceryLine], existing: dict[tuple[str, str], bool]
) -> list[tuple[GroceryLine, bool]]:
    """Carry each tick forward onto the line it belongs to.

    A line is the same line when its trip and its `line_key` are unchanged, so editing a
    plan re-ticks nothing the shopper already found and loses no ticks on lines that did
    not move.
    """
    return [(line, existing.get((line.trip_id, line.line_key), False)) for line in new_lines]
