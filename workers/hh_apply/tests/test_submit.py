"""Поток одного отклика на моке страницы hh.

Здесь проверяется главное правило: форма или элемент, который агент не узнал,
— это needs_manual, и кнопка отправки при этом НЕ нажимается.
"""

import pytest

from workers.hh_apply import hh_selectors as sel
from workers.hh_apply.browser import NetGuard
from workers.hh_apply.confirm import Approval
from workers.hh_apply.submit import (
    AlreadyAppliedError,
    NeedsManualError,
    UnconfirmedError,
    preview,
    send,
    unknown_controls,
)
from workers.hh_apply.tests.fakes import (
    BASELINE,
    HIDDEN,
    LETTER,
    LETTER_QUERY,
    URL,
    VACANCY,
    FakeHH,
    control,
)

LETTER_TEXT = "Здравствуйте!\nИнтересна вакансия «Python-разработчик» в ТОО «Ромашка».\n\nНуржан"


@pytest.fixture
def measured_letter_field(monkeypatch):
    """Поле письма «как после разведки». По умолчанию оно не измерено — см. отдельный тест."""
    monkeypatch.setattr(sel, "LETTER_FIELD", sel.Selector("letter_field", LETTER_QUERY, sel.Where.LETTER, "x"))


def approval(**over) -> Approval:
    base = dict(vacancy_id=VACANCY, url=URL, title="Python-разработчик", company="ТОО «Ромашка»",
                letter=LETTER_TEXT, resume_title="Python-разработчик", card_digest="d")
    base.update(over)
    return Approval(**base)


def submit_clicked(page: FakeHH) -> bool:
    return sel.SUBMIT_BUTTON.query in page.clicks


# --- узнавание формы -------------------------------------------------------------


def test_unknown_controls_counts_duplicates():
    sig = sel.signature(LETTER)
    assert unknown_controls([sig], (sig,)) == []
    assert unknown_controls([sig, sig], (sig,)) == [sig]
    assert unknown_controls([], (sig,)) == []


def test_preview_reads_the_resume_and_hhs_words_without_sending():
    page = FakeHH()
    page.goto(URL)
    result = preview(page, VACANCY, BASELINE)
    assert result.resume_title == "Python-разработчик"
    assert not submit_clicked(page)


def test_preview_sends_an_unrecognised_form_to_a_person():
    page = FakeHH(before=(HIDDEN, control("INPUT", type_="radio", name="resume_choice")))
    page.goto(URL)
    with pytest.raises(NeedsManualError, match="нет в измерении"):
        preview(page, VACANCY, BASELINE)
    assert not submit_clicked(page)


def test_no_known_apply_button_is_needs_manual():
    page = FakeHH(apply_link=False)
    page.goto(URL)
    with pytest.raises(NeedsManualError, match="нет кнопки"):
        preview(page, VACANCY, BASELINE)


def test_an_apply_button_pointing_at_another_vacancy_is_not_clicked():
    page = FakeHH(href="/applicant/vacancy_response?vacancyId=999999999")
    page.goto(URL)
    with pytest.raises(NeedsManualError, match="не на вакансию"):
        preview(page, VACANCY, BASELINE)
    assert sel.APPLY_LINK.query not in page.clicks


# --- отправка -----------------------------------------------------------------------


def test_happy_path_types_exactly_the_approved_letter_inside_the_window(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard)
    send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert page.fills == [(LETTER_QUERY, LETTER_TEXT)]
    assert page.armed_at_submit == VACANCY  # отправка нажата только внутри окна подтверждения
    assert guard.armed_for is None          # и окно закрылось
    assert page.clicks.count(sel.SUBMIT_BUTTON.query) == 1


def test_total_is_checked_before_the_click_already_applied_means_nothing_is_touched(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(total=1, guard=guard)
    with pytest.raises(AlreadyAppliedError):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert page.clicks == []


def test_total_is_checked_again_after_sending(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard, counts_after_send=False)
    with pytest.raises(UnconfirmedError, match="Запрос ушёл"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert submit_clicked(page)
    assert page.loads == 2  # страница открыта заново — старая показала бы старые числа


def test_a_letter_field_that_never_appears_is_needs_manual(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard, revealed=())
    with pytest.raises(NeedsManualError, match="поле письма не появилось"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert page.fills == [] and not submit_clicked(page)


def test_something_extra_appearing_with_the_letter_field_is_needs_manual(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard, revealed=(LETTER, control("INPUT", type_="checkbox", name="agree")))
    with pytest.raises(NeedsManualError, match="не то, что в измерении"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert page.fills == [] and not submit_clicked(page)


def test_an_unrecognised_form_at_send_time_is_needs_manual(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard, before=(HIDDEN, control("SELECT", name="resume")))
    with pytest.raises(NeedsManualError, match="нет в измерении"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert not submit_clicked(page)


def test_a_different_resume_than_the_confirmed_one_is_needs_manual(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard, resume="ML-инженер")
    with pytest.raises(NeedsManualError, match="другое резюме"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert not submit_clicked(page)


def test_a_field_that_does_not_hold_the_typed_letter_is_not_submitted(measured_letter_field):
    guard = NetGuard()
    page = FakeHH(guard=guard, typed_transform=lambda text: text[:10])
    with pytest.raises(NeedsManualError, match="не тот текст"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert not submit_clicked(page)


def test_without_a_measured_letter_field_nothing_is_even_opened():
    """Состояние по умолчанию, до разведки: селектор поля письма пуст — не угадываем."""
    assert sel.LETTER_FIELD.query == ""
    guard = NetGuard()
    page = FakeHH(guard=guard)
    with pytest.raises(NeedsManualError, match="не измерено"):
        send(page, approval(), guard, site="hh.kz", baseline=BASELINE)
    assert page.loads == 0 and page.clicks == []
