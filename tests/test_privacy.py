"""Each adult's fitness data is private, in both directions, through the API and the DB.

This is the cross-cutting acceptance criterion the whole design hangs on, so it is tested
from both ends: the repository layer scopes every query, and row security refuses the rows
even when a query forgets to.
"""
import pytest

from tests.conftest import ADULT_ONE, ADULT_TWO

pytestmark = pytest.mark.usefixtures('household')


def _period(client) -> str:
    response = client(ADULT_ONE).post('/api/periods', json={})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_an_unknown_identity_is_refused(client):
    assert client('stranger@example.com').get('/api/me').status_code == 403


def test_a_request_with_no_identity_header_is_refused(client):
    plain = client(ADULT_ONE)
    plain.headers.pop('X-Auth-User')
    assert plain.get('/api/me').status_code == 403


def test_each_user_is_mapped_to_their_own_member(client):
    one = client(ADULT_ONE).get('/api/me').json()
    two = client(ADULT_TWO).get('/api/me').json()
    assert one['member_slug'] == 'adult-1'
    assert two['member_slug'] == 'adult-2'
    assert one['is_admin'] and not two['is_admin']


def test_a_weight_entry_is_invisible_to_the_other_adult(client, household):
    client(ADULT_ONE).put(
        '/api/metrics', json={'metric_date': '2026-09-19', 'kind': 'weight', 'value': 180}
    ).raise_for_status()

    assert len(client(ADULT_ONE).get('/api/metrics').json()) == 1
    assert client(ADULT_TWO).get('/api/metrics').json() == []


def test_a_workout_log_is_invisible_to_the_other_adult(client):
    client(ADULT_ONE).post(
        '/api/workouts',
        json={
            'log_date': '2026-09-21',
            'exercise_key': 'mon-1',
            'name': 'Back squat',
            'sets': [{'reps': 5, 'weight_lb': 185}],
        },
    ).raise_for_status()

    assert client(ADULT_ONE).get('/api/workouts').json()['total'] == 1
    assert client(ADULT_TWO).get('/api/workouts').json()['total'] == 0


def test_an_activity_is_invisible_to_the_other_adult(client):
    client(ADULT_TWO).post(
        '/api/activities', json={'activity_date': '2026-09-20', 'type': 'run', 'distance_mi': 3}
    ).raise_for_status()

    assert client(ADULT_TWO).get('/api/activities').json()['total'] == 1
    assert client(ADULT_ONE).get('/api/activities').json()['total'] == 0


def test_a_fitness_goal_is_invisible_to_the_other_adult(client, household):
    one = household['adult-1']
    client(ADULT_ONE).post(
        f'/api/members/{one}/goals',
        json={'kind': 'fitness', 'statement': 'Squat 315', 'constraints': 'left knee'},
    ).raise_for_status()

    mine = client(ADULT_ONE).get(f'/api/members/{one}/goals').json()
    theirs = client(ADULT_TWO).get(f'/api/members/{one}/goals').json()
    assert any(goal['statement'] == 'Squat 315' for goal in mine)
    assert theirs == []


def test_a_meal_goal_is_shared_because_the_meal_loop_plans_for_everyone(client, household):
    one = household['adult-1']
    client(ADULT_ONE).post(
        f'/api/members/{one}/goals', json={'kind': 'meal', 'statement': 'More vegetables'}
    ).raise_for_status()

    theirs = client(ADULT_TWO).get(f'/api/members/{one}/goals').json()
    assert [goal['statement'] for goal in theirs] == ['More vegetables']


def test_equipment_profiles_cannot_be_read_by_the_other_adult(client, household):
    one = household['adult-1']
    assert client(ADULT_ONE).get(f'/api/members/{one}/equipment').status_code == 200
    assert client(ADULT_TWO).get(f'/api/members/{one}/equipment').status_code == 403


def test_a_fitness_plan_is_invisible_to_the_other_adult(client, database, household):
    import json

    from tests.conftest import HOUSEHOLD_ID

    period_id = _period(client)
    content = {
        'intent': 'Build',
        'summary': 'Private plan',
        'summary_for_meals': {'training_days': [], 'notes': ''},
        'sessions': [],
    }
    with database.worker_connection(household['adult-1']) as connection:
        connection.execute(
            """INSERT INTO plan (household_id, period_id, kind, owner_member_id, status, content)
               VALUES (%s, %s, 'fitness', %s, 'draft', %s)""",
            (HOUSEHOLD_ID, period_id, household['adult-1'], json.dumps(content)),
        )

    mine = client(ADULT_ONE).get(f'/api/periods/{period_id}/plans').json()
    theirs = client(ADULT_TWO).get(f'/api/periods/{period_id}/plans').json()
    assert mine['fitness']['draft']['content']['summary'] == 'Private plan'
    assert theirs['fitness']['draft'] is None


def test_row_security_hides_private_rows_from_a_query_that_forgets_to_scope(database, household):
    """The belt to the repository layer's braces: the database refuses the rows itself."""
    with database.worker_connection(household['adult-1']) as connection:
        connection.execute(
            "INSERT INTO metric (household_id, member_id, metric_date, kind, value)"
            " VALUES (%s, %s, '2026-09-19', 'weight', 180)",
            ('11111111-1111-1111-1111-111111111111', household['adult-1']),
        )

    with database.worker_connection(household['adult-2']) as connection:
        connection.execute('SET LOCAL ROLE health_rls_probe')
        rows = connection.execute('SELECT * FROM metric').fetchall()
    assert rows == []


def test_a_fitness_plan_can_only_be_accepted_by_its_owner(client, database, household):
    import json

    from tests.conftest import HOUSEHOLD_ID

    period_id = _period(client)
    content = {
        'intent': 'Repeat',
        'summary': 'Mine',
        'summary_for_meals': {'training_days': [], 'notes': ''},
        'sessions': [],
    }
    with database.worker_connection(household['adult-1']) as connection:
        plan_id = connection.execute(
            """INSERT INTO plan (household_id, period_id, kind, owner_member_id, status, content)
               VALUES (%s, %s, 'fitness', %s, 'draft', %s) RETURNING id""",
            (HOUSEHOLD_ID, period_id, household['adult-1'], json.dumps(content)),
        ).fetchone()['id']

    assert client(ADULT_TWO).post(f'/api/plans/{plan_id}/accept').status_code == 404, 'not theirs to find'
    assert client(ADULT_ONE).post(f'/api/plans/{plan_id}/accept').status_code == 200


def test_only_an_admin_may_change_models_and_prompts(client):
    assert client(ADULT_ONE).get('/api/llm-tasks').status_code == 200
    assert client(ADULT_TWO).get('/api/llm-tasks').status_code == 403
    assert client(ADULT_TWO).put(
        '/api/settings/monthly_spend_cap_usd', json={'value': 999}
    ).status_code == 403
