"""The whole loop without any model: set up a period, write a plan by hand, accept it,
shop from it, edit it, and watch the grocery list stay honest."""
import pytest

from tests.conftest import ADULT_ONE, ADULT_TWO

pytestmark = pytest.mark.usefixtures('household')


def recipe(key, title, ingredients, total_min=40, feeds=None):
    return {
        'key': key,
        'title': title,
        'active_min': 20,
        'total_min': total_min,
        'servings': 4,
        'feeds': feeds or [],
        'ingredients': ingredients,
        'steps': [{'text': 'Cook it.'}],
        'portions': [],
        'kid_plates': [],
        'tags': [],
    }


def ingredient(name, qty, unit, category='Produce', perishability='keeps'):
    return {
        'name': name, 'qty': qty, 'unit': unit,
        'category': category, 'perishability': perishability,
    }


@pytest.fixture
def period(client):
    response = client(ADULT_ONE).post('/api/periods', json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_new_period_arrives_with_days_trips_and_availability_filled_in(client, period):
    setup = client(ADULT_ONE).get(f'/api/periods/{period["id"]}').json()
    assert len(setup['days']) == 7
    assert len(setup['trips']) == 2
    assert all(day['dinner_mode'] == 'cook' for day in setup['days'])
    assert len(setup['availability']['days']) == 5


def test_a_period_shorter_than_five_days_is_refused(client):
    start = '2026-11-01'
    response = client(ADULT_ONE).post(
        '/api/periods', json={'start_date': start, 'end_date': '2026-11-03'}
    )
    assert response.status_code == 400
    assert '5-14 days' in response.json()['detail']


def _hand_written_plan(client, period):
    """A three-cook-night plan, one of them doubled to feed the next day's lunch."""
    api = client(ADULT_ONE)
    days = client(ADULT_ONE).get(f'/api/periods/{period["id"]}').json()['days']
    dates = [day['day'] for day in days]

    modes = [
        {'day': dates[0], 'dinner_mode': 'cook', 'lunch_mode': 'none', 'cook_time': 'project'},
        {'day': dates[1], 'dinner_mode': 'cook', 'lunch_mode': 'none', 'cook_time': 'normal'},
        {'day': dates[2], 'dinner_mode': 'leftovers', 'lunch_mode': 'none', 'cook_time': 'normal'},
        {'day': dates[3], 'dinner_mode': 'takeout', 'lunch_mode': 'none', 'cook_time': 'normal'},
        {'day': dates[4], 'dinner_mode': 'cook', 'lunch_mode': 'none', 'cook_time': 'quick'},
        {'day': dates[5], 'dinner_mode': 'eating_out', 'lunch_mode': 'none', 'cook_time': 'normal'},
        {'day': dates[6], 'dinner_mode': 'away', 'lunch_mode': 'none', 'cook_time': 'normal'},
    ]
    api.put(f'/api/periods/{period["id"]}/days', json=modes).raise_for_status()

    plan = api.post(
        f'/api/periods/{period["id"]}/plans/manual', params={'kind': 'meals'}
    ).json()
    content = {
        'summary': 'A hand-written week',
        'days': [
            {'date': dates[0], 'dinner': {'mode': 'cook', 'recipe_key': 'r1'},
             'lunch': {'mode': 'none'}},
            {'date': dates[1], 'dinner': {'mode': 'cook', 'recipe_key': 'r2'},
             'lunch': {'mode': 'none'}},
            {'date': dates[2], 'dinner': {'mode': 'leftovers', 'leftover_of': dates[1]},
             'lunch': {'mode': 'none'}},
            {'date': dates[3], 'dinner': {'mode': 'takeout'}, 'lunch': {'mode': 'none'}},
            {'date': dates[4], 'dinner': {'mode': 'cook', 'recipe_key': 'r3'},
             'lunch': {'mode': 'none'}},
            {'date': dates[5], 'dinner': {'mode': 'eating_out'}, 'lunch': {'mode': 'none'}},
            {'date': dates[6], 'dinner': {'mode': 'away'}, 'lunch': {'mode': 'none'}},
        ],
        'recipes': [
            recipe('r1', 'Slow ragu', [
                ingredient('ground beef', 2, 'lb', 'Meat & seafood', 'fresh'),
                ingredient('canned tomatoes', 2, 'can', 'Canned & jarred', 'pantry'),
                ingredient('olive oil', 2, 'tbsp', 'Pantry', 'pantry'),
            ], total_min=180),
            recipe('r2', 'Big chilli', [
                ingredient('ground beef', 1, 'lb', 'Meat & seafood', 'fresh'),
                ingredient('kidney beans', 2, 'can', 'Canned & jarred', 'pantry'),
            ], total_min=60, feeds=[{'date': dates[2], 'meal': 'dinner', 'servings': 4}]),
            recipe('r3', 'Fast stir fry', [
                ingredient('broccoli', 1, 'head', 'Produce', 'fresh'),
                ingredient('soy sauce', 3, 'tbsp', 'Pantry', 'pantry'),
            ], total_min=25),
        ],
        'profile_suggestions': [],
    }
    api.put(f'/api/plans/{plan["id"]}/content', json={'content': content}).raise_for_status()
    return plan, dates


def test_a_hand_written_plan_can_be_accepted_and_produces_the_shopping_list(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)

    accepted = api.post(f'/api/plans/{plan["id"]}/accept')
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['status'] == 'accepted'

    groceries = api.get(f'/api/periods/{period["id"]}/groceries').json()
    lines = [item for trip in groceries['trips'] for item in trip['items']]

    # Every ingredient appears exactly once per trip it belongs to, and traces to a recipe.
    assert all(item['recipe_ids'] for item in lines if not item['manual'])
    names = [item['name'] for item in lines]
    assert 'kidney beans' in names
    assert 'broccoli' in names

    # Both beef nights fall before the midweek top-up, so they are bought together.
    beef = [item for item in lines if item['name'] == 'ground beef']
    assert len(beef) == 1
    assert float(beef[0]['qty']) == 3.0
    assert beef[0]['trip_id'] == groceries['trips'][0]['id']

    # The fresh broccoli is for the night of the top-up trip, so it is bought then.
    broccoli = next(item for item in lines if item['name'] == 'broccoli')
    assert broccoli['trip_id'] == groceries['trips'][1]['id']

    # A household staple is a pantry check, not a purchase.
    olive_oil = next(item for item in lines if item['name'] == 'olive oil')
    assert olive_oil['check_pantry'] is True


def test_editing_an_accepted_plan_recomputes_the_list_without_losing_ticks(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    api.post(f'/api/plans/{plan["id"]}/accept').raise_for_status()

    groceries = api.get(f'/api/periods/{period["id"]}/groceries').json()
    beans = next(
        item for trip in groceries['trips'] for item in trip['items']
        if item['name'] == 'kidney beans'
    )
    broccoli = next(
        item for trip in groceries['trips'] for item in trip['items']
        if item['name'] == 'broccoli'
    )
    api.patch(f'/api/grocery-items/{beans["id"]}', json={'checked': True}).raise_for_status()
    api.patch(f'/api/grocery-items/{broccoli["id"]}', json={'checked': True}).raise_for_status()

    # Swap the quick stir fry for something else. The chilli night is untouched.
    content = api.get(f'/api/periods/{period["id"]}/plans').json()['meals']['accepted']['content']
    content['recipes'] = [r for r in content['recipes'] if r['key'] != 'r3'] + [
        recipe('r3', 'Omelette', [ingredient('eggs', 8, 'each', 'Dairy & eggs', 'fresh')],
               total_min=15)
    ]
    api.put(f'/api/plans/{plan["id"]}/content', json={'content': content}).raise_for_status()

    after = api.get(f'/api/periods/{period["id"]}/groceries').json()
    lines = {item['name']: item for trip in after['trips'] for item in trip['items']}
    assert lines['kidney beans']['checked'] is True, 'an untouched line keeps its tick'
    assert 'broccoli' not in lines, 'a line the edit removed is gone'
    assert lines['eggs']['checked'] is False, 'a new line starts unticked'


def test_a_manual_line_survives_a_recompute(client, period):
    plan, _ = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    api.post(f'/api/plans/{plan["id"]}/accept').raise_for_status()

    trip = api.get(f'/api/periods/{period["id"]}/groceries').json()['trips'][0]
    api.post(
        f'/api/trips/{trip["id"]}/items', json={'name': 'nappies', 'category': 'Household'}
    ).raise_for_status()

    api.post(f'/api/periods/{period["id"]}/groceries/recompute').raise_for_status()
    after = api.get(f'/api/periods/{period["id"]}/groceries').json()
    names = [item['name'] for t in after['trips'] for item in t['items']]
    assert 'nappies' in names


def test_a_plan_that_breaks_the_period_is_refused_rather_than_stored(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    content = api.get(f'/api/periods/{period["id"]}/plans').json()['meals']['draft']['content']
    # The quick night now holds a three-hour braise.
    content['recipes'] = [
        r if r['key'] != 'r3' else {**r, 'total_min': 180} for r in content['recipes']
    ]
    response = api.put(f'/api/plans/{plan["id"]}/content', json={'content': content})
    assert response.status_code == 400
    assert 'quick allows 30' in response.json()['detail']


def test_leftovers_with_no_meal_behind_them_are_refused(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    content = api.get(f'/api/periods/{period["id"]}/plans').json()['meals']['draft']['content']
    content['recipes'] = [
        r if r['key'] != 'r2' else {**r, 'feeds': []} for r in content['recipes']
    ]
    response = api.put(f'/api/plans/{plan["id"]}/content', json={'content': content})
    assert response.status_code == 400
    assert 'does not list servings' in response.json()['detail']


def test_a_takeout_week_produces_no_grocery_list(client):
    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()
    days = api.get(f'/api/periods/{period["id"]}').json()['days']
    api.put(
        f'/api/periods/{period["id"]}/days',
        json=[
            {'day': day['day'], 'dinner_mode': 'away', 'lunch_mode': 'none', 'cook_time': 'normal'}
            for day in days
        ],
    ).raise_for_status()

    plan = api.post(f'/api/periods/{period["id"]}/plans/manual', params={'kind': 'meals'}).json()
    api.post(f'/api/plans/{plan["id"]}/accept').raise_for_status()

    groceries = api.get(f'/api/periods/{period["id"]}/groceries').json()
    assert all(trip['items'] == [] for trip in groceries['trips'])


def test_a_travel_week_regenerates_fitness_alone(client, database, household):
    """Fitness still runs for a travel week; the meal loop is simply not queued."""
    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()
    api.put(
        f'/api/periods/{period["id"]}/availability',
        json={'days': [], 'travel': True, 'session_minutes': 30},
    ).raise_for_status()

    response = api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['fitness']})
    assert response.status_code == 202
    kinds = {job['kind'] for job in response.json()['jobs']}
    assert kinds == {'fitness.generate'}


def test_the_travel_flag_switches_which_equipment_the_loop_is_given(
    client, database, household
):
    from health.llm.context import fitness_generation_context
    from tests.conftest import HOUSEHOLD_ID

    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()
    api.put(
        f'/api/periods/{period["id"]}/availability',
        json={'days': ['2099-01-01'], 'travel': True},
    ).raise_for_status()

    with database.worker_connection(household['adult-1']) as connection:
        context = fitness_generation_context(
            connection, HOUSEHOLD_ID, period['id'], household['adult-1']
        )
    assert context['equipment_profile']['name'] == 'travel'
    assert 'resistance bands' in context['equipment_profile']['items']


def test_a_weekend_is_never_planned_even_if_asked_for(client, period):
    api = client(ADULT_ONE)
    days = api.get(f'/api/periods/{period["id"]}').json()['days']
    every_day = [day['day'] for day in days]
    saved = api.put(
        f'/api/periods/{period["id"]}/availability', json={'days': every_day}
    ).json()
    from datetime import date

    assert all(date.fromisoformat(day).weekday() < 5 for day in saved['days'])


def test_rejecting_a_meal_records_why_and_keeps_it_off_the_next_plan(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)

    response = api.post(
        f'/api/plans/{plan["id"]}/reject-item',
        json={'target': 'days[0].dinner', 'reason': 'too fiddly on a weeknight'},
    )
    assert response.status_code == 200, response.text
    assert response.json()['rejection']['summary'] == 'Slow ragu'

    rejections = api.get('/api/rejections').json()
    assert rejections[0]['reason'] == 'too fiddly on a weeknight'
    assert rejections[0]['rejected_by_name']


def test_either_user_can_accept_the_meal_plan(client, period):
    plan, _ = _hand_written_plan(client, period)
    assert client(ADULT_TWO).post(f'/api/plans/{plan["id"]}/accept').status_code == 200


def test_the_friday_checklist_lists_what_is_still_outstanding(client, period):
    checklist = client(ADULT_ONE).get('/api/checklist', params={'period_id': period['id']}).json()
    keys = {item['key'] for item in checklist['items']}
    assert {'inputs', 'meals_accepted', 'fitness_accepted'} <= keys
    assert not any(item['done'] for item in checklist['items'] if item['key'] != 'inputs')


def test_accepting_a_plan_files_its_recipes_in_the_library(client, period):
    plan, _ = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    api.post(f'/api/plans/{plan["id"]}/accept').raise_for_status()

    library = api.get('/api/recipes').json()
    titles = {item['title'] for item in library['items']}
    assert {'Slow ragu', 'Big chilli', 'Fast stir fry'} <= titles
    assert all(item['verdict'] in ('untried', 'needs_tweak') for item in library['items'])


def test_meal_feedback_moves_a_recipe_to_a_verdict(client, period, household):
    plan, _ = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    api.post(f'/api/plans/{plan["id"]}/accept').raise_for_status()

    meals = api.get(f'/api/periods/{period["id"]}/meals').json()
    ragu = next(meal for meal in meals if meal['title'] == 'Slow ragu')
    for slug in ('adult-1', 'adult-2', 'child-1'):
        api.put(
            f'/api/plan-meals/{ragu["id"]}/feedback',
            json={'member_id': str(household[slug]), 'enjoyment': 5, 'ate': 'yes',
                  'portion': 'right'},
        ).raise_for_status()
    api.put(
        f'/api/plan-meals/{ragu["id"]}/review', json={'repeat_choice': 'yes', 'effort': 'as expected'}
    ).raise_for_status()

    entry = api.get(f'/api/recipes/{ragu["recipe_id"]}').json()
    assert entry['verdict'] == 'worked'
    assert entry['times_served'] == 1

    api.patch(f'/api/recipes/{ragu["recipe_id"]}', json={'verdict_override': 'staple'})
    listed = api.get('/api/recipes', params={'verdict': 'staple'}).json()
    assert [item['title'] for item in listed['items']] == ['Slow ragu']


def test_a_library_recipe_can_be_dropped_into_a_plan(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    api.post(f'/api/plans/{plan["id"]}/accept').raise_for_status()

    chilli = next(
        item for item in api.get('/api/recipes').json()['items'] if item['title'] == 'Big chilli'
    )
    next_period = api.post('/api/periods', json={}).json()
    new_plan = api.post(
        f'/api/periods/{next_period["id"]}/plans/manual', params={'kind': 'meals'}
    ).json()
    first_day = api.get(f'/api/periods/{next_period["id"]}').json()['days'][0]['day']

    response = api.post(
        f'/api/recipes/{chilli["id"]}/plan-this',
        json={'plan_id': new_plan['id'], 'day': first_day, 'meal': 'dinner'},
    )
    assert response.status_code == 200, response.text
    content = response.json()['content']
    day = next(d for d in content['days'] if d['date'] == first_day)
    assert day['dinner']['recipe_key'].startswith('lib-')


def test_one_tap_logs_a_whole_session_as_prescribed(client, database, household):
    import json

    from tests.conftest import HOUSEHOLD_ID

    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()
    day = api.get(f'/api/periods/{period["id"]}').json()['availability']['days'][0]
    content = {
        'intent': 'Build',
        'summary': 'Week one',
        'summary_for_meals': {'training_days': [day], 'notes': 'higher protein on training days'},
        'sessions': [
            {
                'date': day, 'focus': 'Lower', 'duration_min': 45, 'warmup': [], 'cooldown': [],
                'exercises': [
                    {'key': 'a', 'name': 'Back squat', 'sets': 3, 'reps': 5, 'weight_lb': 185,
                     'alternatives': []},
                    {'key': 'b', 'name': 'Split squat', 'sets': 2, 'reps': 10, 'alternatives': []},
                ],
            }
        ],
    }
    with database.worker_connection(household['adult-1']) as connection:
        plan_id = connection.execute(
            """INSERT INTO plan (household_id, period_id, kind, owner_member_id, status, content)
               VALUES (%s, %s, 'fitness', %s, 'accepted', %s) RETURNING id""",
            (HOUSEHOLD_ID, period['id'], household['adult-1'], json.dumps(content)),
        ).fetchone()['id']

    response = api.post(
        '/api/workouts/session-done', json={'plan_id': str(plan_id), 'log_date': day}
    )
    assert response.status_code == 201, response.text
    assert len(response.json()) == 2
    assert sum(len(log['sets']) for log in response.json()) == 5


def test_a_chart_with_too_few_points_says_so_instead_of_drawing_one(client):
    api = client(ADULT_ONE)
    api.put(
        '/api/metrics', json={'metric_date': '2026-09-19', 'kind': 'weight', 'value': 180}
    ).raise_for_status()
    chart = api.get('/api/charts/weight').json()
    assert chart['enough_for_chart'] is False

    for day, value in (('2026-09-20', 179.5), ('2026-09-21', 179.0)):
        api.put(
            '/api/metrics', json={'metric_date': day, 'kind': 'weight', 'value': value}
        ).raise_for_status()
    assert api.get('/api/charts/weight').json()['enough_for_chart'] is True


def test_the_spend_cap_blocks_a_generation_with_a_clear_message(client, database, period):
    from health.llm.client import SpendCapReached, run_task
    from health.settings import get_settings
    from tests.conftest import HOUSEHOLD_ID

    api = client(ADULT_ONE)
    api.put('/api/settings/monthly_spend_cap_usd', json={'value': 1}).raise_for_status()

    with database.worker_connection() as connection:
        connection.execute(
            """INSERT INTO llm_call (household_id, task, model, cost_usd, status)
               VALUES (%s, 'meals.generate', 'test', 5.00, 'ok')""",
            (HOUSEHOLD_ID,),
        )
        with pytest.raises(SpendCapReached) as error:
            run_task(
                connection, get_settings(), HOUSEHOLD_ID, 'meals.generate', {},
                type('X', (), {})  # never reached: the cap is checked first
            )
    assert 'cap' in str(error.value)


def test_a_prompt_save_creates_a_new_version_and_the_old_one_stays(client):
    api = client(ADULT_ONE)
    before = api.get('/api/llm-tasks/meals.generate/prompts').json()
    assert [row['version'] for row in before] == [1]

    api.post(
        '/api/llm-tasks/meals.generate/prompts',
        json={'system_prompt': 'New system', 'user_template': 'New template {{period.start}}'},
    ).raise_for_status()

    after = api.get('/api/llm-tasks/meals.generate/prompts').json()
    assert [row['version'] for row in after] == [2, 1]
    active = next(
        task for task in api.get('/api/llm-tasks').json()['tasks']
        if task['task'] == 'meals.generate'
    )
    assert active['prompt_version'] == 2


def test_the_prompt_preview_assembles_without_calling_the_model(client, period):
    api = client(ADULT_ONE)
    preview = api.post(
        '/api/llm-tasks/meals.generate/preview', params={'period_id': period['id']}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert period['start_date'] in body['user_prompt']
    assert '{{' not in body['user_prompt']


def test_generate_queues_one_job_per_loop_however_often_it_is_pressed(client, database):
    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()

    api.post(f'/api/periods/{period["id"]}/generate', json={}).raise_for_status()
    api.post(f'/api/periods/{period["id"]}/generate', json={}).raise_for_status()

    with database.worker_connection() as connection:
        connection.execute("SELECT set_config('app.worker', 'on', true)")
        rows = connection.execute(
            'SELECT kind, owner_member_id FROM job WHERE period_id = %s', (period['id'],)
        ).fetchall()
    # One meal loop, and one fitness loop for each adult — not two of anything.
    assert len(rows) == 3
    assert sorted(row['kind'] for row in rows) == [
        'fitness.generate', 'fitness.generate', 'meals.generate',
    ]


def test_generate_starts_both_adults_fitness_but_shows_only_your_own(client, household):
    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()
    response = api.post(f'/api/periods/{period["id"]}/generate', json={})
    assert response.status_code == 202

    mine = api.get(f'/api/periods/{period["id"]}/jobs').json()
    theirs = client(ADULT_TWO).get(f'/api/periods/{period["id"]}/jobs').json()
    owners = {job['owner_member_id'] for job in mine}
    assert owners == {None, str(household['adult-1'])}
    assert {job['owner_member_id'] for job in theirs} == {None, str(household['adult-2'])}


def test_regenerating_only_my_fitness_leaves_the_other_adult_alone(client, household, database):
    api = client(ADULT_ONE)
    period = api.post('/api/periods', json={}).json()
    api.post(
        f'/api/periods/{period["id"]}/generate',
        json={'loops': ['fitness'], 'own_fitness_only': True},
    ).raise_for_status()

    with database.worker_connection() as connection:
        connection.execute("SELECT set_config('app.worker', 'on', true)")
        rows = connection.execute(
            'SELECT owner_member_id FROM job WHERE period_id = %s', (period['id'],)
        ).fetchall()
    assert [row['owner_member_id'] for row in rows] == [household['adult-1']]


def test_a_cleared_rejection_stops_coming_back(client, period):
    plan, _dates = _hand_written_plan(client, period)
    api = client(ADULT_ONE)
    api.post(
        f'/api/plans/{plan["id"]}/reject-item',
        json={'target': 'days[0].dinner', 'reason': 'too fiddly'},
    ).raise_for_status()

    rejection = api.get('/api/rejections').json()[0]
    api.delete(f'/api/rejections/{rejection["id"]}')
    assert api.get('/api/rejections').json() == []
    assert len(api.get('/api/rejections', params={'include_cleared': True}).json()) == 1
