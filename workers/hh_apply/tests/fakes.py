"""Моки страницы hh и сетевых маршрутов: без браузера и без сети.

Форма состояния страницы повторяет измеренную в WWAO 2026-09-06
(``applicantVacancyResponseStatuses[<id>].negotiations.total`` и соседи).
"""

import html
import json
from typing import Any

from workers.hh_apply import hh_selectors as sel
from workers.hh_apply.submit import CONTROLS_QUERY

VACANCY = "136000001"
URL = f"https://almaty.hh.kz/vacancy/{VACANCY}"
LETTER_QUERY = '[data-qa="modal-overlay"] textarea'
MODAL_TEXT = "Отклик на вакансию\nPython-разработчик\nPython-разработчик\nДобавить сопроводительное\nОткликнуться"


def control(tag: str, *, type_: str = "", qa: str = "", name: str = "", visible: bool = True,
            index: int = 0, editable: bool = False) -> dict[str, Any]:
    return {"index": index, "tag": tag, "type": type_, "qa": qa, "name": name, "placeholder": "",
            "aria": "", "editable": editable, "visible": visible}


HIDDEN = control("INPUT", type_="hidden", name="xsrf")
LETTER = control("TEXTAREA", name="letter", index=1)
BASELINE = sel.FormBaseline(before=(), letter=sel.signature(LETTER))


def state(vacancy_id: str = VACANCY, *, total: Any = 0, topics: int = 0, has_test: bool = False,
          letter_required: bool = False, archived: bool = False, closed: bool = False,
          title: str | None = "Python-разработчик", company: str | None = "ТОО «Ромашка»",
          already_applied_flag: bool = False) -> dict[str, Any]:
    return {
        "applicantVacancyResponseStatuses": {
            vacancy_id: {
                "negotiations": {"total": total, "topicList": [{"id": n, "lastState": "RESPONSE"} for n in range(topics)]},
                "test": {"hasTests": has_test},
                "shortVacancy": {"@responseLetterRequired": letter_required},
                "letterMaxLength": 10000,
                # Ключ, который врёт: в WWAO был false при total == 1.
                "alreadyApplied": already_applied_flag,
            }
        },
        "userLabelsForVacancies": {vacancy_id: ["seen"]},
        "vacancyView": {
            "vacancyId": int(vacancy_id),
            "name": title,
            "company": {"visibleName": company, "name": "ТОО Ромашка (юр.)"},
            "status": {"archived": archived},
            "closedForApplicants": closed,
        },
    }


def page_html(payload: dict[str, Any]) -> str:
    return (f'<html><body><template id="HH-Lux-InitialState">'
            f'{html.escape(json.dumps(payload, ensure_ascii=False))}</template></body></html>')


class FakeLocator:
    def __init__(self, page: "FakeHH", query: str) -> None:
        self.page, self.query = page, query

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return self.page.count(self.query)

    def get_attribute(self, name: str) -> str | None:
        return self.page.attribute(self.query, name)

    def click(self, timeout: int | None = None) -> None:
        self.page.click(self.query)

    def inner_text(self) -> str:
        return self.page.text(self.query)

    def fill(self, text: str) -> None:
        self.page.fill(self.query, text)

    def input_value(self) -> str:
        return self.page.values.get(self.query, "")


class FakeHH:
    """Одна вакансия hh с формой отклика и полем письма за «Добавить сопроводительное»."""

    def __init__(self, *, vacancy_id: str = VACANCY, total: int = 0, apply_link: bool = True,
                 href: str | None = None, before: tuple = (HIDDEN,), revealed: tuple = (LETTER,),
                 resume: str | None = "Python-разработчик", counts_after_send: bool = True,
                 guard: Any = None, modal_text: str = MODAL_TEXT, typed_transform: Any = None) -> None:
        self.vacancy_id = vacancy_id
        self.total = total
        self.apply_link = apply_link
        self.href = href or f"/applicant/vacancy_response?vacancyId={vacancy_id}&employerId=1&hhtmFrom=vacancy"
        self.before, self.revealed = before, revealed
        self.resume = resume
        self.counts_after_send = counts_after_send
        self.guard = guard
        self.modal_text = modal_text
        self.typed_transform = typed_transform
        self.url = "about:blank"
        self.modal_open = self.letter_open = self.submitted = False
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.loads = 0
        self.armed_at_submit: Any = "not clicked"

    # навигация
    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.url = url
        self.loads += 1
        self.modal_open = self.letter_open = False
        if self.submitted and self.counts_after_send:
            self.total = max(self.total, 1)  # состояние hh «запекается» в документ при загрузке

    def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        pass

    def content(self) -> str:
        return page_html(state(self.vacancy_id, total=self.total))

    # элементы
    def locator(self, query: str) -> FakeLocator:
        return FakeLocator(self, query)

    def count(self, query: str) -> int:
        if query == sel.APPLY_LINK.query:
            return 1 if self.apply_link else 0
        if query in (sel.RESPONSE_FORM.query, sel.SUBMIT_BUTTON.query, sel.ADD_COVER_LETTER.query):
            return 1 if self.modal_open else 0
        if query == sel.RESUME_TITLE.query:
            return 1 if self.modal_open and self.resume else 0
        if query == LETTER_QUERY:
            return 1 if self.letter_open else 0
        return 0

    def attribute(self, query: str, name: str) -> str | None:
        return self.href if query == sel.APPLY_LINK.query and name == "href" else None

    def click(self, query: str) -> None:
        self.clicks.append(query)
        if query == sel.APPLY_LINK.query:
            self.modal_open = True
        elif query == sel.ADD_COVER_LETTER.query:
            self.letter_open = bool(self.revealed)
        elif query == sel.SUBMIT_BUTTON.query:
            self.submitted = True
            self.armed_at_submit = None if self.guard is None else self.guard.armed_for

    def fill(self, query: str, text: str) -> None:
        self.fills.append((query, text))
        self.values[query] = self.typed_transform(text) if self.typed_transform else text

    def text(self, query: str) -> str:
        if query == sel.RESPONSE_FORM.query:
            return self.modal_text
        if query == sel.SUBMIT_BUTTON.query:
            return "Откликнуться"
        if query == sel.RESUME_TITLE.query:
            return self.resume or ""
        return ""

    def wait_for_selector(self, query: str, timeout: int | None = None) -> None:
        if self.count(query) == 0:
            raise TimeoutError(f"не дождались {query}")

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def eval_on_selector_all(self, query: str, js: str) -> list[dict[str, Any]]:
        assert query == CONTROLS_QUERY
        controls = list(self.before) + (list(self.revealed) if self.letter_open else [])
        return [dict(c) for c in controls]

    def bring_to_front(self) -> None:
        pass

    def screenshot(self, path: str | None = None, full_page: bool = False) -> None:
        pass


class FakeRequest:
    def __init__(self, method: str, url: str, post_data: str | None = None) -> None:
        self.method, self.url, self.post_data = method, url, post_data


class FakeRoute:
    def __init__(self, method: str, url: str, post_data: str | None = None) -> None:
        self.request = FakeRequest(method, url, post_data)
        self.outcome: str | None = None

    def abort(self, error_code: str = "failed") -> None:
        self.outcome = "aborted"

    def continue_(self) -> None:
        self.outcome = "continued"
