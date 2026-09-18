"""The generation loop end to end, with the model replaced by a canned response.

This covers the seam nothing else does: a queued job, the context the loop is handed, the
validation gate, the repair retry, and what lands in the database afterwards. No network.
"""
import json

import pytest

from health.domain.contracts import FitnessPlan, MealPlan
from health.llm import client as llm_client
from health.llm import jobs
from tests.conftest import ADULT_ONE, HOUSEHOLD_ID

pytestmark = pytest.mark.usefixtures('household')


class FakeModel:
    """Stands in for OpenRouter. Hands back queued responses and records what it was asked."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, config, messages, schema, model=None):
        self.calls.append(messages)
        text = self.responses.pop(0) if self.responses else '{}'
        return llm_client.Completion(
            text=text, model=model or config.model, tokens_in=100, tokens_out=200,
            cost_usd=0.002, latency_ms=1200,
        )


@pytest.fixture
def fake_model(monkeypatch):
    def install(*responses: str) -> FakeModel:
        fake = FakeModel(*responses)
        monkeypatch.setattr(llm_client.OpenRouterClient, 'complete', fake)
        return fake

    return install


@pytest.fixture
def period(client):
    return client(ADULT_ONE).post('/api/periods', json={}).json()


def meal_plan_json(days: list[str]) -> str:
    """A plan that cooks the first night and leaves the rest takeout, so it validates."""
    return json.dumps(
        {
            'summary': 'One real dinner, the rest easy.',
            'days': [
                {
                    'date': day,
                    'dinner': (
                        {'mode': 'cook', 'recipe_key': 'r1'}
                        if index == 0
                        else {'mode': 'cook', 'recipe_key': 'r2'}
                    ),
                    'lunch': {'mode': 'none'},
                }
                for index, day in enumerate(days)
            ],
            'recipes': [
                {
                    'key': 'r1',
                    'title': 'Lemon chicken traybake',
                    'description': 'One tray, one lemon.',
                    'active_min': 15,
                    'total_min': 45,
                    'servings': 4,
                    'feeds': [],
                    'ingredients': [
                        {'name': 'chicken thighs', 'qty': 2, 'unit': 'lb',
                         'category': 'Meat & seafood', 'perishability': 'fresh'},
                        {'name': 'olive oil', 'qty': 2, 'unit': 'tbsp',
                         'category': 'Pantry', 'perishability': 'pantry'},
                    ],
                    'steps': [{'text': 'Roast at 425F.', 'make_ahead': False}],
                    'portions': [{'member': 'adult-1', 'guidance': 'Larger protein portion.'}],
                    'kid_plates': [{'member': 'child-1', 'serve': 'Components separated.',
                                    'fallback': 'Plain chicken and bread.'}],
                    'tags': ['chicken', 'sheet pan'],
                },
                {
                    'key': 'r2',
                    'title': 'Weeknight pasta',
                    'description': '',
                    'active_min': 10,
                    'total_min': 25,
                    'servings': 4,
                    'feeds': [],
                    'ingredients': [
                        {'name': 'pasta', 'qty': 1, 'unit': 'lb',
                         'category': 'Pantry', 'perishability': 'pantry'},
                    ],
                    'steps': [{'text': 'Boil, toss, eat.', 'make_ahead': False}],
                    'portions': [],
                    'kid_plates': [],
                    'tags': ['pasta'],
                },
            ],
            'profile_suggestions': [
                {'member': 'child-1', 'change': 'Serve sauce on the side',
                 'evidence': 'Refused mixed sauces three times.'},
            ],
        }
    )


def fitness_plan_json(days: list[str]) -> str:
    return json.dumps(
        {
            'intent': 'Build',
            'summary': 'Three full-body days.',
            'summary_for_meals': {
                'training_days': days[:3],
                'notes': 'higher-protein target on training days',
            },
            'sessions': [
                {
                    'date': day,
                    'focus': 'Full body',
                    'duration_min': 45,
                    'warmup': ['Bike 5 min'],
                    'cooldown': ['Hip flexor stretch'],
                    'exercises': [
                        {
                            'key': f'{index}-1', 'name': 'Back squat', 'sets': 3, 'reps': 5,
                            'duration_s': None, 'distance_mi': None, 'pace': None,
                            'weight_lb': 185, 'rest_s': 150, 'rpe': 7, 'cues': 'Brace first.',
                            'alternatives': [
                                {'name': 'Goblet squat', 'reason': 'joint-friendly',
                                 'sets': 3, 'reps': 10, 'weight_lb': 50}
                            ],
                        }
                    ],
                }
                for index, day in enumerate(days[:3])
            ],
        }
    )


def _days(client, period) -> list[str]:
    return [day['day'] for day in client(ADULT_ONE).get(f'/api/periods/{period["id"]}').json()['days']]


def _run_one_job(database, kind: str) -> dict:
    """Claim and run the next queued job of this kind, as the worker would."""
    from health.settings import get_settings

    while True:
        job = jobs.claim_next(database)
        assert job is not None, f'no queued job left before reaching {kind}'
        jobs.run_job(database, get_settings(), job)
        with database.queue_connection() as connection:
            done = connection.execute('SELECT * FROM job WHERE id = %s', (job['id'],)).fetchone()
        if done['kind'] == kind:
            return done


def test_a_generated_meal_plan_becomes_a_draft_that_can_be_accepted(
    client, database, period, fake_model
):
    days = _days(client, period)
    fake_model(meal_plan_json(days))
    api = client(ADULT_ONE)

    api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_MEALS)
    assert job['status'] == 'succeeded', job['detail']

    plans = api.get(f'/api/periods/{period["id"]}/plans').json()
    draft = plans['meals']['draft']
    assert draft is not None
    assert draft['content']['summary'] == 'One real dinner, the rest easy.'
    assert draft['model'] == 'deepseek/deepseek-chat'

    accepted = api.post(f'/api/plans/{draft["id"]}/accept')
    assert accepted.status_code == 200, accepted.text

    groceries = api.get(f'/api/periods/{period["id"]}/groceries').json()
    names = {item['name'] for trip in groceries['trips'] for item in trip['items']}
    assert 'chicken thighs' in names
    assert 'pasta' in names


def test_the_meal_loop_proposes_profile_updates_for_a_person_to_decide(
    client, database, period, fake_model
):
    fake_model(meal_plan_json(_days(client, period)))
    api = client(ADULT_ONE)
    api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}).raise_for_status()
    _run_one_job(database, jobs.GENERATE_MEALS)

    suggestions = api.get('/api/profile-suggestions').json()
    assert [s['change'] for s in suggestions] == ['Serve sauce on the side']
    assert suggestions[0]['status'] == 'pending'

    api.post(
        f'/api/profile-suggestions/{suggestions[0]["id"]}',
        json={'accept': True, 'apply_to': 'presentation_rules'},
    ).raise_for_status()
    child = next(m for m in api.get('/api/members').json() if m['slug'] == 'child-1')
    assert 'Serve sauce on the side' in child['presentation_rules']['toggles']
    assert api.get('/api/profile-suggestions').json() == []


def test_a_plan_that_breaks_the_period_is_sent_back_once_then_the_loop_fails(
    client, database, period, fake_model
):
    days = _days(client, period)
    broken = json.loads(meal_plan_json(days))
    broken['days'][0]['dinner'] = {'mode': 'takeout', 'recipe_key': None}  # the day is set to cook
    fake = fake_model(json.dumps(broken), json.dumps(broken))

    client(ADULT_ONE).post(
        f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}
    ).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_MEALS)

    assert job['status'] == 'failed'
    assert 'the day is set to cook' in job['detail']
    assert len(fake.calls) == 2, 'exactly one repair retry'
    assert 'That response has problems' in fake.calls[1][-1]['content']

    # Nothing reached the database.
    assert client(ADULT_ONE).get(f'/api/periods/{period["id"]}/plans').json()['meals']['draft'] is None


def test_a_repaired_second_attempt_is_accepted(client, database, period, fake_model):
    days = _days(client, period)
    broken = json.loads(meal_plan_json(days))
    broken['days'][0]['dinner'] = {'mode': 'away', 'recipe_key': None}
    fake_model(json.dumps(broken), meal_plan_json(days))

    client(ADULT_ONE).post(
        f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}
    ).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_MEALS)
    assert job['status'] == 'succeeded', job['detail']

    with database.worker_connection() as connection:
        call = connection.execute(
            'SELECT status FROM llm_call ORDER BY created_at DESC LIMIT 1'
        ).fetchone()
    assert call['status'] == 'repaired'


def test_a_fitness_loop_publishes_only_a_metric_free_summary_to_the_meal_loop(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    days = api.get(f'/api/periods/{period["id"]}').json()['availability']['days']
    fake_model(fitness_plan_json(days))

    api.post(
        f'/api/periods/{period["id"]}/generate',
        json={'loops': ['fitness'], 'own_fitness_only': True},
    ).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_FITNESS)
    assert job['status'] == 'succeeded', job['detail']

    with database.worker_connection() as connection:
        summary = connection.execute(
            'SELECT summary FROM training_summary WHERE period_id = %s', (period['id'],)
        ).fetchone()['summary']
    assert summary['notes'] == 'higher-protein target on training days'
    assert 'weight_lb' not in json.dumps(summary)
    assert set(summary) == {'training_days', 'notes'}


def test_the_summary_handed_to_the_meal_loop_may_not_smuggle_a_weight(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    days = api.get(f'/api/periods/{period["id"]}').json()['availability']['days']
    leaky = json.loads(fitness_plan_json(days))
    leaky['summary_for_meals']['notes'] = 'down 4 lb, keep protein high'
    fake_model(json.dumps(leaky), json.dumps(leaky))

    api.post(
        f'/api/periods/{period["id"]}/generate',
        json={'loops': ['fitness'], 'own_fitness_only': True},
    ).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_FITNESS)

    assert job['status'] == 'failed'
    assert 'must not mention weight' in job['detail']


def test_the_meal_loop_is_given_the_training_summary_but_never_the_sessions(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    availability = api.get(f'/api/periods/{period["id"]}').json()['availability']['days']
    days = _days(client, period)
    fake = fake_model(fitness_plan_json(availability), meal_plan_json(days))

    api.post(f'/api/periods/{period["id"]}/generate', json={}).raise_for_status()
    _run_one_job(database, jobs.GENERATE_FITNESS)
    _run_one_job(database, jobs.GENERATE_MEALS)

    meal_prompt = fake.calls[-1][-1]['content']
    assert 'higher-protein target on training days' in meal_prompt
    assert 'Back squat' not in meal_prompt
    assert '185' not in meal_prompt


def test_a_failed_loop_leaves_the_other_loops_draft_alone(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    availability = api.get(f'/api/periods/{period["id"]}').json()['availability']['days']
    days = _days(client, period)
    broken = json.loads(fitness_plan_json(availability))
    broken['sessions'][0]['date'] = '2099-01-01'
    fake_model(json.dumps(broken), json.dumps(broken), meal_plan_json(days))

    api.post(f'/api/periods/{period["id"]}/generate', json={}).raise_for_status()
    fitness = _run_one_job(database, jobs.GENERATE_FITNESS)
    meals = _run_one_job(database, jobs.GENERATE_MEALS)

    assert fitness['status'] == 'failed'
    assert meals['status'] == 'succeeded', meals['detail']
    plans = api.get(f'/api/periods/{period["id"]}/plans').json()
    assert plans['meals']['draft'] is not None
    assert plans['fitness']['draft'] is None

    retried = api.post(f'/api/jobs/{fitness["id"]}/retry')
    assert retried.status_code == 202


def test_the_spend_cap_stops_a_loop_before_the_model_is_called(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    fake = fake_model(meal_plan_json(_days(client, period)))
    api.put('/api/settings/monthly_spend_cap_usd', json={'value': 0.5}).raise_for_status()
    with database.worker_connection() as connection:
        connection.execute(
            """INSERT INTO llm_call (household_id, task, model, cost_usd, status)
               VALUES (%s, 'meals.generate', 'test', 1.00, 'ok')""",
            (HOUSEHOLD_ID,),
        )

    api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_MEALS)

    assert job['status'] == 'failed'
    assert 'cap' in job['detail']
    assert fake.calls == [], 'the cap is checked before any model is called'


def test_a_revision_replaces_only_the_night_it_was_asked_about(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    days = _days(client, period)
    fake_model(meal_plan_json(days))
    api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}).raise_for_status()
    _run_one_job(database, jobs.GENERATE_MEALS)
    draft = api.get(f'/api/periods/{period["id"]}/plans').json()['meals']['draft']

    revision = json.dumps(
        {
            'note': 'Something lighter on Sunday.',
            'replacements': [
                {'target': 'days[1].dinner', 'value': {'mode': 'cook', 'recipe_key': 'r1'}},
                {'target': 'summary', 'value': 'A completely different week'},
            ],
        }
    )
    fake_model(revision)
    api.post(
        f'/api/plans/{draft["id"]}/revise',
        json={'target': 'days[1].dinner', 'feedback_text': 'lighter please', 'chips': ['lighter']},
    ).raise_for_status()
    job = _run_one_job(database, jobs.REVISE_MEALS)

    assert job['status'] == 'succeeded', job['detail']
    assert 'refused' in job['detail']
    content = api.get(f'/api/periods/{period["id"]}/plans').json()['meals']['draft']['content']
    assert content['days'][1]['dinner']['recipe_key'] == 'r1'
    assert content['summary'] == 'One real dinner, the rest easy.', 'the summary was out of scope'


def test_a_no_cooking_period_skips_the_meal_loop_entirely(
    client, database, period, fake_model
):
    api = client(ADULT_ONE)
    days = api.get(f'/api/periods/{period["id"]}').json()['days']
    api.put(
        f'/api/periods/{period["id"]}/days',
        json=[
            {'day': day['day'], 'dinner_mode': 'away', 'lunch_mode': 'none', 'cook_time': 'normal'}
            for day in days
        ],
    ).raise_for_status()
    fake = fake_model(meal_plan_json([day['day'] for day in days]))

    api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}).raise_for_status()
    job = _run_one_job(database, jobs.GENERATE_MEALS)

    assert job['status'] == 'succeeded'
    assert 'No cooking is planned' in job['detail']
    assert fake.calls == [], 'no model call for a week with nothing to cook'


def test_every_call_is_logged_with_its_cost_and_model(client, database, period, fake_model):
    fake_model(meal_plan_json(_days(client, period)))
    api = client(ADULT_ONE)
    api.post(f'/api/periods/{period["id"]}/generate', json={'loops': ['meals']}).raise_for_status()
    _run_one_job(database, jobs.GENERATE_MEALS)

    calls = api.get('/api/llm-calls').json()
    assert calls['total'] == 1
    entry = calls['items'][0]
    assert entry['task'] == 'meals.generate'
    assert entry['status'] == 'ok'
    assert float(entry['cost_usd']) == 0.002
    assert entry['tokens_in'] == 100


def test_the_canned_plans_match_the_contracts_they_claim_to():
    """A guard on the fixtures themselves, so a contract change fails here first."""
    MealPlan.model_validate(json.loads(meal_plan_json(['2026-09-19'])))
    FitnessPlan.model_validate(json.loads(fitness_plan_json(['2026-09-21', '2026-09-23'])))
