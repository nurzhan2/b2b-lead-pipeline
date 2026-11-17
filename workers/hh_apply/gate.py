"""Проверки-стопы перед откликом. Останавливают отправку только проверяемые факты.

Список короткий и закрытый, и каждый пункт читается со страницы вакансии ДО
открытия формы (из её состояния, см. page_state.py):

* отклик уже есть — ``negotiations.total >= 1`` (или непустой список переписок);
* вакансия в архиве;
* вакансия закрыта для откликов;
* у работодателя тест — ответы работодатель читает как ответы кандидата,
  агент на них не отвечает никогда;
* нужно письмо, а его нет.

Отдельно — «не знаю». Счётчик откликов в незнакомой форме, условия отклика в
незнакомой форме, противоречие hh самому себе, поле письма, которое никто не
измерял: всё это не «можно», а ``needs_manual``. «Не знаю» уходит человеку, а
не угадывается.

Чего здесь НЕТ, и это решение, а не пробел: слова hh в форме отклика («такой
отклик может получить отказ», «поменяйте видимость резюме») отправку не
останавливают. В WWAO вторая фраза сутки считалась отказом, и это было
неверно — отклик с ней уходит (измерено 2026-09-07). Они показываются человеку.

**Письмо здесь всегда нужно.** Этот агент откликается только с письмом из
cover_letter.md: нет шаблона — ``needs_manual`` на любую вакансию, даже где hh
письмо не требует. Так договорено: вакансия без письма уходит человеку, а не
отправляется «как получится».
"""

from dataclasses import dataclass
from enum import StrEnum

from .page_state import Negotiations, ResponseFacts, VacancyStatus


class Verdict(StrEnum):
    PROCEED = "proceed"   # ничего проверяемого не мешает
    SKIP = "skip"         # делать нечего: отклик есть, архив, закрыта
    MANUAL = "manual"     # человеку: тест, нет письма, «не знаю»


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    #: Причина — настройка агента (нет шаблона, поле письма не измерено), а не
    #: факт о вакансии. Такое не пишется в журнал: иначе после исправления
    #: настройки вакансия навсегда застряла бы в needs_manual.
    setup: bool = False


#: Ответ hh двумя способами сразу: откликов 0, а переписка по вакансии есть.
CONTRADICTORY = (
    "hh отвечает про эту вакансию двумя способами: откликов — 0, а переписка есть ({topics}). "
    "Что это значит, никто не измерял — агент не отправляет, посмотрите сами."
)


def decide(
    *,
    negotiations: Negotiations | None,
    facts: ResponseFacts | None,
    status: VacancyStatus,
    has_letter: bool,
    letter_field_known: bool,
) -> Decision:
    """Что делать с вакансией, чья страница только что прочитана.

    «Откликались ли уже» — первым: повторный отклик — единственная ошибка,
    которую владелец не может отменить, и её производит отставший журнал.
    """
    if negotiations is None:
        return Decision(Verdict.MANUAL, "hh не сообщил, откликались ли уже (negotiations.total не прочитан)")
    if negotiations.contradictory:
        return Decision(Verdict.MANUAL, CONTRADICTORY.format(topics=negotiations.topics))
    if negotiations.exists:
        return Decision(Verdict.SKIP, f"отклик уже отправлен (negotiations.total = {negotiations.total})")
    if status.archived:
        return Decision(Verdict.SKIP, "вакансия в архиве")
    if status.closed_for_applicants:
        return Decision(Verdict.SKIP, "вакансия закрыта для откликов")
    if facts is None:
        return Decision(Verdict.MANUAL, "не удалось прочитать условия отклика на странице")
    if facts.has_test:
        return Decision(Verdict.MANUAL, "у работодателя тест — на тесты отвечает человек")
    if facts.response_impossible:
        return Decision(Verdict.MANUAL, "hh отметил вакансию как недоступную для отклика")
    if not has_letter:
        return Decision(Verdict.MANUAL, "нет письма: cover_letter.md отсутствует или пуст", setup=True)
    if not letter_field_known:
        return Decision(
            Verdict.MANUAL,
            "поле письма не подтверждено измерением (нужна разведка probe.py) — письмо не печатается",
            setup=True,
        )
    return Decision(Verdict.PROCEED, "можно откликаться")


def confirmed_after_send(negotiations: Negotiations | None) -> bool:
    """После отправки верим hh: любой след отклика на вакансии — отклик есть.

    Асимметрия с :func:`decide` нарочная: до отправки разногласие — стоп, после
    отправки — «дошло». Сказать человеку «ничего не ушло», когда запрос ушёл, —
    это то, как вакансию откликают дважды.
    """
    return negotiations is not None and negotiations.exists
