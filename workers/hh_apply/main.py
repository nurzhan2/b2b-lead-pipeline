"""Точка входа: поиск → стопы → карточки → подтверждение → отклики.

    python -m workers.hh_apply.main              # сухой прогон: ничего не отправляет
    python -m workers.hh_apply.main --send       # дойти до подтверждения и отправить одобренное
    python -m workers.hh_apply.main --requeue ID [ID ...]   # вернуть needs_manual/failed в очередь

**Сухой прогон — по умолчанию, отправка — два независимых действия**: флаг
``--send`` и слово «отправляем» в подтверждении. Ни одно по отдельности не
отправляет.

**Этот воркер — не источник лидов.** Он ничего не шлёт в ядро лидгена: нет
core_client, нет POST /companies/ingest, нет HTTP-клиента вообще — наружу он
ходит только браузером на hh. Проверяется tests/test_isolation.py.

Весь прогон браузер держит сетевой предохранитель: любой не-GET запрос
обрывается, кроме как внутри окна, открытого подтверждением человека для одной
конкретной вакансии (см. browser.NetGuard).
"""

import argparse
import random
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import hh_selectors as sel
from .browser import CaptchaError, NavigatedElsewhereError, NetGuard, install_guard, open_browser, screenshot, vacancy_url
from .config import JOURNAL_PATH, KEYWORDS_PATH, LETTER_PATH, DelayRange, Settings
from .confirm import Approval, CancelledError, Candidate, confirm
from .gate import Verdict
from .journal import Actor, Entry, IllegalTransitionError, Journal, Status, digest
from .letter import LetterUnavailableError, Template, load_template, render, warnings
from .login import SessionExpiredError, SignalUnknownError, ensure_session
from .page_state import printable
from .search import Keywords, excluded_by, load_keywords, search
from .submit import AlreadyAppliedError, NeedsManualError, UnconfirmedError, preview, read_vacancy, send


def pause(delay: DelayRange, rng: random.Random, sleep: Callable[[float], Any] = time.sleep) -> float:
    seconds = delay.draw(rng)
    sleep(seconds)
    return seconds


@dataclass
class Prepared:
    candidates: list[Candidate] = field(default_factory=list)
    #: Что мешает всем вакансиям сразу (нет шаблона, поле письма не измерено…) —
    #: это про настройку, а не про вакансию, в журнал не пишется.
    setup_problems: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def _remember(journal: Journal, entry: Entry, actor: Actor = Actor.AGENT) -> None:
    try:
        journal.record(entry, actor=actor)
    except IllegalTransitionError as error:
        print(f"  журнал не принял запись {entry.vacancy_id}: {error}", file=sys.stderr)


