"""Поиск: из выдачи hh берутся только ссылки /vacancy/<id>, по адресу, а не по разметке карточек."""

from pathlib import Path

import pytest

from workers.hh_apply.search import Query, excluded_by, load_keywords, search_url, vacancy_ids_in_html

SERP = """
<a data-qa="serp-item__title" href="https://almaty.hh.kz/vacancy/136131345?query=python&amp;hhtmFrom=vacancy_search_list">Python</a>
<a data-qa="vacancy-serp__vacancy_response" href="/applicant/vacancy_response?vacancyId=136131345&amp;employerId=5991214">Откликнуться</a>
<a href="https://hh.kz/vacancy/137025931">Другая</a>
<a href="https://almaty.hh.kz/vacancy/136131345#details">дубль</a>
<a href="/vacancy/136907917">относительная</a>
<a href="https://hh.kz/vacancy/123/">слишком короткий id</a>
<span>/vacancy/999999999 — это текст, не ссылка</span>
"""


def test_vacancy_ids_are_taken_from_links_in_order_without_duplicates():
    assert vacancy_ids_in_html(SERP) == ["136131345", "137025931", "136907917"]


def test_the_apply_link_in_the_serp_is_not_a_vacancy_link():
    assert vacancy_ids_in_html('<a href="/applicant/vacancy_response?vacancyId=136131345">x</a>') == []


def test_search_url_is_the_ordinary_hh_search_page():
    url = search_url("hh.kz", Query("python разработчик", "160"))
    assert url.startswith("https://hh.kz/search/vacancy?")
    assert "text=python+%D1%80%D0%B0%D0%B7%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D1%87%D0%B8%D0%BA" in url
    assert "area=160" in url and "page=" not in url
    assert "page=2" in search_url("hh.kz", Query("x"), 2)


def test_the_shipped_keywords_file_loads():
    keywords = load_keywords(Path(__file__).resolve().parents[1] / "keywords.yml")
    assert keywords.queries and all(q.text for q in keywords.queries)
    assert keywords.queries[0].area == "160"  # Алматы, id с api.hh.ru/areas/40
    assert "senior" in keywords.exclude_title_words


def test_a_malformed_keywords_file_is_a_clear_error(tmp_path):
    path = tmp_path / "keywords.yml"
    path.write_text("queries:\n  - area: '160'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="нет text"):
        load_keywords(path)


def test_plain_strings_are_accepted_as_queries(tmp_path):
    path = tmp_path / "keywords.yml"
    path.write_text("queries:\n  - python\n", encoding="utf-8")
    assert load_keywords(path).queries == (Query("python"),)


def test_exclusion_words_match_the_title_case_insensitively():
    assert excluded_by("Senior Python Developer", ("senior", "lead")) == "senior"
    assert excluded_by("Python-разработчик", ("senior",)) is None
    assert excluded_by(None, ("senior",)) is None
