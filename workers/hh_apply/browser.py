"""Браузер владельца: видимое окно, свой профиль, сетевой предохранитель, переходы по hh.

**Окно всегда видимое, и это не параметр.** ``headless=False`` вписан в вызов,
а не вынесен в настройку: владелец должен видеть, что происходит на его
аккаунте, и иметь возможность остановить. Флаг — это то, что кто-нибудь
однажды передаст. Это порог, а не стена: под виртуальным дисплеем окно можно
спрятать, и README так и пишет.

**Вход — руками, пароль агент не видит и не хранит.** Сессия живёт в профиле
Chromium в ``session/profile`` (в .gitignore). В коде нет обращений к кукам и
``storage_state`` — нечему утечь, потому что нечего читать.

**Service workers заблокированы, и это аргумент безопасности.** ``context.route``
не видит запросов из service worker'а; без блокировки сетевой предохранитель
ниже был бы неполным (урок WWAO, ``agent/browser.py``).

**Сетевой предохранитель (NetGuard).** Отклик уходит не «когда нажата кнопка»,
а когда из браузера уходит запрос; способов его вызвать много (клик, Enter в
поле, обработчик на blur). Поэтому охраняется сеть:

* вне окна подтверждения **любой не-GET запрос обрывается**. Отклик — не-GET,
  значит в режиме поиска, подготовки карточек и разведки отправить что-либо
  физически нельзя. Измерено в WWAO 2026-09-06: форма отклика при этом
  открывается целиком (её содержимое приходит обычным GET
  ``/applicant/vacancy_response/popup?vacancyId=…``);
* окно открывается только на вакансию, которую человек подтвердил, и внутри
  него обрывается всё, что называет **другую** вакансию — в адресе или в теле.

Импорт playwright — внутри функции: все чистые модули пакета (письмо, стопы,
журнал, разбор состояния) импортируются и тестируются без браузера.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import unquote, urlsplit

from .config import PROFILE_DIR, SCREENSHOT_DIR, base_site
from .page_state import vacancy_id_from_url

if TYPE_CHECKING:  # pragma: no cover - playwright нужен только этому воркеру
    from playwright.sync_api import BrowserContext

#: Замедление действий, мс. Не маскировка — чтобы человек у окна видел, что происходит.
DEFAULT_SLOW_MO_MS: Final[int] = 250
DEFAULT_TIMEOUT_MS: Final[int] = 30_000

#: Куда hh уводит браузер, решив, что это робот. Измерено краулером WWAO
#: 2026-09-06: ``GET /vacancy/<id>`` -> ``302`` на ``/account/captcha?backurl=…``.
#: Определяется по АДРЕСУ, а не по словам на странице: hh возит в каждой странице
#: словарь ошибок с ключом ``error.signup.captcha.invalid``, и поиск слова в теле
#: отвечал «капча» на любую вакансию (урок WWAO, ``agent/submit.py``).
CAPTCHA_PATHS: Final[tuple[str, ...]] = ("/account/captcha",)

#: Что Playwright пишет, когда навигацию перебивает другая — ровно так выглядит
#: редирект hh на региональный поддомен после входа.
INTERRUPTED_NAVIGATION: Final[tuple[str, ...]] = ("net::ERR_ABORTED", "interrupted by another navigation")

#: Символы, которые Python и Chromium читают в адресе по-разному
#: (``https://evil.com\\@hh.kz``). В адресе вакансии им взяться неоткуда.
AMBIGUOUS_IN_A_URL: Final[frozenset[str]] = frozenset({"\\", "\r", "\n", "\t"})

#: Форма отклика живёт под этим путём (измерено в WWAO 2026-09-06).
APPLICATION_PATH: Final[str] = "/applicant/vacancy_response"
_NAMED_ID = re.compile(r"vacanc(?:y[ _-]?id|yid)\"?\s*[:=]\s*\"?(\d{4,})", re.IGNORECASE)
_PATH_ID = re.compile(r"/vacanc(?:y|y_response)/(\d{4,})")


class BrowserUnavailableError(RuntimeError):
    """playwright или его браузер не установлены."""


class NavigatedElsewhereError(RuntimeError):
    """Открылась не та страница, которую просили."""


class CaptchaError(NavigatedElsewhereError):
    """hh проверяет, не робот ли это. Агент её не решает — никогда."""


@contextmanager
def open_browser(
    *, profile_dir: Path = PROFILE_DIR, slow_mo_ms: int = DEFAULT_SLOW_MO_MS
) -> "Iterator[BrowserContext]":
    """Браузер владельца, видимый, с той сессией, что уже есть в профиле."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - окружение, не логика
        raise BrowserUnavailableError(
            "playwright не установлен: pip install -r workers/hh_apply/requirements.txt "
            "&& python -m playwright install chromium"
        ) from exc

    profile_dir.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,  # всегда видимое окно; см. docstring модуля
            slow_mo=slow_mo_ms,
            service_workers="block",  # иначе context.route видит не всё; см. docstring
        )
        try:
            yield context
        finally:
            context.close()


@contextmanager
def open_throwaway_browser(*, slow_mo_ms: int = DEFAULT_SLOW_MO_MS) -> "Iterator[BrowserContext]":
    """Одноразовый браузер без профиля и без кук — чтобы мерить, как страницу видит аноним.

    Тоже видимый. Ничего не сохраняет: контекст живёт в памяти и закрывается.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - окружение, не логика
        raise BrowserUnavailableError("playwright не установлен") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, slow_mo=slow_mo_ms)
        context = browser.new_context(service_workers="block")
        try:
            yield context
        finally:
            context.close()
            browser.close()


def screenshot(page: Any, name: str) -> Path | None:
    """Картинка того, что пошло не так. Пиксели, не куки. В .gitignore."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:  # noqa: BLE001 - неудачный скриншот не должен прятать саму ошибку
        return None
    return path


