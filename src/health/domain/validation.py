"""App-side validation of a generated plan (PRD 11), run after schema parsing.

Schema parsing proves the shape. These checks prove the plan is actually plannable:
the right days, recipes that fit the night available, leftovers that come from a meal
that really produced them, and nothing the household has just eaten or already rejected.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from health.domain.contracts import FitnessPlan, MealPlan

# Total minutes a recipe may take for each cooking-time setting. `project` is unbounded.
COOK_TIME_LIMITS = {'quick': 30, 'normal': 75, 'project': None}

_METRIC_PATTERN = re.compile(r'\b\d+(\.\d+)?\s?(lb|lbs|pound|pounds|kg|kilo)\b|\bweigh', re.IGNORECASE)


@dataclass
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f'{self.path}: {self.message}'


@dataclass
class MealContext:
    start: date
    end: date
    day_modes: dict[date, str]
    lunch_modes: dict[date, str] = field(default_factory=dict)
    cook_times: dict[date, str] = field(default_factory=dict)
    recent_dinner_titles: set[str] = field(default_factory=set)
    rejected_titles: set[str] = field(default_factory=set)
    staple_titles: set[str] = field(default_factory=set)
    member_slugs: set[str] = field(default_factory=set)
    cook_time_limits: dict[str, int | None] = field(default_factory=lambda: dict(COOK_TIME_LIMITS))


@dataclass
class FitnessContext:
    start: date
    end: date
    available_days: set[date]


def _as_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _norm(title: str) -> str:
    return ' '.join(title.lower().split())


def validate_meal_plan(plan: MealPlan, context: MealContext) -> list[Problem]:
    problems: list[Problem] = []
    recipes = {recipe.key: recipe for recipe in plan.recipes}

    duplicate_keys = len(plan.recipes) - len(recipes)
    if duplicate_keys:
        problems.append(Problem('recipes', 'recipe keys must be unique'))

    seen_days: set[date] = set()
    cook_days: dict[date, str] = {}

    for index, day in enumerate(plan.days):
        path = f'days[{index}]'
        day_date = _as_date(day.date)
        if day_date is None:
            problems.append(Problem(path, f'{day.date!r} is not a date'))
            continue
        if not context.start <= day_date <= context.end:
            problems.append(Problem(path, f'{day.date} falls outside the period'))
            continue
        if day_date in seen_days:
            problems.append(Problem(path, f'{day.date} appears twice'))
        seen_days.add(day_date)

        expected = context.day_modes.get(day_date)
        if expected and day.dinner.mode != expected:
            problems.append(
                Problem(f'{path}.dinner', f'mode is {day.dinner.mode}, the day is set to {expected}')
            )
        expected_lunch = context.lunch_modes.get(day_date, 'none')
        if day.lunch.mode != expected_lunch:
            problems.append(
                Problem(
                    f'{path}.lunch', f'mode is {day.lunch.mode}, the day is set to {expected_lunch}'
                )
            )

        if day.dinner.mode == 'cook':
            if not day.dinner.recipe_key:
                problems.append(Problem(f'{path}.dinner', 'a cook day needs a recipe'))
            elif day.dinner.recipe_key not in recipes:
                problems.append(
                    Problem(f'{path}.dinner', f'recipe {day.dinner.recipe_key!r} is not defined')
                )
            else:
                cook_days[day_date] = day.dinner.recipe_key
                problems += _check_cook_time(
                    f'{path}.dinner', recipes[day.dinner.recipe_key], day_date, context
                )
                problems += _check_repeats(
                    f'{path}.dinner', recipes[day.dinner.recipe_key].title, context
                )
        elif day.dinner.recipe_key:
            problems.append(
                Problem(f'{path}.dinner', f'a {day.dinner.mode} day must not carry a recipe')
            )

    missing = sorted(d for d in context.day_modes if d not in seen_days)
    for day_date in missing:
        problems.append(Problem('days', f'{day_date.isoformat()} is missing from the plan'))

    problems += _check_leftovers(plan, recipes, cook_days)
    problems += _check_members(plan, context)
    return problems


def _check_cook_time(path: str, recipe, day_date: date, context: MealContext) -> list[Problem]:
    setting = context.cook_times.get(day_date, 'normal')
    limit = context.cook_time_limits.get(setting, COOK_TIME_LIMITS.get(setting))
    if limit is not None and recipe.total_min > limit:
        return [
            Problem(path, f'{recipe.title} takes {recipe.total_min} min; {setting} allows {limit}')
        ]
    return []


def _check_repeats(path: str, title: str, context: MealContext) -> list[Problem]:
    key = _norm(title)
    if key in context.staple_titles:
        return []
    problems = []
    if key in context.recent_dinner_titles:
        problems.append(Problem(path, f'{title} was served inside the repeat window'))
    if key in context.rejected_titles:
        problems.append(Problem(path, f'{title} is on the rejection list'))
    return problems


def _check_leftovers(plan: MealPlan, recipes: dict, cook_days: dict[date, str]) -> list[Problem]:
    """A leftovers meal must come from an earlier cook meal that produced enough for it."""
    problems: list[Problem] = []
    for index, day in enumerate(plan.days):
        day_date = _as_date(day.date)
        if day_date is None:
            continue
        for slot, entry in (('dinner', day.dinner), ('lunch', day.lunch)):
            if entry.mode != 'leftovers':
                continue
            path = f'days[{index}].{slot}'
            if not entry.leftover_of:
                problems.append(Problem(path, 'leftovers must name the meal they come from'))
                continue
            source_date = _as_date(entry.leftover_of)
            if source_date is None:
                problems.append(Problem(path, f'{entry.leftover_of!r} is not a date'))
                continue
            if source_date >= day_date:
                problems.append(Problem(path, 'leftovers must come from an earlier day'))
                continue
            recipe_key = cook_days.get(source_date)
            if recipe_key is None:
                problems.append(
                    Problem(path, f'nothing is cooked on {entry.leftover_of} to leave over')
                )
                continue
            recipe = recipes[recipe_key]
            produced = sum(
                feed.servings
                for feed in recipe.feeds
                if feed.date == day.date and feed.meal == slot
            )
            if produced <= 0:
                problems.append(
                    Problem(
                        path,
                        f'{recipe.title} does not list servings feeding {slot} on {day.date}',
                    )
                )
    return problems


def _check_members(plan: MealPlan, context: MealContext) -> list[Problem]:
    if not context.member_slugs:
        return []
    problems = []
    for index, recipe in enumerate(plan.recipes):
        for portion in recipe.portions:
            if portion.member not in context.member_slugs:
                problems.append(
                    Problem(f'recipes[{index}].portions', f'unknown member {portion.member!r}')
                )
        for plate in recipe.kid_plates:
            if plate.member not in context.member_slugs:
                problems.append(
                    Problem(f'recipes[{index}].kid_plates', f'unknown member {plate.member!r}')
                )
    for index, suggestion in enumerate(plan.profile_suggestions):
        if suggestion.member not in context.member_slugs:
            problems.append(
                Problem(f'profile_suggestions[{index}]', f'unknown member {suggestion.member!r}')
            )
    return problems


def validate_fitness_plan(plan: FitnessPlan, context: FitnessContext) -> list[Problem]:
    problems: list[Problem] = []
    seen: set[date] = set()

    for index, session in enumerate(plan.sessions):
        path = f'sessions[{index}]'
        session_date = _as_date(session.date)
        if session_date is None:
            problems.append(Problem(path, f'{session.date!r} is not a date'))
            continue
        if not context.start <= session_date <= context.end:
            problems.append(Problem(path, f'{session.date} falls outside the period'))
        elif session_date not in context.available_days:
            problems.append(Problem(path, f'{session.date} is not an available training day'))
        if session_date in seen:
            problems.append(Problem(path, f'{session.date} has two sessions'))
        seen.add(session_date)

        keys = [exercise.key for exercise in session.exercises]
        if len(keys) != len(set(keys)):
            problems.append(Problem(f'{path}.exercises', 'exercise keys must be unique'))
        if not session.exercises:
            problems.append(Problem(f'{path}.exercises', 'a session needs at least one exercise'))

    problems += _check_summary_is_metric_free(plan)
    return problems


def _check_summary_is_metric_free(plan: FitnessPlan) -> list[Problem]:
    """`summary_for_meals` crosses into shared context, so it must carry no body metrics."""
    if _METRIC_PATTERN.search(plan.summary_for_meals.notes):
        return [
            Problem(
                'summary_for_meals.notes',
                'the summary shared with the meal loop must not mention weight or other metrics',
            )
        ]
    return []