def prepare(
    page: Any,
    ids: Sequence[str],
    *,
    settings: Settings,
    journal: Journal,
    template: Template | None,
    keywords: Keywords,
    baseline: sel.FormBaseline | None,
    rng: random.Random,
) -> Prepared:
    """Открыть каждую найденную вакансию, применить стопы, собрать письмо и карточку."""
    result = Prepared()
    field_known = baseline is not None
    consecutive = 0
    first = True
    for vacancy_id in ids:
        if len(result.candidates) >= settings.max_applies_per_run:
            break
        known = journal.get(vacancy_id)
        if known is not None and known.status is not Status.QUEUED:
            result.bump(f"уже в журнале: {known.status.value}")
            continue
        if not first:
            pause(settings.browse_delay, rng)
        first = False
        url = vacancy_url(settings.hh_host, vacancy_id)
        try:
            reading = read_vacancy(page, url, vacancy_id, site=settings.site,
                                   has_letter=template is not None, letter_field_known=field_known)
        except CaptchaError:
            raise
        except NeedsManualError as error:
            _remember(journal, Entry(vacancy_id, Status.NEEDS_MANUAL, url=url, reason=str(error)))
            result.bump("needs_manual")
            continue
        except NavigatedElsewhereError as error:
            print(f"  {vacancy_id}: {error}")
            consecutive += 1
            if consecutive >= settings.stop_after_consecutive_failures:
                print("Две страницы подряд открылись не так — останавливаю подготовку.")
                break
            continue
        consecutive = 0
        title, company = reading.meta.title, reading.meta.company
        word = excluded_by(title, keywords.exclude_title_words)
        if word:
            print(f"  {vacancy_id}: «{printable(title)}» — стоп-слово «{word}» из keywords.yml")
            result.bump("исключено keywords.yml")
            continue

        decision = reading.decision
        if decision.verdict is Verdict.SKIP:
            _remember(journal, Entry(vacancy_id, Status.SKIPPED, title, company, reading.url, reason=decision.reason))
            result.bump("skipped")
            continue
        if decision.verdict is Verdict.MANUAL:
            if decision.setup:
                # Причина — настройка, а не вакансия: в журнал не пишем, иначе
                # после исправления настройки вакансия застряла бы в needs_manual.
                result.setup_problems[decision.reason] = result.setup_problems.get(decision.reason, 0) + 1
                continue
            _remember(journal, Entry(vacancy_id, Status.NEEDS_MANUAL, title, company, reading.url, reason=decision.reason))
            result.bump("needs_manual")
            continue

        assert template is not None and baseline is not None  # PROCEED требует и то, и другое
        try:
            letter = render(template, company=company, position=title, my_name=settings.my_name,
                            max_length=reading.facts.letter_max_length if reading.facts else None)
        except LetterUnavailableError as error:
            text = str(error)
            if error.setup:
                result.setup_problems[text] = result.setup_problems.get(text, 0) + 1
                continue
            _remember(journal, Entry(vacancy_id, Status.NEEDS_MANUAL, title, company, reading.url, reason=text))
            result.bump("needs_manual")
            continue

        try:
            form = preview(page, vacancy_id, baseline)
        except NeedsManualError as error:
            _remember(journal, Entry(vacancy_id, Status.NEEDS_MANUAL, title, company, reading.url, reason=str(error)))
            result.bump("needs_manual")
            continue

        _remember(journal, Entry(vacancy_id, Status.QUEUED, title, company, reading.url))
        result.candidates.append(Candidate(
            vacancy_id=vacancy_id, url=reading.url, title=title, company=company, letter=letter,
            resume_title=form.resume_title, hh_said=form.hh_said, letter_notes=warnings(letter),
        ))
    return result


def send_all(
    page: Any, approvals: Sequence[Approval], guard: NetGuard, *,
    settings: Settings, journal: Journal, baseline: sel.FormBaseline, rng: random.Random,
) -> dict[str, int]:
    """Отправить одобренные по очереди, с человеческой паузой между откликами."""
    counts: dict[str, int] = {}
    consecutive = 0
    attempted: set[str] = set()
    for index, approval in enumerate(approvals):
        if consecutive >= settings.stop_after_consecutive_failures:
            print("Две ошибки подряд — останавливаюсь. Посмотрите, что происходит.")
            break
        if index:
            seconds = pause(settings.apply_delay, rng)
            print(f"  (пауза {seconds:.0f} с)")
        attempted.add(approval.vacancy_id)
        status, reason, said = Status.SENT, None, ()
        try:
            said = send(page, approval, guard, site=settings.site, baseline=baseline)
            consecutive = 0
        except AlreadyAppliedError as error:
            status, reason = Status.SKIPPED, str(error)
        except NeedsManualError as error:
            status, reason = Status.NEEDS_MANUAL, str(error)
        except UnconfirmedError as error:
            status, reason = Status.NEEDS_MANUAL, str(error)
            consecutive += 1
        except CaptchaError as error:
            _remember(journal, Entry(approval.vacancy_id, Status.NEEDS_MANUAL, reason=str(error)))
            print(f"\n{error}")
            page.bring_to_front()
            counts["needs_manual"] = counts.get("needs_manual", 0) + 1
            break
        except Exception as error:  # noqa: BLE001 - одна вакансия не роняет прогон; две подряд — роняют
            screenshot(page, f"fail-{approval.vacancy_id}")
            status, reason = Status.FAILED, f"{type(error).__name__}: {error}"
            consecutive += 1
        _remember(journal, Entry(
            approval.vacancy_id, status, letter_digest=digest(approval.letter) if status is Status.SENT else None,
            reason=reason, hh_said="\n".join(said) or None,
        ))
        counts[status.value] = counts.get(status.value, 0) + 1
        tail = f" — {printable(reason)}" if reason else ""
        print(f"  {approval.vacancy_id} {printable(approval.title or '')}: {status.value}{tail}")
        for line in said:
            print(f"    hh: {line}")
    for approval in approvals:
        if approval.vacancy_id not in attempted:
            _remember(journal, Entry(approval.vacancy_id, Status.FAILED,
                                     reason="прогон остановлен, не дойдя до этой вакансии"))
            counts["failed"] = counts.get("failed", 0) + 1
    return counts


