"""Grocery aggregation and trip assignment, on fixed fixtures.

This is the part that has to be right every time: a missing ingredient means an
improvised dinner, and a duplicated one means money wasted.
"""
from datetime import date

from health.domain.groceries import (
    GroceryLine,
    IngredientLine,
    RecipeNeed,
    Trip,
    assign_trip,
    build_lines,
    merge_ticks,
)

SAT = date(2026, 9, 19)
SUN = date(2026, 9, 20)
WED = date(2026, 9, 23)
THU = date(2026, 9, 24)
FRI = date(2026, 9, 25)

TRIPS = [Trip(id='major', trip_date=SAT), Trip(id='topup', trip_date=WED)]


def line(name, qty=1.0, unit='lb', category='Produce', perishability='keeps'):
    return IngredientLine(
        name=name, qty=qty, unit=unit, category=category, perishability=perishability
    )


def by_key(lines: list[GroceryLine]) -> dict[tuple[str, str], GroceryLine]:
    return {(item.trip_id, item.line_key): item for item in lines}


def test_assign_trip_picks_the_latest_trip_before_the_day():
    assert assign_trip(TRIPS, THU).id == 'topup'
    assert assign_trip(TRIPS, SUN).id == 'major'
    assert assign_trip(TRIPS, SAT).id == 'major'


def test_assign_trip_falls_back_to_the_first_trip_when_every_trip_is_too_late():
    assert assign_trip(TRIPS, date(2026, 9, 18)).id == 'major'


def test_non_fresh_items_are_bought_once_on_the_first_trip_that_covers_them():
    needs = [
        RecipeNeed('r1', SUN, [line('rice', 1, 'lb', 'Pantry', 'pantry')]),
        RecipeNeed('r2', THU, [line('rice', 2, 'lb', 'Pantry', 'pantry')]),
    ]
    lines = build_lines(needs, TRIPS)
    assert len(lines) == 1
    assert lines[0].trip_id == 'major'
    assert lines[0].qty == 3


def test_fresh_items_are_bought_on_the_trip_closest_before_the_meal():
    needs = [
        RecipeNeed('r1', SUN, [line('spinach', 1, 'bunch', 'Produce', 'fresh')]),
        RecipeNeed('r2', THU, [line('spinach', 2, 'bunch', 'Produce', 'fresh')]),
    ]
    lines = by_key(build_lines(needs, TRIPS))
    assert lines[('major', 'spinach|bunch')].qty == 1
    assert lines[('topup', 'spinach|bunch')].qty == 2


def test_same_name_different_units_stay_separate_lines():
    needs = [
        RecipeNeed('r1', SUN, [line('butter', 4, 'tbsp', 'Dairy & eggs')]),
        RecipeNeed('r2', SUN, [line('butter', 1, 'stick', 'Dairy & eggs')]),
    ]
    lines = build_lines(needs, TRIPS)
    assert {item.unit for item in lines} == {'tbsp', 'stick'}
    assert all(item.qty in (4, 1) for item in lines)


def test_quantities_of_the_same_unit_are_summed_case_insensitively():
    needs = [
        RecipeNeed('r1', SUN, [line('Chicken Thighs', 1.5, 'lb', 'Meat & seafood')]),
        RecipeNeed('r2', SUN, [line('chicken thighs', 0.5, 'LB', 'Meat & seafood')]),
    ]
    lines = build_lines(needs, TRIPS)
    assert len(lines) == 1
    assert lines[0].qty == 2.0


def test_staples_are_marked_check_pantry_rather_than_buy():
    needs = [RecipeNeed('r1', SUN, [line('Olive oil', 2, 'tbsp', 'Pantry', 'pantry')])]
    lines = build_lines(needs, TRIPS, staples={'olive oil'})
    assert lines[0].check_pantry is True


def test_every_line_traces_back_to_the_recipes_that_need_it():
    needs = [
        RecipeNeed('r1', SUN, [line('onion', 1, 'each', 'Produce', 'pantry')]),
        RecipeNeed('r2', SUN, [line('onion', 2, 'each', 'Produce', 'pantry')]),
    ]
    lines = build_lines(needs, TRIPS)
    assert lines[0].recipe_ids == ['r1', 'r2']


def test_lines_are_grouped_in_the_order_the_store_is_walked():
    needs = [
        RecipeNeed('r1', SUN, [
            line('milk', 1, 'gal', 'Dairy & eggs', 'fresh'),
            line('apples', 3, 'each', 'Produce', 'fresh'),
            line('flour', 1, 'lb', 'Pantry', 'pantry'),
        ])
    ]
    lines = build_lines(needs, TRIPS, category_order=['Produce', 'Dairy & eggs', 'Pantry'])
    assert [item.category for item in lines] == ['Produce', 'Dairy & eggs', 'Pantry']
    assert [item.sort_order for item in lines] == [0, 1, 2]


def test_an_unknown_category_sorts_last_rather_than_disappearing():
    needs = [RecipeNeed('r1', SUN, [
        line('saffron', 1, 'g', 'Spice cupboard', 'pantry'),
        line('apples', 3, 'each', 'Produce', 'fresh'),
    ])]
    lines = build_lines(needs, TRIPS, category_order=['Produce'])
    assert [item.category for item in lines] == ['Produce', 'Spice cupboard']


def test_an_ingredient_with_no_quantity_still_gets_a_line():
    needs = [RecipeNeed('r1', SUN, [IngredientLine(name='salt to taste', qty=None)])]
    lines = build_lines(needs, TRIPS)
    assert lines[0].qty is None


def test_ticks_survive_a_recompute_on_lines_that_did_not_change():
    needs = [RecipeNeed('r1', SUN, [line('rice', 1, 'lb', 'Pantry', 'pantry')])]
    first = build_lines(needs, TRIPS)
    existing = {('major', 'rice|lb'): True}

    needs.append(RecipeNeed('r2', THU, [line('spinach', 1, 'bunch', 'Produce', 'fresh')]))
    second = merge_ticks(build_lines(needs, TRIPS), existing)

    ticked = {(item.line_key, checked) for item, checked in second}
    assert ('rice|lb', True) in ticked
    assert ('spinach|bunch', False) in ticked
    assert len(first) == 1


def test_no_trips_means_no_lines_rather_than_a_crash():
    needs = [RecipeNeed('r1', SUN, [line('rice')])]
    assert build_lines(needs, []) == []
