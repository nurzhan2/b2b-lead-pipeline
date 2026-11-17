"""Карточка подтверждения: по умолчанию не уходит ничего, письмо показывается целиком."""

import io

import pytest

from workers.hh_apply.confirm import CONFIRM_WORD, CancelledError, Candidate, confirm, parse_selection

LETTER = "Здравствуйте!\n\nСтрока два письма.\n" + "длинный абзац " * 30 + "\nКонец письма — последняя строка."


def candidate(vid="1001", **over):
    base = dict(vacancy_id=vid, url=f"https://hh.kz/vacancy/{vid}", title="Python-разработчик",
                company="ТОО «Ромашка»", letter=LETTER, resume_title="Python-разработчик",
                hh_said=("Такой отклик может получить отказ Английский ниже уровня.",))
    base.update(over)
    return Candidate(**base)


def answer(*lines):
    return io.StringIO("".join(f"{line}\n" for line in lines))


def test_the_card_shows_the_whole_letter_and_what_hh_said():
    card = candidate().render()
    assert "Конец письма — последняя строка." in card
    assert "Python-разработчик" in card and "hh пишет" in card


def test_selected_numbers_and_the_word_produce_approvals_carrying_the_exact_letter():
    out = io.StringIO()
    approvals = confirm([candidate("1"), candidate("2")], stream_in=answer("2", CONFIRM_WORD), stream_out=out)
    assert [a.vacancy_id for a in approvals] == ["2"]
    assert approvals[0].letter == LETTER
    assert approvals[0].resume_title == "Python-разработчик"
    assert "Конец письма" in out.getvalue()


def test_all_is_accepted_but_still_needs_the_word():
    approvals = confirm([candidate("1"), candidate("2")], stream_in=answer("все", CONFIRM_WORD), stream_out=io.StringIO())
    assert len(approvals) == 2


def test_an_empty_line_selects_nothing():
    with pytest.raises(CancelledError, match="ничего не выбрано"):
        confirm([candidate()], stream_in=answer(""), stream_out=io.StringIO())


def test_a_wrong_word_sends_nothing():
    with pytest.raises(CancelledError):
        confirm([candidate()], stream_in=answer("1", "y"), stream_out=io.StringIO())


def test_closed_input_sends_nothing():
    with pytest.raises(CancelledError, match="ввод закрыт"):
        confirm([candidate()], stream_in=io.StringIO(""), stream_out=io.StringIO())


def test_garbage_is_asked_again():
    out = io.StringIO()
    approvals = confirm([candidate()], stream_in=answer("7", "²", "1", CONFIRM_WORD), stream_out=out)
    assert len(approvals) == 1
    assert out.getvalue().count("Нужны номера") == 2


def test_the_same_vacancy_twice_is_refused_before_anything_is_asked():
    with pytest.raises(CancelledError, match="дважды"):
        confirm([candidate("1"), candidate("1")], stream_in=answer("1", CONFIRM_WORD), stream_out=io.StringIO())


@pytest.mark.parametrize("raw, expected", [("1 3", {1, 3}), ("1,3", {1, 3}), ("ВСЕ", {1, 2, 3}), ("", set()),
                                           ("4", None), ("0", None), ("a", None)])
def test_parse_selection(raw, expected):
    assert parse_selection(raw, 3) == expected


def test_characters_outside_the_console_codepage_are_announced():
    card = candidate(company="Қазақ ТОО").render()
    assert "?аза?" in card and "уйдёт оригинал" in card
