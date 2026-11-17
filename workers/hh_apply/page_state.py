"""Чтение состояния страницы hh: откликались ли уже, можно ли откликаться, что это за вакансия.

Страница вакансии поднимает свой фронтенд из JSON в скрытом шаблоне
``HH-Lux-InitialState``. Всё, что агенту нужно знать до клика, лежит там, и
читается оттуда — не из разметки, не из текста кнопок.

**Откликались ли уже — это ЧИСЛО.** Измерено в WWAO 2026-09-06 под аккаунтом
владельца на двух вакансиях (``Who_wants_an_offer/agent/state_page.py``)::

    "applicantVacancyResponseStatuses": {
      "136962420": {"negotiations": {"topicList": [], "total": 0, ...}, ...},
      "133542745": {"negotiations": {"topicList": [{...}], "total": 1, ...}, ...}
    }

``negotiations.total >= 1`` — отклик есть, ``0`` — нет. Две соседние вещи
выглядят как ответ и им не являются, обе измерены там же:

* кнопка. На вакансии с откликом ``vacancy-response-link-top`` нет вообще, там
  «Отклик другим резюме» — hh разрешает повторный отклик, так что наличие
  кнопки не говорит ничего;
* ключ, буквально названный ``alreadyApplied``. На вакансии 133542745 с
  ``total == 1`` он был ``false``. Имя ключа — не измерение.

Поэтому ответ трёхзначный: ``None`` значит «страница ответила в форме, которой
этот код не знает», и такая вакансия уходит человеку. Схема, в которой
непонятная форма читается как «не откликались», начинает откликаться повторно
— на всё сразу, в тот день, когда hh переименует ключ.

Список переписок (``topicList``) — второй стоп и никогда не второе разрешение:
непустой список при ``total == 0`` никто не измерял, и такое разногласие
решается в сторону «не отправлять».

Названия вакансии и компании — из ``vacancyView``: поля ``name`` и
``company.visibleName`` / ``company.name`` разбирает модель краулера WWAO
(``backend/app/sources/hh.py``, ``HHVacancyView``/``HHCompany``), то есть они
тоже взяты из измеренной формы, а не придуманы. Код здесь с тем проектом не
общий: те программы не должны быть связаны, и эта тоже.
"""

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Any, Final

STATE_MARKER: Final[re.Pattern[str]] = re.compile(
    r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', re.DOTALL
)

#: Консоль Windows у владельца — cp1251. Всё, что цитируется со страницы hh
#: (название, компания, слова формы), сводится к ней перед печатью: один символ
#: вне кодовой страницы роняет прогон посреди пачки. Казахские ә ғ қ ң ө ұ ү һ
#: в названиях компаний на hh.kz — обычное дело.
CONSOLE_ENCODING: Final[str] = "cp1251"

#: Две фразы формы отклика, измеренные в WWAO 2026-09-06/07. Ни одна не
#: останавливает отправку — вторая (про видимость резюме) до 07.09 считалась
#: отказом, и это было неверно: отклик с ней уходит, счётчик 0 -> 1
#: (WWAO ``agent/evidence/20260907-send-under-visibility-notice.json``). Здесь
#: они только читаются и показываются человеку.
REJECTION_ANCHOR: Final[str] = "может получить отказ"
VISIBILITY_WORDS: Final[tuple[str, ...]] = ("видимост", "резюме")


def printable(text: str | None) -> str:
    """Текст hh, сведённый к тому, что консоль способна напечатать."""
    return (text or "").encode(CONSOLE_ENCODING, errors="replace").decode(CONSOLE_ENCODING)


def read_state(page_html: str) -> dict[str, Any] | None:
    """Загрузочное состояние hh или None, если маркера нет на месте."""
    match = STATE_MARKER.search(page_html or "")
    if match is None:
        return None
    try:
        decoded = json.loads(html_lib.unescape(match.group(1)))
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _status_entry(state: dict[str, Any], vacancy_id: str) -> dict[str, Any] | None:
    statuses = state.get("applicantVacancyResponseStatuses")
    if not isinstance(statuses, dict):
        return None
    entry = statuses.get(str(vacancy_id))
    return entry if isinstance(entry, dict) else None


@dataclass(frozen=True)
class Negotiations:
    """Собственный счётчик hh по откликам на эту вакансию."""

    #: ``negotiations.total`` — измеренный ответ на «откликались ли уже».
    total: int
    #: Сколько записей было в ``topicList``.
    topics: int

    @property
    def exists(self) -> bool:
        """Отклик есть — по любому из двух признаков. Список может только добавить стоп."""
        return self.total >= 1 or self.topics > 0

    @property
    def contradictory(self) -> bool:
        """``total == 0`` рядом с непустым списком переписок. Никто не измерял, что это значит."""
        return self.total == 0 and self.topics > 0


def read_negotiations(state: dict[str, Any], vacancy_id: str) -> Negotiations | None:
    """Счётчик откликов или None, когда форма ответа незнакома. None — это стоп, а не ноль."""
    entry = _status_entry(state, vacancy_id)
    if entry is None:
        return None
    negotiations = entry.get("negotiations")
    if not isinstance(negotiations, dict):
        return None
    total = negotiations.get("total")
    # bool — это int в Python, и true прочиталось бы как 1.
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return None
    topics = negotiations.get("topicList")
    count = len([t for t in topics if isinstance(t, dict)]) if isinstance(topics, list) else 0
    return Negotiations(total=total, topics=count)


