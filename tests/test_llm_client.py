"""Prompt assembly, JSON recovery, and the rule that a revision stays inside its target."""
import pytest

from health.domain.contracts import ReviseResult
from health.llm.client import extract_json, render
from health.llm.jobs import _apply_revision

DRAFT = {
    'summary': 'A week',
    'days': [
        {'date': '2026-09-21', 'dinner': {'mode': 'cook', 'recipe_key': 'r1'}},
        {'date': '2026-09-22', 'dinner': {'mode': 'cook', 'recipe_key': 'r2'}},
    ],
    'recipes': [{'key': 'r1', 'title': 'Chilli'}, {'key': 'r2', 'title': 'Curry'}],
}


def test_a_template_is_filled_from_nested_context():
    template = 'Plan {{period.start}} to {{period.end}} for {{count}} people.'
    filled = render(template, {'period': {'start': '2026-09-19', 'end': '2026-09-25'}, 'count': 4})
    assert filled == 'Plan 2026-09-19 to 2026-09-25 for 4 people.'


def test_a_structured_value_is_rendered_as_json():
    assert '"cook"' in render('{{days}}', {'days': [{'mode': 'cook'}]})


def test_an_unknown_variable_renders_empty_rather_than_leaking_its_name():
    assert render('a{{nope}}b', {}) == 'ab'


@pytest.mark.parametrize(
    'text',
    [
        '{"summary": "ok"}',
        '```json\n{"summary": "ok"}\n```',
        'Here you go:\n{"summary": "ok"}\nHope that helps.',
    ],
)
def test_json_is_recovered_from_however_the_model_wrapped_it(text):
    assert extract_json(text) == {'summary': 'ok'}


def test_unparseable_output_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        extract_json('I would rather not.')


def test_a_revision_applies_inside_its_target():
    result = ReviseResult(
        note='Lighter',
        replacements=[{'target': 'days[0].dinner', 'value': {'mode': 'cook', 'recipe_key': 'r3'}}],
    )
    revised, refused = _apply_revision(DRAFT, 'days[0].dinner', result)
    assert revised['days'][0]['dinner']['recipe_key'] == 'r3'
    assert refused == []


def test_a_revision_that_reaches_outside_its_target_is_refused():
    result = ReviseResult(
        replacements=[
            {'target': 'days[0].dinner', 'value': {'mode': 'cook', 'recipe_key': 'r3'}},
            {'target': 'days[1].dinner', 'value': {'mode': 'takeout'}},
            {'target': 'summary', 'value': 'Completely different week'},
        ]
    )
    revised, refused = _apply_revision(DRAFT, 'days[0].dinner', result)
    assert revised['days'][1]['dinner']['mode'] == 'cook'
    assert revised['summary'] == 'A week'
    assert set(refused) == {'days[1].dinner', 'summary'}


def test_the_original_draft_is_never_mutated_in_place():
    result = ReviseResult(replacements=[{'target': 'days[0].dinner', 'value': {'mode': 'away'}}])
    _apply_revision(DRAFT, 'days[0].dinner', result)
    assert DRAFT['days'][0]['dinner']['mode'] == 'cook'


def test_a_plan_scoped_revision_replaces_the_whole_document():
    replacement = {'summary': 'New', 'days': [], 'recipes': []}
    result = ReviseResult(replacements=[{'target': 'plan', 'value': replacement}])
    revised, refused = _apply_revision(DRAFT, 'plan', result)
    assert revised == replacement
    assert refused == []


def test_a_replacement_addressing_something_that_does_not_exist_is_refused():
    result = ReviseResult(replacements=[{'target': 'days[9].dinner', 'value': {'mode': 'away'}}])
    revised, refused = _apply_revision(DRAFT, 'plan', result)
    assert refused == ['days[9].dinner']
    assert revised == DRAFT
