"""Разведка: открыть форму отклика на живой вакансии, нажать «Добавить сопроводительное», записать что появилось.

В WWAO это единственный незакрытый пробел: поле письма прячется за кнопкой,
её в прогоне никто не нажал, textarea не попала ни в один дамп. Здесь пробел
закрывается измерением, а не догадкой — угаданный селектор печатает письмо
неизвестно куда.

По шагам, и почему это безопасно:

1. Ставится сетевой предохранитель «только GET»: **любой не-GET запрос
   обрывается**. Отклик — не-GET, так что отправить что-либо в этом прогоне
   физически нельзя. (WWAO 2026-09-06: форма при таком режиме открывается
   целиком.)
2. Открывается вакансия; авторизация измеряется по session_signal.json, а не
   вписывается.
3. Записывается, какая кнопка отклика на странице и куда ведёт её href.
4. Клик по «Откликнуться»: это GET-переход, карточка формы приходит GET-запросом
   ``/applicant/vacancy_response/popup`` (WWAO: клик сам ничего не отправляет).
   Модалка ждётся селектором.
5. Снимаются элементы формы и дамп DOM модалки — «до».
6. Нажимается «Добавить сопроводительное», ждётся появление нового элемента,
   снимается «после».
7. Для появившегося поля проверяются кандидаты-запросы: какие находят РОВНО
   ЕГО. В поле вписывается пробная строка, читается обратно и стирается.
8. **Кнопка отправки не нажимается никогда.** Браузер закрывается.

Результат в ``evidence/`` (в .gitignore): ``<дата-время>-open-form.json`` —
измерение, по которому hh_selectors.py проверяет селекторы, и два HTML-дампа
модалки (до и после клика по кнопке письма).

    python -m workers.hh_apply.probe --url https://hh.kz/vacancy/<id>
    python -m workers.hh_apply.probe --from-search     # первая подходящая из keywords.yml
"""

import argparse
import json
import random
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from . import hh_selectors as sel
from .browser import install_guard, open_browser, open_hh_page, vacancy_url
from .config import EVIDENCE_DIR, KEYWORDS_PATH, Settings
from .login import looks_authenticated
from .page_state import read_form_warnings, read_negotiations, read_state, vacancy_id_from_url
from .search import load_keywords, search
from .submit import CONTROLS_JS, CONTROLS_QUERY

MODAL_TIMEOUT_MS: Final[int] = 20_000
REVEAL_TIMEOUT_S: Final[float] = 8.0
PROBE_TEXT: Final[str] = "проба разведки — не отправляется"
DATA_QA: Final[re.Pattern[str]] = re.compile(r'data-qa="([^"]+)"')

#: Для каждого кандидата-запроса — сколько элементов он находит и тот ли это,
#: что появился. Страница отвечает сама; мы ничего не утверждаем.
MATCH_JS: Final[str] = """([queries, targetIndex, controlsQuery]) => {
  const target = Array.from(document.querySelectorAll(controlsQuery))[targetIndex];
  const out = {};
  for (const q of queries) {
    let count = -1, same = false;
    try { const m = document.querySelectorAll(q); count = m.length; same = count === 1 && m[0] === target; }
    catch (e) { count = -1; }
    out[q] = {count: count, is_target: same};
  }
  return out;
}"""

MODAL_QA_JS: Final[str] = """els => els.flatMap(e => (e.getAttribute('data-qa') || '').split(/\\s+/)).filter(Boolean)"""


def _names(page_html: str) -> list[str]:
    return sorted({part for value in DATA_QA.findall(page_html) for part in value.split()})


def _modal_names(page: Any) -> list[str]:
    q = sel.RESPONSE_FORM.query
    names = page.eval_on_selector_all(f"{q}, {q} [data-qa]", MODAL_QA_JS)
    return sorted(set(names))


def _outer_html(page: Any) -> str:
    return str(page.eval_on_selector(sel.RESPONSE_FORM.query, "e => e.outerHTML"))


def _all_controls(page: Any) -> list[dict[str, Any]]:
    return [c for c in page.eval_on_selector_all(CONTROLS_QUERY, CONTROLS_JS) if isinstance(c, dict)]


