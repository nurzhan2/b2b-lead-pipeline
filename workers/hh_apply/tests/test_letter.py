"""Шаблон письма и подстановка плейсхолдеров. Правило одно: лучше не напечатать, чем напечатать не то."""

from pathlib import Path

import pytest

from workers.hh_apply.letter import (
    PLACEHOLDERS,
    LetterUnavailableError,
    Template,
    load_template,
    render,
    warnings,
)

MODULE_DIR = Path(__file__).resolve().parents[1]


def template(text: str) -> Template:
    return Template(text=text, path=Path("cover_letter.md"))


# --- подстановка ------------------------------------------------------------


def test_all_three_placeholders_are_filled_from_the_vacancy_and_env():
    letter = render(
        template("Здравствуйте! Интересна вакансия «{position}» в {company}.\n\n{my_name}"),
        company="ТОО «Ромашка»", position="Python-разработчик", my_name="Нуржан",
    )
    assert letter == "Здравствуйте! Интересна вакансия «Python-разработчик» в ТОО «Ромашка».\n\nНуржан"


def test_a_placeholder_used_twice_is_filled_twice():
    letter = render(template("{company} — это {company}"), company="Ромашка", position="x", my_name="y")
    assert letter == "Ромашка — это Ромашка"


def test_whitespace_inside_hh_values_is_collapsed():
    letter = render(template("в {company}"), company="  ТОО\n  Ромашка  ", position="x", my_name="y")
    assert letter == "в ТОО Ромашка"


def test_braces_that_are_not_placeholders_are_left_alone():
    """str.format упал бы на одиночной скобке; письмо с кодом или смайликом — обычное дело."""
    text = "Пишу dict = {1: 2} и { } — и это не плейсхолдеры. {my_name}"
    assert render(template(text), company="c", position="p", my_name="Н") == text.replace("{my_name}", "Н")


def test_unused_placeholders_need_no_value():
    assert render(template("Просто текст."), company=None, position=None, my_name="") == "Просто текст."


# --- без шаблона ------------------------------------------------------------


def test_no_file_means_no_template(tmp_path):
    assert load_template(tmp_path / "cover_letter.md") is None


def test_an_empty_file_means_no_template(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text("   \n\n  \n", encoding="utf-8")
    assert load_template(path) is None


def test_a_file_with_only_the_instructions_comment_means_no_template(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text("<!--\nнапишите здесь письмо\n-->\n", encoding="utf-8")
    assert load_template(path) is None


def test_comments_are_cut_and_never_reach_the_employer(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text("<!-- подсказка -->\nЗдравствуйте, {company}!\n<!-- ещё одна\nмногострочная -->\nПока.",
                    encoding="utf-8")
    loaded = load_template(path)
    assert loaded is not None
    assert "подсказка" not in loaded.text and "<!--" not in loaded.text
    assert loaded.text == "Здравствуйте, {company}!\n\nПока."


def test_a_bom_and_windows_line_endings_are_tolerated(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_bytes("﻿Строка 1\r\nСтрока 2\r\n".encode("utf-8"))
    assert load_template(path).text == "Строка 1\nСтрока 2"


# --- когда письмо собирать нельзя ---------------------------------------------


def test_a_typo_in_a_placeholder_stops_the_letter_instead_of_sending_braces():
    with pytest.raises(LetterUnavailableError) as info:
        render(template("Здравствуйте, {compnay}!"), company="Ромашка", position="p", my_name="n")
    assert "{compnay}" in str(info.value)
    assert info.value.setup is True  # виноват шаблон, а не вакансия


def test_an_unknown_company_stops_the_letter_and_it_is_about_the_vacancy():
    with pytest.raises(LetterUnavailableError) as info:
        render(template("в {company}"), company=None, position="p", my_name="n")
    assert "{company}" in str(info.value)
    assert info.value.setup is False


def test_an_empty_my_name_is_a_setup_problem():
    with pytest.raises(LetterUnavailableError) as info:
        render(template("С уважением, {my_name}"), company="c", position="p", my_name="   ")
    assert "MY_NAME" in str(info.value)
    assert info.value.setup is True


def test_a_letter_longer_than_hhs_limit_is_not_cut():
    with pytest.raises(LetterUnavailableError) as info:
        render(template("x" * 50), company="c", position="p", my_name="n", max_length=40)
    assert "50 > 40" in str(info.value)


# --- предупреждения на карточке -------------------------------------------------


def test_links_and_at_signs_are_warnings_not_stops():
    assert warnings("Портфолио: github.com/me, почта me@mail.ru")
    assert warnings("Работаю с Python 3.12, Django и т.д.") == ()


# --- заготовка в репозитории ------------------------------------------------------


def test_the_example_template_uses_only_known_placeholders_and_renders():
    example = load_template(MODULE_DIR / "cover_letter.example.md")
    assert example is not None
    assert set(example.placeholders) <= set(PLACEHOLDERS)
    letter = render(example, company="ТОО Ромашка", position="Python-разработчик", my_name="Нуржан")
    assert "ТОО Ромашка" in letter and "Нуржан" in letter and "{" not in letter
