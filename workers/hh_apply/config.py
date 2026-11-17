"""Настройки агента откликов. Свои и только свои.

Этот воркер не делит конфигурацию ни с ядром лидгена, ни с другими воркерами:
у него другой радиус поражения — он действует под личным аккаунтом человека,
а не анонимно. Поэтому здесь нет ``CORE_URL`` и нет ничего, что вело бы в
ядро; это проверяется тестом (``tests/test_isolation.py``).

Все значения по умолчанию консервативны в сторону «медленно». Темп задуман
человеческим: пауза между откликами — случайная из диапазона (постоянный
интервал — самый узнаваемый признак автомата), у паузы есть нижняя граница,
которую не сдвинуть опечаткой в ``.env``, и у числа откликов за прогон есть
потолок.
"""

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR: Final[Path] = Path(__file__).resolve().parent

#: Всё, что относится к живой сессии hh: профиль Chromium (куки, история) и
#: записанный признак входа. В .gitignore целиком — это самый чувствительный
#: каталог воркера.
SESSION_DIR: Final[Path] = MODULE_DIR / "session"
PROFILE_DIR: Final[Path] = SESSION_DIR / "profile"
SIGNAL_PATH: Final[Path] = SESSION_DIR / "session_signal.json"

#: Скриншоты ошибок: на них залогиненный аккаунт. В .gitignore.
SCREENSHOT_DIR: Final[Path] = MODULE_DIR / "screenshots"
#: Дампы реальных измерений с датами (probe.py). В .gitignore: там разметка
#: страницы, открытой под аккаунтом владельца.
EVIDENCE_DIR: Final[Path] = MODULE_DIR / "evidence"
#: Журнал откликов. В .gitignore (``*.sqlite3``).
JOURNAL_PATH: Final[Path] = MODULE_DIR / "hh_apply.sqlite3"
KEYWORDS_PATH: Final[Path] = MODULE_DIR / "keywords.yml"
#: Шаблон письма пишет сам пользователь; это его личный текст, в .gitignore.
#: Рядом лежит cover_letter.example.md — заготовка, которую можно коммитить.
LETTER_PATH: Final[Path] = MODULE_DIR / "cover_letter.md"

#: Сайты hh, на которых агент вообще готов открывать страницы.
HH_SITES: Final[frozenset[str]] = frozenset({"hh.kz", "hh.ru", "hh.uz"})

#: Нижняя граница паузы между откликами. Опечатка «APPLY_DELAY_SECONDS=2» не
#: должна превращать агента в пулемёт на личном аккаунте.
MIN_APPLY_DELAY_SECONDS: Final[float] = 10.0
#: Потолок откликов за один прогон, какое бы число ни стояло в .env.
HARD_MAX_APPLIES_PER_RUN: Final[int] = 50

_RANGE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:-|–|\.\.)\s*(\d+(?:[.,]\d+)?)\s*$")
_SINGLE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")


@dataclass(frozen=True)
class DelayRange:
    """Пауза «от и до», в секундах. Каждая пауза тянется заново."""

    low: float
    high: float

    @classmethod
    def parse(cls, raw: str | float | int) -> "DelayRange":
        """«20-30», «20–30», «20..30» или одно число «25»."""
        text = str(raw)
        match = _RANGE.match(text)
        if match:
            low, high = (float(group.replace(",", ".")) for group in match.groups())
        else:
            match = _SINGLE.match(text)
            if not match:
                raise ValueError(f"ожидается число или диапазон вида «20-30», а не {text!r}")
            low = high = float(match.group(1).replace(",", "."))
        if low > high:
            low, high = high, low
        return cls(low, high)

    def draw(self, rng: random.Random) -> float:
        """Одна пауза. Генератор передаётся снаружи, чтобы тест мог его зафиксировать."""
        return rng.uniform(self.low, self.high)

    def __str__(self) -> str:
        return f"{self.low:g}–{self.high:g} с" if self.low != self.high else f"{self.low:g} с"


def base_site(host: str) -> str:
    """Две последние метки имени хоста: ``almaty.hh.kz`` -> ``hh.kz``."""
    labels = [label for label in host.strip().lower().rstrip(".").split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


class Settings(BaseSettings):
    """Из переменных окружения / workers/hh_apply/.env (путь привязан к модулю)."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # -sig: терпим BOM, который ставит Блокнот
        extra="ignore",
    )

    #: Хост hh. Корень страны: hh сам перенаправляет на региональный поддомен,
    #: и browser.open_hh_page этот редирект переживает.
    hh_host: str = "hh.kz"
    #: Подставляется в {my_name} шаблона письма. Пусто, а в шаблоне есть
    #: {my_name} — вакансия уходит в needs_manual, письмо не печатается.
    my_name: str = ""

    #: Пауза между откликами: диапазон, случайная величина из него.
    apply_delay_seconds: str = "20-30"
    #: Пауза между открытиями страниц при поиске и подготовке карточек.
    browse_delay_seconds: str = "3-6"
    max_applies_per_run: int = Field(default=20, ge=1, le=HARD_MAX_APPLIES_PER_RUN)
    #: Сколько страниц вакансий прогон вообще готов открыть, пока собирает карточки.
    max_vacancies_to_inspect: int = Field(default=40, ge=1, le=200)
    #: Страниц выдачи поиска на один запрос из keywords.yml.
    search_pages: int = Field(default=1, ge=1, le=5)
    #: Вторая ошибка подряд останавливает прогон: что-то изменилось, пусть посмотрит человек.
    stop_after_consecutive_failures: int = Field(default=2, ge=1, le=5)
    #: Замедление каждого действия браузера, мс. Не маскировка: чтобы человек
    #: у окна успевал видеть, что происходит, и мог остановить.
    slow_mo_ms: int = Field(default=250, ge=0, le=2000)
    log_level: str = "INFO"

    @field_validator("apply_delay_seconds")
    @classmethod
    def _apply_delay_is_human(cls, value: str) -> str:
        delay = DelayRange.parse(value)
        if delay.low < MIN_APPLY_DELAY_SECONDS:
            raise ValueError(
                f"APPLY_DELAY_SECONDS={value!r}: пауза между откликами меньше "
                f"{MIN_APPLY_DELAY_SECONDS:g} с не принимается — темп должен быть человеческим"
            )
        return value

    @field_validator("browse_delay_seconds")
    @classmethod
    def _browse_delay_parses(cls, value: str) -> str:
        DelayRange.parse(value)
        return value

    @field_validator("hh_host")
    @classmethod
    def _host_is_hh(cls, value: str) -> str:
        host = re.sub(r"^[a-z]+://", "", value.strip().lower()).split("/", 1)[0]
        if base_site(host) not in HH_SITES:
            raise ValueError(f"HH_HOST={value!r} — это не сайт hh ({', '.join(sorted(HH_SITES))})")
        return host

    @property
    def apply_delay(self) -> DelayRange:
        return DelayRange.parse(self.apply_delay_seconds)

    @property
    def browse_delay(self) -> DelayRange:
        return DelayRange.parse(self.browse_delay_seconds)

    @property
    def site(self) -> str:
        """Базовый сайт (``hh.kz``), с которым сверяется каждая открытая страница."""
        return base_site(self.hh_host)