def revealed_controls(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Видимые элементы «после», которых не было «до» (с учётом количества)."""
    remaining = Counter(sel.visible_signatures([c for c in before if c.get("visible")]))
    out = []
    for control in after:
        if not control.get("visible") or str(control.get("type") or "").lower() == "hidden":
            continue
        sig = sel.signature(control)
        if remaining[sig] > 0:
            remaining[sig] -= 1
        else:
            out.append(control)
    return out


def candidate_queries(control: dict[str, Any]) -> list[str]:
    """Запросы, которые могли бы указывать на это поле, от самого точного к общему."""
    modal = sel.RESPONSE_FORM.query
    tag = str(control.get("tag") or "").lower()
    queries = []
    if control.get("qa"):
        queries += [f'{modal} [data-qa="{control["qa"]}"]', f'[data-qa="{control["qa"]}"]']
    if control.get("name"):
        queries.append(f'{modal} {tag}[name="{control["name"]}"]')
    if tag:
        queries.append(f"{modal} {tag}")
    if control.get("editable"):
        queries.append(f'{modal} [contenteditable="true"]')
    return list(dict.fromkeys(queries))


def _pick_from_search(page: Any, settings: Settings, rng: random.Random) -> str:
    keywords = load_keywords(KEYWORDS_PATH)
    ids = search(page, host=settings.hh_host, site=settings.site, keywords=keywords, pages=1, limit=10,
                 pause=lambda: time.sleep(settings.browse_delay.draw(rng)))
    for vacancy_id in ids:
        time.sleep(settings.browse_delay.draw(rng))
        open_hh_page(page, vacancy_url(settings.hh_host, vacancy_id), site=settings.site, expect_vacancy=vacancy_id)
        state = read_state(page.content()) or {}
        negotiations = read_negotiations(state, vacancy_id)
        if negotiations is not None and not negotiations.exists and page.locator(sel.APPLY_LINK.query).count():
            return vacancy_url(settings.hh_host, vacancy_id)
    raise SystemExit("В выдаче не нашлось вакансии без отклика и с кнопкой «Откликнуться». Укажите --url.")


def run(url: str | None, *, settings: Settings, evidence_dir: Path = EVIDENCE_DIR) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema": sel.EVIDENCE_SCHEMA,
        "stage": sel.PROBE_STAGE,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "produced_by": "workers/hh_apply/probe.py",
    }
    with open_browser(slow_mo_ms=settings.slow_mo_ms) as context:
        guard = install_guard(context)  # «только GET» на весь прогон — отправить нельзя
        page = context.new_page()
        if url is None:
            url = _pick_from_search(page, settings, random.Random())
        vacancy_id = vacancy_id_from_url(url)
        if vacancy_id is None:
            raise SystemExit(f"Не похоже на ссылку вакансии: {url}")
        report["url"] = open_hh_page(page, url, site=settings.site, expect_vacancy=vacancy_id)
        report["vacancy_id"] = vacancy_id
        page_html = page.content()
        state = read_state(page_html) or {}
        negotiations = read_negotiations(state, vacancy_id)
        report["authenticated"] = looks_authenticated(state)
        report["negotiations_total"] = None if negotiations is None else negotiations.total
        report["data_qa_on_page"] = _names(page_html)

        top = page.locator(sel.APPLY_LINK.query)
        again = page.locator(sel.APPLY_LINK_AGAIN.query)
        report["apply_controls"] = {
            "top_count": top.count(),
            "again_count": again.count(),
            "top_href": top.first.get_attribute("href") if top.count() else None,
            "top_tag": top.first.evaluate("e => e.tagName") if top.count() else None,
        }
        if top.count() == 0:
            report["stopped"] = "на странице нет vacancy-response-link-top — нужна вакансия без отклика"
            return _write(evidence_dir, stamp, report, None, None, guard)

        top.first.click()
        page.wait_for_selector(sel.RESPONSE_FORM.query, timeout=MODAL_TIMEOUT_MS)
        page.wait_for_selector(sel.SUBMIT_BUTTON.query, timeout=MODAL_TIMEOUT_MS)
        html_before = _outer_html(page)
        controls_before = _all_controls(page)
        names_before = _modal_names(page)
        modal_text = str(page.locator(sel.RESPONSE_FORM.query).first.inner_text())
        report["controls_before"] = [c for c in controls_before if c.get("visible")]
        report["hh_said"] = list(read_form_warnings(modal_text))

        add = page.locator(sel.ADD_COVER_LETTER.query)
        report["add_cover_letter"] = {
            "count": add.count(),
            "tag": add.first.evaluate("e => e.tagName") if add.count() else None,
            "type": add.first.get_attribute("type") if add.count() else None,
        }
        if add.count() == 0:
            report["data_qa_in_modal"] = names_before
            report["stopped"] = "в форме нет add-cover-letter"
            return _write(evidence_dir, stamp, report, html_before, None, guard)

        add.first.click()
        deadline = time.monotonic() + REVEAL_TIMEOUT_S
        controls_after = _all_controls(page)
        while not revealed_controls(controls_before, controls_after) and time.monotonic() < deadline:
            page.wait_for_timeout(250)
            controls_after = _all_controls(page)
        html_after = _outer_html(page)
        report["data_qa_in_modal"] = sorted(set(names_before) | set(_modal_names(page)))
        report["controls_after_letter_click"] = [c for c in controls_after if c.get("visible")]
        revealed = revealed_controls(controls_before, controls_after)
        report["revealed_after_letter_click"] = revealed

        fields = [c for c in revealed if str(c.get("tag")).lower() == "textarea" or c.get("editable")]
        if len(fields) != 1:
            report["letter_field"] = None
            report["letter_field_problem"] = f"появилось полей для текста: {len(fields)} (нужно ровно одно)"
            return _write(evidence_dir, stamp, report, html_before, html_after, guard)

        control = fields[0]
        queries = candidate_queries(control)
        matches = page.evaluate(MATCH_JS, [queries, control["index"], CONTROLS_QUERY])
        exact = [q for q in queries if (matches.get(q) or {}).get("is_target")]
        readback_ok = False
        if exact:
            field = page.locator(exact[0]).first
            field.fill(PROBE_TEXT)
            tag = str(control.get("tag")).lower()
            typed = field.input_value() if tag in ("textarea", "input") else field.inner_text()
            readback_ok = str(typed).strip() == PROBE_TEXT
            field.fill("")
        report["letter_field"] = {
            "control": control,
            "candidate_queries": matches,
            "queries_matching_exactly_it": exact,
            "suggested_query": exact[0] if exact else None,
            "readback_ok": readback_ok,
        }
        return _write(evidence_dir, stamp, report, html_before, html_after, guard)


def _write(evidence_dir: Path, stamp: str, report: dict[str, Any], before: str | None,
           after: str | None, guard: Any) -> Path:
    report["requests_blocked_non_get"] = len(guard.blocked)
    report["requests_blocked_sample"] = guard.blocked[:20]
    if before is not None:
        (evidence_dir / f"{stamp}-modal-before.html").write_text(before, encoding="utf-8")
        report["dom_dump_before"] = f"{stamp}-modal-before.html"
    if after is not None:
        (evidence_dir / f"{stamp}-modal-after-letter-click.html").write_text(after, encoding="utf-8")
        report["dom_dump_after_letter_click"] = f"{stamp}-modal-after-letter-click.html"
    path = evidence_dir / f"{stamp}-open-form.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workers.hh_apply.probe",
                                     description="Разведка формы отклика hh. Ничего не отправляет.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="ссылка на вакансию без отклика")
    target.add_argument("--from-search", action="store_true", help="взять первую подходящую из keywords.yml")
    args = parser.parse_args(argv)

    path = run(None if args.from_search else args.url, settings=Settings())
    report = json.loads(path.read_text(encoding="utf-8"))
    print(f"Измерение: {path}")
    print(f"Авторизация: {report.get('authenticated')}; не-GET запросов оборвано: {report.get('requests_blocked_non_get')}")
    if report.get("stopped"):
        print(f"Остановлено: {report['stopped']}")
        return 1
    field = report.get("letter_field")
    if not field:
        print(f"Поле письма не найдено: {report.get('letter_field_problem')}")
        return 1
    print(f"Поле письма: {field.get('suggested_query')!r}; текст читается обратно: {field.get('readback_ok')}")
    print(f"Все точные запросы: {field.get('queries_matching_exactly_it')}")
    print(f"В hh_selectors.py: MEASURED_BY = {path.stem!r}, LETTER_FIELD.query = {field.get('suggested_query')!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
