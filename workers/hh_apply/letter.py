"""Сопроводительное письмо: шаблон из cover_letter.md и подстановка плейсхолдеров.

Шаблон — файл, а не переменная .env: многострочный текст в .env ложится плохо
(кавычки, переносы, BOM от Блокнота). Файл пишет сам пользователь; это его
личный текст, поэтому ``cover_letter.md`` в .gitignore, а в репозитории лежит
заготовка ``cover_letter.example.md``.

Плейсхолдеры — ``{company}``, ``{position}``, ``{my_name}``. Правила
намеренно жёсткие, и каждое — «не печатать, чем напечатать не то»:

* нет файла или он пуст → письма нет, вакансия в ``needs_manual``, ничего не
  печатается;
* в шаблоне плейсхолдер, которого агент не знает (опечатка ``{compnay}``) →
  ``needs_manual``: иначе работодатель получил бы буквальные фигурные скобки;
* значение для плейсхолдера неизвестно (hh не сказал название компании,
  MY_NAME пуст) → ``needs_manual``: письмо «Здравствуйте, !» хуже, чем никакого;
* письмо длиннее лимита hh для этой вакансии → ``needs_manual``: обрезанное
  письмо — это письмо, чей последний абзац работодатель не увидит, а человек
  думает, что отправил всё.

Подстановка — регулярным выражением по известным именам, а не ``str.format``:
у ``format`` любая одиночная фигурная скобка в тексте пользователя —
исключение, а «{» в письме встречается (фрагмент кода, смайлик).

HTML-комментарии ``<!-- ... -->`` из файла вырезаются — в заготовке ими
написаны подсказки, которые не должны уйти работодателю.

Ссылка или ``@`` в письме — не стоп, а предупреждение на карточке: это не
проверяемый факт о вакансии, а риск, который человек оценивает сам (WWAO
считает, что письма со ссылками hh фильтрует, но этого никто не измерял).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Все плейсхолдеры, которые агент умеет заполнять.
PLACEHOLDERS: Final[tuple[str, ...]] = ("company", "position", "my_name")

_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_COMMENT: Final[re.Pattern[str]] = re.compile(r"<!--.*?-->", re.DOTALL)
_LINK: Final[re.Pattern[str]] = re.compile(
    r"https?://|www\.|(?<![\w.])[\w-]+\.(?:ru|kz|com|net|org|io|dev|me|app|рф)\b", re.IGNORECASE
)
_AT: Final[re.Pattern[str]] = re.compile(r"[@＠]")


class LetterUnavailableError(Exception):
    """Письмо для этой вакансии собрать нельзя. Сообщение — для человека, по-русски.

    ``setup=True`` — виноват шаблон или .env (опечатка в плейсхолдере, пустой
    MY_NAME), а не вакансия: такое не пишется в журнал вакансии.
    """

    def __init__(self, message: str, *, setup: bool = False) -> None:
        super().__init__(message)
        self.setup = setup


@dataclass(frozen=True)
class Template:
    text: str
    path: Path

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Имена плейсхолдеров в шаблоне, по порядку появления, без повторов."""
        return tuple(dict.fromkeys(_PLACEHOLDER.findall(self.text)))


def _clean(text: str) -> str:
    text = _COMMENT.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n").strip()


def load_template(path: Path) -> Template | None:
    """Шаблон письма, или None — когда файла нет или в нём ничего, кроме комментариев."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    text = _clean(raw)
    return Template(text=text, path=path) if text else None


def render(
    template: Template,
    *,
    company: str | None,
    position: str | None,
    my_name: str | None,
    max_length: int | None = None,
) -> str:
    """Письмо для одной вакансии. Или LetterUnavailableError со словами, почему нет."""
    values = {"company": company, "position": position, "my_name": my_name}
    unknown = [name for name in template.placeholders if name not in PLACEHOLDERS]
    if unknown:
        raise LetterUnavailableError(
            f"в шаблоне {template.path.name} неизвестные плейсхолдеры: "
            + ", ".join(f"{{{name}}}" for name in unknown)
            + f" (агент знает только {', '.join('{' + p + '}' for p in PLACEHOLDERS)})",
            setup=True,
        )
    missing = [
        name for name in template.placeholders
        if not (values.get(name) or "").strip()
    ]
    if missing:
        where = {
            "company": "hh не сообщил название компании на странице вакансии",
            "position": "hh не сообщил название вакансии",
            "my_name": "MY_NAME в .env пустой",
        }
        raise LetterUnavailableError(
            "письмо не собрать: " + "; ".join(f"{{{name}}} — {where[name]}" for name in missing),
            setup="my_name" in missing,
        )

    letter = _PLACEHOLDER.sub(lambda m: " ".join((values[m.group(1)] or "").split()), template.text)
    if max_length is not None and len(letter) > max_length:
        raise LetterUnavailableError(
            f"письмо длиннее лимита hh для этой вакансии: {len(letter)} > {max_length} символов"
        )
    return letter


def warnings(letter: str) -> tuple[str, ...]:
    """Что стоит показать на карточке про само письмо. Не стопы."""
    said: list[str] = []
    if _LINK.search(letter):
        said.append("в письме есть ссылка — такие письма hh, по наблюдению WWAO, может фильтровать")
    if _AT.search(letter):
        said.append("в письме есть символ @ (почта или ник)")
    return tuple(said)
