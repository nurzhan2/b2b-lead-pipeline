"""Вход — руками, один раз — и измерение того, как выглядит «вошёл».

Агент пароль не видит, не вводит и не хранит: открывается видимое окно на
профиле ``session/profile``, человек входит сам. Скрипт записывает одно — какие
ключи состояния страницы вакансии заполнены только под аккаунтом — в
``session/session_signal.json``.

**Зачем.** Куки протухают по своему расписанию, и симптом коварный: страницы
открываются, а отклики молча перестают уходить. Проверка «страница
загрузилась» такое пропускает, проверка «признаки аккаунта на месте» — нет.
Health-check (:func:`check_session`) требует ровно те ключи, что записаны
здесь, и без файла отказывается работать, а не деградирует до слабой проверки.

**Признак — только из измерения «аноним против профиля», каждый раз.** Первая
версия взяла у WWAO запасное правило «если ключи
``applicantVacancyResponseStatuses`` / ``userLabelsForVacancies`` непусты —
профиль уже авторизован». Измерено 2026-09-11 на СВЕЖЕМ профиле этого воркера
(``evidence/20260911-anonymous-state.json``): у анонимного посетителя оба ключа
есть и формально непусты — ``applicantVacancyResponseStatuses[<id>]`` несёт
условия отклика (без ``negotiations``), ``userLabelsForVacancies`` =
``{"<id>": []}``. Правило записало «вошёл» для анонима, и health-check
пропускал бы разлогиненную сессию — ровно то, от чего он должен защищать.
Файл удалён, правило убрано.

Теперь так:

* пустота — рекурсивная: словарь из одних ``null``/``[]``/``{}`` — пустая
  оболочка (у анонима ``account`` = ``{firstName: null, …}``, ``userId`` = ``null``);
* анонимная база **меряется каждый раз** в отдельном одноразовом браузере без
  кук, на той же вакансии — не вписывается и не вспоминается;
* «вошёл» — это когда в профиле непусты ключи, которые у анонима пусты, и среди
  них есть :data:`AUTH_KEYS`. Сами ключи для health-check берутся из этой
  разницы, а не из списка;
* каждое измерение пишется в ``evidence/<дата-время>-login-signal.json``.

**Во время входа сетевой предохранитель на профиль не ставится**: вход — это
POST, оборвать его значило бы не дать человеку войти. Скрипт ничего не
откликает и ничего не нажимает — только переходит по страницам.

    python -m workers.hh_apply.login              # ждёт, пока вы войдёте в окне
    python -m workers.hh_apply.login --enter      # или ждёт Enter в консоли
    python -m workers.hh_apply.login --vacancy 136457688
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from .browser import (
    NavigatedElsewhereError,
    install_guard,
    open_browser,
    open_hh_page,
    open_throwaway_browser,
    vacancy_url,
)
from .config import EVIDENCE_DIR, KEYWORDS_PATH, SIGNAL_PATH, Settings
from .page_state import read_state, vacancy_id_from_url

#: Ключи, которые у анонимного посетителя ЕСТЬ, но пусты (измерено 2026-09-11,
#: ``evidence/20260911-anonymous-state.json``): ``userId`` = null, ``account`` и
#: ``hhidAccount`` = {firstName: null, lastName: null, email: null, phone: null}.
#: Что под аккаунтом они заполняются — не вписано, а проверяется разницей при
#: каждом входе: признак сессии записывается только из тех, что реально появились.
AUTH_KEYS: Final[tuple[str, ...]] = ("userId", "account", "hhidAccount")
#: Поле файла, которое читает health-check. Писатель и читатель — в одном модуле.
SIGNAL_FIELD: Final[str] = "appeared_after_login"
LOGIN_PATH: Final[str] = "/account/login"
POLL_SECONDS: Final[float] = 3.0


class SessionExpiredError(Exception):
    """Профиль браузера больше не авторизован (или никогда не был)."""


class SignalUnknownError(Exception):
    """Ещё никто не записал, как выглядит страница под аккаунтом."""


def is_empty(value: object) -> bool:
    """Пусто ли значение по-настоящему: null, "", [], {} — и контейнеры только из них."""
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, dict):
        return all(is_empty(item) for item in value.values())
    if isinstance(value, list):
        return all(is_empty(item) for item in value)
    return False


def top_level_keys(state: dict[str, Any]) -> set[str]:
    """Ключи верхнего уровня с настоящим содержимым (оболочки из null не в счёт)."""
    return {key for key, value in state.items() if not is_empty(value)}


@dataclass(frozen=True)
class Signal:
    #: Ключи, которые health-check будет требовать непустыми.
    keys: tuple[str, ...]
    #: ``difference`` — вход подтверждён разницей; ``unconfirmed`` — что-то
    #: появилось, но не ключи аккаунта; ``none`` — разницы нет.
    method: str
    #: Всё, что появилось, кроме выбранного, — для человека, не для проверки.
    also_appeared: tuple[str, ...] = ()

    @property
    def signed_in(self) -> bool:
        return bool(self.keys)


def choose_signal(anonymous: set[str], profile: set[str]) -> Signal:
    """Что значит «вошёл» — из измерения «аноним против профиля». Чистая функция.

    Только ключи аккаунта, которые реально появились. Всю разницу требовать
    нельзя: в ней бывают ключи, меняющиеся между двумя загрузками одной
    страницы, и обычное изменение страницы читалось бы как «сессия протухла».
    """
    appeared = profile - anonymous
    confirmed = tuple(key for key in AUTH_KEYS if key in appeared)
    if confirmed:
        return Signal(confirmed, "difference", tuple(sorted(appeared - set(confirmed))))
    return Signal((), "unconfirmed" if appeared else "none", tuple(sorted(appeared)))


def signal_payload(signal: Signal, probe_url: str, anonymous_keys: int) -> dict[str, Any]:
    return {
        "probe_url": probe_url,
        SIGNAL_FIELD: list(signal.keys),
        "how_chosen": signal.method,
        "also_appeared_not_asserted": list(signal.also_appeared),
        "anonymous_baseline_nonempty_keys": anonymous_keys,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "note": (
            "Ключи, пустые у анонима (измерено тут же, в одноразовом браузере без кук) и заполненные "
            "в профиле. Перед каждым прогоном проверяется, что они на месте: пропали — сессия протухла."
        ),
    }


def load_signal(path: Path = SIGNAL_PATH) -> tuple[list[str], str]:
    """Записанные признаки входа и страница, на которой их мерили."""
    if not path.is_file():
        raise SignalUnknownError(
            f"Нет {path.name}: неизвестно, как выглядит страница под аккаунтом.\n"
            "Сначала: python -m workers.hh_apply.login"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise SignalUnknownError(f"{path.name} — не JSON: {error}") from error
    keys = payload.get(SIGNAL_FIELD) if isinstance(payload, dict) else None
    url = payload.get("probe_url") if isinstance(payload, dict) else None
    if not isinstance(keys, list) or not keys or not isinstance(url, str):
        raise SignalUnknownError(f"{path.name} не содержит признаков авторизации")
    return [str(key) for key in keys], url


def missing_signal_keys(state: dict[str, Any], keys: list[str]) -> list[str]:
    return [key for key in keys if is_empty(state.get(key))]


def check_session(state: dict[str, Any], keys: list[str]) -> None:
    """Бросает, если страница больше не выглядит авторизованной. Нужны ВСЕ ключи."""
    missing = missing_signal_keys(state, keys)
    if missing:
        raise SessionExpiredError(
            "Сессия hh больше не активна — пропали признаки входа: "
            f"{', '.join(missing)}.\nВойдите заново: python -m workers.hh_apply.login"
        )


def looks_authenticated(state: dict[str, Any], path: Path = SIGNAL_PATH) -> bool:
    """Есть ли на странице все записанные признаки аккаунта. Без файла — честное «нет»."""
    try:
        keys, _ = load_signal(path)
    except SignalUnknownError:
        return False
    return not missing_signal_keys(state, keys)


def ensure_session(page: Any, settings: Settings, path: Path = SIGNAL_PATH) -> None:
    """Health-check перед прогоном: на той же странице, где признак мерили."""
    keys, probe_url = load_signal(path)
    vacancy_id = vacancy_id_from_url(probe_url)
    try:
        open_hh_page(page, probe_url, site=settings.site, expect_vacancy=vacancy_id)
    except NavigatedElsewhereError as error:
        raise SessionExpiredError(
            f"Страница для проверки сессии не открылась как надо: {error}\n"
            "Если вакансия ушла в архив, перезапишите признак: python -m workers.hh_apply.login"
        ) from error
    check_session(read_state(page.content()) or {}, keys)


def _keys_on(page: Any) -> set[str]:
    try:
        return top_level_keys(read_state(page.content()) or {})
    except Exception:  # noqa: BLE001 - страница как раз перерисовывается
        return set()


def _anonymous_baseline(settings: Settings, vacancy_id: str | None) -> tuple[str, str, set[str]]:
    """Одноразовый браузер без кук: вакансия (если не задана — первая из поиска) и её анонимные ключи."""
    from .search import load_keywords, search_url, vacancy_ids_in_html

    with open_throwaway_browser(slow_mo_ms=0) as context:
        install_guard(context)  # аноним только читает
        page = context.new_page()
        if vacancy_id is None:
            keywords = load_keywords(KEYWORDS_PATH)
            open_hh_page(page, search_url(settings.hh_host, keywords.queries[0], 0), site=settings.site)
            ids = vacancy_ids_in_html(page.content())
            if not ids:
                raise SystemExit("В выдаче нет вакансий для измерения. Укажите: --vacancy <id>")
            vacancy_id = ids[0]
        url = vacancy_url(settings.hh_host, vacancy_id)
        try:
            landed = open_hh_page(page, url, site=settings.site, expect_vacancy=vacancy_id, timeout_ms=60_000)
        except NavigatedElsewhereError:
            raise
        except Exception as error:  # noqa: BLE001 - один повтор: измерено 2026-09-11, первая загрузка
            # свежего анонимного браузера однажды не уложилась в 30 с, следующая прошла
            print(f"Анонимная страница не загрузилась ({type(error).__name__}), пробую ещё раз…", flush=True)
            landed = open_hh_page(page, url, site=settings.site, expect_vacancy=vacancy_id, timeout_ms=60_000)
        return vacancy_id, landed.split("?", 1)[0], _keys_on(page)


def _wait_by_polling(page: Any, settings: Settings, probe_url: str, vacancy_id: str,
                     anonymous: set[str], minutes: float) -> Signal | None:
    """Ждать входа, не трогая консоль: смотрим адрес окна, ничего не вводим.

    Пока окно на /account/… — человек входит, мешать нельзя. Ушёл оттуда —
    открываем вакансию и сверяем с анонимной базой.
    """
    deadline = time.monotonic() + minutes * 60
    went_through_account = True
    last = Signal((), "none")
    while time.monotonic() < deadline:
        try:
            path = urlsplit(str(page.url)).path
        except Exception:  # noqa: BLE001
            path = ""
        if path.startswith("/account"):
            went_through_account = True
        else:
            if went_through_account:
                try:
                    open_hh_page(page, probe_url, site=settings.site, expect_vacancy=vacancy_id)
                except NavigatedElsewhereError:
                    time.sleep(POLL_SECONDS)
                    continue
                went_through_account = False
            if vacancy_id_from_url(str(page.url)) == vacancy_id:
                last = choose_signal(anonymous, _keys_on(page))
                if last.signed_in:
                    return last
        time.sleep(POLL_SECONDS)
    if last.also_appeared:
        print(f"Появились ключи, но не ключи аккаунта: {', '.join(last.also_appeared)}")
    return None


def _write_evidence(anonymous: set[str], profile: set[str], signal: Signal, probe_url: str,
                    profile_read_on: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = EVIDENCE_DIR / f"{stamp}-login-signal.json"
    path.write_text(json.dumps({
        "schema": "hh-apply-measurement/1",
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "question": "Какие ключи состояния страницы вакансии пусты у анонима и заполнены под аккаунтом?",
        "probe_url": probe_url,
        "anonymous_nonempty_top_level": sorted(anonymous),
        # Со страницы, где было окно профиля в момент записи: если это не вакансия
        # (например, вход так и не выполнен и окно осталось на /account/login),
        # сравнение с анонимом ничего не значит — и это видно по этому полю.
        "profile_read_on": profile_read_on.split("?", 1)[0],
        "profile_nonempty_top_level": sorted(profile),
        "appeared": sorted(profile - anonymous),
        "chosen_signal": list(signal.keys),
        "how": "Аноним — одноразовый браузер без кук; профиль — session/profile после входа руками.",
        "produced_by": "workers/hh_apply/login.py",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workers.hh_apply.login",
                                     description="Вход в hh руками и запись признака сессии")
    parser.add_argument("--vacancy", help="id вакансии, на которой мерить признак (по умолчанию — первая из поиска)")
    parser.add_argument("--enter", action="store_true", help="ждать Enter в консоли вместо слежения за окном")
    parser.add_argument("--minutes", type=float, default=15.0, help="сколько ждать входа (по умолчанию 15)")
    args = parser.parse_args(argv)
    settings = Settings()

    vacancy_id, probe_url, anonymous = _anonymous_baseline(settings, args.vacancy)
    print(f"Анонимная база измерена на вакансии {vacancy_id}: непустых ключей {len(anonymous)}.")

    print("Откроется окно браузера. Войдите в аккаунт hh РУКАМИ — пароль этот агент не видит и не хранит.")
    with open_browser(slow_mo_ms=settings.slow_mo_ms) as context:
        page = context.new_page()
        open_hh_page(page, probe_url, site=settings.site, expect_vacancy=vacancy_id)
        profile = _keys_on(page)
        signal = choose_signal(anonymous, profile)
        if signal.signed_in:
            print("Профиль уже авторизован (подтверждено разницей с анонимом) — входить не нужно.")
        else:
            open_hh_page(page, f"https://{settings.hh_host}{LOGIN_PATH}", site=settings.site)
            page.bring_to_front()  # окно входа не должно потеряться за другими окнами
            if args.enter:
                print("Войдите в открытом окне, потом нажмите Enter здесь: ", end="", flush=True)
                sys.stdin.readline()
                open_hh_page(page, probe_url, site=settings.site, expect_vacancy=vacancy_id)
                signal = choose_signal(anonymous, _keys_on(page))
            else:
                print(f"Жду входа в открытом окне (до {args.minutes:g} мин)…", flush=True)
                signal = _wait_by_polling(page, settings, probe_url, vacancy_id, anonymous, args.minutes) or signal
            profile = _keys_on(page)

        evidence = _write_evidence(anonymous, profile, signal, probe_url, str(page.url))
        if not signal.signed_in:
            where = urlsplit(str(page.url)).path
            hint = " — окно так и осталось на странице входа" if where.startswith("/account") else ""
            print(f"Признаков аккаунта нет — вход не выполнен или не распознан{hint}.\n"
                  f"Окно было на: {str(page.url).split('?', 1)[0]}\nИзмерение: {evidence}")
            return 1
        SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        SIGNAL_PATH.write_text(json.dumps(signal_payload(signal, probe_url, len(anonymous)),
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Вход виден. Признаки аккаунта: {', '.join(signal.keys)}.")
        print(f"Записано: {SIGNAL_PATH}\nИзмерение: {evidence}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
