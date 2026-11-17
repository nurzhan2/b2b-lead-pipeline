"""Стопы перед откликом: только проверяемые факты, а «не знаю» — человеку."""

from workers.hh_apply.gate import Verdict, confirmed_after_send, decide
from workers.hh_apply.page_state import Negotiations, ResponseFacts, VacancyStatus

OPEN = VacancyStatus(archived=False, closed_for_applicants=False)
FACTS = ResponseFacts(letter_required=False, has_test=False, letter_max_length=10000)
FRESH = Negotiations(total=0, topics=0)


def run(**over):
    base = dict(negotiations=FRESH, facts=FACTS, status=OPEN, has_letter=True, letter_field_known=True)
    base.update(over)
    return decide(**base)


def test_everything_in_order_means_proceed():
    assert run().verdict is Verdict.PROCEED


def test_an_existing_application_is_a_skip():
    decision = run(negotiations=Negotiations(total=1, topics=1))
    assert decision.verdict is Verdict.SKIP
    assert "negotiations.total = 1" in decision.reason


def test_an_unreadable_count_goes_to_a_person_not_to_sending():
    decision = run(negotiations=None)
    assert decision.verdict is Verdict.MANUAL
    assert not decision.setup


def test_hh_contradicting_itself_goes_to_a_person():
    assert run(negotiations=Negotiations(total=0, topics=1)).verdict is Verdict.MANUAL


def test_archived_and_closed_are_skips():
    assert run(status=VacancyStatus(archived=True, closed_for_applicants=False)).verdict is Verdict.SKIP
    assert run(status=VacancyStatus(archived=False, closed_for_applicants=True)).verdict is Verdict.SKIP


def test_the_application_check_comes_before_everything_else():
    decision = run(negotiations=Negotiations(total=2, topics=0),
                   status=VacancyStatus(archived=True, closed_for_applicants=True))
    assert "отклик уже отправлен" in decision.reason


def test_an_employer_test_goes_to_a_person():
    decision = run(facts=ResponseFacts(letter_required=False, has_test=True, letter_max_length=10000))
    assert decision.verdict is Verdict.MANUAL and "тест" in decision.reason


def test_unreadable_facts_go_to_a_person():
    assert run(facts=None).verdict is Verdict.MANUAL


def test_no_letter_template_goes_to_a_person_as_a_setup_problem():
    decision = run(has_letter=False)
    assert decision.verdict is Verdict.MANUAL and decision.setup


def test_no_letter_even_where_hh_does_not_require_one():
    """Этот агент откликается только с письмом — так договорено в задаче."""
    assert run(has_letter=False, facts=FACTS).verdict is Verdict.MANUAL


def test_an_unmeasured_letter_field_goes_to_a_person_as_a_setup_problem():
    decision = run(letter_field_known=False)
    assert decision.verdict is Verdict.MANUAL and decision.setup


def test_after_sending_any_trace_counts_as_arrived():
    assert confirmed_after_send(Negotiations(total=1, topics=0))
    assert confirmed_after_send(Negotiations(total=0, topics=1))
    assert not confirmed_after_send(Negotiations(total=0, topics=0))
    assert not confirmed_after_send(None)
