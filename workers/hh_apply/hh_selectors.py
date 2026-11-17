"""Все селекторы агента — и доказательство, что их кто-то видел на живой странице.

Угаданный селектор — молчаливый провал на личном аккаунте: hh ставит
``data-qa``, догадка кажется безопасной, а совпавшая догадка кликает не то или
печатает письмо неизвестно куда. Поэтому селектор здесь — это строка ПЛЮС
ссылка на файл измерения в ``evidence/``, и файл проверяется при каждом
запуске, а не принимается на веру (подход WWAO ``agent/selectors.py``).

Что проверяется, по месту элемента:

* элемент страницы вакансии (кнопка «Откликнуться») — его ``data-qa`` должен
  быть среди увиденных пробой на странице;
* элемент формы отклика — среди увиденных в открытой модалке;
* **поле письма** — строже всего: сам запрос селектора должен быть в списке
  запросов, которые проба проверила на живой странице и которые нашли РОВНО
  ОДИН элемент — тот, что появился после нажатия «Добавить сопроводительное»,
  и проба ещё и прочитала обратно введённый в него текст.

Плюс: файл измерения — нужной схемы, снят на этапе ``open-form`` и под
измеренной авторизацией.

**Насколько это сильно — честно.** Файл измерения — JSON на диске, подписи
нет. Человек, готовый вписать файл руками, обойдёт проверку. Гарантия уже:
селектор нельзя добавить, не имея файла, где этот селектор записан как
увиденный; догадка без правки файла не проходит.

**evidence/ в .gitignore** (там разметка страницы под аккаунтом владельца), так
что проверка селекторов проходит только на машине, где делалась разведка. На
чужой машине агент откажется отправлять — это безопасное направление ошибки.

Значения запросов для кнопки и модалки взяты из измерений WWAO (2026-09-06,
тот же аккаунт), но ЗДЕСЬ они считаются непроверенными, пока своя проба этого
воркера их не увидела. Поле письма в WWAO не измерено вовсе — его запрос
заполняется только из разведки этого воркера.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from .config import EVIDENCE_DIR

EVIDENCE_SCHEMA: Final[str] = "hh-apply-evidence/1"
PROBE_STAGE: Final[str] = "open-form"


class Where(StrEnum):
    """Где живёт элемент — от этого зависит, какой раздел измерения его подтверждает."""

    PAGE = "page"      # на странице вакансии
    MODAL = "modal"    # в открытой форме отклика
    LETTER = "letter"  # поле письма, появляется после «Добавить сопроводительное»


@dataclass(frozen=True)
class Selector:
    name: str
    #: Запрос Playwright. Пустой — значит «никто не видел».
    query: str
    where: Where
    #: Имя файла измерения в evidence/ без .json. None — непроверен.
    evidence: str | None = None
    note: str = ""


#: Файл измерения, которым подтверждены селекторы ниже. Заполняется после
#: разведки: ``python -m workers.hh_apply.probe --url <вакансия>``.
MEASURED_BY: Final[str | None] = None

APPLY_LINK = Selector(
    "apply_link", '[data-qa="vacancy-response-link-top"]', Where.PAGE, MEASURED_BY,
    "«Откликнуться» на вакансии без отклика. Ссылка на /applicant/vacancy_response?vacancyId=…",
)
#: Кнопка «Отклик другим резюме» на вакансии, где отклик уже есть. Агент её
#: НИКОГДА не нажимает: вакансия с откликом останавливается раньше, по числу.
#: Записана, чтобы страницу можно было узнать, — и не входит в обязательные.
APPLY_LINK_AGAIN = Selector(
    "apply_link_again", '[data-qa="vacancy-response-link-top-again"]', Where.PAGE, None,
    "«Отклик другим резюме». Её наличие ничего не говорит о том, откликались ли уже.",
)
RESPONSE_FORM = Selector(
    "response_form", '[data-qa="modal-overlay"]', Where.MODAL, MEASURED_BY,
    "Модальное окно отклика. Ждать его селектором, никогда не секундами.",
)
SUBMIT_BUTTON = Selector(
    "submit_button", '[data-qa="vacancy-response-submit-popup"]', Where.MODAL, MEASURED_BY,
    "Отправляет отклик. В WWAO измерено: остаётся активной даже под предупреждением hh.",
)
ADD_COVER_LETTER = Selector(
    "add_cover_letter", '[data-qa="add-cover-letter"]', Where.MODAL, MEASURED_BY,
    "«Добавить сопроводительное» — раскрывает поле письма, ничего не отправляет.",
)
RESUME_TITLE = Selector(
    "resume_title", '[data-qa="resume-title"]', Where.MODAL, MEASURED_BY,
    "Какое резюме hh приложит. Показывается на карточке подтверждения.",
)
#: НЕ ИЗМЕРЕНО до разведки этого воркера. Запрос вписывается только из файла
#: evidence/ — из списка запросов, которые проба проверила на живой странице.
LETTER_FIELD = Selector(
    "letter_field", "", Where.LETTER, None,
    "Поле сопроводительного письма, появляется после add_cover_letter.",
)

#: Всё, что поток отклика нажимает или заполняет. Отклик у этого агента всегда
#: с письмом, поэтому поле письма здесь же.
REQUIRED_FOR_APPLYING: Final[tuple[Selector, ...]] = (
    APPLY_LINK, RESPONSE_FORM, SUBMIT_BUTTON, ADD_COVER_LETTER, LETTER_FIELD,
)

_DATA_QA_IN_QUERY: Final[re.Pattern[str]] = re.compile(r'\[data-qa(\^?)="([^"]+)"\]')


class SelectorsNotVerifiedError(Exception):
    """Поток отклика не может начаться: разведка не сделана или её файл не подтверждает селекторы."""


def names_in(query: str) -> list[tuple[str, bool]]:
    """Имена data-qa, от которых зависит запрос, и префиксное ли это совпадение."""
    return [(name, bool(caret)) for caret, name in _DATA_QA_IN_QUERY.findall(query)]


def load_evidence(name: str | None, evidence_dir: Path = EVIDENCE_DIR) -> dict[str, Any] | None:
    """Файл измерения как словарь, или None, если его нет или это не JSON-объект."""
    if not name:
        return None
    path = evidence_dir / f"{name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _names(section: object) -> set[str]:
    return {str(item) for item in section} if isinstance(section, list) else set()


def _problems_with_one(selector: Selector, evidence_dir: Path) -> list[str]:
    if not selector.query:
        return [f"  {selector.name}: не заполнен — {selector.note}"]
    if selector.evidence is None:
        return [f"  {selector.name}: нет файла измерения — запрос {selector.query!r} никто не видел"]
    evidence = load_evidence(selector.evidence, evidence_dir)
    if evidence is None:
        return [f"  {selector.name}: файл evidence/{selector.evidence}.json не найден или не JSON"]

    problems: list[str] = []
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        problems.append(f"  {selector.name}: схема {evidence.get('schema')!r}, ожидается {EVIDENCE_SCHEMA!r}")
    if evidence.get("stage") != PROBE_STAGE:
        problems.append(f"  {selector.name}: этап {evidence.get('stage')!r}, нужен {PROBE_STAGE!r}")
    if evidence.get("authenticated") is not True:
        problems.append(f"  {selector.name}: измерение сделано без авторизации — форма видна только под аккаунтом")

    if selector.where is Where.LETTER:
        field = evidence.get("letter_field")
        if not isinstance(field, dict):
            return [*problems, f"  {selector.name}: в измерении нет поля письма"]
        if selector.query not in _names(field.get("queries_matching_exactly_it")):
            problems.append(
                f"  {selector.name}: запрос {selector.query!r} не из списка запросов, которые проба "
                "проверила на живой странице"
            )
        if field.get("readback_ok") is not True:
            problems.append(f"  {selector.name}: проба не смогла прочитать обратно введённый текст")
        return problems

    section = "data_qa_on_page" if selector.where is Where.PAGE else "data_qa_in_modal"
    seen = _names(evidence.get(section))
    wanted = names_in(selector.query)
    if not wanted:
        problems.append(f"  {selector.name}: в {selector.query!r} нет data-qa — сверить не с чем")
    for name, prefix in wanted:
        if not (any(s.startswith(name) for s in seen) if prefix else name in seen):
            problems.append(f"  {selector.name}: data-qa {name!r} нет среди увиденных ({section})")
    return problems


def problems_with(selectors: Sequence[Selector], evidence_dir: Path = EVIDENCE_DIR) -> list[str]:
    """Всё, что не так с группой селекторов, словами."""
    problems: list[str] = []
    for selector in selectors:
        problems.extend(_problems_with_one(selector, evidence_dir))
    return problems


def assert_ready_to_apply(
    selectors: Sequence[Selector] = REQUIRED_FOR_APPLYING, evidence_dir: Path = EVIDENCE_DIR
) -> None:
    """Отказ стартовать отправку, пока каждый селектор не подтверждён измерением."""
    problems = problems_with(selectors, evidence_dir)
    if problems:
        raise SelectorsNotVerifiedError(
            "Селекторы формы отклика не подтверждены измерением на живой странице:\n"
            + "\n".join(problems)
            + "\n\nСначала: python -m workers.hh_apply.login, затем\n"
            "python -m workers.hh_apply.probe --url https://hh.kz/vacancy/<id>\n"
            "и перенести найденное в workers/hh_apply/selectors.py вместе с именем файла."
        )


def letter_field_known(field: Selector = LETTER_FIELD, evidence_dir: Path = EVIDENCE_DIR) -> bool:
    return not _problems_with_one(field, evidence_dir)


# ── узнавание формы ─────────────────────────────────────────────────────

#: Подпись элемента управления формы: (тег, type, data-qa, name). Значение и
#: текст не входят — они меняются от вакансии к вакансии.
ControlSig = tuple[str, str, str, str]


def signature(control: dict[str, Any]) -> ControlSig:
    return (
        str(control.get("tag") or "").lower(),
        str(control.get("type") or "").lower(),
        str(control.get("qa") or ""),
        str(control.get("name") or ""),
    )


def visible_signatures(controls: Sequence[dict[str, Any]]) -> list[ControlSig]:
    """Подписи всех элементов, кроме скрытых input — те человек не заполняет."""
    return [signature(c) for c in controls if str(c.get("type") or "").lower() != "hidden"]


@dataclass(frozen=True)
class FormBaseline:
    """Как выглядела форма в измерении: элементы до «Добавить сопроводительное» и само поле письма."""

    before: tuple[ControlSig, ...]
    letter: ControlSig


def form_baseline(field: Selector = LETTER_FIELD, evidence_dir: Path = EVIDENCE_DIR) -> FormBaseline | None:
    """Эталон формы из того же измерения, что подтвердило поле письма. None — эталона нет."""
    if not letter_field_known(field, evidence_dir):
        return None
    evidence = load_evidence(field.evidence, evidence_dir) or {}
    controls = evidence.get("controls_before")
    letter = (evidence.get("letter_field") or {}).get("control")
    if not isinstance(controls, list) or not isinstance(letter, dict):
        return None
    return FormBaseline(
        before=tuple(visible_signatures([c for c in controls if isinstance(c, dict)])),
        letter=signature(letter),
    )