def requeue(journal: Journal, ids: Sequence[str]) -> int:
    """Вернуть вакансии из needs_manual/failed в очередь. Переход, который делает только человек."""
    refused = 0
    for vacancy_id in ids:
        entry = journal.get(vacancy_id)
        if entry is None or entry.status not in (Status.NEEDS_MANUAL, Status.FAILED):
            print(f"{vacancy_id}: вернуть можно только из needs_manual или failed "
                  f"(сейчас: {entry.status.value if entry else 'нет в журнале'})")
            refused += 1
            continue
        journal.record(Entry(vacancy_id, Status.QUEUED, reason=entry.reason), actor=Actor.HUMAN)
        print(f"{vacancy_id}: снова в очереди")
    return 1 if refused else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workers.hh_apply.main",
                                     description="Отклики на hh под аккаунтом владельца, каждый — с подтверждения")
    parser.add_argument("--send", action="store_true", help="дойти до подтверждения и отправить (по умолчанию — только показать)")
    parser.add_argument("--requeue", nargs="+", metavar="ID", default=(), help="вернуть needs_manual/failed в очередь и выйти")
    args = parser.parse_args(argv)

    settings = Settings()
    journal = Journal(JOURNAL_PATH)
    if args.requeue:
        return requeue(journal, args.requeue)

    template = load_template(LETTER_PATH)
    keywords = load_keywords(KEYWORDS_PATH)
    problems = sel.problems_with(sel.REQUIRED_FOR_APPLYING)
    baseline = sel.form_baseline()
    if template is None:
        print(f"{LETTER_PATH.name} нет или пуст: письма нет — вакансии не дойдут до карточки "
              "(скопируйте cover_letter.example.md в cover_letter.md и заполните).")
    if problems:
        print("Селекторы формы не подтверждены измерением:\n" + "\n".join(problems))
        if args.send:
            print("\nОтправка запрещена, пока нет разведки: python -m workers.hh_apply.probe --from-search")
            return 2

    rng = random.Random()
    try:
        with open_browser(slow_mo_ms=settings.slow_mo_ms) as context:
            guard = install_guard(context)
            page = context.new_page()
            ensure_session(page, settings)
            ids = search(page, host=settings.hh_host, site=settings.site, keywords=keywords,
                         pages=settings.search_pages, limit=settings.max_vacancies_to_inspect,
                         pause=lambda: pause(settings.browse_delay, rng))
            print(f"Найдено вакансий: {len(ids)}")
            prepared = prepare(page, ids, settings=settings, journal=journal, template=template,
                               keywords=keywords, baseline=baseline, rng=rng)
            for reason, count in prepared.counts.items():
                print(f"  {reason}: {count}")
            for reason, count in prepared.setup_problems.items():
                print(f"  НЕ ОТПРАВИТСЯ, пока не исправлена настройка ({count}): {reason}")

            if not prepared.candidates:
                print("Карточек к подтверждению нет.")
                return 0
            if not args.send:
                print(f"\nСухой прогон. К подтверждению было бы {len(prepared.candidates)}:\n")
                for index, candidate in enumerate(prepared.candidates, start=1):
                    print(f"[{index}] {candidate.render()}\n")
                print("Чтобы отправить: тот же запуск с --send.")
                return 0

            try:
                approvals = confirm(prepared.candidates)
            except CancelledError as error:
                print(f"Ничего не отправлено: {error}")
                return 0
            for approval in approvals:
                _remember(journal, Entry(approval.vacancy_id, Status.CONFIRMED), actor=Actor.HUMAN)
            assert baseline is not None
            counts = send_all(page, approvals, guard, settings=settings, journal=journal, baseline=baseline, rng=rng)
            print(f"\nИтог: {counts}. Оборвано сетевым предохранителем запросов: {len(guard.blocked)}.")
            return 0
    except (SessionExpiredError, SignalUnknownError, CaptchaError) as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
