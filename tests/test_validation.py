"""App-side validation: the checks that stand between a model's output and the database."""
from datetime import date

import pytest

from health.domain.contracts import FitnessPlan, MealPlan
from health.domain.validation import (
    FitnessContext,
    MealContext,
    validate_fitness_plan,
    validate_meal_plan,
)

START = date(2026, 9, 19)
END = date(2026, 9, 25)


def meal_context(**overrides) -> MealContext:
    days = {START: 'cook', date(2026, 9, 20): 'cook'}
    base = {
        'start': START,
        'end': END,
        'day_modes': days,
        'lunch_modes': dict.fromkeys(days, 'none'),
        'cook_times': dict.fromkeys(days, 'normal'),
    }
    return MealContext(**{**base, **overrides})


def recipe(key='r1', title='Sheet-pan chicken', total_min=40, feeds=None):
    return {
        'key': key,
        'title': title,
        'active_min': 20,
        'total_min': total_min,
        'servings': 4,
        'feeds': feeds or [],
        'ingredients': [{'name': 'chicken thighs', 'qty': 2, 'unit': 'lb'}],
        'steps': [{'text': 'Roast.'}],
    }


def plan(days=None, recipes=None) -> MealPlan:
    return MealPlan.model_validate(
        {
            'summary': 'A week',
            'days': days
            or [
                {'date': '2026-09-19', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
                {'date': '2026-09-20', 'dinner': {'mode': 'cook', 'recipe_key': 'r2'}},
            ],
            'recipes': recipes or [recipe(), recipe('r2', 'Lentil stew')],
        }
    )


def messages(problems) -> str:
    return '\n'.join(str(p) for p in problems)


def test_a_plan_that_follows_the_period_passes():
    assert validate_meal_plan(plan(), meal_context()) == []


def test_a_day_outside_the_period_is_rejected():
    days = [
        {'date': '2026-10-05', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
        {'date': '2026-09-19', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
        {'date': '2026-09-20', 'dinner': {'mode': 'cook', 'recipe_key': 'r2'}},
    ]
    assert 'outside the period' in messages(validate_meal_plan(plan(days), meal_context()))


def test_a_missing_day_is_reported():
    days = [{'date': '2026-09-19', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}}]
    assert '2026-09-20 is missing' in messages(validate_meal_plan(plan(days), meal_context()))


def test_a_cook_day_without_a_recipe_is_rejected():
    days = [
        {'date': '2026-09-19', 'dinner': {'mode': 'cook'}},
        {'date': '2026-09-20', 'dinner': {'mode': 'cook', 'recipe_key': 'r2'}},
    ]
    assert 'a cook day needs a recipe' in messages(validate_meal_plan(plan(days), meal_context()))


def test_the_plan_may_not_change_a_days_mode():
    days = [
        {'date': '2026-09-19', 'dinner': {'mode': 'takeout'}},
        {'date': '2026-09-20', 'dinner': {'mode': 'cook', 'recipe_key': 'r2'}},
    ]
    assert 'the day is set to cook' in messages(validate_meal_plan(plan(days), meal_context()))


def test_a_recipe_that_does_not_fit_the_nights_cooking_time_is_rejected():
    context = meal_context(cook_times={START: 'quick', date(2026, 9, 20): 'normal'})
    problems = validate_meal_plan(plan(recipes=[recipe(total_min=90), recipe('r2')]), context)
    assert 'quick allows 30' in messages(problems)


def test_a_project_night_has_no_time_limit():
    context = meal_context(cook_times={START: 'project', date(2026, 9, 20): 'normal'})
    assert validate_meal_plan(plan(recipes=[recipe(total_min=240), recipe('r2')]), context) == []


def test_leftovers_must_come_from_a_meal_that_produced_them():
    days = [
        {'date': '2026-09-19', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
        {'date': '2026-09-20', 'dinner': {'mode': 'leftovers', 'leftover_of': '2026-09-19'}},
    ]
    context = meal_context(day_modes={START: 'cook', date(2026, 9, 20): 'leftovers'})
    problems = validate_meal_plan(plan(days, [recipe()]), context)
    assert 'does not list servings' in messages(problems)


def test_leftovers_pass_when_the_cooked_meal_feeds_them_forward():
    days = [
        {'date': '2026-09-19', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
        {'date': '2026-09-20', 'dinner': {'mode': 'leftovers', 'leftover_of': '2026-09-19'}},
    ]
    context = meal_context(day_modes={START: 'cook', date(2026, 9, 20): 'leftovers'})
    fed = recipe(feeds=[{'date': '2026-09-20', 'meal': 'dinner', 'servings': 4}])
    assert validate_meal_plan(plan(days, [fed]), context) == []


def test_leftovers_cannot_come_from_a_later_day():
    days = [
        {'date': '2026-09-19', 'dinner': {'mode': 'leftovers', 'leftover_of': '2026-09-20'}},
        {'date': '2026-09-20', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
    ]
    context = meal_context(day_modes={START: 'leftovers', date(2026, 9, 20): 'cook'})
    problems = validate_meal_plan(plan(days, [recipe()]), context)
    assert 'must come from an earlier day' in messages(problems)


def test_a_dinner_inside_the_repeat_window_is_rejected():
    context = meal_context(recent_dinner_titles={'sheet-pan chicken'})
    assert 'repeat window' in messages(validate_meal_plan(plan(), context))


def test_a_staple_may_repeat_inside_the_window():
    context = meal_context(
        recent_dinner_titles={'sheet-pan chicken'}, staple_titles={'sheet-pan chicken'}
    )
    assert validate_meal_plan(plan(), context) == []


def test_a_rejected_dinner_is_not_suggested_again():
    context = meal_context(rejected_titles={'lentil stew'})
    assert 'rejection list' in messages(validate_meal_plan(plan(), context))


def test_an_unknown_member_in_portion_guidance_is_rejected():
    recipes = [{**recipe(), 'portions': [{'member': 'nobody', 'guidance': 'more'}]}, recipe('r2')]
    context = meal_context(member_slugs={'adult-1', 'adult-2'})
    assert "unknown member 'nobody'" in messages(validate_meal_plan(plan(recipes=recipes), context))


# --- fitness ----------------------------------------------------------------------


def fitness(sessions=None, notes='') -> FitnessPlan:
    return FitnessPlan.model_validate(
        {
            'intent': 'Build',
            'summary': 'Three full-body days',
            'summary_for_meals': {'training_days': ['2026-09-21'], 'notes': notes},
            'sessions': sessions
            if sessions is not None
            else [
                {
                    'date': '2026-09-21',
                    'focus': 'Lower strength',
                    'duration_min': 45,
                    'exercises': [{'key': 'mon-1', 'name': 'Back squat', 'sets': 3, 'reps': 5}],
                }
            ],
        }
    )


def fitness_context(days=None) -> FitnessContext:
    return FitnessContext(
        start=START, end=END, available_days=days or {date(2026, 9, 21)}
    )


def test_a_session_on_an_available_day_passes():
    assert validate_fitness_plan(fitness(), fitness_context()) == []


def test_a_session_on_an_unavailable_day_is_rejected():
    context = fitness_context(days={date(2026, 9, 22)})
    assert 'not an available training day' in messages(validate_fitness_plan(fitness(), context))


def test_a_weekend_session_is_rejected_because_no_weekend_is_ever_available():
    sessions = [
        {
            'date': '2026-09-20',
            'focus': 'Long run',
            'duration_min': 60,
            'exercises': [{'key': 'sun-1', 'name': 'Run'}],
        }
    ]
    problems = validate_fitness_plan(fitness(sessions), fitness_context())
    assert 'not an available training day' in messages(problems)


def test_a_session_with_no_exercises_is_rejected():
    sessions = [{'date': '2026-09-21', 'focus': 'Rest', 'duration_min': 0, 'exercises': []}]
    problems = validate_fitness_plan(fitness(sessions), fitness_context())
    assert 'at least one exercise' in messages(problems)


@pytest.mark.parametrize(
    'notes',
    ['down 4 lb this month', 'target 180 lbs', 'weighing in lighter', 'now 82 kg'],
)
def test_the_summary_shared_with_the_meal_loop_may_not_carry_body_metrics(notes):
    problems = validate_fitness_plan(fitness(notes=notes), fitness_context())
    assert 'must not mention weight' in messages(problems)


def test_an_appetite_note_without_metrics_is_allowed():
    plan_ = fitness(notes='higher-protein target on training days')
    assert validate_fitness_plan(plan_, fitness_context()) == []
