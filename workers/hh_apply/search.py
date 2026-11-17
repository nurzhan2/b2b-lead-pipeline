"""Поиск вакансий через обычный UI поиска hh — теми же страницами, что видит человек.

Открывается ``https://<host>/search/vacancy?text=…&area=…`` в окне владельца, и
из выдачи берутся id вакансий. Не API и не скрытые запросы: это ровно то, что
человек сделал бы руками, в том же темпе (пауза между страницами).

**Из выдачи читаются только ссылки вида ``/vacancy/<id>``** — по адресу, а не
по разметке карточек. Селекторов карточек выдачи никто не измерял, а угадывать
их — то, чего этот пакет не делает. Всё остальное о вакансии (название,
компания, условия, откликались ли уже) агент читает со страницы самой вакансии,
из её состояния, — там это измерено. Если в выдачу попадёт лишняя ссылка
(«вакансия дня»), она пройдёт те же стопы и ту же карточку подтверждения, где
человек её просто не выберет.

keywords.yml — человекочитаемый файл: список запросов (текст + id региона hh)
и стоп-слова в названии вакансии.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

import yaml

from .browser import open_hh_page

#: Ссылка на вакансию в href: абсолютная (региональный поддомен) или относительная.
VACANCY_HREF: Final[re.Pattern[str]] = re.compile(
    r'href="(?:https?://[^"/]+)?/vacancy/(\d{4,})(?=[?#"/])', re.IGNORECASE
)


@dataclass(frozen=True)
class Query:
    text: str
    #: id региона hh (строкой): 160 — Алматы, 159 — Астана. None — без региона.
    area: str | None = None


@dataclass(frozen=True)
class Keywords:
    queries: tuple[Query, ...]
    #: Слова, при которых вакансия не показывается вовсе (сравнение без регистра).
    exclude_title_words: tuple[str, ...] = ()


def load_keywords(path: Path) -> Keywords:
    """Разобрать keywords.yml. Кривой файл — ValueError с понятным текстом."""
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: ожидался словарь с ключом queries")
    raw_queries = data.get("queries") or []
    if not isinstance(raw_queries, list):
        raise ValueError(f"{path.name}: queries должен быть списком")
    queries: list[Query] = []
    for item in raw_queries:
        if isinstance(item, str) and item.strip():
            queries.append(Query(text=item.strip()))
            continue
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            raise ValueError(f"{path.name}: у запроса нет text: {item!r}")
        area = item.get("area")
        queries.append(Query(text=str(item["text"]).strip(), area=None if area is None else str(area).strip()))
    words = data.get("exclude_title_words") or []
    if not isinstance(words, list):
        raise ValueError(f"{path.name}: exclude_title_words должен быть списком")
    return Keywords(tuple(queries), tuple(str(word).strip().casefold() for word in words if str(word).strip()))


def search_url(host: str, query: Query, page: int = 0) -> str:
    """Адрес страницы выдачи — тот же, что строит поиск hh в браузере."""
    params: dict[str, str | int] = {"text": query.text}
    if query.area:
        params["area"] = query.area
    if page:
        params["page"] = page
    return f"https://{host}/search/vacancy?{urlencode(params)}"


def vacancy_ids_in_html(page_html: str) -> list[str]:
    """Id вакансий из ссылок выдачи, по порядку, без повторов."""
    return list(dict.fromkeys(VACANCY_HREF.findall(page_html or "")))


def excluded_by(title: str | None, words: tuple[str, ...]) -> str | None:
    """Стоп-слово из keywords.yml, которое есть в названии, или None."""
    lowered = (title or "").casefold()
    return next((word for word in words if word in lowered), None)


def search(
    page: Any,
    *,
    host: str,
    site: str,
    keywords: Keywords,
    pages: int,
    limit: int,
    pause: Callable[[], None],
) -> list[str]:
    """Пройти запросы из keywords.yml и собрать id вакансий, не больше ``limit``."""
    found: list[str] = []
    first = True
    for query in keywords.queries:
        for number in range(pages):
            if len(found) >= limit:
                return found
            if not first:
                pause()
            first = False
            open_hh_page(page, search_url(host, query, number), site=site)
            ids = vacancy_ids_in_html(page.content())
            fresh = [vid for vid in ids if vid not in found]
            found.extend(fresh[: max(0, limit - len(found))])
            print(f"  поиск «{query.text}» стр. {number + 1}: вакансий {len(ids)}, новых {len(fresh)}")
            if not ids:
                break
    return found