def already_applied(state: dict[str, Any], vacancy_id: str) -> bool | None:
    """True / False / **неизвестно**. Решается только числом, не кнопкой и не alreadyApplied."""
    negotiations = read_negotiations(state, vacancy_id)
    return None if negotiations is None else negotiations.exists


@dataclass(frozen=True)
class ResponseFacts:
    """Условия отклика на эту вакансию — из того же ``applicantVacancyResponseStatuses``.

    В WWAO измерено: поля, которые обычно советуют читать из ``vacancyView``
    (``@responseLetterRequired``, ``userTestPresent``, ``autoResponse``), там
    ``null``; настоящие значения — здесь, по строковому id вакансии.
    """

    letter_required: bool
    has_test: bool
    #: Потолок hh на длину письма. Измерен как 10 000 на живой странице.
    letter_max_length: int
    #: ``responseImpossible``. Смысл выведен из имени, поэтому читается только
    #: как дополнительный стоп: ошибиться здесь стоит одной вакансии человеку.
    response_impossible: bool = False


def read_facts(state: dict[str, Any], vacancy_id: str) -> ResponseFacts | None:
    """Условия отклика или None при незнакомой форме — и тогда вакансия идёт человеку."""
    entry = _status_entry(state, vacancy_id)
    if entry is None:
        return None
    test = entry.get("test")
    if not isinstance(test, dict) or not isinstance(test.get("hasTests"), bool):
        return None
    short = entry.get("shortVacancy")
    if not isinstance(short, dict):
        return None
    required = short.get("@responseLetterRequired")
    if not isinstance(required, bool):
        return None
    max_length = entry.get("letterMaxLength")
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        return None
    return ResponseFacts(
        letter_required=required,
        has_test=test["hasTests"],
        letter_max_length=max_length,
        response_impossible=entry.get("responseImpossible") is True,
    )


@dataclass(frozen=True)
class VacancyStatus:
    archived: bool
    closed_for_applicants: bool


def read_status(state: dict[str, Any]) -> VacancyStatus:
    """В архиве ли вакансия и закрыта ли для откликов — из ``vacancyView``.

    ``closedForApplicants`` измерен в WWAO как настоящий bool на живых
    страницах; архив — ``vacancyView.status.archived`` (модель ``HHStatus``
    краулера), плюс плоское ``archived`` на всякий случай.
    """
    view = state.get("vacancyView")
    if not isinstance(view, dict):
        return VacancyStatus(archived=False, closed_for_applicants=False)
    status = view.get("status")
    nested = status.get("archived") if isinstance(status, dict) else None
    archived = nested if isinstance(nested, bool) else view.get("archived")
    return VacancyStatus(
        archived=archived is True,
        closed_for_applicants=view.get("closedForApplicants") is True,
    )


@dataclass(frozen=True)
class VacancyMeta:
    """Что за вакансия, словами hh. None — hh этого на странице не сказал."""

    title: str | None
    company: str | None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def read_meta(state: dict[str, Any]) -> VacancyMeta:
    """Название и компания из ``vacancyView`` — для карточки и для {position}/{company}."""
    view = state.get("vacancyView")
    if not isinstance(view, dict):
        return VacancyMeta(title=None, company=None)
    company = view.get("company")
    name = None
    if isinstance(company, dict):
        name = _clean(company.get("visibleName")) or _clean(company.get("name"))
    return VacancyMeta(title=_clean(view.get("name")), company=name)


def _normalised(text: str) -> str:
    for invisible in "​‌‍⁠﻿­":
        text = text.replace(invisible, "")
    return " ".join(text.split()).casefold()


def read_form_warnings(modal_text: str) -> tuple[str, ...]:
    """Что hh написал в форме отклика, его же словами. Ничего не решает — только показывается.

    Одна строка предупреждения на hh значит разное, поэтому разбирается по
    тексту. «Такой отклик может получить отказ» идёт вместе с причиной, которую
    hh пишет следующей строкой.
    """
    lines = [line.strip() for line in (modal_text or "").splitlines()]
    said: list[str] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        normalised = _normalised(line)
        if all(word in normalised for word in VISIBILITY_WORDS):
            said.append(printable(line))
        elif REJECTION_ANCHOR in normalised:
            reason = []
            for tail in lines[index + 1:]:
                if not tail or _normalised(tail) in ("добавить сопроводительное", "откликнуться"):
                    break
                reason.append(tail)
            said.append(printable(" ".join([line, *reason])))
    return tuple(dict.fromkeys(said))


VACANCY_IN_PATH: Final[re.Pattern[str]] = re.compile(r"^/vacancy/(\d+)")


def vacancy_id_from_url(url: str) -> str | None:
    """Id вакансии из пути адреса (не из ?backurl=…, где он бывает у страницы входа)."""
    from urllib.parse import urlsplit

    match = VACANCY_IN_PATH.match(urlsplit(url).path)
    return match.group(1) if match else None
