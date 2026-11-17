"""Одна вакансия: открыть, перечитать, решить заново, открыть форму, письмо, отправить, переспросить hh.

Порядок — это и есть свойство безопасности (подход WWAO ``agent/submit.py``):

1. **Открыть страницу, которую видел человек**, через ``open_hh_page``:
   переживает региональный редирект, не читает чужую страницу как эту.
2. **Перечитать состояние и решить заново** (gate.decide). Карточка могла быть
   показана минуты назад: вакансию успели закрыть, откликнуться с телефона.
   ``negotiations.total`` проверяется ПЕРЕД кликом — по сайту, а не по журналу.
3. **Открыть окно в сети** (``guard.armed``) только на эту вакансию — и только
   теперь. До этой строки любой не-GET запрос из браузера обрывается.
4. **Открыть форму той кнопкой, что знаем**, сверив, что её href ведёт на эту
   вакансию. Нет такой кнопки → ``needs_manual``.
5. **Узнать форму.** Элементы формы сравниваются с эталоном из измерения. Любой
   элемент, которого в измерении не было (радиокнопки, вопросы, второе поле,
   выбор резюме), — «не знаю», вакансия уходит человеку. Не угадываем.
6. **Сверить резюме** с тем, что было на карточке. Другое — человеку.
7. **Раскрыть поле письма** («Добавить сопроводительное»), проверить, что
   появилось ровно то, что в измерении, и что селектор находит ровно одно поле.
   Поле не нашлось → ``needs_manual``.
8. **Напечатать ``approval.letter`` и прочитать обратно.** Не совпало —
   ``needs_manual``, кнопку не нажимаем.
9. **Нажать отправку**, подождать и **перечитать страницу заново**: ``total``
   должен стать ``>= 1``. Перечитывать надо новой загрузкой: состояние hh
   запечено в документ, и старая страница показала бы старые числа.

Если после клика hh не подтвердил — это «не удалось подтвердить», а не «не
отправлено»: запрос ушёл, и сказать человеку «ничего не случилось» — ровно то,
как вакансию откликают дважды.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Final

from . import hh_selectors as sel
from .browser import NavigatedElsewhereError, NetGuard, open_hh_page, vacancy_ids_in
from .confirm import Approval
from .gate import Decision, Verdict, confirmed_after_send, decide
from .hh_selectors import ControlSig, FormBaseline, visible_signatures
from .page_state import (
    ResponseFacts,
    VacancyMeta,
    printable,
    read_facts,
    read_form_warnings,
    read_meta,
    read_negotiations,
    read_state,
    read_status,
)

#: Модалка появляется позже, чем через 2 секунды после клика (измерено в WWAO),
#: поэтому ждём селектором с таймаутом, а не секундами.
FORM_TIMEOUT_MS: Final[int] = 15_000
LETTER_TIMEOUT_MS: Final[int] = 8_000
#: Единственная пауза «вслепую»: между необратимым кликом и перечитыванием страницы.
SEND_SETTLE_MS: Final[int] = 2_500

#: Всё в модалке, что человек может заполнить или выбрать.
CONTROLS_QUERY: Final[str] = ", ".join(
    f"{sel.RESPONSE_FORM.query} {part}"
    for part in ("textarea", "input", "select", '[contenteditable="true"]', '[role="radio"]',
                 '[role="checkbox"]', '[role="combobox"]', '[role="textbox"]')
)
#: Атрибуты элементов формы — спрашиваем у страницы, что у неё есть, а не утверждаем.
CONTROLS_JS: Final[str] = """els => els.map((e, i) => ({
  index: i,
  tag: e.tagName,
  qa: e.getAttribute('data-qa') || '',
  name: e.getAttribute('name') || '',
  type: e.getAttribute('type') || '',
  placeholder: e.getAttribute('placeholder') || '',
  aria: e.getAttribute('aria-label') || '',
  editable: e.isContentEditable === true,
  visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
}))"""

UNCONFIRMED: Final[str] = (
    "hh не подтвердил отклик после отправки. Запрос ушёл, так что отклик мог быть создан — "
    "проверьте вакансию руками, прежде чем отправлять снова."
)


class NeedsManualError(Exception):
    """Агент не знает, что делать с этой вакансией, — значит, её делает человек."""


class AlreadyAppliedError(Exception):
    """hh говорит, что отклик уже есть (или вакансия закрыта/в архиве). Делать нечего."""


class UnconfirmedError(Exception):
    """Отправка нажата, а hh не подтвердил отклик. Не «не отправлено»."""


@dataclass(frozen=True)
class PageReading:
    vacancy_id: str
    url: str
    decision: Decision
    meta: VacancyMeta
    facts: ResponseFacts | None


def read_vacancy(
    page: Any, url: str, vacancy_id: str, *, site: str, has_letter: bool, letter_field_known: bool
) -> PageReading:
    """Открыть вакансию и решить по её собственному состоянию."""
    landed = open_hh_page(page, url, site=site, expect_vacancy=vacancy_id)
    state = read_state(page.content())
    if state is None:
        raise NeedsManualError("страница открылась, но hh не отдал на ней своё состояние")
    facts = read_facts(state, vacancy_id)
    decision = decide(
        negotiations=read_negotiations(state, vacancy_id),
        facts=facts,
        status=read_status(state),
        has_letter=has_letter,
        letter_field_known=letter_field_known,
    )
    return PageReading(vacancy_id, landed.split("?", 1)[0], decision, read_meta(state), facts)


def read_controls(page: Any) -> list[dict[str, Any]]:
    """Видимые элементы формы, с атрибутами."""
    controls = page.eval_on_selector_all(CONTROLS_QUERY, CONTROLS_JS)
    return [c for c in controls if isinstance(c, dict) and c.get("visible")]


def unknown_controls(live: list[ControlSig], known: tuple[ControlSig, ...]) -> list[ControlSig]:
    """Элементы, которых нет в эталоне (с учётом количества: второе такое же поле — тоже новое)."""
    return list((Counter(live) - Counter(known)).elements())


def describe(controls: list[ControlSig]) -> str:
    return "; ".join(
        f"<{tag}{' type=' + kind if kind else ''}{' data-qa=' + qa if qa else ''}{' name=' + name if name else ''}>"
        for tag, kind, qa, name in controls
    )


def _text(page: Any, query: str) -> str:
    return str(page.locator(query).first.inner_text())


def open_form(page: Any, vacancy_id: str) -> str:
    """Нажать «Откликнуться» (GET-переход, сам по себе ничего не отправляет) и дождаться карточки."""
    control = page.locator(sel.APPLY_LINK.query)
    if control.count() == 0:
        raise NeedsManualError(
            "на странице нет кнопки «Откликнуться» (vacancy-response-link-top), которую агент знает"
        )
    href = control.first.get_attribute("href") or ""
    if vacancy_ids_in(href) != frozenset({vacancy_id}):
        raise NeedsManualError(f"кнопка отклика ведёт не на вакансию {vacancy_id}: {href!r}")
    control.first.click()
    try:
        page.wait_for_selector(sel.RESPONSE_FORM.query, timeout=FORM_TIMEOUT_MS)
        page.wait_for_selector(sel.SUBMIT_BUTTON.query, timeout=FORM_TIMEOUT_MS)
    except Exception as error:  # noqa: BLE001 - «не открылась» — вакансия человеку, не падение прогона
        raise NeedsManualError(f"форма отклика не открылась за {FORM_TIMEOUT_MS // 1000} с") from error
    modal = _text(page, sel.RESPONSE_FORM.query)
    label = _text(page, sel.SUBMIT_BUTTON.query).strip()
    if not label or label not in modal:
        raise NeedsManualError("форма отклика открылась, но не прочиталась — ничего не отправлено")
    return modal


def _resume_title(page: Any) -> str | None:
    locator = page.locator(sel.RESUME_TITLE.query)
    if locator.count() == 0:
        return None
    title = " ".join(str(locator.first.inner_text()).split())
    return title or None


@dataclass(frozen=True)
class FormPreview:
    resume_title: str | None
    hh_said: tuple[str, ...]


def preview(page: Any, vacancy_id: str, baseline: FormBaseline) -> FormPreview:
    """Открыть форму БЕЗ окна подтверждения, прочитать её и не трогать больше ничего.

    Вызывается, пока сетевой предохранитель обрывает любой не-GET — отправить
    отсюда нельзя физически. Нужно, чтобы карточка показала резюме и слова hh,
    и чтобы форма, которую агент не узнал, ушла человеку ещё до вопроса.
    """
    modal = open_form(page, vacancy_id)
    unknown = unknown_controls(visible_signatures(read_controls(page)), baseline.before)
    if unknown:
        raise NeedsManualError(f"в форме отклика элементы, которых нет в измерении: {describe(unknown)}")
    return FormPreview(resume_title=_resume_title(page), hh_said=read_form_warnings(modal))


def _typed(page: Any, query: str, tag: str) -> str:
    field = page.locator(query).first
    return str(field.input_value() if tag in ("textarea", "input") else field.inner_text())


def _same_text(one: str, other: str) -> bool:
    def norm(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return norm(one) == norm(other)


def send(page: Any, approval: Approval, guard: NetGuard, *, site: str, baseline: FormBaseline) -> tuple[str, ...]:
    """Отправить ровно этот одобренный отклик — или бросить, ничего не отправив, где можно.

    Возвращает то, что hh сказал в форме (его словами), чтобы записать в журнал.
    """
    if not sel.LETTER_FIELD.query:
        raise NeedsManualError("поле письма не измерено — письмо не печатается")

    reading = read_vacancy(page, approval.url, approval.vacancy_id, site=site,
                           has_letter=True, letter_field_known=True)
    if reading.decision.verdict is Verdict.SKIP:
        raise AlreadyAppliedError(reading.decision.reason)
    if reading.decision.verdict is not Verdict.PROCEED:
        raise NeedsManualError(reading.decision.reason)
    if reading.facts is not None and len(approval.letter) > reading.facts.letter_max_length:
        raise NeedsManualError(f"письмо длиннее лимита hh: {len(approval.letter)} > {reading.facts.letter_max_length}")

    with guard.armed(approval.vacancy_id):
        modal = open_form(page, approval.vacancy_id)
        before = visible_signatures(read_controls(page))
        unknown = unknown_controls(before, baseline.before)
        if unknown:
            raise NeedsManualError(f"в форме отклика элементы, которых нет в измерении: {describe(unknown)}")
        resume = _resume_title(page)
        if approval.resume_title is not None and resume != approval.resume_title:
            raise NeedsManualError(
                f"hh подставил другое резюме: «{printable(resume)}», а подтверждено «{printable(approval.resume_title)}»"
            )

        add = page.locator(sel.ADD_COVER_LETTER.query)
        if add.count() == 0:
            raise NeedsManualError("в форме нет кнопки «Добавить сопроводительное» (add-cover-letter)")
        add.first.click()
        try:
            page.wait_for_selector(sel.LETTER_FIELD.query, timeout=LETTER_TIMEOUT_MS)
        except Exception as error:  # noqa: BLE001
            raise NeedsManualError("поле письма не появилось после «Добавить сопроводительное»") from error

        revealed = list((Counter(visible_signatures(read_controls(page))) - Counter(before)).elements())
        if revealed != [baseline.letter]:
            raise NeedsManualError(
                "после «Добавить сопроводительное» появилось не то, что в измерении: "
                f"{describe(revealed) or 'ничего'} (ожидалось {describe([baseline.letter])})"
            )
        field = page.locator(sel.LETTER_FIELD.query)
        if field.count() != 1:
            raise NeedsManualError(f"селектор поля письма нашёл {field.count()} элементов, а не один")

        field.first.fill(approval.letter)
        if not _same_text(_typed(page, sel.LETTER_FIELD.query, baseline.letter[0]), approval.letter):
            raise NeedsManualError("в поле письма оказался не тот текст, что подтверждён — отправка не нажата")

        said = tuple(dict.fromkeys(read_form_warnings(modal) + read_form_warnings(_text(page, sel.RESPONSE_FORM.query))))
        page.locator(sel.SUBMIT_BUTTON.query).first.click()
        page.wait_for_timeout(SEND_SETTLE_MS)

    # Вне окна: дальше из браузера не уйдёт ни один не-GET.
    try:
        open_hh_page(page, approval.url, site=site, expect_vacancy=approval.vacancy_id)
    except NavigatedElsewhereError as error:
        raise UnconfirmedError(UNCONFIRMED) from error
    state = read_state(page.content())
    if not confirmed_after_send(None if state is None else read_negotiations(state, approval.vacancy_id)):
        raise UnconfirmedError(UNCONFIRMED)
    return said
