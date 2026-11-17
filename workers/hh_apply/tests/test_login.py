"""Health-check сессии: признаки аккаунта по разнице «аноним против профиля», а не «страница загрузилась».

Анонимная форма здесь — та, что измерена 2026-09-11 на свежем профиле
(evidence/20260911-anonymous-state.json): ``userId`` = null, ``account`` —
словарь из null, ``userLabelsForVacancies`` = {"<id>": []}, а
``applicantVacancyResponseStatuses`` есть и непуст (условия отклика, без
``negotiations``). Именно на этом первая версия записала «вошёл» для анонима.
"""

import json

import pytest

from workers.hh_apply.login import (
    AUTH_KEYS,
    SessionExpiredError,
    SignalUnknownError,
    check_session,
    choose_signal,
    is_empty,
    load_signal,
    looks_authenticated,
    signal_payload,
    top_level_keys,
)

VID = "136457688"
SHELL = {"firstName": None, "middleName": None, "lastName": None, "email": None, "phone": None}


def anonymous_state() -> dict:
    return {
        "userId": None,
        "account": dict(SHELL),
        "hhidAccount": dict(SHELL),
        "userLabelsForVacancies": {VID: []},
        "applicantVacancyResponseStatuses": {VID: {"test": {"hasTests": False}, "letterMaxLength": 10000,
                                                   "usedResumeIds": []}},
        "userType": "anonymous",
        "vacancyView": {"name": "Python"},
    }


def signed_in_state() -> dict:
    state = anonymous_state()
    state["userId"] = 123456
    state["account"] = dict(SHELL, firstName="Нуржан")
    state["applicantVacancyResponseStatuses"][VID]["negotiations"] = {"total": 0, "topicList": []}
    return state


# --- пустота --------------------------------------------------------------------


def test_shells_of_nulls_are_empty():
    assert is_empty(SHELL)
    assert is_empty({VID: []})
    assert is_empty([None, {}, ""])
    assert not is_empty({"firstName": "Нуржан"})
    assert not is_empty(0) and not is_empty(False)


def test_the_anonymous_page_does_not_look_signed_in_by_its_markers_any_more():
    """Регрессия: WWAO-маркеры непусты у анонима, но это оболочки / условия отклика."""
    keys = top_level_keys(anonymous_state())
    assert "userLabelsForVacancies" not in keys
    assert "account" not in keys and "userId" not in keys
    assert "applicantVacancyResponseStatuses" in keys  # есть и у анонима — поэтому это не признак


# --- выбор признака -----------------------------------------------------------------


def test_signed_in_is_decided_by_the_difference_with_the_anonymous_baseline():
    signal = choose_signal(top_level_keys(anonymous_state()), top_level_keys(signed_in_state()))
    assert signal.signed_in and signal.method == "difference"
    assert set(signal.keys) == {"userId", "account"}
    assert set(signal.keys) <= set(AUTH_KEYS)


def test_the_same_page_twice_is_not_signed_in():
    """Именно так ошиблась первая версия: «маркеры есть» при отсутствии разницы."""
    anonymous = top_level_keys(anonymous_state())
    assert not choose_signal(anonymous, anonymous).signed_in


def test_noise_that_is_not_an_account_key_does_not_count_as_a_login():
    signal = choose_signal({"vacancyView"}, {"vacancyView", "someBanner"})
    assert not signal.signed_in and signal.method == "unconfirmed"
    assert signal.also_appeared == ("someBanner",)


# --- файл признака и health-check -----------------------------------------------------


def test_the_signal_round_trips_and_a_live_session_passes(tmp_path):
    path = tmp_path / "session_signal.json"
    signal = choose_signal(top_level_keys(anonymous_state()), top_level_keys(signed_in_state()))
    path.write_text(json.dumps(signal_payload(signal, f"https://almaty.hh.kz/vacancy/{VID}", 80)), encoding="utf-8")
    keys, url = load_signal(path)
    check_session(signed_in_state(), keys)
    assert looks_authenticated(signed_in_state(), path)
    assert url.endswith(VID)


def test_a_logged_out_page_fails_the_check_even_though_it_loaded_fine(tmp_path):
    with pytest.raises(SessionExpiredError, match="userId"):
        check_session(anonymous_state(), ["userId", "account"])


def test_an_account_key_that_is_only_a_shell_of_nulls_counts_as_missing():
    state = signed_in_state()
    state["account"] = dict(SHELL)
    with pytest.raises(SessionExpiredError, match="account"):
        check_session(state, ["userId", "account"])


def test_without_a_signal_file_the_check_refuses_instead_of_degrading(tmp_path):
    with pytest.raises(SignalUnknownError):
        load_signal(tmp_path / "missing.json")
    assert not looks_authenticated(signed_in_state(), tmp_path / "missing.json")