# ── сетевой предохранитель ──────────────────────────────────────────────


def looks_like_application(url: str) -> bool:
    """Может ли этот адрес быть запросом отклика. Широко нарочно."""
    parts = urlsplit(url)
    return parts.path.startswith(APPLICATION_PATH) or "vacancyId=" in parts.query


def vacancy_ids_in(url: str, body: str | None = None) -> frozenset[str]:
    """Все id вакансий, которые называет запрос: в адресе, в пути, в теле, в %-кодировке."""
    found: set[str] = set()
    for raw in (url, body):
        if raw:
            text = unquote(raw)
            found.update(_NAMED_ID.findall(text))
            found.update(_PATH_ID.findall(text))
    return frozenset(found)


def _body(request: Any) -> str | None:
    try:
        data = getattr(request, "post_data", None)
    except Exception:  # noqa: BLE001 - тело, которое не отдаётся, — это «тела не видно»
        return None
    return data if isinstance(data, str) else None


class NetGuard:
    """Обрывает любой не-GET запрос вне окна, открытого подтверждением человека.

    Не ``slots``: playwright при регистрации обработчика делает ``setattr`` на
    владельца связанного метода (урок WWAO, ``agent/gate.py``).
    """

    def __init__(self) -> None:
        self._armed_for: str | None = None
        #: Что было оборвано и почему — для отчёта прогона.
        self.blocked: list[str] = []
        #: Не-GET запросы, пропущенные внутри окна подтверждения.
        self.allowed_while_armed: list[str] = []

    @property
    def armed_for(self) -> str | None:
        return self._armed_for

    @contextmanager
    def armed(self, vacancy_id: str) -> Iterator[None]:
        """Окно, в котором может уйти отклик на ЭТУ вакансию. Закрывается при любом исходе."""
        if not vacancy_id or not vacancy_id.isdigit():
            raise ValueError(f"окно открывается только на id вакансии, а не на {vacancy_id!r}")
        self._armed_for = vacancy_id
        try:
            yield
        finally:
            self._armed_for = None

    def _block(self, route: Any, line: str) -> None:
        self.blocked.append(line)
        route.abort()

    def handle(self, route: Any) -> None:
        """Обработчик ``context.route("**/*", ...)``. Через него проходит каждый запрос."""
        request = route.request
        method = str(request.method).upper()
        url = str(request.url)
        armed = self._armed_for
        if armed is None:
            if method != "GET":
                self._block(route, f"{method} {url} — вне окна подтверждения")
                return
            route.continue_()
            return
        strangers = vacancy_ids_in(url, _body(request)) - {armed}
        if strangers and (method != "GET" or looks_like_application(url)):
            self._block(route, f"{method} {url} — чужие вакансии {sorted(strangers)}")
            return
        if method != "GET":
            self.allowed_while_armed.append(f"{method} {url}")
        route.continue_()


def install_guard(context: Any) -> NetGuard:
    """Повесить предохранитель на весь контекст. До первой открытой страницы."""
    guard = NetGuard()
    context.route("**/*", guard.handle)
    return guard


# ── переходы по hh ──────────────────────────────────────────────────────


def is_captcha(url: str) -> bool:
    """Проверка на робота — по пути адреса, как путь, а не как подстрока."""
    path = urlsplit(url).path.rstrip("/")
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in CAPTCHA_PATHS)


def on_site(url: str, site: str) -> bool:
    """https-адрес на этом сайте hh (региональные поддомены — тоже он)."""
    if any(bad in url for bad in AMBIGUOUS_IN_A_URL):
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and bool(host) and base_site(host) == site


def open_hh_page(
    page: Any,
    url: str,
    *,
    site: str,
    expect_vacancy: str | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> str:
    """Открыть страницу hh, пережить региональный редирект и проверить, куда попали.

    Три вопроса, первый — ДО того, как браузер куда-то пошёл: адрес на hh?
    Приземлились на hh (капча — отдельная ошибка)? Это та вакансия? Страница,
    которая не прошла проверку, не читается как та, о которой шла речь.
    """
    if not on_site(url, site):
        raise NavigatedElsewhereError(f"Адрес не на {site}, агент его не откроет: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as error:
        if not any(marker in str(error) for marker in INTERRUPTED_NAVIGATION):
            raise
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

    landed = str(page.url)
    if is_captcha(landed):
        raise CaptchaError(
            f"hh показывает проверку на робота: {landed}\n"
            "Агент её не решает. Пройдите её руками в открытом окне и запустите прогон заново."
        )
    if not on_site(landed, site):
        raise NavigatedElsewhereError(f"Открылась страница не на {site}: {landed} (просили {url})")
    if expect_vacancy is not None and vacancy_id_from_url(landed) != expect_vacancy:
        raise NavigatedElsewhereError(
            f"Просили вакансию {expect_vacancy}, а открылось: {landed}\n"
            "Так выглядит требование войти, архивная вакансия или капча."
        )
    return landed


def vacancy_url(host: str, vacancy_id: str) -> str:
    return f"https://{host}/vacancy/{vacancy_id}"
