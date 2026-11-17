"""Локальный журнал откликов: SQLite на машине владельца.

Отвечает на один вопрос — «разбирался ли я уже с этой вакансией и чем
кончилось» — и явно **не** является источником правды о том, ушёл ли отклик.
Источник правды — hh (``negotiations.total``), и он проверяется перед каждым
кликом: журнал может отстать, hh не отстаёт.

Состояния и кто имеет право их менять (подход WWAO ``agent/state.py``)::

    QUEUED ──человек──▶ CONFIRMED ──▶ SENT
      │                    ├──▶ FAILED ──человек──▶ QUEUED
      ├──▶ SKIPPED         └──▶ NEEDS_MANUAL ──человек──▶ QUEUED | SKIPPED
      └──▶ NEEDS_MANUAL

Подтвердить может только человек; агент может увидеть, что делать нечего или
нужен человек, но не может «увидеть», что человек согласился. ``SENT`` —
терминальное: отклик не отзывается.

Что НЕ хранится: письмо (только его sha256 — хватает, чтобы потом доказать,
что ушло подтверждённое), куки, токены, состояние сессии.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final


class Status(StrEnum):
    QUEUED = "queued"
    CONFIRMED = "confirmed"
    SENT = "sent"
    FAILED = "failed"
    NEEDS_MANUAL = "needs_manual"
    SKIPPED = "skipped"


class Actor(StrEnum):
    HUMAN = "human"
    AGENT = "agent"


TRANSITIONS: Final[dict[tuple[Status, Status], Actor]] = {
    (Status.QUEUED, Status.CONFIRMED): Actor.HUMAN,
    (Status.QUEUED, Status.SKIPPED): Actor.AGENT,
    (Status.QUEUED, Status.NEEDS_MANUAL): Actor.AGENT,
    (Status.QUEUED, Status.FAILED): Actor.AGENT,
    (Status.CONFIRMED, Status.SENT): Actor.AGENT,
    (Status.CONFIRMED, Status.FAILED): Actor.AGENT,
    (Status.CONFIRMED, Status.NEEDS_MANUAL): Actor.AGENT,
    (Status.CONFIRMED, Status.SKIPPED): Actor.AGENT,
    (Status.FAILED, Status.QUEUED): Actor.HUMAN,
    (Status.NEEDS_MANUAL, Status.QUEUED): Actor.HUMAN,
    (Status.NEEDS_MANUAL, Status.SKIPPED): Actor.HUMAN,
}
TERMINAL: Final[frozenset[Status]] = frozenset({Status.SENT, Status.SKIPPED})
#: В каком статусе вакансия может впервые появиться в журнале. Ни SENT, ни
#: CONFIRMED сюда не входят: иначе одна запись создала бы «отправленный» отклик.
INITIAL: Final[frozenset[Status]] = frozenset(
    {Status.QUEUED, Status.SKIPPED, Status.NEEDS_MANUAL, Status.FAILED}
)


class IllegalTransitionError(Exception):
    """Переход, которого нет, или не для этого участника."""


def check(source: Status | None, target: Status, *, actor: Actor) -> None:
    """Бросает, если этот участник не может сделать этот переход. Никогда не возвращает bool."""
    if source is None:
        if target not in INITIAL:
            raise IllegalTransitionError(f"{actor} не может сразу записать вакансию как {target}")
        return
    if source is target:
        return
    if source in TERMINAL or TRANSITIONS.get((source, target)) is not actor:
        raise IllegalTransitionError(f"{actor} не может перевести отклик из {source} в {target}")


def digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS application (
    vacancy_id    TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    title         TEXT,
    company       TEXT,
    url           TEXT,
    letter_digest TEXT,
    reason        TEXT,
    hh_said       TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_application_status ON application (status);
"""


@dataclass(frozen=True)
class Entry:
    vacancy_id: str
    status: Status
    title: str | None = None
    company: str | None = None
    url: str | None = None
    #: sha256 подтверждённого письма. Никогда не само письмо.
    letter_digest: str | None = None
    #: Почему закончилось здесь — словами для человека.
    reason: str | None = None
    #: Что hh сказал в форме отклика, его словами.
    hh_said: str | None = None


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Одно соединение на операцию: нельзя держать блокировку, пока ждём человека."""
        connection = sqlite3.connect(self.path)
        with closing(connection), connection:
            yield connection

    def get(self, vacancy_id: str) -> Entry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT vacancy_id, status, title, company, url, letter_digest, reason, hh_said "
                "FROM application WHERE vacancy_id = ?",
                (vacancy_id,),
            ).fetchone()
        return None if row is None else Entry(row[0], Status(row[1]), *row[2:])

    def record(self, entry: Entry, *, actor: Actor) -> None:
        """Записать состояние вакансии. Переход проверяется здесь — единственной точке записи."""
        previous = self.get(entry.vacancy_id)
        check(None if previous is None else previous.status, entry.status, actor=actor)
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO application (vacancy_id, status, title, company, url, letter_digest,
                                         reason, hh_said, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (vacancy_id) DO UPDATE SET
                    status = excluded.status,
                    title = COALESCE(excluded.title, application.title),
                    company = COALESCE(excluded.company, application.company),
                    url = COALESCE(excluded.url, application.url),
                    letter_digest = COALESCE(excluded.letter_digest, application.letter_digest),
                    reason = excluded.reason,
                    hh_said = COALESCE(excluded.hh_said, application.hh_said),
                    updated_at = excluded.updated_at
                """,
                (entry.vacancy_id, entry.status.value, entry.title, entry.company, entry.url,
                 entry.letter_digest, entry.reason, entry.hh_said, now, now),
            )

    def by_status(self, status: Status) -> list[Entry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT vacancy_id, status, title, company, url, letter_digest, reason, hh_said "
                "FROM application WHERE status = ? ORDER BY updated_at",
                (status.value,),
            ).fetchall()
        return [Entry(row[0], Status(row[1]), *row[2:]) for row in rows]

    def count_sent_since(self, moment: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM application WHERE status = ? AND updated_at >= ?",
                (Status.SENT.value, moment.isoformat()),
            ).fetchone()
        return int(row[0])
