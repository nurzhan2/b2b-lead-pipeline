"""Чистые части разведки: что считается «появившимся» и какие запросы проверяются."""

from workers.hh_apply.probe import candidate_queries, revealed_controls
from workers.hh_apply.tests.fakes import HIDDEN, LETTER, control


def test_only_visible_new_controls_count_as_revealed():
    before = [HIDDEN, control("BUTTON", qa="x", visible=False)]
    after = [HIDDEN, LETTER, control("TEXTAREA", name="hidden-draft", visible=False)]
    assert revealed_controls(before, after) == [LETTER]


def test_a_second_identical_control_is_revealed_too():
    first = control("TEXTAREA", name="letter")
    assert revealed_controls([first], [first, dict(first, index=1)]) == [dict(first, index=1)]


def test_candidate_queries_go_from_most_to_least_specific():
    queries = candidate_queries(control("TEXTAREA", qa="vacancy-response-popup-form-letter-input", name="letter"))
    assert queries == [
        '[data-qa="modal-overlay"] [data-qa="vacancy-response-popup-form-letter-input"]',
        '[data-qa="vacancy-response-popup-form-letter-input"]',
        '[data-qa="modal-overlay"] textarea[name="letter"]',
        '[data-qa="modal-overlay"] textarea',
    ]
