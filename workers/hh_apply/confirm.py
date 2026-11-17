"""Карточка подтверждения в консоли: что именно уйдёт, и явное «да» человека.

Каждый отклик — только с явного подтверждения. Устроено так, что по умолчанию
не уходит ничего:

* человек **вводит номера** вакансий, на которые отправить (или «все»).
  Пустая строка — ничего не выбрано;
* затем набирает слово «отправляем» целиком. Не «y»: одну букву производит
  залипшая клавиша;
* Enter, Ctrl-C, закрытый ввод (EOF от cron или перенаправления) — всё это
  «ничего не отправлять». Флага, который отвечает на вопрос за человека, нет.

**Карточка показывает то, что уйдёт, целиком**: вакансия, компания, ссылка, id,
какое резюме предлагает hh (прочитано из открытой формы, которая при этом
ничего не отправила), что hh сказал в форме, и **письмо полностью** — не
первые строки. Человек соглашается на текст, который прочитал.

Подтверждение — это значение :class:`Approval`, и только этот модуль его
создаёт. Отправка печатает в поле ровно ``approval.letter`` — другой строки у
неё нет. Если между подтверждением и кликом hh предложит другое резюме,
отправка остановится (submit.py сверяет).
"""

import hashlib
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, TextIO

from .page_state import printable

CONFIRM_WORD: Final[str] = "отправляем"
ALL_WORDS: Final[frozenset[str]] = frozenset({"все", "всё", "all"})


class CancelledError(Exception):
    """Человек не подтвердил. Не ошибка — ожидаемый ответ на вопрос."""


@dataclass(frozen=True)
class Candidate:
    vacancy_id: str
    url: str
    title: str | None
    company: str | None
    #: Письмо, уже собранное из шаблона для этой вакансии.
    letter: str
    #: Резюме, которое hh подставил в форму. None — форму не открывали.
    resume_title: str | None = None
    #: Что hh сказал в форме отклика, его словами. Ничего не останавливает.
    hh_said: tuple[str, ...] = ()
    #: Замечания про само письмо (ссылка, @). Не стопы.
    letter_notes: tuple[str, ...] = ()

    def render(self) -> str:
        """Ровно то, что видит человек. Подтверждение привязано к этому тексту."""
        lines = [
            f"{self.title or 'без названия'} — {self.company or 'компания не указана'}",
            f"  вакансия {self.vacancy_id}: {self.url}",
            f"  резюме: {self.resume_title or 'hh не показал (форма не открывалась)'}",
        ]
        if self.hh_said:
            lines.append("  hh пишет в форме отклика:")
            lines.extend(f"  | {line}" for line in self.hh_said)
        lines.extend(f"  ! {note}" for note in self.letter_notes)
        lines.append(f"  письмо ({len(self.letter)} симв.), уйдёт целиком:")
        lines.extend(f"  | {line}" for line in self.letter.splitlines() or [""])
        card = "\n".join(lines)
        shown = printable(card)
        if shown != card:
            shown += "\n  (часть символов не в кодировке консоли и показана как «?»; уйдёт оригинал)"
        return shown


@dataclass(frozen=True)
class Approval:
    """Согласие человека на один конкретный отклик. Создаётся только в :func:`confirm`."""

    vacancy_id: str
    url: str
    title: str | None
    company: str | None
    letter: str
    resume_title: str | None
    #: sha256 карточки, которую человек видел.
    card_digest: str


def _read_line(src: TextIO) -> str:
    line = src.readline()
    if line == "":
        raise CancelledError("ввод закрыт — подтверждать некому")
    return line


def parse_selection(raw: str, count: int) -> set[int] | None:
    """Номера «1 3 5», «1,3», «все». None — ввод непонятен, надо переспросить."""
    text = raw.strip().casefold()
    if not text:
        return set()
    if text in ALL_WORDS:
        return set(range(1, count + 1))
    parts = text.replace(",", " ").split()
    # Только ASCII-цифры: str.isdigit() верно и для «²», которую int() не примет.
    if all(part.isascii() and part.isdigit() and 1 <= int(part) <= count for part in parts):
        return {int(part) for part in parts}
    return None


def confirm(
    candidates: Sequence[Candidate],
    *,
    stream_in: TextIO | None = None,
    stream_out: TextIO | None = None,
) -> list[Approval]:
    """Показать карточки, принять номера и слово, вернуть одобренные отклики."""
    out = stream_out or sys.stdout
    src = stream_in or sys.stdin
    if not candidates:
        return []
    repeated = sorted(vid for vid, n in Counter(c.vacancy_id for c in candidates).items() if n > 1)
    if repeated:
        raise CancelledError(f"одна и та же вакансия в списке дважды: {', '.join(repeated)}")

    print(f"\nГотово к отклику: {len(candidates)}\n", file=out)
    rendered = [candidate.render() for candidate in candidates]
    for index, card in enumerate(rendered, start=1):
        print(f"[{index}] {card}\n", file=out)

    print("Введите номера, на которые ОТПРАВИТЬ отклик, через пробел («все» — все; "
          "пустая строка — ничего):", file=out)
    while True:
        print("> ", end="", file=out)
        out.flush()
        chosen = parse_selection(_read_line(src), len(candidates))
        if chosen is not None:
            break
        print(f"Нужны номера от 1 до {len(candidates)} через пробел, или «все».", file=out)
    if not chosen:
        raise CancelledError("ничего не выбрано")

    picked = sorted(chosen)
    print(f"\nОтправляем {len(picked)} из {len(candidates)}:", file=out)
    for index in picked:
        c = candidates[index - 1]
        print(f"  [{index}] {printable(c.title or 'без названия')} — {printable(c.company or '')}", file=out)
    print(f"Чтобы подтвердить, введите слово «{CONFIRM_WORD}»: ", end="", file=out)
    out.flush()
    if _read_line(src).strip().casefold() != CONFIRM_WORD:
        raise CancelledError("подтверждение не получено")

    return [
        Approval(
            vacancy_id=candidates[i - 1].vacancy_id,
            url=candidates[i - 1].url,
            title=candidates[i - 1].title,
            company=candidates[i - 1].company,
            letter=candidates[i - 1].letter,
            resume_title=candidates[i - 1].resume_title,
            card_digest=hashlib.sha256(rendered[i - 1].encode("utf-8")).hexdigest(),
        )
        for i in picked
    ]
