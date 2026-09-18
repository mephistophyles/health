"""Leftover linking, what a plan buys, and the addressing a revision is confined to."""
from datetime import UTC, date, datetime

import pytest

from health.domain import paths, periods
from health.domain.contracts import MealPlan
from health.domain.library import RecipeStats, derive_verdict
from health.domain.plans import cook_double_links, planned_meals, recipe_needs

PLAN = MealPlan.model_validate(
    {
        'summary': 'Cook Monday, eat it again Tuesday',
        'days': [
            {
                'date': '2026-09-21',
                'dinner': {'mode': 'cook', 'recipe_key': 'r1'},
                'lunch': {'mode': 'none'},
            },
            {
                'date': '2026-09-22',
                'dinner': {'mode': 'leftovers', 'leftover_of': '2026-09-21'},
                'lunch': {'mode': 'leftovers', 'leftover_of': '2026-09-21'},
            },
            {'date': '2026-09-23', 'dinner': {'mode': 'takeout'}},
        ],
        'recipes': [
            {
                'key': 'r1',
                'title': 'Big pot chilli',
                'active_min': 20,
                'total_min': 70,
                'servings': 8,
                'feeds': [
                    {'date': '2026-09-22', 'meal': 'dinner', 'servings': 4},
                    {'date': '2026-09-22', 'meal': 'lunch', 'servings': 2},
                ],
                'ingredients': [
                    {'name': 'ground beef', 'qty': 2, 'unit': 'lb', 'perishability': 'fresh'},
                    {'name': 'kidney beans', 'qty': 2, 'unit': 'can', 'perishability': 'pantry'},
                ],
                'steps': [{'text': 'Brown the beef.'}],
            }
        ],
    }
)


def test_only_meals_that_are_actually_served_become_rows():
    rows = planned_meals(PLAN)
    assert [(row.day.isoformat(), row.meal) for row in rows] == [
        ('2026-09-21', 'dinner'),
        ('2026-09-22', 'dinner'),
        ('2026-09-22', 'lunch'),
        ('2026-09-23', 'dinner'),
    ]


def test_a_cook_double_buys_once_on_the_day_it_is_cooked():
    needs = recipe_needs(PLAN, {'r1': 'recipe-uuid'})
    assert len(needs) == 1
    assert needs[0].cook_date == date(2026, 9, 21)
    assert needs[0].recipe_id == 'recipe-uuid'


def test_the_cook_double_link_is_visible_from_the_cooked_meal():
    assert cook_double_links(PLAN) == {
        'r1': ['2026-09-22 dinner x4', '2026-09-22 lunch x2']
    }


def test_a_recipe_nothing_cooks_buys_nothing():
    plan = MealPlan.model_validate(
        {
            'summary': 'Takeout week',
            'days': [{'date': '2026-09-21', 'dinner': {'mode': 'takeout'}}],
            'recipes': [
                {
                    'key': 'orphan',
                    'title': 'Unused',
                    'active_min': 5,
                    'total_min': 5,
                    'servings': 2,
                    'ingredients': [{'name': 'bread'}],
                    'steps': [{'text': 'Toast it.'}],
                }
            ],
        }
    )
    assert recipe_needs(plan, {}) == []


# --- paths ------------------------------------------------------------------------


def test_a_path_addresses_one_item():
    document = {'days': [{'dinner': {'mode': 'cook'}}, {'dinner': {'mode': 'takeout'}}]}
    assert paths.get(document, 'days[1].dinner')['mode'] == 'takeout'
    paths.set_value(document, 'days[1].dinner', {'mode': 'away'})
    assert document['days'][1]['dinner']['mode'] == 'away'


@pytest.mark.parametrize('bad', ['', 'days[', 'days..dinner', '[]'])
def test_a_malformed_path_is_refused(bad):
    with pytest.raises(ValueError):
        paths.parse(bad)


def test_a_replacement_inside_the_target_is_within_scope():
    assert paths.is_within('days[3].dinner.recipe_key', 'days[3].dinner')
    assert paths.is_within('days[3].dinner', 'days[3].dinner')
    assert paths.is_within('anything', 'plan')


def test_a_replacement_outside_the_target_is_not():
    assert not paths.is_within('days[4].dinner', 'days[3].dinner')
    assert not paths.is_within('recipes[0]', 'days[3].dinner')
    # A prefix that is not a path boundary must not slip through.
    assert not paths.is_within('days[30]', 'days[3]')


def test_an_exercise_can_be_addressed_by_its_key():
    document = {
        'sessions': [
            {'exercises': [{'key': 'mon-1'}, {'key': 'mon-2'}]},
            {'exercises': [{'key': 'wed-1'}]},
        ]
    }
    assert paths.exercise_target(document, 'wed-1') == 'sessions[1].exercises[0]'
    assert paths.exercise_target(document, 'nope') is None


# --- periods ----------------------------------------------------------------------


def test_the_next_period_starts_the_day_after_the_last_one_ended():
    start, end = periods.next_period_dates(date(2026, 9, 25), date(2026, 9, 25))
    assert (start, end) == (date(2026, 9, 26), date(2026, 10, 2))


def test_the_first_period_starts_on_the_coming_saturday():
    start, end = periods.next_period_dates(None, date(2026, 9, 16))
    assert start == date(2026, 9, 19)
    assert (end - start).days == 6


def test_training_days_never_include_a_weekend():
    days = periods.default_training_days(date(2026, 9, 19), date(2026, 9, 25))
    assert all(day.weekday() < 5 for day in days)
    assert len(days) == 5


def test_the_checklist_opens_at_noon_on_the_last_friday_of_the_period():
    end = date(2026, 9, 25)
    assert periods.checklist_opens_at(end) == end
    assert not periods.is_planning_window(datetime(2026, 9, 25, 11, tzinfo=UTC), end)
    assert periods.is_planning_window(datetime(2026, 9, 25, 12, tzinfo=UTC), end)
    assert periods.is_planning_window(datetime(2026, 9, 26, 9, tzinfo=UTC), end)


def test_without_an_active_period_planning_is_always_open():
    assert periods.is_planning_window(datetime(2026, 9, 22, 9, tzinfo=UTC), None)


# --- library verdicts -------------------------------------------------------------


def test_a_meal_everyone_liked_and_would_repeat_worked():
    stats = RecipeStats(times_served=2, average_enjoyment=4.5, repeat_yes=2)
    assert derive_verdict(stats) == 'worked'


def test_a_meal_nobody_wants_again_did_not_work():
    stats = RecipeStats(times_served=1, average_enjoyment=4.0, repeat_no=1)
    assert derive_verdict(stats) == 'didnt_work'


def test_a_middling_meal_needs_a_tweak():
    stats = RecipeStats(times_served=1, average_enjoyment=3.5, repeat_with_tweaks=1)
    assert derive_verdict(stats) == 'needs_tweak'


def test_a_meal_never_served_is_untried():
    assert derive_verdict(RecipeStats()) == 'untried'


def test_a_staple_stays_a_staple_whatever_the_numbers_say():
    stats = RecipeStats(times_served=5, average_enjoyment=2.0, repeat_no=5)
    assert derive_verdict(stats, staple=True) == 'staple'
