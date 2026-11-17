"""Разбор состояния страницы. Главное: «откликались ли уже» — ЧИСЛО negotiations.total."""

import pytest

from workers.hh_apply.page_state import (
    already_applied,
    printable,
    read_facts,
    read_form_warnings,
    read_meta,
    read_negotiations,
    read_state,
    read_status,
    vacancy_id_from_url,
)
from workers.hh_apply.tests.fakes import VACANCY, page_html, state

# --- negotiations.total -------------------------------------------------------


def test_total_zero_means_not_applied():
    negotiations = read_negotiations(state(total=0), VACANCY)
    assert negotiations.total == 0 and not negotiations.exists
    assert already_applied(state(total=0), VACANCY) is False


def test_total_one_means_applied():
    assert read_negotiations(state(total=1, topics=1), VACANCY).exists
    assert already_applied(state(total=1, topics=1), VACANCY) is True


def test_the_already_applied_key_is_ignored_because_it_lies():
    """WWAO, вакансия 133542745: total == 1, а alreadyApplied == false."""
    payload = state(total=1, already_applied_flag=False)
    assert already_applied(payload, VACANCY) is True


def test_already_applied_true_with_total_zero_does_not_invent_an_application():
    assert already_applied(state(total=0, already_applied_flag=True), VACANCY) is False


def test_a_conversation_beside_total_zero_is_contradictory_and_counts_as_a_stop():
    negotiations = read_negotiations(state(total=0, topics=1), VACANCY)
    assert negotiations.contradictory
    assert negotiations.exists  # список может только ДОБАВИТЬ стоп


@pytest.mark.parametrize("total", [None, "1", True, False, -1, 1.0])
def test_a_total_of_the_wrong_shape_is_unknown_not_zero(total):
    assert read_negotiations(state(total=total), VACANCY) is None
    assert already_applied(state(total=total), VACANCY) is None


def test_no_entry_for_this_vacancy_is_unknown():
    assert read_negotiations(state(), "999999") is None
    assert read_negotiations({}, VACANCY) is None
    assert read_negotiations({"applicantVacancyResponseStatuses": []}, VACANCY) is None


def test_the_id_is_looked_up_as_a_string():
    assert read_negotiations(state(total=1), int(VACANCY)) is not None  # type: ignore[arg-type]


# --- состояние из HTML ----------------------------------------------------------


def test_state_is_read_from_the_html_escaped_template():
    assert read_state(page_html(state(total=1)))["applicantVacancyResponseStatuses"][VACANCY]["negotiations"]["total"] == 1


def test_no_marker_or_broken_json_is_none():
    assert read_state("<html></html>") is None
    assert read_state('<template id="HH-Lux-InitialState">{нет</template>') is None


# --- условия отклика, статус, мета ------------------------------------------------


def test_response_facts():
    facts = read_facts(state(has_test=True, letter_required=True), VACANCY)
    assert facts.has_test and facts.letter_required and facts.letter_max_length == 10000


def test_facts_of_unknown_shape_are_none():
    broken = state()
    del broken["applicantVacancyResponseStatuses"][VACANCY]["test"]
    assert read_facts(broken, VACANCY) is None


def test_status_reads_nested_archive_and_closed_flag():
    assert read_status(state(archived=True)).archived
    assert read_status(state(closed=True)).closed_for_applicants
    assert read_status({"vacancyView": {"archived": True}}).archived
    assert not read_status({}).archived


def test_meta_prefers_the_visible_company_name_and_collapses_spaces():
    meta = read_meta(state(title="  Python\n разработчик ", company="ТОО «Ромашка»"))
    assert meta.title == "Python разработчик"
    assert meta.company == "ТОО «Ромашка»"
    assert read_meta({}).title is None


# --- слова формы и прочее ----------------------------------------------------------


def test_form_warnings_are_read_by_text():
    modal = (
        "Отклик на вакансию\nAI/ML-инженер\n"
        "Чтобы откликнуться на эту вакансию, поменяйте видимость резюме на «Видно компаниям-клиентам HeadHunter»\n"
        "Такой отклик может получить отказ\nАнглийский язык в резюме ниже обязательного уровня.\n"
        "Добавить сопроводительное\nОткликнуться"
    )
    said = read_form_warnings(modal)
    assert len(said) == 2
    assert "видимость резюме" in said[0]
    assert said[1].startswith("Такой отклик может получить отказ Английский язык")


def test_printable_survives_characters_outside_cp1251():
    assert printable("Қазақстан 🙂") == "?аза?стан ?"


def test_vacancy_id_is_read_from_the_path_not_the_backurl():
    assert vacancy_id_from_url("https://almaty.hh.kz/vacancy/136773120?from=x") == "136773120"
    assert vacancy_id_from_url("https://hh.kz/account/login?backurl=%2Fvacancy%2F136773120") is None
